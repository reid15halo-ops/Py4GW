#!/usr/bin/env python3
"""night_session_report.py — Detailed HTML report of the most recent Py4GW session.

Parses full_log.tsv from the latest START marker forward.
Self-contained HTML output with Chart.js (CDN). No pip dependencies.
"""
import os
import sys
import json
import html as html_mod
from collections import defaultdict, Counter
from datetime import datetime, timedelta

LOG_PATH = r"C:\Users\reid1\Documents\Py4GW\Settings\Global\full_log.tsv"
ACCOUNTS_PATH = r"C:\Users\reid1\Documents\Py4GW\accounts.json"
OUT_PATH = r"C:\Users\reid1\Documents\Py4GW\Settings\Global\night_session_report.html"

EVENT_NAMES = {
    "S": "Skill cast", "D": "Death", "R": "Resurrect", "H": "HP change",
    "M": "Map change", "C": "Combat state", "P": "Position", "K": "Kill",
    "W": "Wipe", "I": "Item pickup", "X": "Exception", "L": "Gold change",
    "B": "Boss event", "Z": "Resign", "F": "FSM step", "T": "Strategy change",
    "#": "Session marker",
}


def parse_all_with_absolute_time(path, hours_back=14):
    """Parse the whole file. Each event gets an absolute datetime = current_session_start + ms.
    Returns events filtered to the last `hours_back` hours and the earliest session label.
    """
    cutoff = datetime.now() - timedelta(hours=hours_back)
    events = []
    current_session_start = None
    session_labels = []
    with open(path, "rb") as f:
        for raw in f:
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
            except Exception:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            try:
                ms = int(parts[0])
            except ValueError:
                continue
            acct = parts[1]
            evt = parts[2]
            data = parts[3] if len(parts) > 3 else ""
            if acct == "*" and evt == "#" and data.startswith("START "):
                iso = data[len("START "):].strip()
                try:
                    current_session_start = datetime.fromisoformat(iso)
                    session_labels.append(iso)
                except Exception:
                    current_session_start = None
            if current_session_start is None:
                continue
            abs_time = current_session_start + timedelta(milliseconds=ms)
            if abs_time < cutoff:
                continue
            events.append((abs_time, acct, evt, data))
    earliest_label = None
    if events:
        earliest_label = events[0][0].strftime("%Y-%m-%d %H:%M:%S")
    return events, earliest_label, session_labels


def load_accounts():
    if not os.path.exists(ACCOUNTS_PATH):
        return {}
    try:
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def aggregate(events):
    by_acct = defaultdict(lambda: Counter())
    by_acct_maps = defaultdict(Counter)
    map_state_count = defaultdict(Counter)
    by_acct_x_msgs = defaultdict(list)
    by_acct_first_seen = {}
    by_acct_last_seen = {}
    by_acct_resigns = defaultdict(int)
    timeline_5min = defaultdict(lambda: defaultdict(int))
    timeline_hour = defaultdict(lambda: defaultdict(int))
    total_events = 0

    if not events:
        return {"by_acct": {}, "total_events": 0}
    t0 = events[0][0]

    for abs_t, acct, evt, data in events:
        total_events += 1
        by_acct[acct][evt] += 1
        if acct not in by_acct_first_seen:
            by_acct_first_seen[acct] = abs_t
        by_acct_last_seen[acct] = abs_t

        if evt == "M":
            try:
                p = data.split(",")
                map_id = int(p[0])
                state = p[1] if len(p) > 1 else "?"
                by_acct_maps[acct][map_id] += 1
                map_state_count[acct][state] += 1
            except Exception:
                pass
        elif evt == "X":
            by_acct_x_msgs[acct].append((abs_t, data))
        elif evt == "Z":
            by_acct_resigns[acct] += 1

        delta_min_5 = int((abs_t - t0).total_seconds() // (5 * 60))
        delta_h = int((abs_t - t0).total_seconds() // 3600)
        timeline_5min[delta_min_5][evt] += 1
        timeline_hour[delta_h][evt] += 1

    return {
        "by_acct": {k: dict(v) for k, v in by_acct.items()},
        "by_acct_maps": {k: dict(v) for k, v in by_acct_maps.items()},
        "map_state_count": {k: dict(v) for k, v in map_state_count.items()},
        "by_acct_x_msgs": {k: [(t.isoformat(), d) for t, d in v] for k, v in by_acct_x_msgs.items()},
        "by_acct_first_seen": {k: v.isoformat() for k, v in by_acct_first_seen.items()},
        "by_acct_last_seen": {k: v.isoformat() for k, v in by_acct_last_seen.items()},
        "by_acct_resigns": dict(by_acct_resigns),
        "timeline_5min": {int(k): dict(v) for k, v in timeline_5min.items()},
        "timeline_hour": {int(k): dict(v) for k, v in timeline_hour.items()},
        "total_events": total_events,
        "t0": t0.isoformat() if events else None,
    }


def fmt_duration_ms(ms):
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(sec):02d}"


def build_html(stats, window_label, duration_str, session_labels, accounts_json):
    by_acct = stats["by_acct"]
    accts = sorted([a for a in by_acct.keys() if a != "*"], key=lambda x: int(x) if x.isdigit() else 99)

    totals = Counter()
    for a, evs in by_acct.items():
        for e, c in evs.items():
            totals[e] += c

    session_label = window_label

    # Per-account table rows
    table_rows = []
    for a in accts:
        row = by_acct.get(a, {})
        d = row.get("D", 0)
        r = row.get("R", 0)
        w = row.get("W", 0)
        k = row.get("K", 0)
        i = row.get("I", 0)
        l = row.get("L", 0)
        s_evt = row.get("S", 0)
        x = row.get("X", 0)
        z = row.get("Z", 0)
        h = row.get("H", 0)
        survival_pct = (r / max(d, 1)) * 100 if d else 0
        first = stats["by_acct_first_seen"].get(a)
        last = stats["by_acct_last_seen"].get(a)
        if first and last:
            try:
                fd = datetime.fromisoformat(first); ld = datetime.fromisoformat(last)
                active_secs = (ld - fd).total_seconds()
                active_for = f"{int(active_secs // 3600):02d}:{int((active_secs % 3600) // 60):02d}:{int(active_secs % 60):02d}"
            except Exception:
                active_for = "?"
        else:
            active_for = "?"
        unique_maps = len(stats["by_acct_maps"].get(a, {}))
        table_rows.append({
            "acct": a, "d": d, "r": r, "w": w, "k": k, "i": i, "l": l,
            "s": s_evt, "x": x, "z": z, "h": h,
            "survival": survival_pct, "active_for": active_for,
            "unique_maps": unique_maps,
        })
    table_rows.sort(key=lambda r: (-r["k"], r["d"]))

    best_idx = next((r["acct"] for r in table_rows if r["k"] > 0), None)
    least_dead = min(table_rows, key=lambda r: r["d"]) if table_rows else None
    most_dead = max(table_rows, key=lambda r: r["d"]) if table_rows else None

    # Chart data
    timeline_5 = stats["timeline_5min"]
    if timeline_5:
        max_bucket = max(timeline_5.keys())
        min_bucket = min(timeline_5.keys())
        bucket_range = list(range(min_bucket, max_bucket + 1))
    else:
        bucket_range = []
    if bucket_range:
        timeline_labels = []
        try:
            t0 = datetime.fromisoformat(stats.get("t0")) if stats.get("t0") else None
        except Exception:
            t0 = None
        for b in bucket_range:
            if t0:
                tt = t0 + timedelta(minutes=5 * b)
                timeline_labels.append(tt.strftime("%H:%M"))
            else:
                timeline_labels.append(f"+{(b - bucket_range[0]) * 5}m")
    else:
        timeline_labels = []
    timeline_d = [timeline_5.get(b, {}).get("D", 0) for b in bucket_range]
    timeline_w = [timeline_5.get(b, {}).get("W", 0) for b in bucket_range]
    timeline_k = [timeline_5.get(b, {}).get("K", 0) for b in bucket_range]
    timeline_x = [timeline_5.get(b, {}).get("X", 0) for b in bucket_range]
    timeline_m = [timeline_5.get(b, {}).get("M", 0) for b in bucket_range]
    timeline_total = [sum(timeline_5.get(b, {}).values()) for b in bucket_range]

    per_acct_d = [by_acct.get(a, {}).get("D", 0) for a in accts]
    per_acct_w = [by_acct.get(a, {}).get("W", 0) for a in accts]
    per_acct_k = [by_acct.get(a, {}).get("K", 0) for a in accts]
    per_acct_i = [by_acct.get(a, {}).get("I", 0) for a in accts]
    per_acct_x = [by_acct.get(a, {}).get("X", 0) for a in accts]
    per_acct_h = [by_acct.get(a, {}).get("H", 0) for a in accts]
    per_acct_s = [by_acct.get(a, {}).get("S", 0) for a in accts]
    per_acct_survival = []
    for a in accts:
        d = by_acct.get(a, {}).get("D", 0)
        r = by_acct.get(a, {}).get("R", 0)
        per_acct_survival.append(round((r / max(d, 1)) * 100 if d else 0, 1))

    map_state_data = {a: stats["map_state_count"].get(a, {}) for a in accts}
    map_state_loading = [map_state_data[a].get("L", 0) for a in accts]
    map_state_explorable = [map_state_data[a].get("E", 0) for a in accts]
    map_state_outpost = [map_state_data[a].get("O", 0) for a in accts]

    map_visits_top5_per_acct = {}
    for a in accts:
        m = stats["by_acct_maps"].get(a, {})
        top5 = sorted(m.items(), key=lambda x: -x[1])[:5]
        map_visits_top5_per_acct[a] = top5

    x_messages = []
    for a, msgs in stats["by_acct_x_msgs"].items():
        for iso, data in msgs:
            x_messages.append({"acct": a, "iso": iso, "data": data})
    x_messages.sort(key=lambda x: x["iso"], reverse=True)
    x_messages = x_messages[:50]

    activity_per_hour_per_acct = defaultdict(lambda: defaultdict(int))
    for a in accts:
        for evt, c in by_acct.get(a, {}).items():
            pass
    hour_buckets = sorted(stats["timeline_hour"].keys())
    hour_labels = [f"H+{b}" for b in hour_buckets]
    hour_total_events = [sum(stats["timeline_hour"][b].values()) for b in hour_buckets]
    hour_d = [stats["timeline_hour"][b].get("D", 0) for b in hour_buckets]
    hour_k = [stats["timeline_hour"][b].get("K", 0) for b in hour_buckets]

    # Honest verdict
    total_k = totals.get("K", 0)
    total_d = totals.get("D", 0)
    total_w = totals.get("W", 0)
    total_i = totals.get("I", 0)
    total_l = totals.get("L", 0)
    total_x = totals.get("X", 0)
    total_z = totals.get("Z", 0)

    verdict_class = "verdict-bad" if total_k == 0 else ("verdict-warn" if total_k < total_d else "verdict-ok")
    verdict_headline = (
        "ZERO PRODUCTIVE OUTPUT" if total_k == 0
        else f"More deaths ({total_d}) than kills ({total_k})" if total_k < total_d
        else f"Net positive: {total_k} kills vs {total_d} deaths"
    )

    # HTML
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
        .kpi .sub { color: #64748b; font-size: 12px; margin-top: 2px; }
        .kpi.bad .value { color: #ef4444; }
        .kpi.warn .value { color: #f59e0b; }
        .kpi.ok .value { color: #10b981; }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { text-align: left; padding: 8px 10px; background: #0f1419; color: #94a3b8; font-weight: 500; text-transform: uppercase; font-size: 11px; letter-spacing: 0.05em; border-bottom: 1px solid #2a3441; }
        td { padding: 8px 10px; border-bottom: 1px solid #1e293b; }
        td.num { text-align: right; font-variant-numeric: tabular-nums; }
        tr:hover { background: #1e293b; }
        .verdict { padding: 20px; border-radius: 8px; margin: 16px 0; font-size: 15px; }
        .verdict-bad { background: rgba(239, 68, 68, 0.1); border: 1px solid #ef4444; color: #fecaca; }
        .verdict-warn { background: rgba(245, 158, 11, 0.1); border: 1px solid #f59e0b; color: #fde68a; }
        .verdict-ok { background: rgba(16, 185, 129, 0.1); border: 1px solid #10b981; color: #a7f3d0; }
        .verdict h3 { margin: 0 0 8px; color: inherit; font-size: 18px; }
        canvas { max-height: 300px; }
        .chart-wrap { background: #1a2332; border: 1px solid #2a3441; border-radius: 8px; padding: 16px; }
        .chart-wrap h3 { margin-bottom: 12px; }
        .x-msg { font-family: "Cascadia Code", Consolas, monospace; background: #0f1419; padding: 8px; border-radius: 4px; font-size: 12px; margin: 4px 0; color: #fbbf24; }
        details summary { cursor: pointer; padding: 8px 0; color: #94a3b8; }
        .small { font-size: 12px; color: #64748b; }
        .pill { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 500; }
        .pill-bad { background: rgba(239, 68, 68, 0.2); color: #fecaca; }
        .pill-good { background: rgba(16, 185, 129, 0.2); color: #a7f3d0; }
        .pill-neutral { background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }
    """

    # Build per-account table HTML
    table_html_rows = ""
    for r in table_rows:
        kpill = f'<span class="pill pill-good">{r["k"]}</span>' if r["k"] > 0 else f'<span class="pill pill-bad">0</span>'
        ipill = f'<span class="pill pill-good">{r["i"]}</span>' if r["i"] > 0 else f'<span class="pill pill-bad">0</span>'
        lpill = f'<span class="pill pill-good">{r["l"]}</span>' if r["l"] > 0 else f'<span class="pill pill-bad">0</span>'
        survival_class = "pill-good" if r["survival"] >= 90 else ("pill-neutral" if r["survival"] >= 50 else "pill-bad")
        table_html_rows += f"""
        <tr>
          <td><b>Acct {html_mod.escape(r['acct'])}</b></td>
          <td class="num">{r['active_for']}</td>
          <td class="num">{kpill}</td>
          <td class="num">{r['d']}</td>
          <td class="num">{r['r']}</td>
          <td class="num">{r['w']}</td>
          <td class="num"><span class="pill {survival_class}">{r['survival']:.1f}%</span></td>
          <td class="num">{ipill}</td>
          <td class="num">{lpill}</td>
          <td class="num">{r['s']}</td>
          <td class="num">{r['h']}</td>
          <td class="num">{r['z']}</td>
          <td class="num">{r['x']}</td>
          <td class="num">{r['unique_maps']}</td>
        </tr>"""

    # Map visits per acct HTML
    map_visits_html = ""
    for a in accts:
        items = map_visits_top5_per_acct.get(a, [])
        if not items:
            continue
        list_html = "".join([f"<li>Map <b>{mid}</b> — {cnt} visits</li>" for mid, cnt in items])
        map_visits_html += f'<div class="card"><h3>Acct {html_mod.escape(a)}</h3><ul style="margin-left:20px;font-size:13px;color:#cbd5e1">{list_html}</ul></div>'

    # X messages HTML
    if x_messages:
        x_html = "<table><tr><th>Acct</th><th>Time</th><th>Message</th></tr>"
        for x in x_messages:
            x_html += f'<tr><td>{html_mod.escape(x["acct"])}</td><td class="num">{html_mod.escape(x["iso"][:19])}</td><td><code class="x-msg">{html_mod.escape(x["data"])}</code></td></tr>'
        x_html += "</table>"
    else:
        x_html = '<div class="small">No exception/error events logged this session.</div>'

    # Best/worst summary
    if best_idx:
        best_html = f'Best account by kills: <b>Acct {best_idx}</b>'
    else:
        best_html = '<span class="pill pill-bad">No account scored a single kill</span>'

    least_dead_html = f"<b>Acct {least_dead['acct']}</b> ({least_dead['d']} deaths)" if least_dead else "n/a"
    most_dead_html = f"<b>Acct {most_dead['acct']}</b> ({most_dead['d']} deaths)" if most_dead else "n/a"

    js_data = json.dumps({
        "labels": timeline_labels,
        "d": timeline_d, "w": timeline_w, "k": timeline_k, "x": timeline_x, "m": timeline_m, "total": timeline_total,
        "accts": accts,
        "per_acct_d": per_acct_d, "per_acct_w": per_acct_w, "per_acct_k": per_acct_k,
        "per_acct_i": per_acct_i, "per_acct_x": per_acct_x, "per_acct_h": per_acct_h,
        "per_acct_s": per_acct_s, "per_acct_survival": per_acct_survival,
        "map_loading": map_state_loading, "map_explorable": map_state_explorable, "map_outpost": map_state_outpost,
        "hour_labels": hour_labels, "hour_total": hour_total_events, "hour_d": hour_d, "hour_k": hour_k,
    })

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Py4GW Night-Session Report — {html_mod.escape(session_label)}</title>
<style>{css}</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
<h1>Py4GW Night-Session Report</h1>
<div class="meta">Session window: <b>{html_mod.escape(session_label)}</b> · {stats['total_events']:,} events · {len(accts)} active account slots</div>

<div class="verdict {verdict_class}">
<h3>{html_mod.escape(verdict_headline)}</h3>
{best_html}<br>
Least deaths: {least_dead_html}<br>
Most deaths: {most_dead_html}
</div>

<h2>KPIs</h2>
<div class="grid grid-4">
  <div class="kpi"><div class="label">Total events</div><div class="value">{stats['total_events']:,}</div></div>
  <div class="kpi {'bad' if total_k == 0 else 'ok'}"><div class="label">Kills (K)</div><div class="value">{total_k:,}</div></div>
  <div class="kpi bad"><div class="label">Deaths (D)</div><div class="value">{total_d:,}</div></div>
  <div class="kpi bad"><div class="label">Wipes (W)</div><div class="value">{total_w:,}</div></div>
  <div class="kpi {'bad' if total_i == 0 else 'ok'}"><div class="label">Item pickups (I)</div><div class="value">{total_i:,}</div></div>
  <div class="kpi {'bad' if total_l == 0 else 'ok'}"><div class="label">Gold events (L)</div><div class="value">{total_l:,}</div></div>
  <div class="kpi {'bad' if total_x > 0 else 'ok'}"><div class="label">Exceptions (X)</div><div class="value">{total_x:,}</div></div>
  <div class="kpi"><div class="label">Resigns (Z)</div><div class="value">{total_z:,}</div></div>
</div>

<h2>Per-Account Breakdown</h2>
<div class="card">
<table>
<thead>
<tr>
  <th>Account</th><th>Active for</th><th>Kills</th><th>Deaths</th><th>Resurr.</th><th>Wipes</th><th>Survival</th>
  <th>Items</th><th>Gold ev.</th><th>Skills</th><th>HP changes</th><th>Resigns</th><th>Errors</th><th>Maps</th>
</tr>
</thead>
<tbody>{table_html_rows}</tbody>
</table>
</div>

<h2>Activity Timeline (5-min buckets)</h2>
<div class="grid grid-2">
  <div class="chart-wrap"><h3>Total events / 5min</h3><canvas id="ch_timeline_total"></canvas></div>
  <div class="chart-wrap"><h3>Deaths · Wipes · Kills / 5min</h3><canvas id="ch_timeline_dwk"></canvas></div>
</div>

<h2>Per-Account Charts</h2>
<div class="grid grid-2">
  <div class="chart-wrap"><h3>Deaths per account</h3><canvas id="ch_d"></canvas></div>
  <div class="chart-wrap"><h3>Kills per account</h3><canvas id="ch_k"></canvas></div>
  <div class="chart-wrap"><h3>Wipes per account</h3><canvas id="ch_w"></canvas></div>
  <div class="chart-wrap"><h3>Survival ratio R/D %</h3><canvas id="ch_surv"></canvas></div>
  <div class="chart-wrap"><h3>Item pickups per account</h3><canvas id="ch_i"></canvas></div>
  <div class="chart-wrap"><h3>Skill casts per account</h3><canvas id="ch_s"></canvas></div>
</div>

<h2>Map State Distribution per Account</h2>
<div class="chart-wrap"><h3>Loading vs Explorable vs Outpost (event counts)</h3><canvas id="ch_map_state"></canvas></div>

<h2>Hourly Activity</h2>
<div class="grid grid-2">
  <div class="chart-wrap"><h3>Total events per hour</h3><canvas id="ch_hour_total"></canvas></div>
  <div class="chart-wrap"><h3>Deaths vs Kills per hour</h3><canvas id="ch_hour_dk"></canvas></div>
</div>

<h2>Top Maps Visited per Account</h2>
<div class="grid grid-3">{map_visits_html}</div>

<h2>Exception Log (last 50)</h2>
<div class="card">{x_html}</div>

<h2>Notes</h2>
<div class="card small">
This report parses <code>full_log.tsv</code> from the most recent <code>START</code> marker to end of file.
Account slots are 0-indexed by Py4GW shared-memory order, not by email — cross-reference with <code>accounts.json</code> manually if you need to know which character is which slot.
Event codes per <code>FullLogger.py</code>:
<b>D</b> death · <b>R</b> resurrect · <b>W</b> wipe · <b>K</b> kill · <b>I</b> item pickup · <b>L</b> gold · <b>S</b> skill cast · <b>H</b> HP change >20% · <b>M</b> map change · <b>C</b> combat state · <b>P</b> position snapshot · <b>X</b> exception · <b>Z</b> resign · <b>F</b> FSM step · <b>B</b> boss event · <b>T</b> strategy change.
<br><br>
<b>Important:</b> a "kill" is logged only when the local client confirms a kill, which depends on whether the client was the one credited. In multibox setups, kills are often credited to one specific client. Account slots with K=0 may still have participated in successful runs — but the explicit confirmation event was logged elsewhere.
</div>

<script>
const D = {js_data};
const palette = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'];
const grid = '#1e293b';
const tick = '#94a3b8';
Chart.defaults.color = tick;
Chart.defaults.borderColor = grid;

new Chart(document.getElementById('ch_timeline_total'), {{
  type: 'line', data: {{ labels: D.labels, datasets: [{{ label: 'Total events', data: D.total, borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.1)', fill: true, tension: 0.2 }}] }},
  options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('ch_timeline_dwk'), {{
  type: 'line', data: {{ labels: D.labels, datasets: [
    {{ label: 'Deaths', data: D.d, borderColor: '#ef4444', tension: 0.2 }},
    {{ label: 'Wipes', data: D.w, borderColor: '#f59e0b', tension: 0.2 }},
    {{ label: 'Kills', data: D.k, borderColor: '#10b981', tension: 0.2 }}
  ]}}, options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

const acctLabels = D.accts.map(a => 'Acct ' + a);
function bar(id, label, data, color) {{
  new Chart(document.getElementById(id), {{
    type: 'bar', data: {{ labels: acctLabels, datasets: [{{ label, data, backgroundColor: color }}] }},
    options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
  }});
}}
bar('ch_d', 'Deaths', D.per_acct_d, '#ef4444');
bar('ch_k', 'Kills', D.per_acct_k, '#10b981');
bar('ch_w', 'Wipes', D.per_acct_w, '#f59e0b');
bar('ch_surv', 'Survival %', D.per_acct_survival, '#3b82f6');
bar('ch_i', 'Items', D.per_acct_i, '#06b6d4');
bar('ch_s', 'Skill casts', D.per_acct_s, '#8b5cf6');

new Chart(document.getElementById('ch_map_state'), {{
  type: 'bar', data: {{ labels: acctLabels, datasets: [
    {{ label: 'Loading', data: D.map_loading, backgroundColor: '#ef4444' }},
    {{ label: 'Explorable', data: D.map_explorable, backgroundColor: '#10b981' }},
    {{ label: 'Outpost', data: D.map_outpost, backgroundColor: '#3b82f6' }}
  ]}}, options: {{ responsive: true, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('ch_hour_total'), {{
  type: 'bar', data: {{ labels: D.hour_labels, datasets: [{{ label: 'Events', data: D.hour_total, backgroundColor: '#3b82f6' }}] }},
  options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('ch_hour_dk'), {{
  type: 'bar', data: {{ labels: D.hour_labels, datasets: [
    {{ label: 'Deaths', data: D.hour_d, backgroundColor: '#ef4444' }},
    {{ label: 'Kills', data: D.hour_k, backgroundColor: '#10b981' }}
  ]}}, options: {{ responsive: true, scales: {{ y: {{ beginAtZero: true }} }} }}
}});
</script>
</body>
</html>
"""
    return html_doc


def main():
    hours_back = 14
    if len(sys.argv) > 1:
        try:
            hours_back = int(sys.argv[1])
        except ValueError:
            pass
    if not os.path.exists(LOG_PATH):
        print(f"ERROR: log not found at {LOG_PATH}", file=sys.stderr)
        sys.exit(1)
    print(f"Reading {LOG_PATH} ({os.path.getsize(LOG_PATH):,} bytes), window={hours_back}h back...")
    events, earliest_label, session_labels = parse_all_with_absolute_time(LOG_PATH, hours_back=hours_back)
    if not events:
        print("ERROR: no events in window")
        sys.exit(1)
    duration = events[-1][0] - events[0][0]
    secs = duration.total_seconds()
    duration_str = f"{int(secs // 3600):02d}:{int((secs % 3600) // 60):02d}:{int(secs % 60):02d}"
    window_label = f"{events[0][0].strftime('%Y-%m-%d %H:%M:%S')} -> {events[-1][0].strftime('%Y-%m-%d %H:%M:%S')} ({duration_str}, {len(session_labels)} FullLogger session(s))"
    try:
        print(f"Parsed {len(events):,} events spanning {window_label}")
    except UnicodeEncodeError:
        print(f"Parsed {len(events):,} events.")
    stats = aggregate(events)
    accounts = load_accounts()
    html_doc = build_html(stats, window_label, duration_str, session_labels, accounts)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_doc)
    print(f"Report written: {OUT_PATH} ({os.path.getsize(OUT_PATH):,} bytes)")


if __name__ == "__main__":
    main()
