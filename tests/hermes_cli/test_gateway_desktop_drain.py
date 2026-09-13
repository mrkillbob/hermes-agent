from pathlib import Path


def test_wait_for_desktop_drain_marks_every_profile_and_waits_for_two_idle_samples():
    from hermes_cli.gateway_desktop_drain import wait_for_desktop_drain

    events = []
    snapshots = iter([(1, 2), (0, 1), (0, 0), (0, 0)])
    homes = [Path("/profiles/default"), Path("/profiles/research")]

    result = wait_for_desktop_drain(
        homes=homes,
        snapshot=lambda: next(snapshots),
        write_marker=lambda home: events.append(("mark", home)),
        sleep=lambda seconds: events.append(("sleep", seconds)),
        poll_interval=0.25,
        idle_samples_required=2,
    )

    assert result.gateway_agents == 0
    assert result.kanban_workers == 0
    assert events[:2] == [("mark", homes[0]), ("mark", homes[1])]
    assert events.count(("sleep", 0.25)) == 3


def test_wait_for_desktop_drain_refreshes_markers_during_long_work():
    from hermes_cli.gateway_desktop_drain import wait_for_desktop_drain

    marks = []
    now = iter([0.0, 0.0, 31.0, 31.0])
    snapshots = iter([(0, 1), (0, 1), (0, 0)])

    wait_for_desktop_drain(
        homes=[Path("/profiles/default")],
        snapshot=lambda: next(snapshots),
        write_marker=marks.append,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(now),
        refresh_interval=30.0,
        idle_samples_required=1,
    )

    assert marks == [Path("/profiles/default"), Path("/profiles/default")]


def test_drain_all_desktop_work_scopes_single_profile_to_current_home(monkeypatch, tmp_path):
    import hermes_constants
    import hermes_cli.gateway_desktop_drain as drain

    other_home = tmp_path / "other"
    current_home = tmp_path / "current"
    seen = []
    monkeypatch.setattr(
        drain,
        "desktop_profile_homes",
        lambda: (current_home, other_home),
    )
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: current_home)
    monkeypatch.setattr(
        drain,
        "wait_for_desktop_drain",
        lambda **kwargs: seen.append(tuple(kwargs["homes"])) or drain.DesktopDrainSnapshot(0, 0),
    )

    drain.drain_all_desktop_work(all_profiles=False)

    assert seen == [(current_home,)]
