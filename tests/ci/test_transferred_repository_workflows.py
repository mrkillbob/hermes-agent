"""Replay repository-transfer gates without executing jobs or requesting reruns."""
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from tests.ci import _gha_expr as gha


WORKFLOWS = Path(__file__).resolve().parents[2] / '.github' / 'workflows'
REPOSITORY_ID = '1341623970'


def workflow(name):
    return YAML(typ='safe').load((WORKFLOWS / name).read_text())


def context(repository='Coding-Sensations/hermes-agent', repository_id=REPOSITORY_ID,
            event='pull_request', ref='refs/heads/main', head=None, label='run-e2e'):
    return {'github': {'repository': repository, 'repository_id': repository_id,
                      'event_name': event, 'ref': ref,
                      'event': {'label': {'name': label}, 'pull_request': {
                          'head': {'repo': {'full_name': head or repository}}}}}}


@pytest.mark.parametrize('repository', [
    'mrkillbob/hermes-agent', 'Coding-Sensations/hermes-agent', 'future-owner/renamed-agent'])
@pytest.mark.parametrize('label', ['run-e2e', 'ci-reviewed'])
def test_same_repository_label_rerun_survives_transfer(repository, label):
    job = workflow('label-rerun.yml')['jobs']['rerun-failed']
    assert gha.condition(job['if'], context(repository=repository, label=label))


@pytest.mark.parametrize('changes', [
    {'repository_id': '123'}, {'repository_id': ''},
    {'head': 'external-fork/hermes-agent'}, {'label': 'unrelated'}])
def test_label_rerun_retains_identity_head_and_label_restrictions(changes):
    job = workflow('label-rerun.yml')['jobs']['rerun-failed']
    assert not gha.condition(job['if'], context(**changes))


@pytest.mark.parametrize('repository', ['mrkillbob/hermes-agent', 'Coding-Sensations/hermes-agent'])
def test_reused_repository_name_does_not_inherit_workflow_authority(repository):
    ctx = context(repository=repository, repository_id='123')
    assert not gha.condition(workflow('label-rerun.yml')['jobs']['rerun-failed']['if'], ctx)
    job = workflow('fork-desktop-candidate.yml')['jobs']['native-contracts']
    assert not gha.condition(job['if'], ctx)


@pytest.mark.parametrize('repository', ['Coding-Sensations/hermes-agent', 'future-owner/renamed-agent'])
def test_desktop_jobs_survive_transfer_with_original_event_constraints(repository):
    jobs = workflow('fork-desktop-candidate.yml')['jobs']
    assert gha.condition(jobs['native-contracts']['if'], context(repository=repository))
    assert not gha.condition(jobs['macos-arm64']['if'], context(repository=repository))
    dispatch = context(repository=repository, event='workflow_dispatch')
    assert gha.condition(jobs['macos-arm64']['if'], dispatch)
    assert not gha.condition(jobs['native-contracts']['if'], dispatch)


@pytest.mark.parametrize('changes', [
    {'repository_id': '123'}, {'repository_id': ''},
    {'event': 'push'}, {'ref': 'refs/heads/topic'}, {'ref': 'refs/tags/v1'}])
def test_desktop_build_rejects_wrong_repository_event_or_branch(changes):
    options = {'event': 'workflow_dispatch', **changes}
    job = workflow('fork-desktop-candidate.yml')['jobs']['macos-arm64']
    assert not gha.condition(job['if'], context(**options))


def test_workflow_triggers_and_token_permissions_remain_bounded():
    rerun = workflow('label-rerun.yml')
    assert rerun['on'] == {'pull_request': {'types': ['labeled']}}
    assert rerun['permissions'] == {'actions': 'write', 'contents': 'read'}
    candidate = workflow('fork-desktop-candidate.yml')
    assert set(candidate['on']) == {'pull_request', 'workflow_dispatch'}
    assert candidate['permissions'] == {'contents': 'read'}
