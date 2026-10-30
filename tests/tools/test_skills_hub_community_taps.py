"""New browseable vendor skills keep the fork's community admission policy."""

from unittest.mock import MagicMock

import pytest

from tools.skills_hub_github import GitHubAuth, GitHubSource


@pytest.mark.parametrize("repo", ["wandb/skills", "qdrant/skills", "pinecone-io/skills"])
def test_vendor_taps_remain_community_rated(repo):
    source = GitHubSource(auth=MagicMock(spec=GitHubAuth))
    assert source.trust_level_for(f"{repo}/example") == "community"
