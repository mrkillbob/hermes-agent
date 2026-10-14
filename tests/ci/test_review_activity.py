"""The clean thumbs-up witness tolerates Codex's write ordering but not an older review."""

from scripts.ci.review_activity import clean_witness

CODEX = 7


def witness(reaction_at, completed="2026-10-14T06:17:34.501987+00:00",
            summary_edit="2026-10-14T06:17:35+00:00", reviews=None, content="+1"):
    activity = {"updated_at": summary_edit, "passes": {"code": {"at": completed}}}
    reactions = [{"content": content, "created_at": reaction_at, "user": {"id": CODEX}}]
    return clean_witness(activity, reactions, CODEX, reviews or {})


def test_thumbs_up_just_before_completion_stamp_and_summary_edit_is_fresh():
    assert witness("2026-10-14T06:17:33+00:00")


def test_thumbs_up_from_an_earlier_review_is_stale():
    assert not witness("2026-10-14T05:00:00+00:00")


def test_eyes_reaction_blocks_the_witness():
    activity = {"updated_at": "2026-10-14T06:17:35+00:00", "passes": {"code": {"at": "2026-10-14T06:17:34+00:00"}}}
    reactions = [{"content": "+1", "created_at": "2026-10-14T06:17:36+00:00", "user": {"id": CODEX}},
                 {"content": "eyes", "created_at": "2026-10-14T06:17:37+00:00", "user": {"id": CODEX}}]
    assert not clean_witness(activity, reactions, CODEX, {})


def test_thumbs_up_exactly_at_the_skew_limit_is_fresh_and_just_beyond_is_stale():
    assert witness("2026-10-14T06:17:29.501987+00:00")
    assert not witness("2026-10-14T06:17:29.501986+00:00")
