#!/usr/bin/env python3
"""account_tracker_dashboard.py — End-of-night per-account dashboard.

Reads:
  - Settings/Global/account_track/<email>.jsonl (60s snapshots from AccountTracker widget)
  - Settings/Global/farm_stats.jsonl (per-run records from FarmRunTracker)
  - Settings/Global/full_log.tsv (death/wipe/skill events)

Produces a self-contained HTML dashboard at:
  Settings/Global/account_dashboard.html

Per-account: gold curve, inventory curve, runs completed, success rate,
deaths, wipes, runs/hour, gold/hour, items/run, deposit cycles.
"""
import os
import sys
import json
import html as html_mod
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(r"C:\Users\reid1\Documents\Py4GW\Settings\Global")
TRACK_DIR = BASE / "account_track"
FARM_STATS = BASE / "farm_stats.jsonl"
FULL_LOG = BASE / "full_log.tsv"
OUT = BASE / "account_dashboard.html"


def load_snapshots():
    """Returns {email: [snapshot_dict, ...]} sorted by ts."""
    by_email = {}
    if not TRACK_DIR.exists():
        return by_email
    for path in TRACK_DIR.glob("*.jsonl"):
        records = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue
        except Exception:
            continue
        if not records:
            continue
        records.sort(key=lambda r: r.get("ts", ""))
        email = records[0].get("email") or path.stem
        by_email[email] = records
    return by_email


def load_farm_runs(since=None):
    runs = []
    if not FARM_STATS.exists():
        return runs
    try:
        with open(FARM_STATS, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("timestamp", "")
                if since and ts < since:
                    continue
                runs.append(r)
    except Exception:
        pass
    return runs


def load_full_log_events(since_iso=None):
    """Returns count of D/W per email-prefix-or-slot from the most recent N sessions."""
    counts = defaultdict(lambda: Counter())
    if not FULL_LOG.exists():
        return counts
    cutoff = None
    if since_iso:
        try:
            cutoff = datetime.fromisoformat(since_iso)
        except Exception:
            pass
    current_session_start = None
    try:
        with open(FULL_LOG, "rb") as f:
            for raw in f:
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                acct = parts[1]
                evt = parts[2]
                data = parts[3] if len(parts) > 3 else ""
                if acct == "*" and evt == "#" and data.startswith("START "):
                    iso = data[len("START "):].strip()
                    try:
                        current_session_start = datetime.fromisoformat(iso)
                    except Exception:
                        current_session_start = None
                    continue
                if cutoff and current_session_start and current_session_start < cutoff:
                    continue
                if evt in ("D", "W", "K", "I"):
                    counts[acct][evt] += 1
    except Exception:
        pass
    return counts


def aggregate_per_account(snapshots):
    """Compute per-account derived metrics."""
    out = {}
    for email, records in snapshots.items():
        if not records:
            continue
        first = records[0]
        last = records[-1]
        try:
            t0 = datetime.fromisoformat(first["ts"])
            t1 = datetime.fromisoformat(last["ts"])
            duration_h = max((t1 - t0).total_seconds() / 3600, 0.0001)
        except Exception:
            t0 = t1 = None
            duration_h = 0
        gold_start = first.get("gold", {}).get("total", 0)
        gold_end = last.get("gold", {}).get("total", 0)
        gold_delta = gold_end - gold_start
        gold_per_h = gold_delta / duration_h if duration_h else 0

        # Track inventory churn: count outpost transitions (= deposit cycles)
        deposits = 0
        prev_outpost = None
        prev_inv_total = first.get("inv", {}).get("total", 0)
        max_inv_during = prev_inv_total
        for rec in records:
            in_outpost = rec.get("is_outpost", False)
            inv_now = rec.get("inv", {}).get("total", 0)
            if in_outpost and prev_outpost is False:
                deposits += 1
            prev_outpost = in_outpost
            max_inv_during = max(max_inv_during, inv_now)

        # Inventory by rarity from last record
        last_inv = last.get("inv", {})

        out[email] = {
            "character": last.get("character") or first.get("character") or "",
            "first_ts": first.get("ts", ""),
            "last_ts": last.get("ts", ""),
            "duration_h": round(duration_h, 2),
            "snapshots": len(records),
            "gold_start": gold_start,
            "gold_end": gold_end,
            "gold_delta": gold_delta,
            "gold_per_h": int(gold_per_h),
            "deposits": deposits,
            "max_inv_during": max_inv_during,
            "current_inv_total": last_inv.get("total", 0),
            "current_inv_white": last_inv.get("white", 0),
            "current_inv_blue": last_inv.get("blue", 0),
            "current_inv_purple": last_inv.get("purple", 0),
            "current_inv_gold": last_inv.get("gold", 0),
            "current_inv_green": last_inv.get("green", 0),
            "current_inv_materials": last_inv.get("materials", 0),
            "free_slots_now": last.get("free_slots", 0),
            "current_map_id": last.get("map_id", 0),
            "is_outpost_now": last.get("is_outpost", False),
            # Time-series for charts
            "ts_series": [r.get("ts", "") for r in records],
            "gold_series": [r.get("gold", {}).get("total", 0) for r in records],
            "inv_series": [r.get("inv", {}).get("total", 0) for r in records],
            "free_slots_series": [r.get("free_slots", 0) for r in records],
        }
    return out


def aggregate_runs_per_account(runs):
    """Group farm_stats records by character (since email is often empty)."""
    by_key = defaultdict(list)
    for r in runs:
        key = r.get("character_name") or r.get("account_email") or "unknown"
        by_key[key].append(r)
    out = {}
    for key, recs in by_key.items():
        wins = sum(1 for r in recs if r.get("success"))
        total_time = sum(r.get("run_time_ms", 0) for r in recs)
        out[key] = {
            "runs": len(recs),
            "wins": wins,
            "fails": len(recs) - wins,
            "win_rate": round((wins / len(recs)) * 100, 1) if recs else 0,
            "avg_run_ms": int(total_time / len(recs)) if recs else 0,
            "total_time_h": round(total_time / 1000 / 3600, 2),
        }
    return out


def render_html(per_acct, per_acct_runs, log_counts, window_label):
    accts = sorted(per_acct.keys())

    # KPI totals
    total_gold_delta = sum(d["gold_delta"] for d in per_acct.values())
    total_runs = sum(d.get("runs", 0) for d in per_acct_runs.values())
    total_wins = sum(d.get("wins", 0) for d in per_acct_runs.values())

    # Per-account table
    table_rows_html = ""
    for email in accts:
        d = per_acct[email]
        char = d["character"] or "(unknown)"
        runs_data = per_acct_runs.get(char) or per_acct_runs.get(email) or {}
        runs = runs_data.get("runs", 0)
        wins = runs_data.get("wins", 0)
        fails = runs_data.get("fails", 0)
        win_rate = runs_data.get("win_rate", 0)
        avg_run_min = round(runs_data.get("avg_run_ms", 0) / 60000, 1) if runs else 0
        runs_per_h = round(runs / d["duration_h"], 2) if d["duration_h"] else 0

        gold_class = "good" if d["gold_delta"] > 0 else ("bad" if d["gold_delta"] < 0 else "neutral")
        win_class = "good" if win_rate >= 80 else ("warn" if win_rate >= 50 else "bad")

        table_rows_html += f"""
        <tr>
          <td><b>{html_mod.escape(char)}</b><div class="small">{html_mod.escape(email)}</div></td>
          <td class="num">{d['duration_h']}h</td>
          <td class="num">{runs}</td>
          <td class="num"><span class="pill pill-{win_class}">{win_rate}% ({wins}W/{fails}L)</span></td>
          <td class="num">{avg_run_min} min</td>
          <td class="num">{runs_per_h}</td>
          <td class="num"><span class="pill pill-{gold_class}">{d['gold_delta']:+,}</span></td>
          <td class="num">{d['gold_per_h']:,}/h</td>
          <td class="num">{d['deposits']}</td>
          <td class="num">{d['current_inv_total']}</td>
          <td class="num">{d['current_inv_gold']}</td>
          <td class="num">{d['current_inv_purple']}</td>
          <td class="num">{d['current_inv_blue']}</td>
          <td class="num">{d['free_slots_now']}</td>
        </tr>"""

    # Charts
    chart_payload = {}
    for email in accts:
        d = per_acct[email]
        chart_payload[email] = {
            "ts": d["ts_series"],
            "gold": d["gold_series"],
            "inv": d["inv_series"],
            "free": d["free_slots_series"],
            "char": d["character"],
        }

    css = """
    :root { color-scheme: dark; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, "Segoe UI", system-ui, sans-serif; background: #0f1419; color: #e0e6ed; padding: 24px; line-height: 1.5; }
    h1 { color: #fff; margin-bottom: 8px; font-size: 28px; }
    h2 { color: #94a3b8; margin: 32px 0 12px; font-size: 18px; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #1e293b; padding-bottom: 8px; }
    h3 { color: #cbd5e1; margin: 16px 0 8px; font-size: 15px; }
    .meta { color: #64748b; font-size: 13px; margin-bottom: 24px; }
    .grid { display: grid; gap: 16px; }
    .grid-3 { grid-template-columns: repeat(3, 1fr); }
    .grid-4 { grid-template-columns: repeat(4, 1fr); }
    .grid-2 { grid-template-columns: repeat(2, 1fr); }
    @media (max-width: 900px) { .grid-3, .grid-4 { grid-template-columns: 1fr 1fr; } .grid-2 { grid-template-columns: 1fr; } }
    .card { background: #1a2332; border: 1px solid #2a3441; border-radius: 8px; padding: 16px; }
    .kpi { background: #1a2332; border: 1px solid #2a3441; border-radius: 8px; padding: 16px; }
    .kpi .label { color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
    .kpi .value { color: #fff; font-size: 28px; font-weight: 600; margin-top: 4px; }
    .kpi.good .value { color: #10b981; }
    .kpi.bad .value { color: #ef4444; }
    .kpi.warn .value { color: #f59e0b; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 8px 10px; background: #0f1419; color: #94a3b8; font-weight: 500; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; border-bottom: 1px solid #2a3441; position: sticky; top: 0; }
    td { padding: 8px 10px; border-bottom: 1px solid #1e293b; vertical-align: top; }
    td.num { text-align: right; font-variant-numeric: tabular-nums; }
    tr:hover { background: #1e293b; }
    .small { font-size: 11px; color: #64748b; margin-top: 2px; }
    .pill { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500; }
    .pill-good { background: rgba(16, 185, 129, 0.2); color: #a7f3d0; }
    .pill-bad { background: rgba(239, 68, 68, 0.2); color: #fecaca; }
    .pill-warn { background: rgba(245, 158, 11, 0.2); color: #fde68a; }
    .pill-neutral { background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }
    .chart-wrap { background: #1a2332; border: 1px solid #2a3441; border-radius: 8px; padding: 16px; }
    canvas { max-height: 220px; }
    """

    payload_json = json.dumps(chart_payload)

    html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Py4GW Per-Account Dashboard</title>
<style>{css}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head><body>
<h1>Py4GW Per-Account Dashboard</h1>
<div class="meta">{html_mod.escape(window_label)} · {len(accts)} account(s) tracked</div>

<h2>KPIs</h2>
<div class="grid grid-4">
  <div class="kpi"><div class="label">Tracked accounts</div><div class="value">{len(accts)}</div></div>
  <div class="kpi"><div class="label">Total runs</div><div class="value">{total_runs}</div></div>
  <div class="kpi {'good' if total_wins >= total_runs/2 else 'bad'}"><div class="label">Total wins</div><div class="value">{total_wins}</div></div>
  <div class="kpi {'good' if total_gold_delta > 0 else 'bad'}"><div class="label">Total gold gained</div><div class="value">{total_gold_delta:+,}</div></div>
</div>

<h2>Per-Account Breakdown</h2>
<div class="card" style="overflow-x:auto;">
<table>
<thead>
<tr>
  <th>Account / Character</th><th>Tracked</th><th>Runs</th><th>Win %</th>
  <th>Avg run</th><th>Runs/h</th><th>Gold Δ</th><th>Gold/h</th>
  <th>Deposits</th><th>Inv total</th><th>Golds</th><th>Purples</th><th>Blues</th><th>Free slots</th>
</tr>
</thead>
<tbody>{table_rows_html}</tbody>
</table>
</div>

<h2>Gold Over Time (per Account)</h2>
<div class="grid grid-2" id="gold_grid"></div>

<h2>Free Slots Over Time</h2>
<div class="grid grid-2" id="slots_grid"></div>

<h2>Notes</h2>
<div class="card small">
Snapshots taken every 60 seconds by <code>AccountTracker.py</code> widget while Py4GW is running.
Per-account identification falls back to ShMem if Player.GetAccountEmail() returns empty during transitions.
Run statistics from <code>FarmRunTracker</code> (per <code>JagaMoraineFarmRoutine</code> cycle, success/fail tracked).
Kills are NOT tracked — FullLogger never emits K events; would need a separate combat hook.
Gold/h is rough — character gold + storage gold combined; sells/deposits affect both.
</div>

<script>
const D = {payload_json};
const palette = ['#3b82f6','#ef4444','#10b981','#f59e0b','#8b5cf6','#ec4899','#06b6d4','#84cc16'];
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#1e293b';

function makeCanvas(parent, title) {{
  const wrap = document.createElement('div');
  wrap.className = 'chart-wrap';
  wrap.innerHTML = '<h3>' + title + '</h3><canvas></canvas>';
  parent.appendChild(wrap);
  return wrap.querySelector('canvas');
}}

const goldGrid = document.getElementById('gold_grid');
const slotsGrid = document.getElementById('slots_grid');

let i = 0;
for (const [email, d] of Object.entries(D)) {{
  const color = palette[i % palette.length]; i++;
  const label = (d.char || email).slice(0, 30);

  const goldCanvas = makeCanvas(goldGrid, label + ' — gold');
  new Chart(goldCanvas, {{
    type: 'line',
    data: {{ labels: d.ts, datasets: [{{ label: 'Gold', data: d.gold, borderColor: color, backgroundColor: color + '20', fill: true, tension: 0.2, pointRadius: 0 }}] }},
    options: {{ responsive: true, scales: {{ x: {{ ticks: {{ maxTicksLimit: 8 }} }}, y: {{ beginAtZero: false }} }} }}
  }});

  const slotsCanvas = makeCanvas(slotsGrid, label + ' — free slots');
  new Chart(slotsCanvas, {{
    type: 'line',
    data: {{ labels: d.ts, datasets: [{{ label: 'Free Slots', data: d.free, borderColor: color, backgroundColor: color + '20', fill: true, tension: 0.2, pointRadius: 0 }}] }},
    options: {{ responsive: true, scales: {{ x: {{ ticks: {{ maxTicksLimit: 8 }} }}, y: {{ beginAtZero: true }} }} }}
  }});
}}
</script>
</body></html>
"""
    return html_doc


def main():
    snapshots = load_snapshots()
    if not snapshots:
        print("ERROR: no snapshots in", TRACK_DIR, file=sys.stderr)
        sys.exit(1)
    per_acct = aggregate_per_account(snapshots)
    runs = load_farm_runs()
    per_acct_runs = aggregate_runs_per_account(runs)

    # Window label
    all_ts = []
    for d in per_acct.values():
        all_ts.append(d["first_ts"])
        all_ts.append(d["last_ts"])
    all_ts = [t for t in all_ts if t]
    if all_ts:
        window_label = f"{min(all_ts)} -> {max(all_ts)}"
    else:
        window_label = "no data"

    log_counts = load_full_log_events()

    html_doc = render_html(per_acct, per_acct_runs, log_counts, window_label)
    OUT.write_text(html_doc, encoding="utf-8")
    print(f"Dashboard written: {OUT} ({OUT.stat().st_size:,} bytes)")
    print(f"Accounts: {len(per_acct)} | Runs: {len(runs)}")


if __name__ == "__main__":
    main()
