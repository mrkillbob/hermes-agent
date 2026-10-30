"""Auth failure guidance, with dependencies read through the public auth facade."""

def format_auth_error(error: Exception) -> str:
    """Map auth failures to concise user-facing guidance."""
    from hermes_cli import auth as auth_mod

    if not isinstance(error, auth_mod.AuthError) or auth_mod.is_rate_limited_auth_error(error):
        # Rate-limit / quota errors are not credential problems: never append "re-authenticate".
        return str(error)
    if error.relogin_required:
        # Profile-aware: a bare `hermes model` from a named profile re-signs the ROOT store (#114012).
        from hermes_constants import profile_cli_selector

        return f"{error} Run `hermes {profile_cli_selector()}model` to re-authenticate."
    if error.code in auth_mod._ENTITLEMENT_ERROR_CODES:
        if error.provider == "nous":
            return auth_mod._format_nous_entitlement_auth_error(error)
        generic = auth_mod._GENERIC_ENTITLEMENT_MESSAGES.get(error.code)
        if generic:
            return generic
    if error.code == "temporarily_unavailable":
        return f"{error} Please retry in a few seconds."
    return str(error)
