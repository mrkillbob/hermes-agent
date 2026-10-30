"""Producer associations between a discovered alias and an independently granted target."""
from dataclasses import dataclass
from pathlib import Path

from agent.llm_egress_firewall import SourceGrant, source_grant_digest


@dataclass(frozen=True)
class ContextReadBinding:
    grant: SourceGrant
    working_root: Path
    relative_path: str

    def matches(self, grant, source_path):
        from agent.source_context_rendering import hint_relative_path
        from agent.subdirectory_hints import _resolved_hint_target

        relative = hint_relative_path(self.relative_path)
        if relative is None:
            return False
        alias = self.working_root.joinpath(*relative.parts)
        try:
            return (self.working_root.resolve() == self.working_root
                    and _resolved_hint_target(alias, self.working_root) == grant.canonical_path
                    and source_path.is_relative_to(alias.parent.resolve()))
        except (OSError, RuntimeError, ValueError):
            return False


class ContextReadBindings:
    """No file bytes; at most one bounded association per issued producer read."""

    def __init__(self):
        self._bindings = {}

    def remember(self, grant, working_root, relative_path, limit, byte_limit):
        binding = ContextReadBinding(grant, working_root, relative_path)
        if not binding.matches(grant, working_root.joinpath(*Path(relative_path).parts)):
            return
        key = (source_grant_digest(grant), working_root, relative_path)
        if key in self._bindings:
            return
        current = tuple(item for item in self._bindings.values() if item.grant.request_id == grant.request_id)
        size = sum(self._path_bytes(item) for item in (*current, binding))
        if len(current) < limit and size <= byte_limit:
            self._bindings[key] = binding

    @staticmethod
    def _path_bytes(binding):
        return len(str(binding.working_root).encode('utf-8')) + len(binding.relative_path.encode('utf-8'))

    def select(self, digest, original_request_id, identity, eligible):
        if not eligible:
            return ()
        return tuple(
            binding for key, binding in self._bindings.items()
            if key[0] == digest and binding.grant.request_id == original_request_id
            and (binding.grant.session_id, binding.grant.turn_id, binding.grant.policy_digest)
            == (identity['session_id'], identity['turn_id'], identity['policy_digest'])
        )

    def clear_request(self, request_id, validated):
        self._bindings = {
            key: binding for key, binding in self._bindings.items()
            if binding.grant.request_id != request_id or key[0] in validated
        }

    def clear_turn(self, turn_id):
        self._bindings = {
            key: binding for key, binding in self._bindings.items()
            if binding.grant.turn_id != turn_id
        }
