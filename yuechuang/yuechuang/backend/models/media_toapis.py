"""ToAPIs' documented async image/video contract; never retry task creation.

Each invocation writes a receipt before POST, then stores the provider task id.
If interrupted, an identical pending request resumes by id/business id. Successful
receipts are retained as history; a later explicit generation creates a new task.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import random
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from PIL import Image

from config import Config
from core.service_settings import SettingsError, validate_video_options

IMAGE_MODELS = {"nano_banana", "gpt-image-2"}
VIDEO_MODELS = {"grok-video-1.5", "MiniMax-H3-Max-Turbo"}
IMAGE_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
GPT_RATIOS = IMAGE_RATIOS | {"5:4", "4:5", "2:1", "1:2", "21:9", "9:21"}
MAX_INPUT = 10 * 1024 * 1024


def invalid(message):
    return SettingsError("TOAPIS_INPUT_INVALID", message)


def public_url(value):
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password or parsed.fragment:
            raise ValueError()
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError()
    except (ValueError, TypeError):
        raise invalid("素材地址必须是可公开访问的 HTTP(S) 地址") from None
    return value


def validate_reference(value):
    """Pure validation: no upload or network before the entire request is valid."""
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return public_url(value)
    try:
        path = Path(value)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT:
            raise ValueError()
        with Image.open(path) as im:
            if im.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                raise ValueError()
            im.verify()
    except (OSError, ValueError, TypeError, Image.DecompressionBombError):
        raise invalid("ToAPIs 参考图需为有效的 JPEG、PNG、WebP 或 GIF，单张不超过 10 MB") from None
    return str(path)


def video_request(prompt, image_path=None, model="grok-video-1.5", duration=5,
                  video_ratio="16:9", resolution="720P", last_image_path=None,
                  reference_image_paths=None):
    if model not in VIDEO_MODELS:
        raise invalid("未接入该 ToAPIs 视频型号")
    if not isinstance(prompt, str) or not prompt.strip():
        raise invalid("视频提示词不能为空")
    if model == "MiniMax-H3-Max-Turbo" and len(prompt) > 7000:
        raise invalid("MiniMax 提示词最多 7000 字符，请缩短分镜描述")
    refs = list(reference_image_paths or [])
    if (refs and (image_path or last_image_path)) or (last_image_path and not image_path):
        raise invalid("参考图模式不能与首尾帧混用，尾帧必须搭配首帧")
    mode = "start_end_frame" if last_image_path else "reference" if refs else "first_frame" if image_path else "text"
    validate_video_options(model, ratio=video_ratio, resolution=resolution, duration=duration, mode=mode)
    if isinstance(duration, bool):
        raise invalid("视频时长必须是整数秒")
    limit = 7 if model == "grok-video-1.5" else 9
    if len(refs) > limit:
        raise invalid(f"所选视频模型最多支持 {limit} 张参考图")
    for value in [image_path, last_image_path, *refs]:
        if value:
            validate_reference(value)
    body = {"model": model, "prompt": prompt, "duration": int(duration),
            "aspect_ratio": video_ratio, "resolution": str(resolution).lower() if model == "grok-video-1.5" else str(resolution).upper()}
    if model == "grok-video-1.5":
        body["video_generation_mode"] = {"text": "text_to_video", "first_frame": "first_frame_image_to_video", "reference": "reference_images_to_video"}[mode]
        if image_path:
            body["image"] = image_path
        if refs:
            body["reference_images"] = refs
    else:
        images = [{"url": ref, "role": "reference_image"} for ref in refs]
        if image_path:
            body["aspect_ratio"] = "adaptive"
            images.append({"url": image_path, "role": "first_frame"})
        if last_image_path:
            images.append({"url": last_image_path, "role": "last_frame"})
        if images:
            body["image_with_roles"] = images
    return body


def image_request(prompt, model, image_paths=None, video_ratio="16:9", resolution="1K"):
    if model not in IMAGE_MODELS:
        raise invalid("未接入该 ToAPIs 图片型号")
    limit = 1000 if model == "nano_banana" else 32000
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > limit:
        raise invalid(f"{model} 提示词需为 1–{limit} 字符，请缩短描述或切换图片模型")
    if video_ratio not in (IMAGE_RATIOS if model == "nano_banana" else GPT_RATIOS):
        raise invalid("所选图片模型不支持该画幅，请调整比例或切换模型")
    refs = list(image_paths or [])
    if len(refs) > (14 if model == "nano_banana" else 6):
        raise invalid("参考图片超过所选模型上限")
    for ref in refs:
        validate_reference(ref)
    body = {"model": model, "prompt": prompt, "size": video_ratio, "n": 1}
    if model == "nano_banana":
        body["metadata"] = {"resolution": "1K"}  # This model has a fixed native resolution.
        if refs:
            body["image_urls"] = [{"url": ref} for ref in refs]
    else:
        # Existing asset workflows pass both named and slot/pixel resolutions.
        value = str(resolution or "1K").lower()
        if value not in {"1k", "2k", "4k"}:
            value = "1k"
        body.update(resolution=value, response_format="url")
        if refs:
            body["reference_images"] = refs
    return body


class ToAPIsClient:
    def __init__(self, api_key=None, base_url=None, *, transport=None, poll_interval=10, max_wait=2700):
        self.api_key = api_key or Config.TOAPIS_API_KEY
        self.base_url = (base_url or Config.TOAPIS_BASE_URL or "https://toapis.cn").rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.http = transport or requests.Session()
        self.http.trust_env = False
        self.http.proxies.update(Config.requests_proxies("toapis") or {})
        self.poll_interval, self.max_wait = poll_interval, max_wait

    def request(self, method, path, **kwargs):
        try:
            return self.http.request(method, self.base_url + path, headers={"Authorization": "Bearer " + self.api_key},
                                     timeout=(15, 120), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise SettingsError("TOAPIS_NETWORK_ERROR", "ToAPIs 连接中断；不会自动重提生成任务，可稍后恢复查询", 502) from None

    @staticmethod
    def data(response):
        if response.status_code not in {200, 201, 202}:
            messages = {401: "ToAPIs Key 无效", 403: "ToAPIs 账号无权调用", 402: "ToAPIs 余额不足", 429: "ToAPIs 请求频率受限"}
            raise SettingsError("TOAPIS_REQUEST_FAILED", messages.get(response.status_code, f"ToAPIs 请求失败（HTTP {response.status_code}），请检查平台任务记录"), 502)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, TypeError):
            raise SettingsError("TOAPIS_RESPONSE_INVALID", "ToAPIs 返回了无法解析的响应", 502) from None

    def upload(self, value):
        validate_reference(value)
        if value.startswith(("https://", "http://")):
            return value
        with Image.open(value) as im:
            mime = Image.MIME[im.format]
            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}[im.format]
        with open(value, "rb") as f:
            data = self.data(self.request("POST", "/v1/uploads/images", files={"file": ("reference" + suffix, f, mime)}))
        url = (data.get("data") or {}).get("url")
        if data.get("success") is not True or not url:
            raise SettingsError("TOAPIS_UPLOAD_FAILED", "ToAPIs 素材上传未成功，未提交生成请求", 502)
        return public_url(url)

    def _upload_body(self, body):
        cache = {}
        def url(value):
            if value not in cache:
                cache[value] = self.upload(value)
            return cache[value]
        body = json.loads(json.dumps(body))
        if body.get("image"):
            body["image"] = url(body["image"])
        for field in ("image_urls", "image_with_roles"):
            for ref in body.get(field, []):
                ref["url"] = url(ref["url"])
        for field in ("reference_images",):
            if field in body:
                body[field] = [url(ref) for ref in body[field]]
        return body

    @staticmethod
    def _save(path, receipt):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".receipt-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(receipt, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def generate(self, kind, body, directory):
        if not self.api_key:
            raise SettingsError("SERVICE_NOT_CONFIGURED", "请先配置 ToAPIs Key")
        directory = Path(directory) / ".toapis-tasks"
        directory.mkdir(parents=True, exist_ok=True)
        # Include reference file contents and account identity, never persist prompt/key.
        source = json.dumps(body, sort_keys=True)
        refs = [body.get("image"), *body.get("reference_images", []),
                *[r["url"] for key in ("image_urls", "image_with_roles") for r in body.get(key, [])]]
        digests = [hashlib.sha256(Path(r).read_bytes()).hexdigest() for r in refs if r and not r.startswith(("http://", "https://"))]
        fingerprint = hashlib.sha256(json.dumps([kind, self.base_url, self.api_key, source, digests]).encode()).hexdigest()
        # Local application already runs on macOS/Linux; advisory lock prevents double submit.
        import fcntl
        with open(directory / (fingerprint + ".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = directory / (fingerprint + ".json")
            receipt = json.loads(path.read_text()) if path.exists() else None
            if receipt and receipt.get("status") in {"completed", "failed", "rejected"}:
                self._save(directory / (receipt["business_id"] + ".json"), receipt)
                receipt = None
            endpoint = f"/v1/{kind}/generations"
            if not receipt:
                prepared = self._upload_body(body)
                business_id = "yc_" + uuid.uuid4().hex
                receipt = {"business_id": business_id, "model": body["model"], "status": "submitting", "created_at": time.time()}
                self._save(path, receipt)  # Unknown outcome must never cause an automatic POST retry.
                response = self.request("POST", endpoint, json={**prepared, "client_business_id": business_id})
                if response.status_code in {400, 401, 402, 403, 404, 422, 429}:
                    receipt["status"] = "rejected"
                    self._save(path, receipt)
                data = self.data(response)
                task_id = data.get("id")
                if not isinstance(task_id, str) or not task_id:
                    raise SettingsError("TOAPIS_RESPONSE_INVALID", "ToAPIs 未返回任务编号，已保存业务编号；请恢复查询，勿重复提交", 502)
                receipt.update(task_id=task_id, status="queued")
                self._save(path, receipt)
            return self._poll(endpoint, receipt, path)

    def _poll(self, endpoint, receipt, path):
        deadline = time.monotonic() + self.max_wait
        delay = self.poll_interval
        while time.monotonic() < deadline:
            time.sleep(min(delay + random.uniform(0, 1), max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            try:
                response = self.request("GET", endpoint + "/" + quote(receipt.get("task_id") or receipt["business_id"], safe=""))
            except SettingsError:
                delay = min(max(delay * 2, self.poll_interval), 60)
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                try:
                    delay = max(float(response.headers.get("Retry-After", min(delay * 2, 60))), self.poll_interval)
                except (ValueError, TypeError):
                    delay = min(max(delay * 2, self.poll_interval), 60)
                continue
            data = self.data(response)
            status = data.get("status")
            if status not in {"queued", "in_progress", "completed", "failed"}:
                raise SettingsError("TOAPIS_RESPONSE_INVALID", "ToAPIs 返回了未知任务状态，已保留任务编号", 502)
            receipt.update(status=status, updated_at=time.time())
            if status == "completed":
                result = data.get("result") or {}
                items = result.get("data") if isinstance(result, dict) else None
                url = items[0].get("url") if isinstance(items, list) and items and isinstance(items[0], dict) else None
                if not url:
                    raise SettingsError("TOAPIS_RESPONSE_INVALID", "ToAPIs 任务完成但没有返回媒体地址", 502)
                # Keep resumable until local download succeeds; remote URL is not exposed in logs.
                receipt["status"] = "downloading"
                self._save(path, receipt)
                return public_url(url), receipt, path
            self._save(path, receipt)
            if status == "failed":
                raise SettingsError("TOAPIS_GENERATION_FAILED", "ToAPIs 生成失败，请在平台核对该任务；未自动重试", 502)
            delay = self.poll_interval
        raise SettingsError("TOAPIS_POLL_TIMEOUT", "ToAPIs 查询超时，任务编号已保存；再次执行会恢复查询，避免重复提交", 504)

    def download(self, url, destination, kind):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        limit = (40 if kind == "images" else 200) * 1024 * 1024
        fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".toapis-download-")
        try:
            # Deliberately omit Authorization when fetching provider result/CDN URLs.
            with requests.Session() as download:
                download.trust_env = False
                with download.get(public_url(url), stream=True, timeout=(15, 120)) as response:
                    if response.status_code != 200:
                        raise ValueError()
                    total = 0
                    with os.fdopen(fd, "wb") as f:
                        fd = None
                        for chunk in response.iter_content(1024 * 128):
                            total += len(chunk)
                            if total > limit:
                                raise ValueError()
                            f.write(chunk)
            if kind == "images":
                with Image.open(temporary) as im:
                    im.verify()
                with Image.open(temporary) as im:
                    im.save(temporary, format="PNG")
            else:
                with open(temporary, "rb") as f:
                    if f.read(12)[4:8] != b"ftyp":
                        raise ValueError()
            os.replace(temporary, destination)
        except (OSError, ValueError, requests.RequestException, Image.DecompressionBombError):
            raise SettingsError("TOAPIS_DOWNLOAD_FAILED", "ToAPIs 结果下载或文件校验失败，任务编号已保留，可恢复下载", 502) from None
        finally:
            if fd is not None:
                os.close(fd)
            if os.path.exists(temporary):
                os.unlink(temporary)

    def generate_image(self, prompt, model, save_dir, image_paths=None, video_ratio="16:9", resolution="1K", **_):
        body = image_request(prompt, model, image_paths, video_ratio, resolution)
        url, receipt, path = self.generate("images", body, save_dir)
        destination = Path(save_dir) / (receipt["business_id"] + ".png")
        self.download(url, destination, "images")
        receipt["status"] = "completed"
        self._save(path, receipt)
        return [str(destination)]

    def generate_video(self, prompt, image_path, save_path, model, duration=5, video_ratio="16:9", resolution="720P", last_image_path=None, reference_image_paths=None):
        body = video_request(prompt, image_path, model, duration, video_ratio, resolution, last_image_path, reference_image_paths)
        url, receipt, path = self.generate("videos", body, Path(save_path).parent)
        self.download(url, save_path, "videos")
        receipt["status"] = "completed"
        self._save(path, receipt)
        return url
