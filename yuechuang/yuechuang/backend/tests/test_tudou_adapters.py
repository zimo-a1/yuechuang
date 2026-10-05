import base64
import json
import hashlib
import time
from pathlib import Path

import pytest

from models.image_tudou import TudouImageClient
from models.video_tudou import TudouVideoClient


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9eQAAAABJRU5ErkJggg=="
)


class Response:
    def __init__(self, body=None, *, status_code=200, content=b""):
        self._body = body
        self.status_code = status_code
        self.content = content
        self.ok = status_code < 400

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_tudou_gemini_uses_one_native_create_and_saves_inline_image(monkeypatch, tmp_path):
    from models import image_tudou

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response({
            "candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG_1X1).decode("ascii")}}
            ]}}]
        })

    monkeypatch.setattr(image_tudou.requests, "post", post)
    client = TudouImageClient(api_key="test-key", base_url="https://unit.test")
    paths = client.generate_image("a concise prompt", save_dir=str(tmp_path), video_ratio="9:16", size="1K")

    assert len(calls) == 1
    assert calls[0][0].endswith("/v1beta/models/gemini-3-pro-image-preview:generateContent")
    body = calls[0][1]["json"]
    assert body["generationConfig"]["imageConfig"] == {"aspectRatio": "9:16", "imageSize": "1K"}
    assert Path(paths[0]).read_bytes() == PNG_1X1


def test_tudou_grok_uses_one_create_and_does_not_forward_key_to_media(monkeypatch, tmp_path):
    from models import video_tudou

    posts = []
    gets = []

    def post(url, **kwargs):
        posts.append((url, kwargs))
        return Response({"code": 200, "data": {"id": "task-1"}})

    def get(url, **kwargs):
        gets.append((url, kwargs))
        if "/v1/tasks/" in url:
            return Response({"code": 200, "data": {"status": "completed", "result": {"video_url": "https://media.unit.test/out.mp4"}}})
        return Response(content=b"0000ftypunit-test-mp4")

    monkeypatch.setattr(video_tudou.requests, "post", post)
    monkeypatch.setattr(video_tudou.requests, "get", get)
    monkeypatch.setattr(video_tudou.time, "sleep", lambda _: None)

    target = tmp_path / "sample.mp4"
    client = TudouVideoClient(api_key="test-key", base_url="https://unit.test")
    client.generate_video("a concise prompt", None, str(target), duration=6, video_ratio="9:16", resolution="720P")

    assert len(posts) == 1
    body = posts[0][1]["json"]
    assert body["model"] == "grok-imagine-video-1.5"
    assert body["duration"] == 6
    assert gets[0][1]["headers"]["Authorization"] == "Bearer test-key"
    assert "headers" not in gets[1][1]
    assert b"ftyp" in target.read_bytes()


def test_tudou_grok_rejects_unsupported_duration_before_create(monkeypatch, tmp_path):
    from models import video_tudou

    monkeypatch.setattr(video_tudou.requests, "post", lambda *_args, **_kwargs: pytest.fail("must not create a task"))
    client = TudouVideoClient(api_key="test-key", base_url="https://unit.test")

    with pytest.raises(ValueError, match="6, 10, or 15"):
        client.generate_video("prompt", None, str(tmp_path / "sample.mp4"), duration=7)


@pytest.mark.parametrize("mode", ["first", "first_last", "references"])
def test_grok_facade_and_native_request_preserve_reference_roles(monkeypatch, tmp_path, mode):
    from models import video_tudou
    from models.video_client import VideoClient
    posts = []
    def post(url, **kwargs):
        posts.append(kwargs["json"])
        return Response({"data": {"id": "task-one"}})
    monkeypatch.setattr(video_tudou.requests, "post", post)
    monkeypatch.setattr(TudouVideoClient, "_poll", lambda *args: "https://media.example.test/video.mp4")
    monkeypatch.setattr(TudouVideoClient, "_download", lambda *args: None)
    first, last = "https://media.example.test/first.jpg", "https://media.example.test/last.jpg"
    client = VideoClient(tudou_api_key="test-key")
    client.generate_video("prompt", first if mode != "references" else None, str(tmp_path / "out.mp4"),
                          model="grok-imagine-video-1.5", duration=6,
                          last_image_path=last if mode == "first_last" else None,
                          reference_image_paths=[first, last] if mode == "references" else None)
    assert len(posts) == 1
    body = posts[0]
    if mode == "first":
        assert body["input_reference"] == first
        assert "reference_images" not in body["extra"]
    else:
        assert "input_reference" not in body
        roles = ["first_frame", "last_frame"] if mode == "first_last" else ["reference_image"] * 2
        assert body["extra"]["reference_images"] == [{"url": url, "role": role} for url, role in zip([first, last], roles)]


def test_local_reference_uses_explicit_mapping_without_upload(monkeypatch, tmp_path):
    from models.video_tudou import public_reference_url
    image = tmp_path / "first.png"
    image.write_bytes(PNG_1X1)
    mapping = tmp_path / "urls.json"
    url = "https://media.example.test/first.png?signature=placeholder"
    mapping.write_text(json.dumps({str(image.resolve()): url}))
    monkeypatch.setenv("TUDOU_REFERENCE_URL_MAP_FILE", str(mapping))
    assert public_reference_url(str(image)) == url


@pytest.mark.parametrize("case", ["valid", "expired", "changed", "missing_expiry"])
def test_temporary_reference_checks_expiry_and_source_digest(monkeypatch, tmp_path, case):
    from models.video_tudou import public_reference_url, VideoInputError
    image = tmp_path / "reference.png"
    image.write_bytes(PNG_1X1)
    entry = {"url": "https://media.example.test/ref.png", "expires_at": time.time() + 3600,
             "sha256": hashlib.sha256(PNG_1X1).hexdigest()}
    if case == "expired":
        entry["expires_at"] = time.time() - 1
    elif case == "missing_expiry":
        entry.pop("expires_at")
    elif case == "changed":
        image.write_bytes(b"changed")
    mapping = tmp_path / "urls.json"
    mapping.write_text(json.dumps({str(image.resolve()): entry}))
    monkeypatch.setenv("TUDOU_REFERENCE_URL_MAP_FILE", str(mapping))
    if case == "valid":
        assert public_reference_url(str(image)) == entry["url"]
    else:
        with pytest.raises(VideoInputError):
            public_reference_url(str(image))


@pytest.mark.parametrize("inputs", [
    {"image_path": "/unpublished/local.png"},
    {"image_path": "data:image/png;base64,eA=="},
    {"image_path": "https://127.0.0.1/image.png"},
    {"image_path": "https://localhost/image.png"},
    {"image_path": "https://192.168.1.2/image.png"},
    {"image_path": "https://user:password@media.example.test/image.png"},
    {"last_image_path": "https://media.example.test/last.png"},
    {"reference_image_paths": ["https://media.example.test/only.png"]},
    {"reference_image_paths": ["https://media.example.test/first.png"] * 8},
    {"image_path": "https://media.example.test/first.png", "reference_image_paths": ["https://media.example.test/ref.png"] * 2},
])
def test_invalid_references_never_send_create(monkeypatch, tmp_path, inputs):
    from models import video_tudou
    monkeypatch.delenv("TUDOU_REFERENCE_URL_MAP_FILE", raising=False)
    monkeypatch.setattr(video_tudou.requests, "post", lambda *_args, **_kwargs: pytest.fail("must not create"))
    with pytest.raises(video_tudou.VideoInputError):
        TudouVideoClient(api_key="test-key").generate_video(
            "prompt", inputs.pop("image_path", None), str(tmp_path / "out.mp4"), **inputs)
