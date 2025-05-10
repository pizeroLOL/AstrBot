import uuid
import xml.etree.ElementTree as Et
from os import remove
from pathlib import Path

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

TEMP_DIR = Path("data/temp")


@register_provider_adapter("azure_tts", "Azure TTS", ProviderType.TEXT_TO_SPEECH)
class ProviderAzureTTS(TTSProvider):
    config: SpeechConfig
    ssml: Et.Element | None

    @staticmethod
    def __empty_str_to_none(s: str | None) -> str | None:
        return s if s != "" else None

    @staticmethod
    def __replace_slot(root: Et.Element, text: str) -> str:
        for slot in root.findall("slot"):
            parent = slot.getparent() if hasattr(slot, "getparent") else root
            parent.remove(slot)
            parent.text = text
        return Et.tostring(root, encoding="unicode")

    def __init__(self, provider_config: dict, provider_settings: dict):
        super().__init__(provider_config, provider_settings)
        region = provider_config.get("azure_tts_region", "")
        subscription = provider_config.get("azure_tts_subscription_key", "")
        self.config = SpeechConfig(
            region=self.__empty_str_to_none(region), subscription=self.__empty_str_to_none(subscription)
        )
        ssml = self.__empty_str_to_none(provider_config.get("azure_tts_ssml", ""))
        self.ssml = ssml if ssml is None else Et.fromstring(ssml)
        self.set_model("azure_tts")

    async def get_audio(self, text: str) -> str:
        """获取文本的音频，返回音频文件路径"""
        file = TEMP_DIR / f"azure-tts-temp-{uuid.uuid4()}.wav"
        file_text = str(file)
        config_set = SpeechSynthesizer(
            speech_config=self.config,
            audio_config=AudioOutputConfig(filename=file_text),
        )
        result = (
            config_set.speak_text_async(text)
            if self.ssml is None
            else config_set.speak_ssml_async(self.__replace_slot(self.ssml, text))
        )
        result = future.get()
        has_file = file.is_file()
        if result.reason == ResultReason.SynthesizingAudioCompleted and has_file:
            return file_text
        if has_file:
            remove(file)
        if result.reason != ResultReason.Canceled:
            raise RuntimeError(f"azure_tts 未知错误 {file_text}, result: {result}")
        cancellation_details = result.cancellation_details
        logger.error(f"azure_tts 取消生成: {cancellation_details.reason}")
        if cancellation_details.reason == CancellationReason.Error:
            logger.error(f"azure_tts 错误信息: {cancellation_details.error_details}")
        raise RuntimeError(f"azure_tts 生成错误 {file_text}")
