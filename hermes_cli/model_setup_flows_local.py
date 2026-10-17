"""``hermes model`` → Local models: the desktop's Local Models setup, in the terminal.

The flow drives the gateway routes behind the desktop's Local Models pane
using existing profile-bound native HTTP tickets, so both surfaces run one code path:
the catalog priced for this machine, quickstart (pinned engine via PM, model download, server
start, default assignment) and activate for a model already on disk. Progress renders from the
same job view the desktop polls; Ctrl+C pauses a download, and the next run resumes from the
bytes already on disk.
"""

from __future__ import annotations

import time

_POLL_S = 0.2
_PAUSE_WAIT_S = 15.0


def local_models_available() -> bool:
    """Whether PM pins an engine for this machine at all. CPU is pinned on every target that has
    any build, so its absence means no backend can install; GPU detection waits for selection."""
    try:
        from hermes_cli.local_runtime.binaries import unavailable_reason

        return unavailable_reason("cpu") is None
    except Exception:
        return False


def _fmt_eta(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _rate(bytes_per_sec: float) -> str:
    mb = bytes_per_sec / (1 << 20)
    return f"{mb / 1024:.1f} GB/s" if mb >= 1024 else f"{mb:.0f} MB/s" if mb >= 10 else f"{mb:.1f} MB/s"


def _progress_line(lm, view: dict) -> str:
    line = f"  {view.get('detail') or 'Starting'}"
    if view.get("phase") not in lm._DOWNLOAD_PHASES:
        return line  # bytes stay on the job after the transfer; later steps show no counter
    total = view.get("total_bytes")
    if total:
        line += f"  {view.get('percent', 0)}%  {lm._human_gb(view.get('done_bytes') or 0)} / {lm._human_gb(total)}"
    if view.get("bytes_per_sec"):
        line += f"  {_rate(view['bytes_per_sec'])}"
    if view.get("eta_seconds") is not None:
        line += f"  ~{_fmt_eta(view['eta_seconds'])} left"
    return line


def _connect_local_models():
    """Use the selected profile's gateway; only explicit root setup may start its owner."""
    from hermes_cli.gateway_runtime import discover_gateway_endpoint, ensure_gateway_runtime
    from hermes_cli.local_model_client import LocalModelClient, LocalModelClientError
    from hermes_cli.setup import prompt_yes_no
    from hermes_constants import get_default_hermes_root, get_hermes_home

    home = get_hermes_home().resolve()
    observed = discover_gateway_endpoint(home)
    if observed.state != 'ready':
        if home != get_default_hermes_root(home=home).resolve():
            raise LocalModelClientError('Start the main Hermes gateway from the default profile first.')
        if not prompt_yes_no('  Start or await the main Hermes gateway? It continues in the background.', False):
            return None
        observed = ensure_gateway_runtime(home, idle_exit=False)
    if observed.state != 'ready' or observed.endpoint is None:
        raise LocalModelClientError(f'Gateway is {observed.state}; no model setup submitted.')
    return LocalModelClient(home, observed.endpoint)


def _follow_job(lm, client, job_id: str) -> str:
    """Follow an owner-side job; interrupting the viewer never stops the gateway."""
    from hermes_cli.local_model_client import LocalModelClientError

    shown_phase, width = None, 0
    try:
        while True:
            view = client.get(f'jobs/{job_id}')
            if view['status'] != 'running':
                break
            if view['phase'] != shown_phase:
                if shown_phase is not None:
                    print()
                shown_phase, width = view['phase'], 0
            line = _progress_line(lm, view)
            print(f"\r{line}{' ' * max(0, width - len(line))}", end='', flush=True)
            width = len(line)
            time.sleep(_POLL_S)
    except KeyboardInterrupt:
        print()
        try:
            paused = client.post('download/pause', {'job_id': job_id}).get('paused')
            if paused:
                deadline = time.monotonic() + _PAUSE_WAIT_S
                while time.monotonic() < deadline:
                    view = client.get(f'jobs/{job_id}')
                    if view['status'] != 'running':
                        if view['status'] == 'paused':
                            print('  Paused. Run `hermes model` → Local models again to resume; finished bytes are kept.')
                            return 'paused'
                        break
                    time.sleep(_POLL_S)
        except LocalModelClientError as exc:
            print(f'  {exc}')
        print('  Stopped following. Check Local Models in the gateway for the current job state.')
        return 'interrupted'
    except LocalModelClientError as exc:
        print(f'\n  ✗ {exc}')
        return 'unknown'
    if shown_phase is not None:
        print()
    if view['status'] == 'error':
        print(f"  ✗ {view.get('error') or 'Setup failed'}")
    elif view['status'] == 'done':
        print(f"  ✓ {view.get('detail')}")
    return view['status']


def _start(client, path, body) -> str | None:
    from hermes_cli.local_model_client import LocalModelClientError

    try:
        return client.post(path, body).get('job_id')
    except LocalModelClientError as exc:
        print(f'  ✗ {exc}')
        return None


def _row_label(row: dict, active_id: str | None) -> str:
    label = f"{'★ ' if row.get('recommended') else '  '}{row['display_name']} ({row['size_label']}"
    label += ", downloaded)" if row.get("downloaded") else ")"
    label += f" — {row.get('fit_summary', '')}"
    if active_id and active_id in (row.get("downloaded_model_id"), row.get("model_id")):
        label += "  ← currently active"
    return label


def _model_flow_local(config, current_model=""):
    """Pick a catalog model sized for this machine (or one already on disk) and make it the default,
    installing the pinned engine and downloading the model as needed."""
    del config, current_model
    from hermes_cli.main_provider_setup import _prompt_provider_choice
    from hermes_cli.setup import prompt_yes_no
    from hermes_cli.web_routers import local_models as lm

    hw = lm.local_models_hardware()
    status = lm.local_models_status()
    gpu = f"{hw['gpu_name']} · " if hw.get("gpu_name") else ""
    print(f"  Machine:  {gpu}{hw.get('vram_label')} {'unified' if hw.get('uma') else 'GPU'} memory · "
          f"{lm._human_gb(hw.get('ram_total_bytes') or 0)} RAM")
    if status["runtime_installed"]:
        print(f"  Engine:   llama.cpp {status['tag']} ({status['runtime_backend']})"
              + ("  (update available)" if status.get("update_available") else ""))
    else:
        print(f"  Engine:   not installed; llama.cpp {status['configured_tag']} installs with your first model")
    print()

    rows = lm.local_models_catalog()["models"]
    choices = [r for r in rows if r.get("fits") and not r.get("needs_engine")]
    catalog_ids = {r.get("downloaded_model_id") for r in rows}
    staged = [m for m in status["models"] if m["id"] not in catalog_ids]
    active_id = status.get("active_model_id")
    labels = [_row_label(r, active_id) for r in choices]
    labels += [f"  {m['id']} — {m['size_label']} · on disk" + ("  ← currently active" if m["id"] == active_id else "")
               for m in staged]
    labels.append("Leave unchanged")
    skipped = len(rows) - len(choices)
    if skipped:
        print(f"  {skipped} catalog model(s) need more memory or a newer engine than this machine has.")
    default = next((i for i, r in enumerate(choices)
                    if active_id and active_id in (r.get("downloaded_model_id"), r.get("model_id"))),
                   next((i for i, r in enumerate(choices) if r.get("recommended")), 0))
    idx = _prompt_provider_choice(labels, default=default, title="Select a local model (★ recommended for this machine):")
    if idx is None or idx == len(labels) - 1:
        print("No change.")
        return

    from hermes_cli.local_model_client import LocalModelClientError

    try:
        client = _connect_local_models()
        if client is None:
            print('No change.')
            return
        gateway_status = client.get('status')
    except LocalModelClientError as exc:
        print(f'  ✗ {exc}')
        return
    can_control = gateway_status.get('control_available', False)
    if not can_control and not gateway_status.get('server_running'):
        print('  Start a long-lived main gateway from the default profile before setup; no downloads started.')
        return

    if idx < len(choices):
        row = choices[idx]
        name, use_downloaded = row["display_name"], row.get("downloaded") and status["runtime_installed"]
        if not use_downloaded:
            if not can_control:
                print('  Install new models from the default-profile gateway; no downloads started.')
                return
            need = [] if status["runtime_installed"] else [f"the llama.cpp engine ({status['configured_tag']})"]
            if not row.get("downloaded"):
                need.append(f"{name} ({row['size_label']})")
            if need and not prompt_yes_no(f"  Download {' and '.join(need)}?", True):
                print("No change.")
                return
            job_id = _start(client, "quickstart", {"model_id": row["id"]})
            if job_id is None or _follow_job(lm, client, job_id) != "done":
                return
            print(f"Default model set to: {name} (via Local models · llama.cpp)")
            return
        model_id = row["downloaded_model_id"]
    else:
        model_id = staged[idx - len(choices)]["id"]
        name = model_id

    if not status["runtime_installed"]:
        if not can_control:
            print('  Install the engine from the default-profile gateway; no downloads started.')
            return
        if not prompt_yes_no(f"  Download the llama.cpp engine ({status['configured_tag']})?", True):
            print("No change.")
            return
        job_id = _start(client, "runtime/install", {})
        if job_id is None or _follow_job(lm, client, job_id) != "done":
            return
    job_id = _start(client, "activate", {"model_id": model_id})
    if job_id is not None and _follow_job(lm, client, job_id) == "done":
        print(f"Default model set to: {name} (via Local models · llama.cpp)")
