"""Deleted skills stay out of catalog discovery without hiding active skills."""
import pytest

from agent.skill_utils import is_excluded_skill_path, iter_skill_index_files


@pytest.mark.parametrize('filename', ['SKILL.md', 'DESCRIPTION.md'])
def test_retired_trash_skill_is_not_indexed(tmp_path, filename):
    active = tmp_path / 'active' / filename
    retired = tmp_path / '.trash' / 'deleted-previous-name' / filename
    for path in (active, retired):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('---\nname: sample\n---\nActive or deleted sample', encoding='utf-8')
    assert list(iter_skill_index_files(tmp_path, filename)) == [active]


def test_trash_is_excluded_for_other_skill_scanners(tmp_path):
    assert is_excluded_skill_path(tmp_path / '.trash' / 'retired' / 'SKILL.md', root=tmp_path)
    assert not is_excluded_skill_path(tmp_path / 'trash-analysis' / 'SKILL.md', root=tmp_path)
    assert not is_excluded_skill_path(tmp_path / 'active' / 'SKILL.md', root=tmp_path)
