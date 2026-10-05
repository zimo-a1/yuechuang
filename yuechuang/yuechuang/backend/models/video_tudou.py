"""Tudou Grok contract. Reference images require public HTTPS URLs."""

from __future__ import annotations

import logging
import hashlib
import ipaddress
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlsplit

import requests

from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.ai-tudou.net"
ALLOWED_DURATIONS = {6, 10, 15}
ALLOWED_RATIOS = {"16:9", "9:16", "1:1"}
MAX_VIDEO_BYTES = 100 * 1024 * 1024


class VideoInputError(ValueError):
    code = "VIDEO_INPUT_INVALID"

    def __init__(self, message):
        self.message = message
        super().__init__(message)


def public_reference_url(value: str) -> str:
    """Resolve an explicitly configured local asset mapping; never upload it."""
    if not isinstance(value, str):
        raise VideoInputError("参考图必须是公网 HTTPS URL 或已配置映射的本地图片")
    if not value.startswith("https://"):
        mapping_file = os.environ.get("TUDOU_REFERENCE_URL_MAP_FILE", "")
        try:
            if not mapping_file or not Path(value).is_file():
                raise ValueError()
            if Path(mapping_file).stat().st_size > 1024 * 1024:
                raise ValueError()
            mapping = json.loads(Path(mapping_file).read_text(encoding="utf-8"))
            source = Path(value).resolve()
            value = mapping[str(source)]
            if isinstance(value, dict):
                expires = value.get("expires_at")
                if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= time.time():
                    raise VideoInputError("参考图临时链接已过期或缺少有效期，请更新链接；未发送生成请求")
                digest = value.get("sha256")
                if not isinstance(digest, str) or source.stat().st_size > 12 * 1024 * 1024 or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    raise VideoInputError("参考图与已上传版本不一致，请更新链接；未发送生成请求")
                value = value.get("url")
        except VideoInputError:
            raise
        except (ValueError, OSError, KeyError, TypeError):
            raise VideoInputError("土豆参考图需要公网 HTTPS URL；请先配置本地图片的 TUDOU_REFERENCE_URL_MAP_FILE 映射，未发送生成请求") from None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise VideoInputError("参考图 URL 必须使用公网 HTTPS 地址，不能使用本机、内网或 base64 地址") from None
    return value


class TudouVideoClient:
    """One-create, bounded-polling client for ``grok-imagine-video-1.5``."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: int = 120):
        self.api_key = api_key or Config.TUDOU_API_KEY
        self.base_url = (base_url or Config.TUDOU_BASE_URL or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def build_request(prompt, image_path=None, model="grok-imagine-video-1.5", duration=6,
                      video_ratio="16:9", resolution="720P", last_image_path=None,
                      reference_image_paths=None) -> dict:
        """Pure preflight shared by the director and the single-create adapter."""
        if model != "grok-imagine-video-1.5":
            raise VideoInputError("Unsupported Tudou video model")
        if isinstance(duration, bool) or duration not in ALLOWED_DURATIONS:
            raise VideoInputError("grok-imagine-video-1.5 only accepts 6, 10, or 15 second clips")
        if video_ratio not in ALLOWED_RATIOS or str(resolution or "720P").upper() != "720P":
            raise VideoInputError("Grok 仅支持 16:9、9:16、1:1 比例和 720P")
        extra = {"aspect_ratio": video_ratio, "resolution": "720p"}
        body = {"model": model, "prompt": prompt, "duration": int(duration),
                "aspect_ratio": video_ratio, "resolution": "720p", "extra": extra}
        if reference_image_paths:
            if image_path or last_image_path or not isinstance(reference_image_paths, list) or not 2 <= len(reference_image_paths) <= 7:
                raise VideoInputError("多参考图模式需要2至7张图，不能混用首尾帧；单图请使用首帧模式")
            extra["reference_images"] = [{"url": public_reference_url(path), "role": "reference_image"} for path in reference_image_paths]
        elif last_image_path:
            if not image_path:
                raise VideoInputError("首尾帧模式缺少首帧图片")
            extra["reference_images"] = [{"url": public_reference_url(path), "role": role} for path, role in
                                         [(image_path, "first_frame"), (last_image_path, "last_frame")]]
        elif image_path:
            body["input_reference"] = public_reference_url(image_path)
        return body

    @staticmethod
    def _task_data(response: requests.Response) -> dict:
        if not response.ok:
            raise RuntimeError(f"Tudou video request failed (HTTP {response.status_code})")
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("Tudou video response was not JSON") from exc
        if not isinstance(body, dict) or body.get("code", 200) != 200:
            raise RuntimeError("Tudou video response had an invalid task envelope")
        data = body.get("data", body)
        if not isinstance(data, dict):
            raise RuntimeError("Tudou video response did not include task data")
        return data

    @staticmethod
    def _result_url(data: dict) -> str:
        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("Tudou video task completed without a result")
        url = result.get("video_url")
        if not url:
            records = result.get("videos") or result.get("images") or []
            if isinstance(records, list) and records and isinstance(records[0], dict):
                url = records[0].get("url")
                if isinstance(url, list):
                    url = url[0] if url else None
        if not isinstance(url, str) or urlsplit(url).scheme != "https":
            raise RuntimeError("Tudou video task completed without a valid HTTPS result URL")
        return url

    def _poll(self, task_id: str, timeout_seconds: int = 900, interval_seconds: int = 10) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(interval_seconds)
            try:
                response = requests.get(
                    f"{self.base_url}/v1/tasks/{quote(task_id, safe='')}",
                    headers=self._headers(),
                    timeout=self.timeout,
                    proxies=Config.requests_proxies("tudou") or None,
                )
            except requests.RequestException as exc:
                logger.warning("Tudou video task poll failed (%s)", type(exc).__name__)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                logger.warning("Tudou video task poll HTTP %s", response.status_code)
                continue
            data = self._task_data(response)
            status = str(data.get("status") or "").lower()
            if status == "completed":
                return self._result_url(data)
            if status in {"failed", "error", "cancelled"}:
                raise RuntimeError("Tudou video task failed")
            if status not in {"submitted", "queued", "processing"}:
                raise RuntimeError("Tudou video task returned an unknown status")
        raise RuntimeError("Tudou video task polling timed out; do not resubmit automatically")

    def _download(self, url: str, save_path: str) -> None:
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f"{target.stem}_", suffix=".part", dir=target.parent)
        bytes_written = 0
        try:
            # Result URLs are hosted separately. Never forward the supplier API key.
            with requests.get(url, timeout=self.timeout, stream=True, allow_redirects=True) as response:
                response.raise_for_status()
                with os.fdopen(fd, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        bytes_written += len(chunk)
                        if bytes_written > MAX_VIDEO_BYTES:
                            raise RuntimeError("Tudou video download exceeded the size limit")
                        handle.write(chunk)
            with open(temp_name, "rb") as video:
                if b"ftyp" not in video.read(256):
                    raise RuntimeError("Tudou video result was not an MP4")
            os.replace(temp_name, target)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def generate_video(
        self,
        prompt: str,
        image_path: Optional[str],
        save_path: str,
        model: str = "grok-imagine-video-1.5",
        duration: int = 6,
        video_ratio: str = "16:9",
        resolution: Optional[str] = "720P",
        last_image_path: Optional[str] = None,
        reference_image_paths: Optional[list[str]] = None,
        **_: object,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("TUDOU_API_KEY not set")
        body = self.build_request(prompt, image_path, model, duration, video_ratio, resolution,
                                  last_image_path, reference_image_paths)
        logger.info("TudouVideoClient create model=%s duration=%ss ratio=%s", model, duration, video_ratio)
        try:
            response = requests.post(
                f"{self.base_url}/v1/videos/generations",
                headers=self._headers(),
                json=body,
                timeout=self.timeout,
                proxies=Config.requests_proxies("tudou") or None,
            )
        except requests.RequestException as exc:
            raise RuntimeError("Tudou video create request failed; it was not retried") from exc
        data = self._task_data(response)
        task_id = data.get("id") or data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError("Tudou video create response did not include a task id")
        url = self._poll(task_id)
        self._download(url, save_path)
        return url
