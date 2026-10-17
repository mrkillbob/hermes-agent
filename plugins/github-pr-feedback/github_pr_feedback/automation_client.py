"""The existing canonical automation credential factory, shared without CLI imports."""

from .github_client import GitHubClient, GitHubClientError
from .policy import PluginPolicy


def automation_client(
    policy: PluginPolicy, *, client_type=GitHubClient, allow_actions=True
) -> GitHubClient:
    """Return the sole authenticated client for governed Hermes GitHub activity."""

    # Lightweight injected test doubles intentionally do not implement the
    # production factory. Production always uses the real class below.
    factory = getattr(client_type, "for_automation_identity", None)
    if not callable(factory):
        if client_type is GitHubClient:
            raise GitHubClientError("automation identity factory unavailable")
        return client_type()
    if not isinstance(policy, PluginPolicy):
        raise GitHubClientError("invalid GitHub automation policy", code="invalid_policy")
    settings = policy.github_identity
    if settings is None:
        raise GitHubClientError(
            "Hermes GitHub automation identity is not configured",
            code="automation_identity_not_configured",
        )
    kwargs = {"expected_login": settings.expected_login, "token_env": settings.token_env}
    if allow_actions:
        actions_settings = policy.github_actions_permissions_identity
        kwargs.update(
            actions_permissions_expected_login=actions_settings.expected_login
            if actions_settings is not None
            else None,
            actions_permissions_gh_config_dir=actions_settings.gh_config_dir
            if actions_settings is not None
            else None,
            actions_permissions_repositories=actions_settings.repositories
            if actions_settings is not None
            else frozenset(),
        )
    return factory(**kwargs)
