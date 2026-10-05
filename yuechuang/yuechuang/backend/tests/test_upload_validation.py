import io
import subprocess
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image

from upload_validation import InvalidUpload, safe_display_name, validate_upload


def image_bytes(format='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize('name', ['../../input.png', r'C:\private\input.png', 'input\x00\n.png'])
def test_safe_filename(name):
    value = safe_display_name(name)
    assert '/' not in value and '\\' not in value and '\x00' not in value and '\n' not in value
    assert value.endswith('.png')


def test_long_unicode_filename_fits_filesystem():
    value = safe_display_name('中文' * 200 + '.png')
    assert len(value.encode('utf-8')) <= 180
    assert value.endswith('.png')


@pytest.mark.parametrize(('extension', 'format'), [('.png', 'PNG'), ('.jpg', 'JPEG'), ('.webp', 'WEBP'), ('.bmp', 'BMP')])
def test_valid_images(tmp_path, extension, format):
    path = tmp_path / ('image' + extension)
    path.write_bytes(image_bytes(format))
    validate_upload(path)


@pytest.mark.parametrize(('extension', 'data'), [('.png', b'not-image'), ('.jpg', image_bytes()),
    ('.pdf', b'not-pdf'), ('.txt', b'\x00binary'), ('.doc', b'not-word'), ('.mp4', b'not-video'),
    ('.webm', b'not-webm'), ('.txt', b''), ('.png', image_bytes()[:30])])
def test_spoofed_or_broken_upload_rejected(tmp_path, extension, data):
    path = tmp_path / ('invalid' + extension)
    path.write_bytes(data)
    with pytest.raises(InvalidUpload):
        validate_upload(path)


@pytest.mark.parametrize('encoding', ['utf-8', 'gbk', 'utf-16'])
def test_text_encodings(tmp_path, encoding):
    path = tmp_path / 'script.txt'
    path.write_bytes('中文剧本\n第二行'.encode(encoding))
    validate_upload(path)


def test_valid_docx_pdf_and_archive_bomb_limit(tmp_path, monkeypatch):
    from docx import Document
    from PyPDF2 import PdfWriter

    import upload_validation
    word = tmp_path / 'script.docx'
    doc = Document()
    doc.add_paragraph('offline document')
    doc.save(word)
    validate_upload(word)
    pdf = tmp_path / 'script.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf.open('wb') as stream:
        writer.write(stream)
    validate_upload(pdf)
    monkeypatch.setattr(upload_validation, 'MAX_DOCX_EXPANDED', 100)
    with pytest.raises(InvalidUpload, match='解压'):
        validate_upload(word)


def test_zip_is_not_automatically_word(tmp_path):
    path = tmp_path / 'fake.docx'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('arbitrary.txt', 'content')
    with pytest.raises(InvalidUpload):
        validate_upload(path)


def test_image_pixel_limit(tmp_path, monkeypatch):
    import upload_validation
    path = tmp_path / 'image.png'
    path.write_bytes(image_bytes())
    monkeypatch.setattr(upload_validation, 'MAX_IMAGE_PIXELS', 10)
    with pytest.raises(InvalidUpload, match='像素'):
        validate_upload(path)


def test_real_video_decoder_and_spoofed_container(tmp_path):
    from upload_validation import ffmpeg_executable
    path = tmp_path / 'clip.mp4'
    subprocess.run([ffmpeg_executable(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=32x32:d=0.1', '-pix_fmt', 'yuv420p', str(path)],
                   check=True, capture_output=True, timeout=15)
    validate_upload(path)
    path.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'\x00' * 40)
    with pytest.raises(InvalidUpload):
        validate_upload(path)


async def test_http_validation_size_and_cleanup(tmp_path, monkeypatch):
    from api.app import app
    from api.routers import files
    monkeypatch.setattr(files.settings, 'TEMP_DIR', str(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        result = await client.post('/api/upload_media', files={'file': ('fake.png', b'private-content', 'image/png')})
        assert result.status_code == 400
        assert result.json()['error']['code'] == 'INVALID_UPLOAD'
        assert 'private-content' not in result.text
        assert not list(tmp_path.iterdir())
        good = await client.post('/api/upload_media', files={'file': ('image.png', image_bytes(), 'application/octet-stream')})
        assert good.status_code == 200  # Content, not client MIME, is authoritative.
        assert Path(good.json()['file_path']).exists()
        monkeypatch.setattr(files, 'DOCUMENT_UPLOAD_LIMIT', 4)
        large = await client.post('/api/upload_file', files={'file': ('note.txt', b'12345')})
        assert large.status_code == 413
        assert large.json()['error']['code'] == 'UPLOAD_TOO_LARGE'
        assert len(list(tmp_path.iterdir())) == 1


def test_artifact_upload_does_not_mutate_on_invalid_image(tmp_path, monkeypatch):
    from config import settings
    from core.orchestrator import WorkflowEngine
    monkeypatch.setattr(settings, 'RESULT_DIR', str(tmp_path / 'result'))
    engine = WorkflowEngine(session_dir=str(tmp_path / 'sessions'), database_path=tmp_path / 'state.sqlite3')
    try:
        engine.create_session('demo', {})
        with pytest.raises(InvalidUpload):
            engine.upload_artifact_image('demo', 'character_design', 'characters', 'char1', io.BytesIO(b'bad'), 'photo.png')
        assert 'character_design' not in engine.get_state('demo').artifacts
        assert not list((tmp_path / 'result').rglob('*.png'))
        result = engine.upload_artifact_image('demo', 'character_design', 'characters', 'char1', io.BytesIO(image_bytes()), 'photo.png')
        assert result['status'] == 'ok'
        assert engine.store.sessions()[0]['artifacts']['character_design']['characters'][0]['selected']
    finally:
        engine.store.close()
