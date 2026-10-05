"""Tudou native Gemini image adapter.

The adapter only supports the model/API contract that was verified for this
project.  It deliberately performs one create request per call: retrying a
create after an ambiguous network failure can create duplicate billable images.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional

import requests

from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.ai-tudou.net"
MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MAX_RESPONSE_IMAGE_BYTES = 30 * 1024 * 1024


class TudouImageClient:
    """Native ``generateContent`` client for Gemini image generation."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: int = 180):
        self.api_key = api_key or Config.TUDOU_API_KEY
        self.base_url = (base_url or Config.TUDOU_BASE_URL or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _image_size(resolution: Optional[str]) -> str:
        value = str(resolution or "1K").strip().upper()
        if value in {"1K", "2K", "4K"}:
            return value
        if value in {"720P", "1080P"}:
            return "1K"
        return "1K"

    @staticmethod
    def _local_part(path: str) -> Optional[dict]:
        if path.startswith("data:"):
            header, separator, data = path.partition(",")
            if not separator or ";base64" not in header.lower():
                return None
            mime = header[5:].split(";", 1)[0] or "image/png"
            return {"inlineData": {"mimeType": mime, "data": data}}
        image_path = Path(path)
        if not image_path.is_file() or image_path.stat().st_size > MAX_REFERENCE_BYTES:
            return None
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return {"inlineData": {"mimeType": mime, "data": data}}

    @staticmethod
    def _extract_inline_image(payload: dict) -> tuple[bytes, str]:
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list):
            raise RuntimeError("Tudou image response did not include candidates")
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            for part in parts:
                inline = part.get("inlineData") or part.get("inline_data") if isinstance(part, dict) else None
                if not isinstance(inline, dict):
                    continue
                data = inline.get("data")
                if not isinstance(data, str) or not data:
                    continue
                try:
                    image = base64.b64decode(data, validate=True)
                except Exception as exc:
                    raise RuntimeError("Tudou image response contained invalid base64") from exc
                if not image or len(image) > MAX_RESPONSE_IMAGE_BYTES:
                    raise RuntimeError("Tudou image response exceeded the size limit")
                return image, str(inline.get("mimeType") or inline.get("mime_type") or "image/png")
        raise RuntimeError("Tudou image response did not include inline image data")

    @staticmethod
    def _suffix(mime_type: str) -> str:
        if mime_type.lower() in {"image/jpeg", "image/jpg"}:
            return ".jpg"
        if mime_type.lower() == "image/webp":
            return ".webp"
        return ".png"

    def generate_image(
        self,
        prompt: str,
        session_id: str = "default",
        model: str = "gemini-3-pro-image-preview",
        size: str = "1K",
        image_paths: Optional[List[str]] = None,
        video_ratio: str = "16:9",
        save_dir: Optional[str] = None,
        **_: object,
    ) -> List[str]:
        if not self.api_key:
            raise RuntimeError("TUDOU_API_KEY not set")
        if model != "gemini-3-pro-image-preview":
            raise ValueError(f"Unsupported Tudou image model: {model}")

        parts: list[dict] = [{"text": prompt}]
        for image_path in image_paths or []:
            part = self._local_part(str(image_path))
            if part is None:
                logger.warning("TudouImageClient skipped an invalid local reference image")
                continue
            parts.append(part)

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {"aspectRatio": video_ratio or "16:9", "imageSize": self._image_size(size)},
            },
        }
        logger.info("TudouImageClient create model=%s refs=%d ratio=%s size=%s", model, len(parts) - 1, video_ratio, self._image_size(size))
        try:
            response = requests.post(
                f"{self.base_url}/v1beta/models/{model}:generateContent",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
                proxies=Config.requests_proxies("tudou") or None,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Tudou image create request failed; it was not retried") from exc
        if not response.ok:
            raise RuntimeError(f"Tudou image create failed (HTTP {response.status_code})")

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Tudou image response was not JSON") from exc
        image, mime_type = self._extract_inline_image(data)
        output_dir = Path(save_dir or Path(Config.RESULT_DIR) / "image" / str(session_id))
        output_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="tudou_", suffix=".part", dir=output_dir)
        target = output_dir / f"tudou_{os.path.basename(temp_name)}{self._suffix(mime_type)}"
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(image)
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return [str(target)]
