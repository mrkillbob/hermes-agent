"""GitHub Copilot ACP's place in the provider tables: picker row, label, group, aliases, overlay.

Core reads these only through generic ``ProviderProfile`` fields (``PICKER_FIELDS``).
"""

PICKER_FIELDS = dict(
    display_name="GitHub Copilot ACP",
    description="GitHub Copilot ACP (Spawns copilot --acp --stdio)",
    picker_after="copilot",
    # Same group as the ``copilot`` provider: one "GitHub Copilot" row in the interactive pickers.
    picker_group=("copilot", "GitHub Copilot", "GitHub token API or copilot --acp process"),
    catalog_aliases=True,
    provider_overlay={"transport": "codex_responses", "auth_type": "external_process",
                      "base_url_override": "acp://copilot", "base_url_env_var": "COPILOT_ACP_BASE_URL"},
    overlay_aliases=("github-copilot-acp",),
)
