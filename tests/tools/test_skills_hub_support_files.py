"""Referenced skill support paths and missing-file recovery contracts."""

from unittest.mock import MagicMock, patch

from tools.skills_hub_github import GitHubAuth, GitHubSource
from tools.skills_hub_models import _referenced_support_paths


# ---------------------------------------------------------------------------
# Referenced-path extraction & missing support files (regression: a prose
# glob or a repo-only dev tool referenced in SKILL.md must not abort install)
# ---------------------------------------------------------------------------

class TestReferencedSupportPaths:
    def test_ignores_globs_placeholders_and_truncated_tokens(self):
        md = (
            "Load `references/type-*.md` before drawing, and "
            "`references/type-<name>.md` for the matching type.\n"
            "Real files: [flowchart](references/type-flowchart.md) and "
            "`references/type-architecture.md`.\n"
        )
        assert _referenced_support_paths(md) == {
            "references/type-flowchart.md",
            "references/type-architecture.md",
        }

    def test_keeps_real_backtick_references(self):
        md = "Run `python3 scripts/self_check.py <file>` and see `references/guide.md`.\n"
        assert _referenced_support_paths(md) == {
            "scripts/self_check.py",
            "references/guide.md",
        }

class TestGitHubSourceFetchMissingReferencedFile:
    def _source(self):
        auth = MagicMock(spec=GitHubAuth)
        return GitHubSource(auth=auth)

    def test_fetch_skips_missing_referenced_file_and_keeps_the_rest(self):
        md = (
            "---\nname: demo\ndescription: demo\n---\n\n"
            "See [guide](references/guide.md) and `references/missing.md`.\n"
        )
        tree_entries = [
            {"path": "skills/demo/references/guide.md", "type": "blob", "mode": "100644"},
        ]
        source = self._source()
        with patch.object(source, "_fetch_file_content", return_value=md), \
             patch.object(source, "_get_repo_tree", return_value=("main", tree_entries)), \
             patch.object(source, "_fetch_file_bytes", return_value=b"# guide"):
            bundle = source.fetch("owner/repo/skills/demo")

        assert bundle is not None
        assert bundle.name == "demo"
        # The present referenced file is bundled…
        assert bundle.files["references/guide.md"] == b"# guide"
        # …and the missing one is warned about and skipped, not fatal.
        assert "references/missing.md" not in bundle.files
