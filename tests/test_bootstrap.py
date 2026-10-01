from unittest.mock import patch
import json

import app_bootstrap


def test_startup_check_loads_features_without_starting_gui():
    with patch.object(app_bootstrap.importlib, 'import_module') as load:
        report = app_bootstrap.check_startup()
    assert report['failed'] == {}
    assert report['ready'] == list(app_bootstrap.FEATURE_MODULES)
    assert 'modules.key' not in report['ready']
    assert load.call_count == len(app_bootstrap.FEATURE_MODULES)
    load.return_value.main.assert_not_called()


def test_failed_import_is_reported_and_remaining_modules_checked(tmp_path):
    def load(name):
        if name == 'modules.sheet_parser':
            raise ImportError('sensitive configuration must not be printed')
    path = tmp_path / 'startup.json'
    with patch.object(app_bootstrap.importlib, 'import_module', side_effect=load):
        result = app_bootstrap.main(['--check-startup', '--startup-report', str(path)])
    report = json.loads(path.read_text())
    assert result == 1
    assert report['failed'] == {'modules.sheet_parser': 'ImportError'}
    assert 'gui' in report['ready']
    assert 'sensitive' not in path.read_text()
