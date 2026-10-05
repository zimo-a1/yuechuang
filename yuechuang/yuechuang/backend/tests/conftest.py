"""Keep app imports in tests away from the developer's sessions and workflow database."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

_test_state = tempfile.TemporaryDirectory(prefix='yuechuang-tests-')
_previous_env = {key: os.environ.get(key) for key in ('YC_STATE_DIR', 'YC_LEGACY_SESSION_DIR', 'YC_SETTINGS_DIR', 'YC_ENV_PATH')}
os.environ['YC_STATE_DIR'] = _test_state.name
os.environ['YC_LEGACY_SESSION_DIR'] = str(Path(_test_state.name) / 'legacy')
os.environ['YC_SETTINGS_DIR'] = str(Path(_test_state.name) / 'settings')
os.environ['YC_ENV_PATH'] = str(Path(_test_state.name) / '.env')


@pytest.fixture(scope='session', autouse=True)
def isolated_app_state():
    yield
    dependencies = sys.modules.get('api.dependencies')
    if dependencies is not None:
        dependencies.workflow_engine.store.close()
    _test_state.cleanup()
    for key, value in _previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
