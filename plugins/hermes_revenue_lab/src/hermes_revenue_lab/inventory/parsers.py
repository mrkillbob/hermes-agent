"""Pure allowlisted parsers for HRL-0 command output."""

import re
from typing import Any

from .redaction import sanitize_diagnostic

_SIZE_PATTERN = r"[0-9]+(?:\.[0-9]+)?\s+[A-Za-z]+"


def parse_hermes_version(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    version = re.search(r"Hermes Agent v([^\s]+)", text)
    upstream = re.search(r"upstream\s+([0-9a-f]+)", text, re.IGNORECASE)
    if version:
        result["version"] = version.group(1)
    if upstream:
        result["upstream_revision"] = upstream.group(1)
    for line in text.splitlines():
        label, separator, value = line.partition(":")
        if not separator:
            continue
        normalized = label.strip().lower()
        if normalized == "install directory":
            result["install_directory"] = sanitize_diagnostic(value.strip())
        elif normalized == "install method":
            result["install_method"] = value.strip()
        elif normalized == "python":
            result["python_version"] = value.strip()
    return result


def parse_hermes_tools(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(r"^\s*[✓✗]\s+(enabled|disabled)\s+(\S+)")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            rows.append({"name": match.group(2), "enabled": match.group(1) == "enabled"})
    return rows


def parse_hermes_profiles(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip().lstrip("◆").strip()
        fields = stripped.split()
        if len(fields) < 2 or fields[0].lower() == "profile":
            continue
        if ":" not in fields[1]:
            continue
        row = {"name": fields[0], "model": fields[1]}
        if len(fields) >= 3:
            row["gateway"] = fields[2]
        rows.append(row)
    return rows


def parse_hermes_cron(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    job_pattern = re.compile(r"^\s*([0-9a-f]{6,})\s+\[(active|paused)\]\s*$", re.I)
    allowed_fields = {
        "name": "name",
        "schedule": "schedule",
        "repeat": "repeat",
        "next run": "next_run",
        "last run": "last_run",
        "deliver": "deliver",
        "workdir": "workdir",
    }
    for line in text.splitlines():
        job = job_pattern.match(line)
        if job:
            current = {"id": job.group(1), "status": job.group(2).lower()}
            rows.append(current)
            continue
        if current is None:
            continue
        label, separator, value = line.strip().partition(":")
        key = allowed_fields.get(label.strip().lower())
        if separator and key:
            current[key] = sanitize_diagnostic(value.strip())
    return rows


def parse_ollama_list(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = re.compile(rf"^(\S+)\s+(\S+)\s+({_SIZE_PATTERN})(?:\s+|$)")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match and match.group(1).upper() != "NAME":
            rows.append(
                {"name": match.group(1), "digest": match.group(2), "size": match.group(3)}
            )
    return rows


def parse_ollama_show(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {"capabilities": []}
    in_capabilities = False
    labels = {
        "architecture": "architecture",
        "parameters": "parameters",
        "context length": "context_length",
        "embedding length": "embedding_length",
        "quantization": "quantization",
        "requires": "requires_ollama",
    }
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped == "Capabilities":
            in_capabilities = True
            continue
        if stripped in {"Model", "Parameters", "Metadata", "System", "License"}:
            in_capabilities = False
            continue
        if in_capabilities:
            if stripped and re.fullmatch(r"[a-z_]+", stripped):
                result["capabilities"].append(stripped)
            continue
        for label, key in labels.items():
            match = re.match(rf"^{re.escape(label)}\s{{2,}}(.+)$", stripped, re.I)
            if not match:
                continue
            value: Any = match.group(1).strip()
            if key in {"context_length", "embedding_length"} and str(value).isdigit():
                value = int(value)
            result[key] = value
            break
    return result


def parse_ollama_ps(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        rf"^(\S+)\s+(\S+)\s+({_SIZE_PATTERN})\s+(\d+%\s+\S+)\s+(\d+)(?:\s+|$)"
    )
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match and match.group(1).upper() != "NAME":
            rows.append(
                {
                    "name": match.group(1),
                    "digest": match.group(2),
                    "size": match.group(3),
                    "processor": match.group(4),
                    "context_length": int(match.group(5)),
                }
            )
    return rows


def parse_hardware(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    allowed = {
        "Model Name": "model_name",
        "Model Identifier": "model_identifier",
        "Chip": "chip",
        "Memory": "memory",
    }
    for raw_line in text.splitlines():
        label, separator, value = raw_line.strip().partition(":")
        if not separator:
            continue
        if label in allowed:
            result[allowed[label]] = value.strip()
        elif label == "Total Number of Cores":
            counts = re.match(r"(\d+)\s+\((\d+) Performance and (\d+) Efficiency\)", value.strip())
            if counts:
                result["total_cores"] = int(counts.group(1))
                result["performance_cores"] = int(counts.group(2))
                result["efficiency_cores"] = int(counts.group(3))
            elif value.strip().isdigit():
                result["total_cores"] = int(value.strip())
    return result


def parse_df(text: str) -> dict[str, int]:
    data_lines = [line for line in text.splitlines() if line.strip()]
    if len(data_lines) < 2:
        return {}
    fields = data_lines[-1].split()
    if len(fields) < 4:
        return {}
    try:
        return {
            "total_bytes": int(fields[1]) * 1024,
            "used_bytes": int(fields[2]) * 1024,
            "available_bytes": int(fields[3]) * 1024,
        }
    except ValueError:
        return {}


def parse_vm_stat(text: str) -> dict[str, int]:
    page_size_match = re.search(r"page size of (\d+) bytes", text)
    if not page_size_match:
        return {}
    page_size = int(page_size_match.group(1))
    pages: dict[str, int] = {}
    labels = {
        "Pages free": "free_bytes",
        "Pages active": "active_bytes",
        "Pages inactive": "inactive_bytes",
        "Pages wired down": "wired_bytes",
        "Pages occupied by compressor": "compressed_bytes",
    }
    for raw_line in text.splitlines():
        label, separator, value = raw_line.strip().partition(":")
        key = labels.get(label)
        if separator and key:
            number = value.strip().rstrip(".")
            if number.isdigit():
                pages[key] = int(number) * page_size
    return pages


def parse_process_table(text: str) -> dict[str, dict[str, float | int]]:
    process_markers = {
        "revenue_lab": ("/hermesrevenuelab/",),
        "luna": ("/tradingbotv18/", "/lunabot-default/", "live_runner"),
        "ollama": ("ollama serve", "ollama runner"),
        "hermes": ("/hermes.app/", "/.hermes/hermes-agent/"),
    }
    totals: dict[str, dict[str, float | int]] = {
        name: {"count": 0, "cpu_percent": 0.0, "rss_bytes": 0}
        for name in process_markers
    }
    for line in text.splitlines():
        fields = line.split(maxsplit=6)
        if len(fields) != 7 or not fields[0].isdigit():
            continue
        command = fields[6].lower()
        executable = command.split(maxsplit=1)[0]
        if executable.endswith(("/rg", "/grep", "/find")) or executable in {"rg", "grep", "find"}:
            continue
        category = next(
            (
                name
                for name, markers in process_markers.items()
                if any(marker in command for marker in markers)
            ),
            None,
        )
        if category is None:
            continue
        totals[category]["count"] = int(totals[category]["count"]) + 1
        totals[category]["cpu_percent"] = round(
            float(totals[category]["cpu_percent"]) + float(fields[2]), 3
        )
        totals[category]["rss_bytes"] = int(totals[category]["rss_bytes"]) + int(fields[4]) * 1024
    return totals
