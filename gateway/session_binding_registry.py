"""Registry publication joins native transport commits; no principal is issued."""
from contextlib import contextmanager


@contextmanager
def binding_change(registry, key, replacement):
    from gateway.session_authority import SessionAuthority
    from gateway.session_native_commit import native_authority_change
    # Generic registry users remain supported but cannot qualify learning admission.
    qualified = replacement if type(replacement) is SessionAuthority else None
    with native_authority_change(registry._by_key.get(key)):
        with registry._binding_generations.replacement(key, qualified):
            if qualified is not None:
                qualified._binding_registry = registry
            yield
