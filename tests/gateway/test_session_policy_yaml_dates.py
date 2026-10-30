"""YAML timestamps must survive frozen cron policy admission without starting a job."""
import asyncio
from datetime import date
import json
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('stamp', ['2026-08-25T06:03:40Z', '2026-08-25T06:03:40.123456+05:30', '2026-08-25'])
def test_cron_admission_freezes_yaml_dates_as_iso_strings(tmp_path, monkeypatch, stamp):
    from cron import jobs
    from gateway import run, session_cron
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import SessionAuthority
    from hermes_state import SessionDB
    from hermes_state_runtime import begin_runtime_epoch

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'config.yaml').write_text(
        'platform_toolsets:\n  cli: []\nplugins:\n  entries:\n    example:\n'
        f'      settings:\n        not_before: {stamp}\n')
    config = run._load_gateway_config(tmp_path / 'config.yaml')
    original = config['plugins']['entries']['example']['settings']['not_before']
    assert isinstance(original, date)
    # Only the persisted job lookup is substituted; admission, YAML loading and policy
    # persistence are real. Scheduling is withheld so this offline probe cannot run an agent.
    monkeypatch.setattr(jobs, 'get_job', lambda jid: {'id': jid, 'prompt': 'offline', 'model': 'test-model'})
    monkeypatch.setattr(run, '_load_gateway_config', lambda: config)
    db = SessionDB(tmp_path / 'state.db')
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(_draining=False, session_store=store, adapters={})
    authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='test', db=db,
                                 epoch=begin_runtime_epoch(db, instance_id='test'))
    runner.session_authority = authority
    monkeypatch.setattr(authority, '_schedule', lambda ref: None)

    async def probe():
        receipt = await session_cron.operation(authority, 'submit', {
            'job_id': 'offline-job', 'request_id': 'offline-fire', 'extra_prompt': None})
        policy = runner.adapters[next(iter(runner.adapters))].policies[receipt['session_id']]
        frozen = json.loads(policy.config_json)
        assert frozen['plugins']['entries']['example']['settings']['not_before'] == original.isoformat()
        assert policy.source == 'cron'
        assert policy.model == 'test-model'
        assert config['plugins']['entries']['example']['settings']['not_before'] is original

    try:
        asyncio.run(probe())
    finally:
        db.close()
        store._db.close()


def test_frozen_policy_still_rejects_unsupported_config_values(tmp_path):
    from gateway.session_policy import build_policy

    with pytest.raises(TypeError, match='Object of type set is not JSON serializable'):
        build_policy({'source': 'cli', 'cwd': str(tmp_path), 'toolsets': []},
                     {'plugins': {'unsupported': {'value'}}}, profile_terminal=False)


def test_yaml_dates_in_nested_mapping_keys_are_frozen_as_iso_strings(tmp_path):
    import yaml
    from gateway.session_policy import build_policy

    config = yaml.safe_load(
        'plugins:\n  entries:\n    example:\n      settings:\n'
        '        schedule:\n          2026-08-25:\n'
        '            2026-08-25T06:03:40.123456+05:30: enabled\n'
    )
    original_settings = config['plugins']['entries']['example']['settings']
    policy = build_policy({'source': 'cli', 'cwd': str(tmp_path), 'toolsets': []}, config,
                          profile_terminal=False)
    frozen = json.loads(policy.config_json)

    assert frozen['plugins']['entries']['example']['settings']['schedule'] == {
        '2026-08-25': {'2026-08-25T06:03:40.123456+05:30': 'enabled'}
    }
    assert config['plugins']['entries']['example']['settings'] is original_settings
    assert next(iter(original_settings['schedule'])) == date(2026, 8, 25)


@pytest.mark.parametrize('date_first', [False, True])
def test_yaml_date_mapping_key_collision_is_rejected(date_first):
    from gateway.session_policy_config import copy_config_for_policy

    date_key = date(2026, 8, 25)
    entries = [(date_key, 'date value'), ('2026-08-25', 'string value')]
    if not date_first:
        entries.reverse()
    config = {'settings': dict(entries)}

    with pytest.raises(ValueError, match='collides with another configuration key'):
        copy_config_for_policy(config)

    assert len(config['settings']) == 2
