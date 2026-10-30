"""GitHub Copilot's place in the provider tables: picker row, label, group, aliases, models.dev overlay.

Core reads these only through generic ``ProviderProfile`` fields (``PICKER_FIELDS``).
"""

# Both Copilot providers fold under one "GitHub Copilot" row in the interactive pickers.
PICKER_GROUP = ("copilot", "GitHub Copilot", "GitHub token API or copilot --acp process")

PICKER_FIELDS = dict(
    display_name="GitHub Copilot",
    description="GitHub Copilot (Uses GITHUB_TOKEN or gh auth token)",
    picker_after="nvidia",
    picker_group=PICKER_GROUP,
    catalog_aliases=True,
    # The Copilot catalog serves OpenAI, Anthropic, Google and xAI models.
    aggregator=True,
    # models.dev lists GitHub Copilot under GITHUB_TOKEN only; Hermes also reads these two.
    provider_overlay={"extra_env_vars": ("COPILOT_GITHUB_TOKEN", "GH_TOKEN")},
    overlay_aliases=("github",),
)
