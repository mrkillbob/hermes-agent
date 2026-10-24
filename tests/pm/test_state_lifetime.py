"""Stable outside-domain ownership coordinates readers with collection."""
from pathlib import Path

import pytest


def test_new_builder_domain_is_qualified_and_held(tmp_path):
    from pm import lifetime

    domain = tmp_path / "installs" / "new"
    with lifetime.activity(domain, create=True):
        with lifetime.gate(domain) as guard:
            assert guard.qualified
            assert guard.held()
    with lifetime.gate(domain) as guard:
        assert guard.qualified
        assert not guard.held()
    assert not guard.namespace.is_relative_to(domain)


def test_legacy_domain_is_never_silently_qualified(tmp_path):
    from pm import lifetime

    domain = tmp_path / "legacy"
    domain.mkdir()
    with lifetime.activity(domain):
        with lifetime.gate(domain) as guard:
            assert not guard.qualified
            assert guard.held()
    with lifetime.gate(domain) as guard:
        assert not guard.qualified


def test_generation_readers_do_not_pin_unrelated_generations(tmp_path):
    from pm import lifetime

    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        first, second = domain / "environments" / "first", domain / "environments" / "second"
        first.mkdir(parents=True)
        second.mkdir()
    with lifetime.activity(domain, generation=first):
        with lifetime.activity(domain, generation=second):
            with lifetime.gate(domain) as guard:
                assert guard.held(generation=first)
                assert guard.held(generation=second)
                assert not guard.held(writers_only=True)
        with lifetime.gate(domain) as guard:
            assert guard.held(generation=first)
            assert not guard.held(generation=second)


def test_domain_replacement_is_not_a_qualified_owner(tmp_path):
    from pm import lifetime

    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        pass
    domain.rename(tmp_path / "previous")
    domain.mkdir()
    with lifetime.gate(domain) as guard:
        assert not guard.qualified
    with pytest.raises(lifetime.LifetimeUnavailable):
        with lifetime.activity(domain):
            pytest.fail("changed physical domain must not acquire a reader")


def test_unreadable_activity_namespace_is_unknown_hold(tmp_path, monkeypatch):
    from pm import lifetime

    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        pass
    real_iterdir = Path.iterdir
    with lifetime.gate(domain) as guard:
        def denied(path):
            if path == guard.activities:
                raise PermissionError("synthetic inaccessible activities")
            return real_iterdir(path)
        monkeypatch.setattr(Path, "iterdir", denied)
        assert guard.held()


def test_unknown_activity_record_is_preserved(tmp_path):
    from pm import lifetime

    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        pass
    with lifetime.gate(domain) as guard:
        unknown = guard.activities / "damaged"
        unknown.write_text("{", encoding="utf-8")
        assert guard.held()
        assert unknown.exists()


def test_unexplained_domain_disappearance_is_not_requalified(tmp_path):
    from pm import lifetime

    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        pass
    domain.rmdir()
    with pytest.raises(lifetime.LifetimeUnavailable):
        with lifetime.activity(domain, create=True):
            pytest.fail("a missing qualified owner requires a confirmed retirement receipt")


def test_confirmed_retirement_allows_a_fresh_domain(tmp_path):
    from pm import lifetime

    domain = tmp_path / "state"
    with lifetime.activity(domain, create=True):
        pass
    with lifetime.gate(domain) as guard:
        ownership = guard._snapshot()
        domain.rmdir()
        guard.confirm_removed(ownership)
    with lifetime.activity(domain, create=True):
        with lifetime.gate(domain) as guard:
            assert guard.qualified


def test_unknown_publish_temporary_record_is_preserved(tmp_path):
    from pm import lifetime

    domain = tmp_path / 'state'
    with lifetime.activity(domain, create=True):
        pass
    with lifetime.gate(domain) as guard:
        unknown = guard.activities / '.publish-unknown'
        unknown.write_text('partial')
        assert guard.held()
        assert unknown.exists()
