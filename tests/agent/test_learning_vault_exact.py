"""Exact-note API behavior with a synthetic operator policy and real temp files only."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path

import pytest
from agent import learning_vault as vault

pytestmark = pytest.mark.platforms('posix')


def note_bytes(record_id='alr-synthetic', *, extra=b''):
    return (f'---\nrecord_id: {record_id}\nschema_name: agent_learning_record_v1\n'
            'sync_owned: true\nclassification: diagnostic-only\nauthority: narrative-only\n'
            'kind: memory\nagent: codex\nstatus: current\n---\nSynthetic durable lesson.\n').encode()+extra


@pytest.fixture
def scope(tmp_path):
    root=tmp_path/'operator-vault';root.mkdir();root=root.resolve()
    rel='Memories/Codex/Governance/synthetic.md';p=root/rel;p.parent.mkdir(parents=True);p.write_bytes(note_bytes())
    def request(**changes):
        assert hasattr(vault,'ExactVaultNoteRequest'), 'public exact-note request is missing'
        st=root.stat()
        base=vault.ExactVaultNoteRequest(str(root),st.st_dev,st.st_ino,rel,'alr-synthetic',hashlib.sha256(p.read_bytes()).hexdigest())
        return replace(base,**changes)
    return root,p,request


def test_exact_note_reads_one_captured_buffer(scope,monkeypatch):
    _,p,request=scope;r=request();reads=[];real=os.read
    monkeypatch.setattr(os,'read',lambda fd,n:(reads.append(n),real(fd,n))[1])
    monkeypatch.setattr(vault,'_catalog_files',lambda *_:pytest.fail('catalog traversal'))
    node,diagnostic=vault.read_exact_vault_learning(r,authorize=lambda candidate:candidate==r)
    assert diagnostic is None and node.record_id==r.expected_record_id
    assert node.summary=='Synthetic durable lesson.'
    assert node.timestamp==int(p.stat().st_mtime)
    assert reads==[r.max_bytes+1]


def test_transparent_guards_remain_on_actual_io(scope,monkeypatch):
    from functools import wraps
    _,_,request=scope;r=request();calls=[]
    original_open,original_stat=os.open,os.stat
    @wraps(original_open)
    def guarded_open(*args,**kwargs):
        calls.append('open')
        return original_open(*args,**kwargs)
    @wraps(original_stat)
    def guarded_stat(*args,**kwargs):
        calls.append('stat')
        return original_stat(*args,**kwargs)
    monkeypatch.setattr(os,'open',guarded_open)
    monkeypatch.setattr(os,'stat',guarded_stat)
    node,diagnostic=vault.read_exact_vault_learning(r,authorize=lambda candidate:candidate==r)
    assert diagnostic is None and node.record_id==r.expected_record_id
    assert 'open' in calls and 'stat' in calls


@pytest.mark.parametrize('decision',[False,None,1,'true'])
def test_non_true_policy_denies_before_access(scope,monkeypatch,decision):
    _,_,request=scope;r=request()
    monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('filesystem opened before authorization'))
    node,d=vault.read_exact_vault_learning(r,authorize=lambda _:decision)
    assert node is None and d.reason_code=='authorization_denied' and d.source_label==''


def test_policy_error_does_not_disclose_error(scope,monkeypatch):
    _,_,request=scope;r=request()
    def deny(_):raise RuntimeError('private body /secret/absolute/path')
    monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('filesystem opened'))
    node,d=vault.read_exact_vault_learning(r,authorize=deny)
    assert node is None and d.reason_code=='authorization_denied'
    assert 'secret' not in repr(d) and 'private' not in repr(d)


@pytest.mark.parametrize('changes',[
    {'relative_path':'../outside.md'}, {'relative_path':'/Memories/Codex/a.md'},
    {'relative_path':'Memories//Codex/a.md'}, {'relative_path':'Memories/Codex/./a.md'},
    {'relative_path':'Memories/Codex/../a.md'}, {'relative_path':'Private/a.md'},
    {'relative_path':'Reference/Agent Skills/Shared/a.md'},
    {'relative_path':'Memories/Codex/a.txt'}, {'relative_path':'Memories/Codex/a\nb.md'},
    {'max_bytes':True}, {'max_bytes':0}, {'max_bytes':16385},
    {'expected_sha256':'A'*64}, {'expected_sha256':'x'},
    {'expected_record_id':''}, {'expected_record_id':5},
    {'root_inode':False}, {'vault_root':'relative-root'},
    {'vault_root':'//'}, {'vault_root':'//synthetic-vault'},
])
def test_invalid_scope_is_rejected_before_policy_and_access(scope,monkeypatch,changes):
    _,_,request=scope;r=request(**changes)
    monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('filesystem opened'))
    node,d=vault.read_exact_vault_learning(r,authorize=lambda _:pytest.fail('policy saw malformed scope'))
    assert node is None and d.reason_code=='invalid_request' and not d.source_label


@pytest.mark.parametrize('field,value,reason',[
    ('expected_sha256','0'*64,'note_hash_mismatch'),
    ('expected_record_id','alr-other','record_id_mismatch'),
    ('root_inode',1,'vault_identity_mismatch'),
])
def test_approved_but_incorrect_binding_refuses(scope,field,value,reason):
    _,_,request=scope;r=request(**{field:value})
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code==reason


@pytest.mark.parametrize('size,reason',[(16384,None),(16385,'note_too_large')])
def test_raw_byte_boundary(scope,size,reason):
    _,p,request=scope;body=note_bytes();p.write_bytes(body+b'x'*(size-len(body)));r=request()
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert (d.reason_code if d else None)==reason
    assert (node is not None)==(reason is None)


@pytest.mark.parametrize('kind',['final-symlink','directory-symlink','ancestor-symlink','hardlink','directory','fifo'])
def test_unsafe_filesystem_objects_refuse_without_read(scope,tmp_path,monkeypatch,kind):
    root,p,request=scope;r=request()
    def final_symlink():
        target=tmp_path/'outside.md';target.write_bytes(p.read_bytes());p.unlink();p.symlink_to(target)
    def directory_symlink():
        original=p.parent;renamed=original.with_name('preserved');original.rename(renamed);original.symlink_to(renamed,target_is_directory=True)
    def ancestor_symlink():
        alias=tmp_path/'alias';alias.symlink_to(root,target_is_directory=True)
        return replace(r,vault_root=str(alias))
    def hardlink():
        os.link(p,tmp_path/'extra-link')
    def directory():
        p.unlink();p.mkdir()
    def fifo():
        p.unlink();os.mkfifo(p)
    selected={
        'final-symlink':final_symlink,'directory-symlink':directory_symlink,
        'ancestor-symlink':ancestor_symlink,'hardlink':hardlink,
        'directory':directory,'fifo':fifo,
    }[kind]()
    if selected is not None:r=selected
    monkeypatch.setattr(os,'read',lambda *_:pytest.fail('unsafe object read'))
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code in {'unsafe_note_path','note_hardlinked','note_not_regular'}
    assert str(root) not in repr(d)


@pytest.mark.parametrize('payload,reason',[(b'\xff','invalid_utf8'),(b'---\nrecord_id: x\nschema_name: wrong\n---\nbody','unsupported_schema')])
def test_invalid_content_refuses(scope,payload,reason):
    _,p,request=scope;p.write_bytes(payload);r=request()
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code==reason


@pytest.mark.parametrize('mutation',['replace-leaf','replace-parent','replace-root','replace-with-symlink','replace-parent-symlink','grow','same-size-write','short-read'])
def test_inconsistent_capture_refuses(scope,monkeypatch,mutation):
    root,p,request=scope;r=request();real=os.read
    def changed_read(fd,n):
        data=real(fd,n)
        def replace_leaf():
            replacement=p.with_suffix('.replacement');replacement.write_bytes(p.read_bytes());os.replace(replacement,p)
            return data
        def replace_parent():
            original=p.parent;original.rename(original.with_name('old'));original.mkdir();p.write_bytes(data)
            return data
        def replace_root():
            root.rename(root.with_name('old-root'));p.parent.mkdir(parents=True);p.write_bytes(data)
            return data
        def replace_with_symlink():
            other=root.parent/'outside.md';other.write_bytes(data);p.unlink();p.symlink_to(other)
            return data
        def replace_parent_symlink():
            original=p.parent;old=original.with_name('old');original.rename(old);original.symlink_to(old,target_is_directory=True)
            return data
        def grow():
            with p.open('ab') as f:f.write(b'x')
            return data
        def same_size_write():
            with p.open('r+b') as f:f.seek(-1,2);f.write(b'!')
            return data
        def short_read():
            return data[:-1]
        return {
            'replace-leaf':replace_leaf,'replace-parent':replace_parent,
            'replace-root':replace_root,'replace-with-symlink':replace_with_symlink,
            'replace-parent-symlink':replace_parent_symlink,'grow':grow,
            'same-size-write':same_size_write,'short-read':short_read,
        }[mutation]()
    monkeypatch.setattr(os,'read',changed_read)
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code=='note_changed'


def test_growth_beyond_limit_uses_only_one_sentinel(scope,monkeypatch):
    _,p,request=scope;r=request();real=os.read;requested=[]
    def grow_before_read(fd,n):
        with p.open('ab') as f:f.write(b'x'*20000)
        requested.append(n);return real(fd,n)
    monkeypatch.setattr(os,'read',grow_before_read)
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code=='note_too_large'
    assert requested==[16385]


def test_replacement_during_parse_refuses(scope,monkeypatch):
    from agent import learning_vault_exact as exact
    _,p,request=scope;r=request();real=exact._node_from_text
    def swap(raw,label,**kwargs):
        result=real(raw,label,**kwargs);other=p.with_suffix('.replacement');other.write_bytes(p.read_bytes());os.replace(other,p);return result
    monkeypatch.setattr(exact,'_node_from_text',swap)
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code=='note_changed'


def test_same_buffer_parser_has_no_path_reopen(scope,monkeypatch):
    _,p,request=scope;r=request();before_mtime=int(p.stat().st_mtime)
    monkeypatch.setattr(Path,'resolve',lambda *_a,**_k:pytest.fail('resolve'))
    monkeypatch.setattr(Path,'read_text',lambda *_a,**_k:pytest.fail('pathname reopen'))
    monkeypatch.setattr(Path,'stat',lambda *_a,**_k:pytest.fail('pathname stat'))
    monkeypatch.setattr(vault,'_catalog_files',lambda *_a:pytest.fail('scan'))
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert d is None and node.timestamp==before_mtime


def test_policy_binds_all_fields(scope,monkeypatch):
    _,_,request=scope;grant=request();changed=replace(grant,max_bytes=100)
    monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('denied request accessed filesystem'))
    node,d=vault.read_exact_vault_learning(changed,authorize=lambda candidate:candidate==grant)
    assert node is None and d.reason_code=='authorization_denied'


def test_missing_policy_is_not_an_allow_default(scope):
    _,_,request=scope
    with pytest.raises(TypeError):vault.read_exact_vault_learning(request())


def test_closed_directory_descriptor_fails_bounded(scope,monkeypatch):
    from agent import learning_vault_exact as exact
    _,_,request=scope;r=request();real=exact._open_child
    def close_after_open(*args):
        fd=real(*args);os.close(fd);return fd
    monkeypatch.setattr(exact,'_open_child',close_after_open)
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code=='note_unreadable'


@pytest.mark.parametrize('old,new,reason',[
    (b'sync_owned: true',b'sync_owned: false','sync_owned_required'),
    (b'classification: diagnostic-only',b'classification: operational','unsupported_classification'),
    (b'authority: narrative-only',b'authority: runtime-truth','unsupported_authority'),
    (b'kind: memory',b'kind: arbitrary','unsupported_kind'),
])
def test_normalized_ingestion_rules_remain_required(scope,old,new,reason):
    _,p,request=scope;p.write_bytes(p.read_bytes().replace(old,new));r=request()
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code==reason


def test_wrong_hash_is_not_parsed(scope,monkeypatch):
    from agent import learning_vault_exact as exact
    _,_,request=scope;r=request(expected_sha256='0'*64)
    monkeypatch.setattr(exact,'_node_from_text',lambda *_a,**_k:pytest.fail('unbound bytes parsed'))
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert node is None and d.reason_code=='note_hash_mismatch'


def test_explicit_roots_do_not_leak_across_host_profiles(tmp_path,monkeypatch):
    grants=[]
    for name in ['A','B']:
        root=(tmp_path/name);root.mkdir();root=root.resolve();rel='Memories/Codex/note.md';p=root/rel;p.parent.mkdir(parents=True)
        p.write_bytes(note_bytes('alr-'+name).replace(b'Synthetic durable lesson.',name.encode()))
        st=root.stat();grants.append(vault.ExactVaultNoteRequest(str(root),st.st_dev,st.st_ino,rel,'alr-'+name,hashlib.sha256(p.read_bytes()).hexdigest()))
    for i in [0,1,0]:
        grant=grants[i];home=tmp_path/('home'+str(i));home.mkdir(exist_ok=True);monkeypatch.setenv('HERMES_HOME',str(home))
        node,d=vault.read_exact_vault_learning(grant,authorize=lambda requested:requested==grant)
        assert d is None and node.record_id==grant.expected_record_id and node.summary==['A','B'][i]


def test_request_is_immutable_and_no_model_or_config_import(scope,monkeypatch):
    import builtins
    from dataclasses import FrozenInstanceError
    _,_,request=scope;r=request();original=builtins.__import__
    with pytest.raises(FrozenInstanceError):r.max_bytes=1
    def bounded_import(name,*args,**kwargs):
        if name in {'run_agent','hermes_cli.config','agent.learning_graph','agent.memory_manager'}:
            pytest.fail('out-of-scope source access')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',bounded_import)
    node,d=vault.read_exact_vault_learning(r,authorize=lambda approved:approved==r)
    assert d is None and node.record_id==r.expected_record_id


def test_title_absent_fallback_is_pure_and_preserved_for_both_apis(scope):
    root,p,request=scope;r=request()
    exact,d=vault.read_exact_vault_learning(r,authorize=lambda candidate:candidate==r)
    catalog,diagnostics=vault.read_vault_learning(root)
    assert d is None and diagnostics==[]
    assert exact.label==p.stem and catalog[0].label==p.stem


def test_legacy_catalog_symlink_keeps_original_filename_fallback(scope):
    root,p,_=scope;alias=p.with_name('alias.md');alias.symlink_to(p)
    node,d=vault._node_from_note(alias,root)
    assert d is None and node.label==alias.stem


def test_policy_receives_private_validated_snapshot(scope):
    _,_,request=scope;r=request();before=replace(r);seen=[]
    def policy(candidate):
        assert candidate is not r;seen.append(candidate)
        object.__setattr__(r,'expected_record_id','changed-original')
        return candidate==before
    node,d=vault.read_exact_vault_learning(r,authorize=policy)
    assert d is None and node.record_id==before.expected_record_id
    assert seen[0]==before


def test_policy_time_selection_mutation_is_not_consumed(scope,monkeypatch):
    _,_,request=scope;r=request()
    def policy(candidate):
        object.__setattr__(candidate,'relative_path','Memories/Codex/other.md')
        return True
    monkeypatch.setattr(os,'open',lambda *_a,**_k:pytest.fail('mutated scope accessed filesystem'))
    node,d=vault.read_exact_vault_learning(r,authorize=policy)
    assert node is None and d.reason_code=='authorization_denied'
