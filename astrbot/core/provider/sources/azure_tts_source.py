import uuid
import xml.etree.ElementTree as Et
from pathlib import Path

from httpx import AsyncClient

from astrbot.core.config.default import VERSION

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter

TEMP_DIR = Path("data/temp")
DEFAULT_SSML = """
<speak version="1.0" xml:lang="zh-CN">
    <voice name="zh-CN-YunxiaNeural">
        <slot />
    </voice>
</speak>
"""


@register_provider_adapter("azure_tts", "Azure TTS", ProviderType.TEXT_TO_SPEECH)
class ProviderAzureTTS(TTSProvider):
    ssml: Et.Element
    endpoint: str
    subscription: str

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
        region = self.__empty_str_to_none(provider_config.get("azure_tts_region", ""))
        subscription = self.__empty_str_to_none(provider_config.get("azure_tts_subscription_key", ""))
        if subscription is None:
            raise ValueError("subscription 不可为空")
        self.subscription = subscription
        self.endpoint = f"https://{region if region is None else 'eastasia'}.api.cognitive.microsoft.com"
        ssml = self.__empty_str_to_none(provider_config.get("azure_tts_ssml", ""))
        self.ssml = Et.fromstring(ssml if ssml is not None else DEFAULT_SSML)
        self.set_model("azure_tts")

    async def get_audio(self, text: str) -> str:
        """获取文本的音频，返回音频文件路径"""
        file = TEMP_DIR / f"azure-tts-temp-{uuid.uuid4()}.wav"
        headers = {
            "Ocp-Apim-Subscription-Key": self.subscription,
            "Content-Type": "application/ssml+xml",
            "User-Agent": f"astrboot/{VERSION}",
            "X-Microsoft-OutputFormat": "raw-48khz-16bit-mono-pcm"
        }
        async with AsyncClient() as client:
            rsp = await client.post(self.endpoint, content=self.__replace_slot(self.ssml.__copy__() ,text),headers=headers)
            if rsp.status_code != 200:
                raise RuntimeError(f"azure tts 状态码错误，{rsp.status_code}：{rsp.text}")
            with file.open("wb") as o:
                async for chunk in rsp.aiter_bytes(4096):
                    o.write(chunk)
            return str(file)
