"""Capability refusal does not spoof the OS and never falls back to path reads."""
import os
from functools import wraps
import pytest
from agent import learning_vault as vault


def test_missing_descriptor_capability_fails_closed(monkeypatch):
    assert hasattr(vault,'ExactVaultNoteRequest'),'public exact-note request is missing'
    request=vault.ExactVaultNoteRequest('/synthetic-vault',1,1,'Memories/Codex/a.md','synthetic','0'*64)
    monkeypatch.setattr(os,'supports_dir_fd',set())
    monkeypatch.setattr(os,'open',lambda *_a,**_k:(_ for _ in ()).throw(AssertionError('filesystem accessed')))
    node,d=vault.read_exact_vault_learning(request,authorize=lambda _:True)
    assert node is None and d.reason_code=='unsupported_safe_descriptors'


@pytest.mark.parametrize('shape',['opaque','cycle','overlong','empty'])
def test_unverifiable_wrapper_metadata_refuses(shape):
    from agent.learning_vault_exact import _supports_wrapped
    def supported():
        raise AssertionError('capability introspection must not call functions')
    @wraps(supported)
    def wrapper():
        raise AssertionError('wrapper must not be called')
    capabilities=(supported,)
    if shape=='opaque':
        del wrapper.__wrapped__
    elif shape=='cycle':
        wrapper.__wrapped__=wrapper
    elif shape=='overlong':
        for _ in range(16):
            wrapper=wraps(wrapper)(lambda:None)
    else:
        capabilities=()
    assert not _supports_wrapped(wrapper,capabilities)
