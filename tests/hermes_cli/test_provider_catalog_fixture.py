"""Inert routing-fixture proof; never creates an agent or provider request."""
from unittest.mock import patch
import hermes_yaml as yaml
from tests.e2e.core.providers._catalog_helpers import write_home
from hermes_cli import model_catalog


def test_routing_fixture_disables_background_catalog_network(tmp_path, monkeypatch):
    with patch('tests.e2e.core.providers._catalog_helpers.select_test_dependencies'):
        home = write_home(tmp_path, {'provider': 'xai', 'base_url': 'http://127.0.0.1:9/v1'})
    config = yaml.safe_load((home / '.hermes/config.yaml').read_text())
    monkeypatch.setattr('hermes_cli.config.load_config', lambda: config)
    def unexpected(**_kwargs):
        raise AssertionError('routing fixture attempted background catalog refresh')
    monkeypatch.setattr(model_catalog, 'get_catalog', unexpected)
    assert model_catalog.refresh_catalogs() is False


def test_explicit_catalog_test_override_is_preserved(tmp_path):
    with patch('tests.e2e.core.providers._catalog_helpers.select_test_dependencies'):
        home = write_home(tmp_path, {'provider': 'xai'}, {'model_catalog': {'enabled': True}})
    config = yaml.safe_load((home / '.hermes/config.yaml').read_text())
    assert config['model_catalog']['enabled'] is True
