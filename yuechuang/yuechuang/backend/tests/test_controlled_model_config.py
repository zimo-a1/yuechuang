import pytest
from fastapi import HTTPException


@pytest.mark.parametrize('stage', ['character_design', 'reference_generation'])
@pytest.mark.parametrize('flag', ['pilot_mode', 'controlled_episode'])
def test_single_version_run_does_not_resolve_retired_reviewer(monkeypatch, stage, flag):
    from api.routers.workflow import _require_model_fields
    from core import service_settings as module
    calls = []
    monkeypatch.setattr(module.service_settings, 'runtime', lambda: {})
    def resolve(model, runtime):
        calls.append(model)
        if model == 'svc/retired/reviewer':
            raise module.SettingsError('SERVICE_UNAVAILABLE', 'retired')
        return {'credentials': {'api_key': 'offline'}}
    monkeypatch.setattr(module, 'resolve_service', resolve)
    values = {flag: True, 'llm_model': 'svc/text/active',
              'image_t2i_model': 'svc/image/active', 'image_it2i_model': 'svc/image/active',
              'vlm_model': 'svc/retired/reviewer'}
    _require_model_fields(values, stage)
    assert 'svc/image/active' in calls
    assert 'svc/retired/reviewer' not in calls
    values[flag] = False
    with pytest.raises(HTTPException):
        _require_model_fields(values, stage)


def test_controlled_run_still_requires_image_credentials(monkeypatch):
    from api.routers.workflow import _require_model_fields
    from core import service_settings as module
    monkeypatch.setattr(module.service_settings, 'runtime', lambda: {})
    monkeypatch.setattr(module, 'resolve_service', lambda *args: {'credentials': {}})
    with pytest.raises(HTTPException):
        _require_model_fields({'controlled_episode': True, 'image_t2i_model': 'svc/image/empty'}, 'character_design')
