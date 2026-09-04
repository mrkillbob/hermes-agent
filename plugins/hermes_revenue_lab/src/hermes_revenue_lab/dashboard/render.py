"""Code-native HTML/CSS renderer for the HRL-16 dashboard concept."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from html import escape

from .types import DashboardSnapshot


def _text(value: object, *, suffix: str = "") -> str:
    if value is None:
        return '<span class="unknown">Unavailable</span>'
    return escape(f"{value}{suffix}")


def _money(value: Decimal | None) -> str:
    return _text(None) if value is None else f"${value:,.2f}"


def _rate(value: Decimal | None) -> str:
    return _text(None) if value is None else f"{value * 100:.1f}%"


def _metric(value: object, *, money: bool = False, suffix: str = "") -> str:
    if value is None:
        return (
            '<span class="metric-dash">—</span><span class="unknown">Unavailable</span>'
        )
    rendered = _money(value) if money else _text(value, suffix=suffix)  # type: ignore[arg-type]
    return f'<span class="metric-known">{rendered}</span>'


def _reason(snapshot: DashboardSnapshot) -> str:
    if not snapshot.source_reasons:
        return "All configured sources available"
    return " · ".join(escape(reason) for reason in snapshot.source_reasons)


def _local_timestamp(value: str, *, time_only: bool = False) -> str:
    observed = datetime.fromisoformat(value).astimezone()
    return observed.strftime("%H:%M:%S" if time_only else "%Y-%m-%d %H:%M:%S")


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    today = snapshot.today
    experiments = snapshot.experiments
    model_rows = (
        "".join(
            f"""<tr><td>{escape(row.model)}</td><td>{row.invocations}</td>
        <td>{_text(row.median_latency_seconds, suffix=" s")}</td><td>{_rate(row.success_rate)}</td>
        <td>{_rate(row.escalation_rate)}</td><td>{_text(row.compute_hours, suffix=" h")}</td></tr>"""
            for row in snapshot.model_economics
        )
        or '<tr><td>—</td><td colspan="5" class="unknown">Unavailable</td></tr>' * 5
    )
    opportunity_rows = (
        "".join(
            f"""<tr><td>{escape(row.candidate_id)}</td><td>{escape(row.score)}</td>
        <td>{row.evidence_count} sources</td><td>{escape(row.proposed_experiment)}</td>
        <td>{escape(row.required_approval)}</td></tr>"""
            for row in snapshot.opportunity_queue
        )
        or '<tr><td>—</td><td colspan="4" class="unknown">Unavailable</td></tr>' * 5
    )
    guard_reasons = (
        " · ".join(escape(item) for item in snapshot.guard.reasons)
        if snapshot.guard.reasons
        else "None"
    )
    resources = snapshot.guard.resources
    resource_text = (
        f"Load {_text(resources.load_1m)} / {_text(resources.cpu_count)} CPU · "
        f"Memory {_text(resources.memory_free_percent, suffix='%')} · "
        f"Swap {_text(resources.swap_used_bytes, suffix=' B')} · "
        f"Foreign models {_text(resources.foreign_ollama_model_count)} · "
        f"Luna {escape(resources.luna_health_status)}"
    )
    state_class = snapshot.guard.state.lower().replace("_", "-")
    data_as_of = (
        escape(_local_timestamp(snapshot.generated_at))
        if snapshot.freshness != "unavailable"
        else '<span class="unknown">Unavailable</span>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="15"><title>Hermes Revenue Lab</title>
<style>
:root{{--bg:#07141e;--rail:#0a1925;--surface:#0d1d29;--line:#2a3b48;--text:#edf3f7;--muted:#9fb0c2;--teal:#34c7a7;--amber:#f6b940;--red:#ff5b5b;--pad:20px}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
body{{letter-spacing:.01em}}.shell{{min-height:100vh;display:grid;grid-template-columns:238px minmax(0,1fr)}}aside{{background:var(--rail);border-right:1px solid var(--line);padding:22px 18px;display:flex;flex-direction:column;gap:22px}}
.rail-title{{color:var(--muted);font-size:12px;letter-spacing:.12em;text-transform:uppercase;margin:0 0 10px}}.rail-row{{display:flex;justify-content:space-between;gap:12px;padding:5px 0;color:var(--muted)}}.rail-row strong{{color:var(--amber);font-weight:550;text-align:right}}.rail-section{{border-bottom:1px solid var(--line);padding-bottom:17px}}.rail-note{{color:var(--muted);font-size:12px}}.legend{{display:grid;gap:7px;color:var(--muted);font-size:12px}}.legend span::before{{content:"—";margin-right:9px}}.legend .known::before{{color:var(--teal)}}.legend .limited::before{{color:var(--amber)}}.legend .paused::before{{color:var(--red)}}.readonly{{margin-top:auto;color:var(--muted);border-top:1px solid var(--line);padding-top:20px}}
main{{min-width:0}}header{{height:62px;display:flex;align-items:center;gap:18px;padding:0 var(--pad);border-bottom:1px solid var(--line)}}h1{{font-size:25px;line-height:1;margin:0;font-weight:650}}header p{{margin:0;color:var(--muted)}}.asof{{margin-left:auto;color:var(--muted)}}.unknown{{color:var(--amber)}}section{{padding:14px var(--pad);border-bottom:1px solid var(--line)}}h2{{font-size:17px;margin:0 0 12px;font-weight:600}}h2 small{{color:var(--muted);font-weight:400;font-size:13px;margin-left:6px}}
.metric-grid{{display:grid;grid-template-columns:repeat(6,1fr);border-top:1px solid var(--line)}}.metric{{text-align:center;padding:13px 10px;border-right:1px solid var(--line)}}.metric:last-child{{border-right:0}}.metric label{{display:block;color:var(--muted);margin-bottom:7px}}.metric strong{{font-size:21px;font-variant-numeric:tabular-nums;font-weight:550}}.metric strong span{{display:block}}.metric .metric-dash{{color:var(--text);line-height:1.1}}.metric .unknown{{font-size:13px;margin-top:6px}}
.experiment-grid{{display:grid;grid-template-columns:repeat(5,1fr);border-top:1px solid var(--line)}}.experiment{{padding:13px;text-align:center;border-right:1px solid var(--line)}}.experiment:last-child{{border:0}}.experiment label{{display:block;color:var(--muted)}}.experiment strong{{display:block;font-size:21px;margin-top:7px}}.experiment.profitable label,.experiment.scaling label{{color:var(--teal)}}.experiment.killed label{{color:var(--red)}}
.split{{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(300px,1fr)}}.split>section{{border-right:1px solid var(--line)}}table{{width:100%;border-collapse:collapse;border:1px solid var(--line);font-variant-numeric:tabular-nums}}th,td{{padding:9px 11px;text-align:left;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-size:12px;font-weight:550}}td{{font-size:13px}}tr:last-child td{{border-bottom:0}}.guard-state{{border:1px solid var(--line);padding:12px;text-align:center;font-size:20px;font-weight:650;margin-bottom:15px}}.guard-state.full{{border-color:var(--teal);color:var(--teal)}}.guard-state.limited,.guard-state.unavailable{{border-color:var(--amber);color:var(--amber)}}.guard-state.paused,.guard-state.emergency-stop{{border-color:var(--red);color:var(--red)}}.detail{{display:grid;grid-template-columns:120px 1fr;gap:11px;margin:9px 0}}.detail span:first-child{{color:var(--muted)}}.detail span:last-child{{text-align:right}}.source-reason{{color:var(--muted);font-size:12px;margin-top:8px}}
@media(max-width:980px){{.shell{{grid-template-columns:1fr}}aside{{display:none}}.metric-grid{{grid-template-columns:repeat(3,1fr)}}.experiment-grid{{grid-template-columns:repeat(3,1fr)}}.split{{grid-template-columns:1fr}}.split>section{{border-right:0}}}}
@media(max-width:600px){{:root{{--pad:12px}}header{{height:auto;min-height:62px;flex-wrap:wrap;padding-block:14px}}.asof{{margin-left:0;width:100%}}.metric-grid,.experiment-grid{{grid-template-columns:repeat(2,1fr)}}section{{overflow-x:auto}}}}
</style></head><body><div class="shell"><aside>
<div class="rail-section"><p class="rail-title">Status</p><div class="rail-row"><span>System time</span><strong>{escape(_local_timestamp(snapshot.generated_at, time_only=True))}</strong></div><div class="rail-row"><span>Data freshness</span><strong>{escape(snapshot.freshness.title())}</strong></div><div class="rail-row"><span>Last event</span><strong>Unavailable</strong></div><div class="rail-row"><span>Environment</span><strong>Local</strong></div></div>
<div class="rail-section"><p class="rail-title">Guardrail</p><div class="rail-row"><span>Luna guard</span><strong>{escape(snapshot.guard.state)}</strong></div><div class="rail-row"><span>Model health</span><strong>Unavailable</strong></div><div class="rail-row"><span>Human capacity</span><strong>Unavailable</strong></div><div class="rail-row"><span>Compute capacity</span><strong>Unavailable</strong></div></div>
<div class="rail-section"><p class="rail-title">Data sources</p><div class="rail-row"><span>Local store</span><strong>Unavailable</strong></div><div class="rail-row"><span>Experiment log</span><strong>Unavailable</strong></div><div class="rail-row"><span>Model telemetry</span><strong>Unavailable</strong></div><div class="rail-row"><span>Human actions</span><strong>Unavailable</strong></div></div>
<div class="rail-section"><p class="rail-title">Notes</p><p class="rail-note">All metrics come from local evidence. Missing evidence is shown as “Unavailable.”</p></div><div class="rail-section"><p class="rail-title">Legend</p><div class="legend"><span class="known">Known / Healthy</span><span class="limited">Limited / Unknown</span><span class="paused">Paused / Killed</span></div></div><div class="readonly">Observability only. No actions.</div></aside>
<main><header><h1>Hermes Revenue Lab</h1><p>Local evidence only</p><div class="asof">All times local &nbsp; | &nbsp; Data as of: {data_as_of}</div></header>
<section><h2>Today summary <small>(local evidence only)</small></h2><div class="metric-grid">
<div class="metric"><label>Revenue</label><strong>{_metric(today.revenue, money=True)}</strong></div><div class="metric"><label>Expenses</label><strong>{_metric(today.expenses, money=True)}</strong></div><div class="metric"><label>Profit</label><strong>{_metric(today.profit, money=True)}</strong></div><div class="metric"><label>Customers</label><strong>{_metric(today.customers)}</strong></div><div class="metric"><label>Compute hours</label><strong>{_metric(today.compute_hours)}</strong></div><div class="metric"><label>Human intervention</label><strong>{_metric(today.human_intervention_minutes, suffix=" min")}</strong></div></div></section>
<section><h2>Experiments status rail <small>(by count)</small></h2><div class="experiment-grid">
<div class="experiment"><label>Researching</label><strong>{_text(experiments.researching)}</strong></div><div class="experiment"><label>Testing</label><strong>{_text(experiments.testing)}</strong></div><div class="experiment profitable"><label>Profitable</label><strong>{_text(experiments.profitable)}</strong></div><div class="experiment scaling"><label>Scaling</label><strong>{_text(experiments.scaling)}</strong></div><div class="experiment killed"><label>Killed</label><strong>{_text(experiments.killed)}</strong></div></div></section>
<div class="split"><section><h2>Model economics <small>(local evidence only)</small></h2><table><thead><tr><th>Model</th><th>Invocations</th><th>Median latency</th><th>Success</th><th>Escalation</th><th>Compute time</th></tr></thead><tbody>{model_rows}</tbody></table></section>
<section><h2>Luna guard <small>(system safety state)</small></h2><div class="guard-state {state_class}">{escape(snapshot.guard.state)}</div><div class="detail"><span>Reason</span><span>{guard_reasons}</span></div><div class="detail"><span>Last transition</span><span>{_text(snapshot.guard.last_transition)}</span></div><div class="detail"><span>Resource state</span><span>{resource_text}</span></div></section></div>
<section><h2>Opportunity queue <small>(local evidence only)</small></h2><table><thead><tr><th>Candidate</th><th>Score</th><th>Evidence</th><th>Proposed experiment</th><th>Required approval</th></tr></thead><tbody>{opportunity_rows}</tbody></table><p class="source-reason">{_reason(snapshot)}</p></section></main></div></body></html>"""
