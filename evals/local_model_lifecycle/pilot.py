"""Native local-model lifecycle pilot: one small staged model, one isolated router, whole-host memory.

Drives the real LlamaServerSupervisor (the existing owner of spawn, watchdog, readiness, unload
and stop) against a throwaway HERMES_HOME and a random port; nothing in ~/.hermes is read or
written and no model is downloaded. Default mode is --plan: preflight and the memory
interpretations only, nothing spawned. A real run needs --run plus --window-ack naming the
coordinated window (runner and installed-Hermes owners have agreed), and it refuses while a CI job
or any other llama-server is active.

Memory is reported three ways because "8 GiB" is ambiguous on unified memory and none is asserted:
  host_used  : (total - available) now minus the pre-run baseline, whole machine
  tree_rss   : summed RSS of the router and every descendant (Metal-mapped weights undercount)
  footprint  : sum of macOS `footprint` physical footprint over the same tree, when it answers
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

GIB = 1 << 30
DEFAULT_MODEL = Path.home() / ".hermes" / "models" / "Qwen3.5-4B-Q6_K.gguf"
LIMIT_GIB = 8


def newest_engine() -> Path | None:
    found = sorted((Path.home() / ".hermes" / "tools").glob("llamacpp-metal-*/llama-server"),
                   key=lambda p: int(p.parent.name.split("-")[2]) if p.parent.name.split("-")[2].isdigit() else 0)
    return found[-1] if found else None


def other_activity() -> list[str]:
    """Things this pilot must not disturb or be disturbed by."""
    names = {"Runner.Worker": "active CI job", "llama-server": "another llama-server"}
    return sorted({label for p in psutil.process_iter(["name"])
                   for key, label in names.items() if (p.info["name"] or "") == key})


def host_used() -> int:
    mem = psutil.virtual_memory()
    return mem.total - mem.available


def tree_pids(root_pid: int) -> list[int]:
    try:
        root = psutil.Process(root_pid)
        return [root.pid] + [c.pid for c in root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def tree_rss(pids: list[int]) -> int:
    total = 0
    for pid in pids:
        try:
            total += psutil.Process(pid).memory_info().rss
        except psutil.NoSuchProcess:
            continue
    return total


def tree_footprint(pids: list[int]) -> int | None:
    """`footprint` counts GPU-wired and compressed pages RSS misses; None when unavailable."""
    if not shutil.which("footprint") or not pids:
        return None
    result = subprocess.run(["footprint", "-j", "/dev/stdout", *[str(p) for p in pids]],
                            capture_output=True, text=True, timeout=30, check=False)
    try:
        data = json.loads(result.stdout)
        return sum(int(p["footprint"]) for p in data.get("processes", []))
    except (ValueError, KeyError, TypeError):
        return None


class Sampler(threading.Thread):
    """Peak whole-host and router-tree memory over named phases (footprint sampled every 2 s)."""

    def __init__(self, baseline: int, router_pid):
        super().__init__(daemon=True)
        self.baseline, self.router_pid = baseline, router_pid
        self.phase, self.stop_flag = "idle", threading.Event()
        self.peaks: dict[str, dict] = {}

    def run(self) -> None:
        tick = 0
        while not self.stop_flag.is_set():
            pids = tree_pids(self.router_pid()) if self.router_pid() else []
            row = self.peaks.setdefault(self.phase, {"host_used_delta": 0, "tree_rss": 0, "footprint": 0})
            row["host_used_delta"] = max(row["host_used_delta"], host_used() - self.baseline)
            row["tree_rss"] = max(row["tree_rss"], tree_rss(pids))
            if tick % 4 == 0 and (fp := tree_footprint(pids)):
                row["footprint"] = max(row["footprint"], fp)
            tick += 1
            self.stop_flag.wait(0.5)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chat(sup, model: str, prompt: str, max_tokens: int, timeout: float = 300) -> int:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(f"{sup.base_url}/chat/completions", data=body, headers={
        "Authorization": f"Bearer {sup.api_key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def resident_children(sup, pids_of) -> int:
    exe = str(sup.binary)
    return sum(1 for pid in pids_of() if pid != sup.proc.pid and _exe(pid) == exe)


def _exe(pid: int) -> str:
    try:
        return psutil.Process(pid).exe()
    except psutil.Error:
        return ""


def wait_for(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.25)
    return False


def scenarios(sup, model: str, sampler: Sampler, log) -> None:
    from hermes_cli.local_runtime.request_control import ManagedChildProbe, ScopedRequest

    def phase(name):
        sampler.phase = name
        log(name, "begin")

    pids = lambda: tree_pids(sup.proc.pid)

    phase("cold_router")
    time.sleep(3)

    phase("load_and_readiness_touch")
    log("ensure_model_ready", sup.ensure_model_ready(model, timeout_s=600))

    phase("shared_two_consumers")
    codes: list[int] = []
    threads = [threading.Thread(target=lambda: codes.append(chat(sup, model, "Say ok.", 16)))
               for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    log("shared_status_codes", sorted(codes))
    log("resident_children", resident_children(sup, pids))

    phase("unload_release")
    sup.unload_model(model)
    log("status_after_unload", sup.models().get(model))
    time.sleep(10)

    phase("cancel_while_loading")
    closer = threading.Thread(target=lambda: log("loading_chat_code", chat(sup, model, "hi", 8, timeout=0.3)))
    closer.start()  # client gives up 0.3 s into the autoload; the native load is not cancellable
    closer.join()
    log("status_right_after_client_gone", sup.models().get(model))
    log("load_finishes_anyway", wait_for(lambda: sup.models().get(model) in ("loaded", "ready"), 300))

    phase("cancel_in_flight")
    probe = ManagedChildProbe(sup._url(""), sup.api_key, model)
    request = ScopedRequest(probe, {"model": model, "max_tokens": 1024, "temperature": 0,
                                    "messages": [{"role": "user", "content": "Count from 1 to 2000."}]},
                            request_timeout_s=120)
    log("bind", request.start(bind_timeout_s=10))
    time.sleep(1)
    result = request.cancel(timeout_s=60)
    log("cancel_outcome", {"outcome": result.outcome.value, "detail": result.detail,
                           "elapsed_s": round(result.elapsed_s, 2)})

    phase("recover_after_router_kill")
    old = sup.proc.pid
    os.kill(old, signal.SIGKILL)
    log("restarted", wait_for(lambda: sup.proc.pid != old and sup.proc.poll() is None, 120))
    log("healthy_again", wait_for(lambda: bool(sup.models()), 120))
    log("orphans_after_kill", resident_children(sup, lambda: [p.pid for p in psutil.process_iter()]))
    log("ready_after_recovery", sup.ensure_model_ready(model, timeout_s=600))

    phase("closure")
    sup.stop()
    time.sleep(10)
    log("processes_left", [p.pid for p in psutil.process_iter() if _exe(p.pid) == str(sup.binary)])


def run(args) -> dict:
    model, engine = Path(args.model), Path(args.engine) if args.engine else newest_engine()
    work = Path(tempfile.mkdtemp(prefix="lifecycle-pilot-", dir=args.work_root))
    os.environ["HERMES_HOME"] = str(work / "home")
    (work / "home" / "models").mkdir(parents=True)
    (work / "home" / "models" / model.name).symlink_to(model)
    from hermes_cli.local_runtime.supervisor import LlamaServerSupervisor

    events: list[dict] = []
    started = time.monotonic()
    log = lambda *kv: events.append({"t": round(time.monotonic() - started, 2), "event": kv})
    sup = LlamaServerSupervisor(engine, work / "home" / "models", port=free_port(),
                                extra_args=["-c", "4096", "--parallel", "1"],
                                log_path=work / "llama-server.log")
    sampler = Sampler(host_used(), lambda: sup.proc.pid if sup.proc and sup.proc.poll() is None else None)
    sampler.start()
    try:
        sup.start(timeout_s=120)
        sup.primary_model = model.stem
        scenarios(sup, model.stem, sampler, log)
    finally:
        sup.stop()
        sampler.stop_flag.set()
        sampler.join(timeout=5)
        shutil.rmtree(work / "home", ignore_errors=True)
    return {"events": events, "peaks_gib": {
        phase: {key: round(value / GIB, 3) for key, value in row.items()}
        for phase, row in sampler.peaks.items()}, "work_dir": str(work)}


def plan(args) -> dict:
    model, engine = Path(args.model), Path(args.engine) if args.engine else newest_engine()
    mem = psutil.virtual_memory()
    blockers = other_activity()
    if not model.is_file():
        blockers.append(f"model missing: {model}")
    if engine is None or not engine.is_file():
        blockers.append("no staged Metal llama-server engine")
    if model.is_file() and mem.available < model.stat().st_size * 1.5 + 2 * GIB:
        blockers.append("available memory below model size x1.5 + 2 GiB")
    return {"model": str(model), "model_gib": round(model.stat().st_size / GIB, 2) if model.is_file() else None,
            "engine": str(engine), "total_gib": round(mem.total / GIB, 1),
            "available_gib": round(mem.available / GIB, 1), "limit_gib": LIMIT_GIB,
            "interpretations": ["host_used peak delta over baseline", "router-tree RSS peak",
                                "router-tree macOS footprint peak"],
            "blockers": blockers, "would_download": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=str(DEFAULT_MODEL))
    parser.add_argument("--engine")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--window-ack", help="name of the coordinated window; required with --run")
    parser.add_argument("--work-root", default=str(Path.home() / "Library" / "Caches" / "AgentBuilds"))
    parser.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args(argv)
    report = plan(args)
    if args.run:
        if not args.window_ack or report["blockers"]:
            print(json.dumps(report, indent=2))
            print("refusing to run: need --window-ack and no blockers", file=sys.stderr)
            return 2
        Path(args.work_root).mkdir(parents=True, exist_ok=True)
        report["run"] = run(args)
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
