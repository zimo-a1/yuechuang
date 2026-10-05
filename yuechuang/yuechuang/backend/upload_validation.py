"""Validate stored uploads before making their paths available to the workflow."""

import codecs
import os
import shutil
import subprocess
import warnings
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_IMAGE_PIXELS = 40_000_000
MAX_DOCX_EXPANDED = 100 * 1024 * 1024


class InvalidUpload(ValueError):
    pass


def safe_display_name(filename):
    # Handle both POSIX and Windows client paths; strip control characters.
    name = str(filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isalnum() or c in " ._-()（）")
    suffix = Path(name).suffix[:16]
    if len(name.encode('utf-8')) > 180:
        stem = name[:-len(suffix)] if suffix else name
        name = stem.encode('utf-8')[:160].decode('utf-8', errors='ignore') + suffix
    return name or "upload"


def _validate_image(path, ext):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as img:
            if img.format != IMAGE_FORMATS[ext] or img.width * img.height > MAX_IMAGE_PIXELS:
                raise InvalidUpload("图片格式与扩展名不符，或像素超过 4000 万")
            img.verify()
        with Image.open(path) as img:
            img.load()


def _validate_docx(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 2000 or sum(entry.file_size for entry in entries) > MAX_DOCX_EXPANDED:
            raise InvalidUpload("Word 文件解压后过大")
        if any(entry.flag_bits & 1 or entry.filename.startswith(("/", "\\"))
               or ".." in entry.filename.replace("\\", "/").split("/") for entry in entries):
            raise InvalidUpload("Word 文件包含不支持的压缩条目")
        for name in ("[Content_Types].xml", "word/document.xml"):
            content = archive.read(name)
            if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise InvalidUpload("Word 文件包含不支持的 XML 定义")
            root = ElementTree.fromstring(content)
            if name == "word/document.xml" and root.tag != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document":
                raise InvalidUpload("文件不是有效的 Word 文档")


def _validate_text(path):
    raw = Path(path).read_bytes()
    encodings = ["utf-16"] if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else ["utf-8-sig", "gbk", "gb18030"]
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
            if any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                continue
            return
        except UnicodeDecodeError:
            continue
    raise InvalidUpload("文本编码或内容无效，请使用 UTF-8、GBK、GB18030 或带 BOM 的 UTF-16 文本")


def ffmpeg_executable():
    configured = os.getenv("YC_FFMPEG_PATH")
    executable = configured or shutil.which("ffmpeg") or str(Path.home() / ".local/bin/ffmpeg")
    if not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise InvalidUpload("视频验证需要 FFmpeg，请配置 YC_FFMPEG_PATH 后重试")
    return executable


def _validate_video(path, ext, head):
    if ext == ".avi":
        matches = head[:4] == b"RIFF" and head[8:12] == b"AVI "
    elif ext in {".mkv", ".webm"}:
        matches = head[:4] == b"\x1aE\xdf\xa3" and (b"webm" in head if ext == ".webm" else b"matroska" in head)
    else:
        matches = head[4:8] == b"ftyp" or (ext == ".mov" and head[4:8] in {b"moov", b"mdat", b"wide"})
    if not matches:
        raise InvalidUpload("视频内容与扩展名不符")
    executable = ffmpeg_executable()
    # Local files only; playlists cannot make the decoder fetch external URLs.
    result = subprocess.run(
        [executable, "-v", "error", "-nostdin", "-max_alloc", "67108864", "-threads", "1",
         "-protocol_whitelist", "file,pipe", "-i", str(path),
         "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
    )
    if result.returncode or len(result.stdout) != 3:
        raise InvalidUpload("视频无法解码或没有视频画面")


def validate_upload(path, extension=None):
    ext = (extension or Path(path).suffix).lower()
    try:
        if not os.path.getsize(path):
            raise InvalidUpload("不能上传空文件")
        with open(path, "rb") as stream:
            head = stream.read(4096)
        if ext in IMAGE_FORMATS:
            _validate_image(path, ext)
        elif ext in VIDEO_EXTENSIONS:
            _validate_video(path, ext, head)
        elif ext in {".txt", ".md"}:
            _validate_text(path)
        elif ext == ".docx" or (ext == ".doc" and head.startswith(b"PK")):
            # Preserve legacy callers uploading DOCX bytes with a .doc name.
            _validate_docx(path)
        elif ext == ".doc":
            import olefile
            with olefile.OleFileIO(path) as doc:
                if not doc.exists("WordDocument") or not (doc.exists("0Table") or doc.exists("1Table")):
                    raise InvalidUpload("文件不是有效的旧版 Word 文档")
        elif ext == ".pdf":
            from PyPDF2 import PdfReader
            if not head.startswith(b"%PDF-"):
                raise InvalidUpload("PDF 内容与扩展名不符")
            with open(path, "rb") as stream:
                reader = PdfReader(stream, strict=True)
                if reader.is_encrypted or not len(reader.pages):
                    raise InvalidUpload("不支持加密或无页面的 PDF")
        else:
            raise InvalidUpload("不支持的文件类型")
    except InvalidUpload:
        raise
    except Exception:
        # Parser errors can contain uploaded text, filenames or local paths.
        raise InvalidUpload("文件内容损坏、类型不符或无法在限制时间内验证") from None
