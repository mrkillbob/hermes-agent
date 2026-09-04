# HRL-0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and certify the isolated, secret-safe HRL-0 environment inventory and temporary Hermes Desktop connection without model inference or TradingBotV18 mutation.

**Architecture:** A Python 3.11 standard-library package runs allowlisted read-only commands, parses only approved fields, classifies missing evidence explicitly, and atomically publishes JSON/Markdown artifacts after redaction validation. A macOS Seatbelt policy and an isolated Hermes wrapper constrain all writes to `/Users/mikedemott/HermesRevenueLab`; a stable lab-only dashboard token lets Hermes Desktop connect to a temporary loopback backend on port 9120.

**Tech Stack:** Python 3.11 standard library, `unittest`, SQLite-free JSON artifacts, zsh, macOS `sandbox-exec`, Hermes Agent v0.20.4, Ollama CLI, Git.

**Spec:** `docs/superpowers/specs/2026-08-20-hermes-revenue-lab-foundation-design.md`

## Global Constraints

- All writes remain under `/Users/mikedemott/HermesRevenueLab`.
- Do not edit, stage, commit, signal, restart, or otherwise mutate TradingBotV18 or Luna.
- HRL-0 makes no model calls, installs no models or dependencies, creates no cron jobs, publishes nothing, spends nothing, and contacts no customer.
- Existing `~/.hermes` configuration, profiles, auth, sessions, memory, and 12-job cron database remain unchanged.
- Unknown numeric values remain JSON `null`; they never become zero.
- Persist no `.env` contents, tokens, cookies, account identifiers, hardware serials, hardware UUIDs, or full process command lines.
- The only Desktop backend address is `127.0.0.1:9120`; an occupied port is a fail-closed error.
- The temporary HRL-0 backend is stopped after the Desktop smoke check. Persistent unattended startup is deferred to HRL-4.
- Use `/Users/mikedemott/.local/bin/python3.11`; do not install packages into Hermes, TradingBotV18, or the system interpreter.

---

## File Map

- `pyproject.toml`: package metadata and Python floor; no third-party dependencies.
- `.gitignore`: excludes the venv, isolated Hermes state, temporary files, caches, and generated run directories while retaining canonical HRL-0 artifacts.
- `src/hermes_revenue_lab/inventory/types.py`: immutable command and observation records.
- `src/hermes_revenue_lab/inventory/redaction.py`: publication allowlist and sensitive-output rejection.
- `src/hermes_revenue_lab/inventory/runner.py`: bounded argv-only subprocess execution.
- `src/hermes_revenue_lab/inventory/parsers.py`: pure parsers for Hermes, Ollama, macOS, scheduler, and resource output.
- `src/hermes_revenue_lab/inventory/classification.py`: process categories and busy/idle verdicts.
- `src/hermes_revenue_lab/inventory/collector.py`: authoritative inventory orchestration.
- `src/hermes_revenue_lab/inventory/render.py`: canonical JSON and Markdown projections.
- `src/hermes_revenue_lab/inventory/publish.py`: staged validation, atomic replacement, and SHA-256 manifest.
- `scripts/collect_environment_inventory.py`: HRL-0 CLI entry point.
- `config/revenue_lab.sb`: macOS process sandbox policy.
- `scripts/verify_isolation.py`: non-mutating TradingBot write-open denial proof.
- `scripts/init_lab_runtime.py`: create the lab-only stable Desktop token with mode `0600`.
- `scripts/hermes-revenue-lab`: run the isolated loopback backend through the sandbox.
- `scripts/desktop_smoke.py`: verify port, health, stable token use, and write the Desktop verdict.
- `tests/`: standard-library unit and integration tests.
- `docs/runbooks/hrl-0.md`: exact inventory, Desktop, shutdown, and evidence commands.

### Task 1: Repository Skeleton and Evidence Types

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `src/hermes_revenue_lab/__init__.py`
- Create: `src/hermes_revenue_lab/inventory/__init__.py`
- Create: `src/hermes_revenue_lab/inventory/types.py`
- Test: `tests/test_types.py`

**Interfaces:**
- Consumes: Python 3.11 standard library only.
- Produces: `ObservationStatus`, `CommandSpec`, `CommandResult`, `Observation`, and `InventoryContext` for every later task.

- [ ] **Step 1: Create the isolated Python 3.11 venv**

Run:

```bash
/Users/mikedemott/.local/bin/python3.11 -m venv .venv
.venv/bin/python --version
```

Expected: `Python 3.11.16`. Do not install packages.

- [ ] **Step 2: Write the failing evidence-type test**

```python
from pathlib import Path
import unittest

from hermes_revenue_lab.inventory.types import Observation, InventoryContext


class EvidenceTypesTest(unittest.TestCase):
    def test_unknown_numeric_value_stays_none(self) -> None:
        observation = Observation.unavailable("swap_bytes", "command blocked")
        self.assertIsNone(observation.value)
        self.assertEqual("unavailable", observation.status)

    def test_context_rejects_workspace_inside_tradingbot(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside TradingBotV18"):
            InventoryContext(
                workspace=Path("/Users/mikedemott/TradingBotV18/HermesRevenueLab"),
                hermes_home=Path("/Users/mikedemott/TradingBotV18/HermesRevenueLab/.hermes"),
                tradingbot_path=Path("/Users/mikedemott/TradingBotV18"),
            )
```

- [ ] **Step 3: Run the test and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_types -v`

Expected: import failure because `types.py` does not exist.

- [ ] **Step 4: Implement the minimal immutable types**

Create the package metadata and ignore rules exactly as follows before adding the types:

```toml
[project]
name = "hermes-revenue-lab"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []
```

```gitignore
.venv/
.hermes/
.cache/
tmp/
__pycache__/
*.py[cod]
artifacts/bootstrap/runs/
artifacts/bootstrap/.*.tmp
```

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ObservationStatus = Literal["available", "unavailable", "blocked", "not_observed"]


@dataclass(frozen=True)
class Observation:
    name: str
    status: ObservationStatus
    value: Any
    reason: str | None = None

    @classmethod
    def unavailable(cls, name: str, reason: str) -> "Observation":
        return cls(name=name, status="unavailable", value=None, reason=reason)


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    timeout_seconds: float = 10.0
    required: bool = False


@dataclass(frozen=True)
class CommandResult:
    name: str
    status: ObservationStatus
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class InventoryContext:
    workspace: Path
    hermes_home: Path
    tradingbot_path: Path

    def __post_init__(self) -> None:
        workspace = self.workspace.resolve()
        tradingbot = self.tradingbot_path.resolve()
        if workspace == tradingbot or tradingbot in workspace.parents:
            raise ValueError("Revenue Lab workspace must be outside TradingBotV18")
        if self.hermes_home.resolve() != workspace / ".hermes":
            raise ValueError("Hermes home must be <workspace>/.hermes")
```

- [ ] **Step 5: Run the test and verify GREEN**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_types -v`

Expected: 2 tests pass.

- [ ] **Step 6: Commit the skeleton**

```bash
git add pyproject.toml .gitignore README.md src tests/test_types.py
git commit -m "feat: establish HRL evidence types"
```

### Task 2: Redaction Gate and Bounded Command Runner

**Files:**
- Create: `src/hermes_revenue_lab/inventory/redaction.py`
- Create: `src/hermes_revenue_lab/inventory/runner.py`
- Test: `tests/test_redaction.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `CommandSpec` and `CommandResult` from Task 1.
- Produces: `assert_publication_safe(value: object) -> None`, `sanitize_diagnostic(text: str) -> str`, and `run_command(spec: CommandSpec, env: Mapping[str, str] | None = None) -> CommandResult`.

- [ ] **Step 1: Write failing redaction tests**

```python
import unittest

from hermes_revenue_lab.inventory.redaction import PublicationSafetyError, assert_publication_safe


class RedactionTest(unittest.TestCase):
    def test_rejects_secret_value(self) -> None:
        with self.assertRaises(PublicationSafetyError):
            assert_publication_safe({"api_key": "secret-value"})

    def test_rejects_hardware_uuid(self) -> None:
        with self.assertRaises(PublicationSafetyError):
            assert_publication_safe({"note": "Hardware UUID: ABCD"})

    def test_accepts_allowlisted_inventory(self) -> None:
        assert_publication_safe({"ollama": {"version": "0.32.14"}})
```

- [ ] **Step 2: Run redaction tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_redaction -v`

Expected: import failure for `redaction.py`.

- [ ] **Step 3: Implement recursive publication safety**

Implement `PublicationSafetyError`, reject case-insensitive keys matching `token`, `secret`, `password`, `api_key`, `cookie`, `authorization`, `serial_number`, `hardware_uuid`, and `provisioning_udid`, and reject strings containing those labeled fields. Do not replace a discovered secret with a placeholder and publish it; reject the candidate artifact.

```python
SENSITIVE_KEYS = frozenset({
    "token", "secret", "password", "api_key", "cookie", "authorization",
    "serial_number", "hardware_uuid", "provisioning_udid",
})
SENSITIVE_LABEL = re.compile(
    r"(?i)(api[_ -]?key|authorization|cookie|hardware uuid|provisioning udid|serial number)\s*[:=]"
)


def assert_publication_safe(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in SENSITIVE_KEYS:
                raise PublicationSafetyError(f"sensitive key: {normalized}")
            assert_publication_safe(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            assert_publication_safe(child)
    elif isinstance(value, str) and SENSITIVE_LABEL.search(value):
        raise PublicationSafetyError("sensitive labeled value")
```

- [ ] **Step 4: Run redaction tests and verify GREEN**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_redaction -v`

Expected: 3 tests pass.

- [ ] **Step 5: Write failing runner tests**

```python
import unittest

from hermes_revenue_lab.inventory.runner import run_command
from hermes_revenue_lab.inventory.types import CommandSpec


class RunnerTest(unittest.TestCase):
    def test_timeout_is_unavailable_and_has_no_fake_exit_code(self) -> None:
        result = run_command(CommandSpec("timeout", ("/bin/sleep", "1"), 0.01))
        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.exit_code)

    def test_argv_execution_does_not_use_a_shell(self) -> None:
        result = run_command(CommandSpec("literal", ("/bin/echo", "$(id)")))
        self.assertEqual("$(id)", result.stdout.strip())
```

- [ ] **Step 6: Run runner tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_runner -v`

Expected: import failure for `runner.py`.

- [ ] **Step 7: Implement the argv-only bounded runner**

Use `subprocess.run(list(spec.argv), shell=False, text=True, capture_output=True, timeout=spec.timeout_seconds, env=effective_env)`. Map exit code 0 to `available`, timeout or missing executable to `unavailable`, and permission errors to `blocked`. Truncate in-memory diagnostics to 16 KiB before sanitization.

```python
def run_command(spec: CommandSpec, env: Mapping[str, str] | None = None) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(spec.argv), shell=False, text=True, capture_output=True,
            timeout=spec.timeout_seconds, env=None if env is None else dict(env), check=False,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(spec.name, "unavailable", None, "", "timeout", time.monotonic() - started)
    except PermissionError:
        return CommandResult(spec.name, "blocked", None, "", "permission denied", time.monotonic() - started)
    except FileNotFoundError:
        return CommandResult(spec.name, "unavailable", None, "", "not installed", time.monotonic() - started)
    status = "available" if completed.returncode == 0 else "unavailable"
    return CommandResult(
        spec.name, status, completed.returncode,
        sanitize_diagnostic(completed.stdout[:16384]),
        sanitize_diagnostic(completed.stderr[:16384]),
        time.monotonic() - started,
    )
```

- [ ] **Step 8: Run runner tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_redaction tests.test_runner -v`

Expected: all tests pass.

```bash
git add src/hermes_revenue_lab/inventory/redaction.py src/hermes_revenue_lab/inventory/runner.py tests/test_redaction.py tests/test_runner.py
git commit -m "feat: add secret-safe command execution"
```

### Task 3: Pure Inventory Parsers

**Files:**
- Create: `src/hermes_revenue_lab/inventory/parsers.py`
- Test: `tests/test_parsers.py`

**Interfaces:**
- Consumes: sanitized command stdout strings.
- Produces: `parse_hermes_version`, `parse_hermes_tools`, `parse_hermes_profiles`, `parse_hermes_cron`, `parse_ollama_list`, `parse_ollama_show`, `parse_ollama_ps`, `parse_hardware`, `parse_df`, `parse_vm_stat`, and `parse_process_table`.

- [ ] **Step 1: Write failing representative parser tests**

```python
import unittest

from hermes_revenue_lab.inventory.parsers import parse_hardware, parse_ollama_list, parse_process_table


class ParserTest(unittest.TestCase):
    def test_hardware_parser_excludes_identifiers(self) -> None:
        parsed = parse_hardware("""Model Name: Mac mini\nChip: Apple M4 Pro\nMemory: 64 GB\nSerial Number (system): REDACTME\nHardware UUID: REDACTME\n""")
        self.assertEqual("Mac mini", parsed["model_name"])
        self.assertNotIn("serial_number", parsed)
        self.assertNotIn("hardware_uuid", parsed)

    def test_ollama_list_preserves_size_text_and_digest(self) -> None:
        rows = parse_ollama_list("NAME ID SIZE MODIFIED\nqwen3.5:4b abc123 3.4 GB 5 days ago\n")
        self.assertEqual("abc123", rows[0]["digest"])
        self.assertEqual("3.4 GB", rows[0]["size"])

    def test_process_parser_returns_aggregates_not_commands(self) -> None:
        parsed = parse_process_table("1 0 2.0 0.1 1024 python3 /x/TradingBotV18/main.py\n")
        self.assertEqual(1, parsed["luna"]["count"])
        self.assertNotIn("command", parsed["luna"])
```

- [ ] **Step 2: Run parser tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_parsers -v`

Expected: import failure for `parsers.py`.

- [ ] **Step 3: Implement allowlisted parsers**

Use line-oriented parsing with explicit field names. Ignore unrecognized lines. `parse_process_table` categorizes rows in this order: `revenue_lab`, `luna`, `ollama`, `hermes`, `other`; it returns only count, aggregate CPU percent, and aggregate RSS bytes per retained category.

```python
HARDWARE_FIELDS = {
    "Model Name": "model_name", "Model Identifier": "model_identifier",
    "Chip": "chip", "Total Number of Cores": "core_summary", "Memory": "memory",
}


def parse_hardware(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        label, separator, value = line.strip().partition(":")
        if separator and label in HARDWARE_FIELDS:
            result[HARDWARE_FIELDS[label]] = value.strip()
    return result


def parse_process_table(text: str) -> dict[str, dict[str, float | int]]:
    process_markers = {
        "revenue_lab": ("/hermesrevenuelab/",),
        "luna": ("/tradingbotv18/", "/lunabot-default/", "live_runner"),
        "ollama": ("ollama serve", "ollama runner"),
        "hermes": ("/hermes.app/", "/.hermes/hermes-agent/"),
    }
    totals = {name: {"count": 0, "cpu_percent": 0.0, "rss_bytes": 0}
              for name in ("revenue_lab", "luna", "ollama", "hermes")}
    for line in text.splitlines():
        fields = line.split(maxsplit=6)
        if len(fields) != 7 or not fields[0].isdigit():
            continue
        command = fields[6].lower()
        category = next((name for name, markers in process_markers.items()
                         if any(marker in command for marker in markers)), None)
        if category is None:
            continue
        totals[category]["count"] += 1
        totals[category]["cpu_percent"] += float(fields[2])
        totals[category]["rss_bytes"] += int(fields[4]) * 1024
    return totals
```

- [ ] **Step 4: Add fixtures for installed and loaded Ollama models plus Hermes cron metadata**

Add tests proving quantization/capabilities parsing, loaded-model GPU/RAM parsing, and that cron prompt bodies are never represented in parsed output.

- [ ] **Step 5: Run parser tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_parsers -v`

Expected: all parser tests pass.

```bash
git add src/hermes_revenue_lab/inventory/parsers.py tests/test_parsers.py
git commit -m "feat: parse allowlisted HRL inventory evidence"
```

### Task 4: Busy/Idle Classification and Inventory Orchestration

**Files:**
- Create: `src/hermes_revenue_lab/inventory/classification.py`
- Create: `src/hermes_revenue_lab/inventory/collector.py`
- Test: `tests/test_classification.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `InventoryContext`, command runner, and pure parsers.
- Produces: `classify_resource_state(samples: Sequence[Mapping[str, object]]) -> dict[str, object]`, `collect_resource_samples(runner: Runner, count: int, interval: float) -> list[dict[str, object]]`, `build_inventory_document(context: InventoryContext, results: Mapping[str, CommandResult], model_rows: Sequence[Mapping[str, str]], model_details: Sequence[str], samples: Sequence[Mapping[str, object]]) -> dict[str, object]`, and `collect_inventory(context: InventoryContext, runner: Runner = run_command, sample_interval: float = 1.0) -> dict[str, object]`. `Runner` is `Callable[[CommandSpec], CommandResult]`.

- [ ] **Step 1: Write failing classification tests**

```python
import unittest

from hermes_revenue_lab.inventory.classification import classify_resource_state


class ClassificationTest(unittest.TestCase):
    def test_loaded_model_forces_busy(self) -> None:
        verdict = classify_resource_state([{"luna_count": 0, "loaded_models": 1, "load_1m": 0.5, "memory_free_percent": 50.0}])
        self.assertEqual("observed_busy", verdict["classification"])

    def test_missing_quiet_window_keeps_idle_unavailable(self) -> None:
        verdict = classify_resource_state([{"luna_count": 0, "loaded_models": 0, "load_1m": None, "memory_free_percent": 50.0}])
        self.assertEqual("unavailable", verdict["idle_baseline"]["status"])
```

- [ ] **Step 2: Run classification tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_classification -v`

Expected: import failure for `classification.py`.

- [ ] **Step 3: Implement conservative classification**

Idle requires at least three samples, zero Luna processes, zero loaded models, zero Revenue Lab workers other than the collector, one-minute load below 3.0, and free-memory percentage at least 35 in every sample. Any active Luna or loaded model yields `observed_busy`. Missing required fields yields an unavailable idle baseline.

```python
def classify_resource_state(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if any(int(sample.get("luna_count") or 0) > 0 for sample in samples):
        return {"classification": "observed_busy", "reasons": ["luna_active"],
                "idle_baseline": {"status": "unavailable", "value": None}}
    if any(int(sample.get("loaded_models") or 0) > 0 for sample in samples):
        return {"classification": "observed_busy", "reasons": ["ollama_model_loaded"],
                "idle_baseline": {"status": "unavailable", "value": None}}
    required = ("load_1m", "memory_free_percent", "revenue_lab_workers")
    if len(samples) < 3 or any(sample.get(key) is None for sample in samples for key in required):
        return {"classification": "not_observed", "reasons": ["quiet_window_unavailable"],
                "idle_baseline": {"status": "unavailable", "value": None}}
    quiet = all(float(sample["load_1m"]) < 3.0
                and float(sample["memory_free_percent"]) >= 35.0
                and int(sample["revenue_lab_workers"]) == 0 for sample in samples)
    return {"classification": "observed_idle" if quiet else "observed_busy",
            "reasons": [] if quiet else ["resource_threshold"],
            "idle_baseline": {"status": "available" if quiet else "unavailable",
                              "value": list(samples) if quiet else None}}
```

- [ ] **Step 4: Write a failing collector test with a fake runner**

Assert all required top-level keys from the spec exist, unavailable commands become explicit observations, and the collector never passes `ollama run`, `hermes -z`, or any model-serving inference endpoint to its runner.

- [ ] **Step 5: Run collector test and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_collector -v`

Expected: import failure for `collector.py`.

- [ ] **Step 6: Implement `collect_inventory`**

Use an explicit tuple of `CommandSpec` values for Hermes metadata, Ollama metadata, hardware, disk, VM, process, user crontab, launchd labels, and browser automation. Add per-model `ollama show -v` only for names returned by `ollama list`. Sample resources three times with a configurable zero-second interval in tests and a one-second interval in the live CLI.

```python
BASE_COMMANDS = (
    CommandSpec("hermes_version", ("/Users/mikedemott/.local/bin/hermes", "version"), required=True),
    CommandSpec("hermes_tools", ("/Users/mikedemott/.local/bin/hermes", "tools", "list")),
    CommandSpec("hermes_profiles", ("/Users/mikedemott/.local/bin/hermes", "profile", "list")),
    CommandSpec("hermes_cron", ("/Users/mikedemott/.local/bin/hermes", "cron", "list")),
    CommandSpec("ollama_version", ("/usr/local/bin/ollama", "--version"), required=True),
    CommandSpec("ollama_list", ("/usr/local/bin/ollama", "list"), required=True),
    CommandSpec("ollama_ps", ("/usr/local/bin/ollama", "ps")),
    CommandSpec("hardware", ("/usr/sbin/system_profiler", "SPHardwareDataType"), required=True),
    CommandSpec("storage", ("/bin/df", "-k", "/Users/mikedemott"), required=True),
    CommandSpec("vm_stat", ("/usr/bin/vm_stat",)),
    CommandSpec("processes", ("/bin/ps", "-axo", "pid,ppid,%cpu,%mem,rss,etime,command")),
    CommandSpec("crontab", ("/usr/bin/crontab", "-l")),
    CommandSpec("launchd", ("/bin/launchctl", "list")),
)


def collect_inventory(context: InventoryContext, runner: Runner = run_command,
                      sample_interval: float = 1.0) -> dict[str, object]:
    results = {spec.name: runner(spec) for spec in BASE_COMMANDS}
    model_rows = parse_ollama_list(results["ollama_list"].stdout)
    model_details = [runner(CommandSpec(f"ollama_show_{index}",
                       ("/usr/local/bin/ollama", "show", "-v", row["name"]))).stdout
                     for index, row in enumerate(model_rows)]
    samples = collect_resource_samples(runner, count=3, interval=sample_interval)
    return build_inventory_document(context, results, model_rows, model_details, samples)
```

- [ ] **Step 7: Run tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_classification tests.test_collector -v`

Expected: all tests pass.

```bash
git add src/hermes_revenue_lab/inventory/classification.py src/hermes_revenue_lab/inventory/collector.py tests/test_classification.py tests/test_collector.py
git commit -m "feat: collect and classify HRL environment evidence"
```

### Task 5: Canonical Rendering and Atomic Publication

**Files:**
- Create: `src/hermes_revenue_lab/inventory/render.py`
- Create: `src/hermes_revenue_lab/inventory/publish.py`
- Create: `scripts/collect_environment_inventory.py`
- Test: `tests/test_publish.py`

**Interfaces:**
- Consumes: inventory dictionary from Task 4 and `assert_publication_safe` from Task 2.
- Produces: `render_json(inventory: Mapping[str, object]) -> str`, `render_markdown(inventory: Mapping[str, object]) -> str`, `write_run_candidates(run_dir: Path, inventory: Mapping[str, object], json_text: str, markdown_text: str) -> dict[str, Path]`, `validate_candidate_set(paths: Mapping[str, Path]) -> None`, `promote_canonical(paths: Mapping[str, Path], artifact_root: Path) -> None`, `publish_inventory(inventory: Mapping[str, object], artifact_root: Path) -> Mapping[str, Path]`, and the live collector CLI.

- [ ] **Step 1: Write failing publication tests**

Test that JSON/Markdown contain the same `inventory_id` and classification, secret rejection leaves previous canonical files unchanged, unknown values render as `null`/`unavailable`, a failed run remains under `artifacts/bootstrap/runs/<inventory-id>/`, and checksum verification covers the five required canonical files.

- [ ] **Step 2: Run publication tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_publish -v`

Expected: import failure for `publish.py` or `render.py`.

- [ ] **Step 3: Implement deterministic rendering**

Serialize JSON with `sort_keys=True`, `indent=2`, and a trailing newline. Render Markdown from the same dictionary without independent calculations. Include unknowns, warnings, source-command statuses, and the resource classification.

```python
def render_json(inventory: Mapping[str, object]) -> str:
    return json.dumps(inventory, sort_keys=True, indent=2) + "\n"


def render_markdown(inventory: Mapping[str, object]) -> str:
    return "\n".join((
        "# Hermes Revenue Lab Environment Inventory",
        "",
        f"- Inventory ID: `{inventory['inventory_id']}`",
        f"- Classification: `{inventory['classification']}`",
        f"- Collected at: `{inventory['collected_at']}`",
        "",
        "## Unknowns",
        *[f"- {item}" for item in inventory.get("unknowns", [])],
        "",
        "## Warnings",
        *[f"- {item}" for item in inventory.get("warnings", [])],
        "",
    ))
```

- [ ] **Step 4: Implement atomic publication**

Write candidate files inside the run directory, call `assert_publication_safe`, re-read and validate JSON, calculate SHA-256, then use `os.replace` from same-filesystem temporary files into canonical paths. Set generated artifact permissions to `0644` and the run directory to `0700`.

```python
def atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def publish_inventory(inventory: Mapping[str, object], artifact_root: Path) -> Mapping[str, Path]:
    assert_publication_safe(inventory)
    run_dir = artifact_root / "runs" / str(inventory["inventory_id"])
    run_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    json_text = render_json(inventory)
    markdown_text = render_markdown(inventory)
    json.loads(json_text)
    paths = write_run_candidates(run_dir, inventory, json_text, markdown_text)
    validate_candidate_set(paths)
    promote_canonical(paths, artifact_root)
    return paths
```

- [ ] **Step 5: Implement the CLI**

The CLI constructs the exact `InventoryContext`, runs the collector, publishes the artifacts, prints only artifact paths/classification, and returns nonzero for a required blocked section or failed publication.

```python
def main() -> int:
    context = InventoryContext(
        workspace=Path("/Users/mikedemott/HermesRevenueLab"),
        hermes_home=Path("/Users/mikedemott/HermesRevenueLab/.hermes"),
        tradingbot_path=Path("/Users/mikedemott/TradingBotV18"),
    )
    inventory = collect_inventory(context)
    paths = publish_inventory(inventory, context.workspace / "artifacts" / "bootstrap")
    print(f"classification={inventory['classification']}")
    for name, path in sorted(paths.items()):
        print(f"{name}={path}")
    return 0 if not inventory.get("required_sections_blocked") else 2
```

- [ ] **Step 6: Run tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_publish -v`

Expected: all tests pass.

```bash
git add src/hermes_revenue_lab/inventory/render.py src/hermes_revenue_lab/inventory/publish.py scripts/collect_environment_inventory.py tests/test_publish.py
git commit -m "feat: publish atomic secret-safe HRL inventory"
```

### Task 6: Process Sandbox and Non-Mutating Isolation Proof

**Files:**
- Create: `config/revenue_lab.sb`
- Create: `scripts/verify_isolation.py`
- Test: `tests/test_isolation.py`

**Interfaces:**
- Consumes: exact workspace and TradingBot paths.
- Produces: `verify_isolation(workspace: Path, tradingbot: Path, probe_file: Path) -> dict[str, object]` and an isolation verdict suitable for the inventory.

- [ ] **Step 1: Write failing isolation tests against temporary decoy roots**

Create a temp lab and temp outside directory. Generate the same policy with those paths, prove a sandboxed child can create a sentinel inside the lab, and prove `os.open(outside_file, os.O_WRONLY)` is denied. Assert the outside file SHA-256 is unchanged.

- [ ] **Step 2: Run isolation tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_isolation -v`

Expected: import failure for `verify_isolation.py`.

- [ ] **Step 3: Implement the sandbox policy and verifier**

The policy allows normal reads and process execution, denies all filesystem writes by default, and allows writes only under the lab root. The verifier computes the probe hash and `git status --porcelain=v1` before and after, attempts only a write-only non-truncating open, writes no bytes, and raises if the open succeeds or either before/after value differs.

```scheme
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "/Users/mikedemott/HermesRevenueLab"))
(deny file-write* (subpath "/Users/mikedemott/TradingBotV18"))
```

```python
def attempt_write_open(probe_file: Path, policy: Path) -> subprocess.CompletedProcess[str]:
    code = "import os,sys; fd=os.open(sys.argv[1], os.O_WRONLY); os.close(fd)"
    return subprocess.run(
        ("/usr/bin/sandbox-exec", "-f", str(policy), sys.executable, "-c", code, str(probe_file)),
        text=True, capture_output=True, check=False,
    )


def verify_isolation(workspace: Path, tradingbot: Path, probe_file: Path) -> dict[str, object]:
    before_hash = sha256_file(probe_file)
    before_status = git_status_hash(tradingbot)
    result = attempt_write_open(probe_file, workspace / "config" / "revenue_lab.sb")
    after_hash = sha256_file(probe_file)
    after_status = git_status_hash(tradingbot)
    if result.returncode == 0 or before_hash != after_hash or before_status != after_status:
        raise IsolationError("Revenue Lab write isolation failed")
    return {"status": "available", "tradingbot_write_denied": True,
            "probe_hash_unchanged": True, "git_status_unchanged": True}
```

- [ ] **Step 4: Run temporary-root integration tests**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_isolation -v`

Expected: inside write succeeds, outside write-open is denied, outside hash unchanged.

- [ ] **Step 5: Run the real non-mutating TradingBot isolation probe**

Use `/Users/mikedemott/TradingBotV18/README.md` as the existing probe file. Capture the TradingBot hash and Git status before/after. Do not stage or modify anything there.

- [ ] **Step 6: Commit isolation controls**

```bash
git add config/revenue_lab.sb scripts/verify_isolation.py tests/test_isolation.py
git commit -m "feat: enforce Revenue Lab write isolation"
```

### Task 7: Stable Loopback Hermes Runtime and Desktop Smoke

**Files:**
- Create: `scripts/init_lab_runtime.py`
- Create: `scripts/hermes-revenue-lab`
- Create: `scripts/desktop_smoke.py`
- Test: `tests/test_runtime_scripts.py`

**Interfaces:**
- Consumes: sandbox policy from Task 6 and custom Hermes home.
- Produces: mode-0600 `.hermes/.env` containing only the stable random `HERMES_DASHBOARD_SESSION_TOKEN`, a sandboxed `serve` wrapper, and `artifacts/bootstrap/desktop_connection_verdict.json`.

- [ ] **Step 1: Write failing token-initialization tests**

Assert first run creates a token of at least 32 random bytes encoded URL-safely, file mode is `0600`, second run preserves the same token, and neither stdout nor returned metadata contains the token.

- [ ] **Step 2: Run runtime tests and verify RED**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_runtime_scripts -v`

Expected: import or file failure because runtime scripts do not exist.

- [ ] **Step 3: Implement runtime initialization and wrapper**

The wrapper exports exact `HERMES_HOME`, `HERMES_WRITE_SAFE_ROOT`, `TMPDIR`, and cache paths under the lab; loads the stable token without printing it; refuses a non-loopback host or non-9120 port; verifies the port is free; and executes `/Users/mikedemott/.local/bin/hermes serve --host 127.0.0.1 --port 9120 --skip-build` through `sandbox-exec`.

```python
def initialize_runtime(hermes_home: Path) -> dict[str, object]:
    env_path = hermes_home / ".env"
    hermes_home.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not env_path.exists():
        token = secrets.token_urlsafe(32)
        env_path.write_text(f"HERMES_DASHBOARD_SESSION_TOKEN={token}\n", encoding="utf-8")
        env_path.chmod(0o600)
    return {"status": "available", "env_path": str(env_path), "token_persisted": True}
```

```bash
#!/bin/zsh
set -euo pipefail
LAB_ROOT=/Users/mikedemott/HermesRevenueLab
export HERMES_HOME="$LAB_ROOT/.hermes"
export HERMES_WRITE_SAFE_ROOT="$LAB_ROOT"
export TMPDIR="$LAB_ROOT/tmp"
export XDG_CACHE_HOME="$LAB_ROOT/.cache"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
set -a
. "$HERMES_HOME/.env"
set +a
exec /usr/bin/sandbox-exec -f "$LAB_ROOT/config/revenue_lab.sb" \
  /Users/mikedemott/.local/bin/hermes serve --host 127.0.0.1 --port 9120 --skip-build
```

- [ ] **Step 4: Implement the Desktop smoke script**

Poll `http://127.0.0.1:9120/api/status` for at most 30 seconds, require loopback identity and successful HTTP status, verify the configured session token authenticates a protected no-write endpoint, write a secret-free verdict, and never call chat/model endpoints.

```python
def wait_for_status(endpoint: str, token: str, timeout_seconds: float = 30.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    request = urllib.request.Request(
        f"{endpoint}/api/status", headers={"Authorization": f"Bearer {token}"}
    )
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                body = json.load(response)
                return {"status": "available", "http_status": response.status,
                        "endpoint": endpoint, "gateway_name": "Hermes Revenue Lab",
                        "auth_required": bool(body.get("auth_required", False))}
        except (OSError, urllib.error.URLError):
            time.sleep(0.25)
    raise RuntimeError("Hermes Revenue Lab did not become healthy within 30 seconds")
```

- [ ] **Step 5: Run tests and commit**

Run: `PYTHONPATH=src .venv/bin/python -m unittest tests.test_runtime_scripts -v`

Expected: all tests pass.

```bash
git add scripts/init_lab_runtime.py scripts/hermes-revenue-lab scripts/desktop_smoke.py tests/test_runtime_scripts.py
git commit -m "feat: add isolated Hermes Desktop runtime"
```

### Task 8: Live HRL-0 Certification and Runbook

**Files:**
- Create: `docs/runbooks/hrl-0.md`
- Generate: `artifacts/bootstrap/environment_inventory.json`
- Generate: `artifacts/bootstrap/environment_inventory.md`
- Generate: `artifacts/bootstrap/command_manifest.json`
- Generate: `artifacts/bootstrap/inventory_checksums.sha256`
- Generate: `artifacts/bootstrap/desktop_connection_verdict.json`
- Modify: `README.md`

**Interfaces:**
- Consumes: every prior task.
- Produces: terminal HRL-0 classification and exact operator commands.

- [ ] **Step 1: Run the complete unit suite**

Run: `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`

Expected: all tests pass with no warnings or network/model calls.

- [ ] **Step 2: Capture TradingBot preflight evidence**

Record read-only HEAD, branch, and `git status --porcelain=v1` hashes for `/Users/mikedemott/TradingBotV18`. Do not store filenames from dirty status in public artifacts; store only the status hash and unchanged verdict.

- [ ] **Step 3: Run the live inventory outside the Codex process sandbox**

Run:

```bash
PYTHONPATH=src .venv/bin/python scripts/collect_environment_inventory.py
```

Expected: the five canonical artifact paths and a truthful classification. A busy machine remains `observed_busy`; unavailable evidence remains explicit.

- [ ] **Step 4: Verify canonical artifacts**

Run JSON parsing, checksum verification, redaction scan, JSON/Markdown `inventory_id` comparison, and file permission checks. Any failure classifies the run as `logic-regression` and prevents HRL-0 completion.

- [ ] **Step 5: Initialize and temporarily start the isolated backend**

Run `scripts/init_lab_runtime.py`, start `scripts/hermes-revenue-lab` in a bounded background session, and run `scripts/desktop_smoke.py`. Confirm no Ollama model is loaded as a result.

- [ ] **Step 6: Register and verify Hermes Desktop with Computer Use**

Use the `computer-use:computer-use` skill to control Hermes Desktop. Add the remote gateway named `Hermes Revenue Lab` with URL `http://127.0.0.1:9120` and enter the stable session token directly from the mode-0600 lab `.env` into the Desktop credential field. Use the app's **Test** action and record only success/failure, timestamp, endpoint, and gateway name in the verdict artifact. Never paste or log the token into chat, terminal output, clipboard history, or artifacts.

- [ ] **Step 7: Stop the temporary backend and prove shutdown**

Stop only the process launched by `scripts/hermes-revenue-lab`, confirm port 9120 no longer listens, confirm existing Hermes/Luna processes were not signaled, and retain the Desktop connection as unavailable/offline until HRL-4.

- [ ] **Step 8: Write the runbook and final acceptance classification**

Document inventory regeneration, secret-safety rules, Desktop start/test/stop, isolation proof, artifact verification, and the exact reason for any unavailable idle baseline. Classify HRL-0 as `infrastructure-valid`, `diagnostic-only`, `environment-blocked`, or `logic-regression` without weakening gates.

- [ ] **Step 9: Commit the certified HRL-0 result**

```bash
git add README.md docs/runbooks/hrl-0.md artifacts/bootstrap src scripts config tests pyproject.toml .gitignore
git commit -m "feat: certify HRL-0 environment inventory"
```

Do not stage `.hermes/`, `.venv/`, caches, temporary process files, or credentials.
