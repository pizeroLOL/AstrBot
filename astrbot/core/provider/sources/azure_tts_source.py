import hashlib
import json
import random
import re
import time
import uuid
import requests
import xml.etree.ElementTree as Et
from os import remove
from pathlib import Path
from typing import Optional, Dict

from azure.cognitiveservices.speech import (
    CancellationReason,
    ResultReason,
    SpeechConfig,
    SpeechSynthesizer,
)
from azure.cognitiveservices.speech.audio import AudioOutputConfig

from astrbot.core import logger

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter

class BaseAzureTTS(TTSProvider):
    TEMP_DIR = Path("data/temp/Azure_TTS/")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    def __init__(self, provider_config: dict, provider_settings: dict):
        super().__init__(provider_config, provider_settings)
        self._load_config(provider_config)
    def _load_config(self, config: Dict):
        self.voice = config.get("azure_tts_voice", "zh-CN-YunxiaNeural")
        self.style = config.get("azure_tts_style", "cheerful")
        self.role = config.get("azure_tts_role", "Boy")
        self.rate = config.get("azure_tts_rate", "1.0")
        self.volume = config.get("azure_tts_volume", "100")

    def _cleanup(self, path: Path):
        for _ in range(3):
            try:
                if path.exists():
                    path.unlink()
                    return
            except PermissionError:
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"文件清理失败: {str(e)}")
                break

class NativeAzureTTS(BaseAzureTTS):
    def __init__(self, provider_config: dict, provider_settings: dict):
        super().__init__(provider_config, provider_settings)
        sub_key = provider_config["azure_tts_subscription_key"]
        if not re.fullmatch(r"^[a-fA-F0-9]{32}$", sub_key):
            raise ValueError("无效的Azure订阅密钥格式")
        self.speech_config = SpeechConfig(
            subscription=sub_key,
            region=provider_config["azure_tts_region"]
        )

    def get_audio(self, text: str) -> str:
        file_path = self.TEMP_DIR / f"azure_{uuid.uuid4()}.wav"
        synthesizer = None
        try:
            synthesizer = SpeechSynthesizer(
                speech_config=self.speech_config,
                audio_config=AudioOutputConfig(filename=str(file_path))
            )
            ssml = self._build_ssml(text)
            result = synthesizer.speak_ssml(ssml)
            if result.reason == ResultReason.SynthesizingAudioCompleted:
                return str(file_path)
            self._handle_error(result, file_path)
        except Exception as e:
            self._cleanup(file_path)
            logger.error(f"语音合成失败: {str(e)}")
            raise
        finally:
            if synthesizer:
                synthesizer.dispose()

    def _build_ssml(self, text: str) -> str:
        return f'''<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis'
            xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='zh-CN'>
            <voice name='{self.voice}'>
                <mstts:express-as style='{self.style}' role='{self.role}'>
                    <prosody rate='{self.rate}' volume='{self.volume}'>
                        {text}
                    </prosody>
                </mstts:express-as>
            </voice>
        </speak>'''

    def _handle_error(self, result, file_path: Path):
        if result.reason == ResultReason.Canceled:
            cancellation = result.cancellation_details
            error_msg = f"合成取消: {cancellation.reason}"
            if cancellation.reason == CancellationReason.Error:
                error_msg += f", 错误详情: {cancellation.error_details}"
            logger.error(error_msg)
        self._cleanup(file_path)
        raise RuntimeError(error_msg)

class OTTSClient(BaseAzureTTS):
    def __init__(self, provider_config: dict, provider_settings: dict):
        super().__init__(provider_config, provider_settings)
        self.skey = provider_config["TTS_SKEY"]
        self.api_url = provider_config["TTS_URL"].rstrip('/')
        self.auth_time_url = provider_config["TTS_AUTH_TIME"]
        self.session = requests.Session()
        self.time_offset = 0

    def get_audio(self, text: str) -> str:
        file_path = self.TEMP_DIR / f"otts_{uuid.uuid4()}.wav"
        try:
            signed_url = self._generate_signed_url()
            response = self.session.post(
                signed_url,
                data={
                    "text": text,
                    "voice": self.voice,
                    "style": self.style,
                    "role": self.role,
                    "rate": self.rate,
                    "volume": self.volume
                },
                timeout=10
            )
            response.raise_for_status()
            with open(file_path, "wb") as f:
                f.write(response.content)
            return str(file_path)
        except requests.exceptions.RequestException as e:
            self._cleanup(file_path)
            logger.error(f"API请求失败: {str(e)}")
            raise RuntimeError("语音服务暂时不可用") from e

    def _generate_signed_url(self) -> str:
        timestamp, nonce = self._get_sync_time()
        path = '/' + self.api_url.split('//', 1)[-1].split('/', 1)[-1]
        signature = hashlib.md5(
            f"{path}-{timestamp}-{nonce}-0-{self.skey}".encode()
        ).hexdigest()
        return f"{self.api_url}?sign={timestamp}-{nonce}-0-{signature}"

    def _get_sync_time(self) -> tuple[int, str]:
        try:
            response = self.session.get(self.auth_time_url, timeout=3)
            server_time = response.json()["timestamp"]
            local_time = int(time.time())
            self.time_offset = server_time - local_time
            return server_time, self._generate_nonce()
        except Exception as e:
            logger.warning(f"时间同步失败: {str(e)}")
            return int(time.time()) + self.time_offset, self._generate_nonce()

    def _generate_nonce(self) -> str:
        return ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=10))

def create_provider(provider_config: dict, provider_settings: dict) -> BaseAzureTTS:
    sub_key = provider_config.get("azure_tts_subscription_key", "")
    if sub_key.startswith("other[") and sub_key.endswith("]"):
        try:
            json_str = sub_key[6:-1]
            custom_config = json.loads(json_str)
            required_keys = {"TTS_SKEY", "TTS_URL", "TTS_AUTH_TIME"}
            if missing := required_keys - custom_config.keys():
                raise ValueError(f"缺少必要配置项: {', '.join(missing)}")
            return OTTSClient(
                {**provider_config, **custom_config},
                provider_settings
            )
        except json.JSONDecodeError as e:
            raise ValueError("OTTS配置格式错误") from e
    return NativeAzureTTS(provider_config, provider_settings)

@register_provider_adapter("azure_tts", "Azure TTS", ProviderType.TEXT_TO_SPEECH)
class AzureTTSProvider(BaseAzureTTS):
    def __init__(self, provider_config: dict, provider_settings: dict):
        super().__init__(provider_config, provider_settings)
        self.impl = create_provider(provider_config, provider_settings)
    def get_audio(self, text: str) -> str:
        return self.impl.get_audio(text)
