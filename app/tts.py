from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from app.config import settings

MAX_TTS_CHARS = 260


class TTSClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        voice: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
    ):
        self.base_url = (base_url or settings.tts_base_url).rstrip("/")
        self.model = model or "tts"
        self.voice = voice or settings.tts_voice
        self.api_key = api_key
        self.provider = provider or "tts-service"

    @classmethod
    def from_config(cls, config) -> "TTSClient":
        """从 ModelConfig 创建 TTSClient。"""
        return cls(
            base_url=config.base_url,
            model=config.model_name,
            voice=config.voice,
            api_key=config.api_key,
            provider=config.provider,
        )

    @classmethod
    def from_settings(cls) -> "TTSClient":
        """从环境变量配置创建 TTSClient（向后兼容）。"""
        return cls()

    def _use_openai_compatible_speech(self) -> bool:
        parsed = urlparse(self.base_url)
        path = parsed.path.rstrip("/")
        return (
            self.provider in {"openai-compatible-tts", "dashscope-compatible-tts"}
            or path.endswith("/audio/speech")
            or "compatible-mode" in self.base_url
        )

    def _use_dashscope_speech_synthesizer(self) -> bool:
        parsed = urlparse(self.base_url)
        path = parsed.path.rstrip("/")
        return (
            "SpeechSynthesizer" in path
            or "/services/audio/tts/" in path
            or "/services/aigc/text2audio/" in path
        )

    def _endpoint_url(self) -> str:
        parsed = urlparse(self.base_url)
        path = parsed.path.rstrip("/")
        if self._use_dashscope_speech_synthesizer():
            return self.base_url
        if path.endswith("/tts") or path.endswith("/audio/speech"):
            return self.base_url
        if self._use_openai_compatible_speech():
            return f"{self.base_url}/audio/speech"
        return f"{self.base_url}/tts"

    def _download_audio_url(self, audio_url: str, output_path: Path) -> None:
        response = requests.get(audio_url, timeout=600)
        response.raise_for_status()
        output_path.write_bytes(response.content)

    def _extract_audio_url(self, payload) -> Optional[str]:
        if isinstance(payload, dict):
            for key in ("url", "audio_url"):
                value = payload.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
            for value in payload.values():
                found = self._extract_audio_url(value)
                if found:
                    return found
        elif isinstance(payload, list):
            for item in payload:
                found = self._extract_audio_url(item)
                if found:
                    return found
        return None

    def synthesize(self, text: str, output_path: Path) -> float:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text = normalize_tts_text(text)
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self._use_dashscope_speech_synthesizer():
            request_json = {
                "model": self.model,
                "input": {
                    "text": text,
                },
                "parameters": {
                    "voice": self.voice,
                    "format": "wav",
                },
            }
        elif self._use_openai_compatible_speech():
            request_json = {
                "model": self.model,
                "input": text,
                "voice": self.voice,
                "response_format": "wav",
            }
        else:
            request_json = {
                "model": self.model,
                "text": text,
                "voice": self.voice,
                "format": "wav",
            }
        response = requests.post(
            self._endpoint_url(),
            json=request_json,
            headers=headers,
            timeout=600,
        )
        if not response.ok:
            raise RuntimeError(
                f"TTS request failed with HTTP {response.status_code}: "
                f"url={self._endpoint_url()} body={response.text[:4000]}"
            )
        content_type = response.headers.get("Content-Type", "")
        content = response.content
        if content_type.startswith("audio/") or content[:4] == b"RIFF":
            output_path.write_bytes(content)
            return audio_duration_seconds(output_path)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"TTS response is not audio or JSON: url={self._endpoint_url()} "
                f"content_type={content_type}"
            ) from exc
        audio_url = self._extract_audio_url(payload)
        if not audio_url:
            raise RuntimeError(
                f"TTS response does not contain audio url: url={self._endpoint_url()} "
                f"body={str(payload)[:4000]}"
            )
        self._download_audio_url(audio_url, output_path)
        return audio_duration_seconds(output_path)


def normalize_tts_text(text: str) -> str:
    text = " ".join((text or "").split())
    if len(text) <= MAX_TTS_CHARS:
        return text
    return text[:MAX_TTS_CHARS].rstrip("，。；、 ") + "。"


def audio_duration_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return max(float(result.stdout.strip()), 0.1)
    except Exception:
        # Conservative fallback: Chinese narration is usually 4-6 chars/sec.
        return max(len(path.read_bytes()) / 32000.0, 3.0)
