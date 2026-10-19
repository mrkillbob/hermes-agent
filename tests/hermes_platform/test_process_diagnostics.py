"""Spawn identity is correlatable without publishing paths, argv or environment."""
from __future__ import annotations

import hashlib
import json
import os
import sys

import pytest


def test_launch_context_pins_images_and_parent_without_private_paths(tmp_path, monkeypatch):
    from hermes_platform.host import process_diagnostics as diagnostics

    root = tmp_path / "private-checkout"
    image = root / "venv" / "Scripts" / "python.exe"
    parent = tmp_path / "private-user" / "unlisted-secret-program"
    actual_image = root / "managed-python" / "python.exe"
    monkeypatch.setattr(diagnostics.os, "getpid", lambda: 321)
    monkeypatch.setattr(diagnostics.os, "getppid", lambda: 123)
    monkeypatch.setattr(diagnostics.sys, "executable", str(image))
    queried = []

    class Parent:
        def __init__(self, pid):
            queried.append(pid)
            self.pid = pid

        def exe(self):
            return str(actual_image if self.pid == 321 else parent)

    monkeypatch.setattr(diagnostics.psutil, "Process", Parent)
    report = diagnostics.process_launch_context(str(image), root)
    assert queried == [321, 123]
    assert report["client"]["pid"] == 321 and report["parent"]["pid"] == 123
    assert report["client"]["parent_pid"] == 123
    assert report["client"]["image"] != report["requested_image"]
    assert report["python_executable"] == report["requested_image"]
    assert report["requested_image"]["name"] == "python.exe"
    assert report["requested_image"]["within_install"] is True
    assert report["parent"]["image"]["name"] == "other"
    expected = hashlib.sha256(os.path.normcase(os.path.abspath(image)).encode()).hexdigest()
    assert report["requested_image"]["path_sha256"] == expected
    assert report["cwd_sha256"] != expected
    text = json.dumps(report)
    assert "private-checkout" not in text and "private-user" not in text
    assert "unlisted-secret-program" not in text


def test_exited_parent_does_not_erase_client_identity(tmp_path, monkeypatch):
    from hermes_platform.host import process_diagnostics as diagnostics

    def gone(pid):
        raise diagnostics.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(diagnostics.psutil, "Process", gone)
    report = diagnostics.process_launch_context(sys.executable, tmp_path)
    assert report["client"]["pid"] == os.getpid()
    assert report["parent"] == {"pid": os.getppid(), "unavailable": True}
    assert report["requested_image"]["path_sha256"]


@pytest.mark.platforms("windows")
def test_native_windows_context_reads_only_current_parent_and_requested_image(tmp_path):
    from hermes_platform.host.process_diagnostics import process_launch_context

    report = process_launch_context(sys.executable, tmp_path)
    assert report["client"]["pid"] == os.getpid()
    assert report["parent"]["pid"] == os.getppid()
    assert report["requested_image"] == report["python_executable"]
    assert report["client"]["image"]["path_sha256"]
    assert sys.executable not in json.dumps(report)
