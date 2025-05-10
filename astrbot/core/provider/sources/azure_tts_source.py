import hashlib
import json
import re
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

class AzureTTS(TTSProvider):
    TEMP_DIR = Path("data/temp/Azure_TTS/")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    def __init__(self, config: Dict):
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.voice = config.get("voice", "zh-CN-YunxiaNeural")
        self.style = config.get("style", "cheerful")
        self.role = config.get("role", "Boy")
        self.rate = config.get("rate", "1.0")
        self.volume = config.get("volume", "100")

    def _cleanup(self, path: Path):
        try:
            if path.exists():
                path.unlink()
        except Exception as e:
            pass

class AzureTTS(AzureTTS):
    def __init__(self, config: Dict):
        super().__init__(config)
        if not re.match(r"^[a-fA-F0-9]{32}$", config["subscription_key"]):
            raise ValueError("Invalid Azure subscription key format")
        self.speech_config = SpeechConfig(
            subscription=config["subscription_key"],
            region=config["region"]
        )

    async def synthesize(self, text: str) -> Path:
        file_path = self.TEMP_DIR / f"azure_{uuid.uuid4()}.wav"
        try:
            synthesizer = SpeechSynthesizer(
                speech_config=self.speech_config,
                audio_config=AudioOutputConfig(filename=str(file_path))
            ssml = self._build_ssml(text)
            result = await synthesizer.speak_ssml_async(ssml)
            if result.reason == ResultReason.SynthesizingAudioCompleted:
                return file_path
            self._handle_error(result)
        except Exception as e:
            self._cleanup(file_path)
            raise
    def _build_ssml(self, text: str) -> str:
        return f"""<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis'
            xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='zh-CN'>
            <voice name='{self.voice}'>
                <mstts:express-as style='{self.style}' role='{self.role}'>
                    <prosody rate='{self.rate}' volume='{self.volume}'>
                        {text}
                    </prosody>
                </mstts:express-as>
            </voice>
        </speak>"""
    def _handle_error(self, result):
        # 错误处理逻辑
        pass

class OTTSClient(AzureTTS):
    def __init__(self, config: Dict):
        super().__init__(config)
        self.skey = config["skey"]
        self.api_url = config["api_url"]
        self.auth_time_url = config["auth_time_url"]
        self.session = requests.Session()
        self.time_offset = 0
    async def synthesize(self, text: str) -> Path:
        file_path = self.TEMP_DIR / f"otts_{uuid.uuid4()}.wav"
        try:
            signed_url = await self._generate_signed_url()
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
            return file_path
        except Exception as e:
            self._cleanup(file_path)
            raise

    async def _generate_signed_url(self) -> str:
        timestamp, nonce = await self._get_sync_time()
        path = "/" + self.api_url.split("/", 3)[-1]
        signature = hashlib.md5(
            f"{path}-{timestamp}-{nonce}-0-{self.skey}".encode()
        ).hexdigest()
        return f"{self.api_url}?sign={timestamp}-{nonce}-0-{signature}"

    async def _get_sync_time(self) -> tuple:
        pass

def create_tts_provider(provider_config: Dict) -> AzureTTS:
    key = provider_config.get("azure_tts_subscription_key", "")

    if key.startswith("other[") and key.endswith("]"):
        try:
            config = json.loads(key[6:-1])
            required_keys = {"TTS_SKEY", "TTS_URL", "TTS_AUTH_TIME"}
            if not required_keys.issubset(config.keys()):
                raise KeyError("Missing required OTTS config keys")

            return OTTSClient({
                "skey": config["TTS_SKEY"],
                "api_url": config["TTS_URL"],
                "auth_time_url": config["TTS_AUTH_TIME"],
                **provider_config
            })
        except Exception as e:
            raise ValueError("Invalid OTTS config") from e

    return AzureTTS({
        "subscription_key": key,
        "region": provider_config["azure_tts_region"],
        **provider_config
    })

@register_provider_adapter("azure_tts", "Azure TTS", ProviderType.TEXT_TO_SPEECH)
class AzureTTSWrapper(TTSProvider):
    def __init__(self, provider_config: dict, settings: dict):
        super().__init__(provider_config, settings)
        self.impl = create_tts_provider(provider_config)

    async def get_audio(self, text: str) -> str:
        path = await self.impl.synthesize(text)
        return str(path)
