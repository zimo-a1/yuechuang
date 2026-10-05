import logging
import queue
import subprocess
from logging.handlers import QueueHandler

import pytest

from log_privacy import PrivacyFilter


def test_redaction_happens_before_queue_formats_exception():
    secret = 'example-secret-123456'
    handler = QueueHandler(queue.Queue())
    handler.addFilter(PrivacyFilter([secret]))
    logger = logging.getLogger('privacy-test')
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        try:
            raise ValueError('PRIVATE_DOCUMENT_BODY')
        except ValueError:
            logger.exception('key=%s Bearer token-abcdef url=https://host.invalid/?signature=private password=private-password', secret)
        record = handler.queue.get_nowait()
        rendered = logging.Formatter().format(record)
        for sensitive in [secret, 'token-abcdef', 'signature=private', 'private-password', 'PRIVATE_DOCUMENT_BODY']:
            assert sensitive not in rendered
        assert 'ValueError' in rendered
        assert '[REDACTED]' in rendered
    finally:
        logger.removeHandler(handler)


def test_uploaded_document_not_logged(tmp_path, monkeypatch, caplog):
    from api.routers import files
    monkeypatch.setattr(files.settings, 'TEMP_DIR', str(tmp_path))
    path = tmp_path / 'sample_input.txt'
    private_text = 'PRIVATE_UPLOADED_SCRIPT_BODY'
    path.write_text(private_text)
    with caplog.at_level(logging.DEBUG):
        result = files.merge_uploaded_file_into_idea('idea', str(path))
    assert private_text in result
    assert private_text not in caplog.text


def test_parser_exception_body_not_logged(monkeypatch, caplog):
    import models.file_reader as reader
    def fail(*args):
        raise RuntimeError('SECRET_PROVIDER_DOCUMENT')
    monkeypatch.setattr(reader, 'Document', fail)
    with caplog.at_level(logging.DEBUG):
        assert reader.FileReader._extract_docx('file.docx') == ''
    assert 'SECRET_PROVIDER_DOCUMENT' not in caplog.text
    assert 'RuntimeError' in caplog.text


def test_filter_uses_updated_runtime_secrets(monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'PRIVACY_TEST_API_KEY', 'new-runtime-secret', raising=False)
    record = logging.LogRecord('test', logging.WARNING, '', 0, '%s', ('new-runtime-secret',), None)
    PrivacyFilter().filter(record)
    assert record.getMessage() == '[REDACTED]'


def test_access_log_query_is_not_logged():
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
                               'GET /api/test?text=PRIVATE_DOCUMENT HTTP/1.1', (), None)
    PrivacyFilter([]).filter(record)
    assert 'PRIVATE_DOCUMENT' not in record.getMessage()


async def test_editor_failure_keeps_exit_code_without_stderr(tmp_path, monkeypatch, caplog):
    from core.agents.editor_agent import VideoEditorAgent
    monkeypatch.chdir(tmp_path)
    clip = tmp_path / 'clip.mp4'
    clip.write_bytes(b'fake')
    from core.workflow_rules import WorkflowConflict
    class FailedProcess:
        returncode = 7
        stderr = 'PRIVATE_FFMPEG_DETAILS'
        def poll(self):
            return self.returncode
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: FailedProcess())
    with caplog.at_level(logging.INFO), pytest.raises(WorkflowConflict, match='视频拼接失败') as error:
        await VideoEditorAgent().process({'session_id': 'demo', 'selected_clips': {'seg1': str(clip)}})
    assert 'PRIVATE_FFMPEG_DETAILS' not in caplog.text + str(error.value)
    assert 'exit code 7' in caplog.text


@pytest.mark.parametrize('kind', ['llm', 'vlm', 'image', 'video'])
def test_debug_input_switch_only_logs_metadata(kind, tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace

    from config import Config
    from models.image_client import ImageClient
    from models.llm_client import LLM
    from models.video_client import VideoClient
    from models.vlm_client import VLM
    monkeypatch.setattr(Config, 'PRINT_MODEL_INPUT', True)
    prompt = 'PRIVATE_PROMPT_TEXT'
    media = 'data:image/png;base64,PRIVATE_MEDIA_BYTES'
    with caplog.at_level(logging.INFO):
        if kind == 'llm':
            client = LLM()
            client._gpt_client = SimpleNamespace(query=lambda *args, **kwargs: 'ok')
            client.query(prompt, image_urls=[media], model='gpt-test')
        elif kind == 'vlm':
            client = VLM()
            client._gpt_client = SimpleNamespace(chat=lambda *args, **kwargs: 'ok')
            client.query(prompt, image_paths=[media], model='gpt-test')
        elif kind == 'image':
            client = ImageClient()
            client._agnes_client = SimpleNamespace(generate_image=lambda **kwargs: [])
            client.generate_image(prompt, image_paths=[media], model='agnes-test', save_dir=str(tmp_path))
        else:
            client = VideoClient()
            client._agnes_client = SimpleNamespace(generate_video=lambda **kwargs: 'ok')
            client.generate_video(prompt, image_path=media, model='agnes-test', save_path=str(tmp_path / 'out.mp4'))
    assert prompt not in caplog.text
    assert 'PRIVATE_MEDIA_BYTES' not in caplog.text
    assert 'prompt_chars=' in caplog.text
