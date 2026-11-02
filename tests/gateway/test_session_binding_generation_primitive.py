"""Actual generation primitive with inert authorities; no native authority is implied."""
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gateway import session_binding_generation as g


def fixture(incarnation='a' * 32):
    owner = g.BindingGenerations(incarnation)
    authority = SimpleNamespace(profile_id='/synthetic/profile', instance_id='instance', epoch=1)
    with owner.replacement(authority.profile_id, authority):
        pass
    scope = g.BindingScope('synthetic-subject', 'transport', authority.profile_id, 'session')
    return owner, authority, scope


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_observation_binds_exact_actor_profile_session_and_decimal_revision(self):
        _, authority, scope = fixture()
        observation = g.observe(authority, scope)
        self.assertEqual(observation.metadata_incarnation, 'a' * 32)
        self.assertEqual(observation.metadata_revision, '2')
        g.validate_observation(observation, authority, scope)
        for field in ('actor_subject', 'actor_transport_id', 'profile_id', 'session_id'):
            with self.subTest(field=field), self.assertRaises(g.GenerationUnavailable):
                g.validate_observation(observation, authority, replace(scope, **{field: 'other'}))
        for changed in (replace(observation, metadata_revision='02'),
                        replace(observation, metadata_revision=2),
                        replace(observation, metadata_incarnation='b' * 32)):
            with self.assertRaises(g.GenerationUnavailable):
                g.validate_observation(changed, authority, scope)

    async def test_remove_readd_and_registry_replacement_reject_old_observations(self):
        owner, authority, scope = fixture()
        old = g.observe(authority, scope)
        with owner.replacement(authority.profile_id, None):
            pass
        with owner.replacement(authority.profile_id, authority):
            pass
        with self.assertRaises(g.GenerationUnavailable):
            g.validate_observation(old, authority, scope)
        current = g.observe(authority, scope)
        self.assertGreater(int(current.metadata_revision), int(old.metadata_revision))
        other_owner = g.BindingGenerations('b' * 32)
        with other_owner.replacement(authority.profile_id, authority):
            pass
        with self.assertRaises(g.GenerationUnavailable):
            g.validate_observation(current, authority, scope)

    async def test_replacement_bookkeeping_reentry_and_partial_failure(self):
        owner, authority, scope = fixture()
        with self.assertRaisesRegex(ValueError, 'partial bookkeeping'):
            with owner.replacement(authority.profile_id, authority):
                with self.assertRaises(g.GenerationUnavailable):
                    g.observe(authority, scope)
                with self.assertRaises(g.GenerationUnavailable):
                    with owner.replacement(authority.profile_id, None):
                        self.fail('reentrant replacement entered')
                raise ValueError('partial bookkeeping')
        with self.assertRaises(g.GenerationUnavailable):
            g.observe(authority, scope)

    async def test_aba_publication_invalidates_equal_scope(self):
        _, authority, scope = fixture()
        old = g.observe(authority, scope)
        for value in ('B', 'A'):
            with g.publication(authority):
                authority.route = value
        with self.assertRaises(g.GenerationUnavailable):
            g.validate_observation(old, authority, scope)

    async def test_preparation_before_await_cannot_publish_after_replacement(self):
        owner, authority, _ = fixture()
        prepared, resume = asyncio.Event(), asyncio.Event()
        async def delayed():
            token = g.prepare(authority)
            prepared.set()
            await resume.wait()
            with g.publication(authority, token):
                self.fail('stale work published')
        task = asyncio.create_task(delayed())
        try:
            await asyncio.wait_for(prepared.wait(), 2)
            with owner.replacement(authority.profile_id, authority):
                pass
        finally:
            resume.set()
        with self.assertRaises(g.GenerationUnavailable):
            await asyncio.wait_for(task, 2)

    async def test_explicit_nested_permit_and_caught_failure_poison(self):
        owner, authority, scope = fixture()
        with g.publication(authority) as parent:
            with self.assertRaises(g.GenerationUnavailable):
                g.observe(authority, scope)
            with self.assertRaises(g.GenerationUnavailable):
                with owner.replacement(authority.profile_id, None):
                    self.fail('replacement entered publication')
            try:
                with g.publication(authority, parent=parent):
                    raise ValueError('nested partial failure')
            except ValueError:
                pass
            with self.assertRaises(g.GenerationUnavailable):
                with g.publication(authority, parent=parent):
                    self.fail('poisoned permit entered')
        with self.assertRaises(g.GenerationUnavailable):
            g.observe(authority, scope)

    async def test_cancellation_poison_and_expired_nested_permit(self):
        _, authority, scope = fixture()
        with g.publication(authority) as parent:
            with g.publication(authority, parent=parent):
                pass
        with self.assertRaises(g.GenerationUnavailable):
            with g.publication(authority, parent=parent):
                self.fail('expired permit reused')
        with self.assertRaises(asyncio.CancelledError):
            with g.publication(authority):
                raise asyncio.CancelledError()
        with self.assertRaises(g.GenerationUnavailable):
            g.observe(authority, scope)

    async def test_retirement_and_changed_authority_context_refuse(self):
        _, authority, scope = fixture()
        observation = g.observe(authority, scope)
        for flag in ('retiring', 'retired'):
            setattr(authority, flag, True)
            with self.assertRaises(g.GenerationUnavailable):
                g.validate_observation(observation, authority, scope)
            setattr(authority, flag, False)
        authority.epoch += 1
        with self.assertRaises(g.GenerationUnavailable):
            g.validate_observation(observation, authority, scope)

    async def test_revision_exhaustion_never_wraps_or_restores_eligibility(self):
        owner, authority, scope = fixture()
        slot = owner.slots[authority.profile_id]
        slot.revision = g.MAX_REVISION - 1
        observation = g.observe(authority, scope)
        self.assertEqual(observation.metadata_revision, str(g.MAX_REVISION - 1))
        with self.assertRaises(g.GenerationUnavailable):
            with g.publication(authority):
                self.fail('overflow publication entered')
        with self.assertRaises(g.GenerationUnavailable):
            with owner.replacement(authority.profile_id, authority):
                self.fail('overflow replacement entered')
        self.assertEqual(slot.revision, g.MAX_REVISION - 1)
        with self.assertRaises(g.GenerationUnavailable):
            g.validate_observation(observation, authority, scope)

    async def test_foreign_thread_identity_refuses_without_mutation(self):
        owner, authority, scope = fixture()
        observation = g.observe(authority, scope)
        with patch.object(g, 'get_ident', return_value=-1):
            with self.assertRaises(g.GenerationUnavailable):
                with owner.replacement(authority.profile_id, None):
                    self.fail('foreign thread entered')
        g.validate_observation(observation, authority, scope)

    async def test_last_complete_publication_reaches_max_then_refuses_next(self):
        owner, authority, scope = fixture()
        slot = owner.slots[authority.profile_id]
        slot.revision = g.MAX_REVISION - 2
        with g.publication(authority):
            pass
        observation = g.observe(authority, scope)
        self.assertEqual(observation.metadata_revision, str(g.MAX_REVISION))
        g.validate_observation(observation, authority, scope)
        with self.assertRaises(g.GenerationUnavailable):
            with g.publication(authority):
                self.fail('publication wrapped')
        self.assertEqual(slot.revision, g.MAX_REVISION)
        with self.assertRaises(g.GenerationUnavailable):
            g.validate_observation(observation, authority, scope)


class InputAndLoopTests(unittest.TestCase):
    def test_bad_incarnations_and_scope_values_refuse(self):
        for value in (None, True, '', 'A' * 32, 'a' * 31, 'a' * 33):
            with self.subTest(value=value), self.assertRaises(g.GenerationUnavailable):
                g.BindingGenerations(value)
        for value in (None, True, '', 'a' * 4097):
            with self.subTest(value=value), self.assertRaises(g.GenerationUnavailable):
                g.BindingScope(value, 'transport', 'profile', 'session')

    def test_bound_registry_rejects_other_loop_and_no_loop(self):
        owner, authority, scope = fixture()
        async def bind():
            g.observe(authority, scope)
        asyncio.run(bind())
        with self.assertRaises(g.GenerationUnavailable):
            with owner.replacement(authority.profile_id, None):
                self.fail('no-loop mutation entered')
        async def another_loop():
            with self.assertRaises(g.GenerationUnavailable):
                g.observe(authority, scope)
        asyncio.run(another_loop())


if __name__ == '__main__':
    unittest.main(verbosity=2)
