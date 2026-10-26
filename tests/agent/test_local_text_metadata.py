"""Restricted probes stay unknown without network or fabricated capability cache."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('kind', ['server','ollama','context','thinking','http','vision'])
def test_restricted_metadata_never_constructs_transport(monkeypatch,kind):
    from agent.local_text_policy import LocalTextRestriction,local_text_scope
    from agent import model_metadata as metadata,model_metadata_http,image_routing
    from hermes_cli.models_local import ollama_model_supports_thinking
    def forbidden(*args,**kwargs): pytest.fail('restricted metadata must not construct HTTP client or call a live probe')
    monkeypatch.setattr('httpx.Client',forbidden)
    monkeypatch.setattr(image_routing,'_VISION_PROBES',[('fixture',forbidden)])
    calls={'server':lambda:metadata.detect_local_server_type('http://127.0.0.1:8000/v1'),
        'ollama':lambda:metadata._ollama_show('http://127.0.0.1:8000','','public'),
        'context':lambda:metadata._query_local_context_length_uncached('public','http://127.0.0.1:8000/v1'),
        'thinking':lambda:ollama_model_supports_thinking('public','http://127.0.0.1:8000/v1'),
        'http':lambda:model_metadata_http.get('http://127.0.0.1:8000/models'),
        'vision':lambda:image_routing._lookup_supports_vision('ollama','public',{})}
    with local_text_scope(True):
        if kind=='http':
            with pytest.raises(LocalTextRestriction,match='metadata_denied'):calls[kind]()
        else:
            assert calls[kind]() is None


def test_restricted_unknown_probe_does_not_poison_normal_cache(monkeypatch):
    from agent.local_text_policy import local_text_scope
    from agent.reasoning_params import _cached_probe
    from agent.agent_init_ollama import _configure_ollama_num_ctx
    agent=SimpleNamespace(model='public',base_url='http://127.0.0.1:8000/v1',api_key='',quiet_mode=True)
    seen=[]
    def probe(*args):
        seen.append(True)
        return 'known'
    def forbidden(*args,**kwargs):pytest.fail('restricted Ollama auto-detection forbidden')
    monkeypatch.setattr('agent.agent_init_ollama.query_ollama_num_ctx',forbidden)
    with local_text_scope(True):
        assert _cached_probe(agent,'_fixture_cache',probe,None,bool) is None
        assert not hasattr(agent,'_fixture_cache')
        _configure_ollama_num_ctx(agent,{},None)
        assert agent._ollama_num_ctx is None
        _configure_ollama_num_ctx(agent,{'ollama_num_ctx':4096},None)
        assert agent._ollama_num_ctx==4096
    assert _cached_probe(agent,'_fixture_cache',probe,None,bool)=='known'
    assert _cached_probe(agent,'_fixture_cache',probe,None,bool)=='known'
    assert seen==[True]
