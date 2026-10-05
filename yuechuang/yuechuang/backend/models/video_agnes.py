# -*- coding: utf-8 -*-
"""Agnes AI 视频生成客户端

平台：https://agnes-ai.cn   （OpenAI Videos 兼容）
创建任务：POST {base_url}/videos
查询任务：GET  {root}/agnesapi?video_id=<ID>&model_name=<MODEL>
流程：提交任务 -> 轮询直到 status=completed -> 下载 url

支持三种模式：
  - text      : 纯文本生成视频
  - keyframe  : 首帧 / 尾帧 / 首尾帧（mode 由 first_frame / last_frame 决定）
  - reference : 图片/音频参考（images / audios）

注意：Agnes 要求媒体参数是【可公开访问的 URL】。本项目产物保存在本地，
这里会尝试用 Data URI(Base64) 传递；若服务端拒绝，需要自行提供公网图床。
"""

import os
import sys
import time
import base64
import logging
import mimetypes
import threading
from typing import List, Optional

import requests

models_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(models_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from config import Config

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.agnes-ai.cn/v1"

# Agnes 免费额度对并发很敏感：串行提交任务，并保持最小间隔，避免 429。
_CREATE_LOCK = threading.Lock()
_LAST_CREATE_AT = [0.0]
MIN_CREATE_INTERVAL_SECONDS = 12
MIN_VIDEO_SECONDS = 4
MAX_VIDEO_SECONDS = 12


def _root_url(base_url: str) -> str:
    """从 .../v1 得到站点根，用于 /agnesapi 查询。"""
    if base_url.endswith("/v1"):
        return base_url[: -len("/v1")]
    return base_url


def _to_media_ref(path: str) -> str:
    """本地文件 -> Data URI；已是 URL / Data URI 则原样返回。"""
    path = str(path)
    if path.startswith("http") or path.startswith("data:"):
        return path
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{encoded}"


class AgnesVideoClient:
    """Agnes AI 视频客户端。"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: int = 120):
        self.api_key = api_key or Config.AGNES_API_KEY
        self.base_url = (base_url or Config.AGNES_BASE_URL or DEFAULT_BASE_URL).rstrip("/")
        self.root_url = _root_url(self.base_url)
        self.timeout = timeout
        self.max_attempts = 5
        if not self.api_key:
            logger.warning("AgnesVideoClient: AGNES_API_KEY 未设置")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate_video(
        self,
        prompt: str,
        image_path: Optional[str],
        save_path: str,
        model: str = "agnes-video-2.5-flash",
        duration: int = 5,
        video_ratio: str = "16:9",
        resolution: Optional[str] = None,
        last_image_path: Optional[str] = None,
        reference_image_paths: Optional[List[str]] = None,
        **kwargs,
    ) -> str:
        if not self.api_key:
            raise RuntimeError("AGNES_API_KEY not set.")

        mode, media = self._resolve_mode(image_path, last_image_path, reference_image_paths)

        payload = {
            "model": model,
            "prompt": prompt,
            # Agnes 要求 seconds ∈ [4, 12]，越界会 400，这里做了夹取
            "seconds": str(max(MIN_VIDEO_SECONDS, min(MAX_VIDEO_SECONDS, int(duration)))),
            "mode": mode,
            "size": resolution or "720P",
            "aspect_ratio": video_ratio or "16:9",
        }
        payload.update(media)

        logger.info("AgnesVideoClient: 提交任务 model=%s mode=%s seconds=%s", model, mode, payload["seconds"])

        last_resp = None
        # 串行 + 最小间隔，避免免费额度下的并发 429
        with _CREATE_LOCK:
            gap = MIN_CREATE_INTERVAL_SECONDS - (time.time() - _LAST_CREATE_AT[0])
            if gap > 0:
                time.sleep(gap)
            for attempt in range(self.max_attempts):
                resp = requests.post(
                    f"{self.base_url}/videos",
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                    proxies=Config.requests_proxies("agnes") or None,
                )
                last_resp = resp
                if resp.ok:
                    break
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_attempts - 1:
                    wait = min(10 * (attempt + 1), 60)
                    logger.warning(
                        "Agnes 视频创建 %s，%ss 后重试 (%d/%d)",
                        resp.status_code, wait, attempt + 1, self.max_attempts,
                    )
                    time.sleep(wait)
                    continue
                break
            _LAST_CREATE_AT[0] = time.time()
        resp = last_resp
        if resp is None or not resp.ok:
            raise RuntimeError(f"Agnes video create {resp.status_code}: {resp.text[:500]}")

        created = resp.json()
        video_id = created.get("video_id") or created.get("id") or created.get("task_id")
        if not video_id:
            raise RuntimeError(f"Agnes video API 未返回 video_id: {str(created)[:300]}")

        video_url = self._poll_until_done(video_id, model)
        self._download(video_url, save_path)
        return video_url

    def _resolve_mode(self, image_path, last_image_path, reference_image_paths):
        media = {}
        if image_path or last_image_path:
            if image_path and os.path.exists(str(image_path)):
                media["first_frame"] = _to_media_ref(image_path)
            if last_image_path and os.path.exists(str(last_image_path)):
                media["last_frame"] = _to_media_ref(last_image_path)
            return "keyframe", media
        if reference_image_paths:
            refs = [p for p in reference_image_paths if p and os.path.exists(str(p))]
            if refs:
                media["images"] = [_to_media_ref(p) for p in refs]
                return "reference", media
        return "text", media

    def _poll_until_done(self, video_id: str, model: str, timeout_seconds: int = 900, interval: int = 5) -> str:
        url = f"{self.root_url}/agnesapi"
        params = {"video_id": video_id, "model_name": model}
        deadline = time.time() + timeout_seconds
        last = None
        while time.time() < deadline:
            resp = requests.get(
                url, headers=self._headers(), params=params,
                timeout=self.timeout, proxies=Config.requests_proxies("agnes") or None,
            )
            # 查询接口限流：退避后重试，不计入正常轮询节奏
            if resp.status_code == 429:
                logger.warning("Agnes 查询限流(429)，15s 后重试")
                time.sleep(15)
                continue
            if resp.ok:
                data = resp.json()
                last = data
                status = str(data.get("status") or "").lower()
                if status == "completed":
                    video_url = data.get("url")
                    if not video_url:
                        raise RuntimeError(f"Agnes 任务完成但无 url: {str(data)[:300]}")
                    return video_url
                if status in ("failed", "error", "cancelled"):
                    raise RuntimeError(f"Agnes 视频任务失败: {str(data)[:300]}")
            else:
                logger.warning("Agnes 查询失败 HTTP %s", resp.status_code)
            time.sleep(interval)
        raise RuntimeError(f"Agnes 视频任务超时（{timeout_seconds}s）: {str(last)[:300]}")

    def _download(self, video_url: str, save_path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        resp = requests.get(
            video_url, timeout=self.timeout,
            proxies=Config.requests_proxies("agnes") or None, stream=True,
        )
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
        logger.info("AgnesVideoClient: 已保存视频 -> %s", save_path)
