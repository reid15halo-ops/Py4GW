"""FarmStatsDashboard — generates a modern HTML dashboard from farm_stats.jsonl.

Pure stdlib + Chart.js CDN. No pip dependencies.
Called by FarmRunTracker.end_run() after each completed run.
Can also be run standalone: python FarmStatsDashboard.py
"""

import json
import os
import msvcrt
import time

from Widgets.Automation.Helpers.FarmStats import (
    read_all_records, compute_aggregates, compute_per_account, compute_per_bot,
    DEFAULT_STATS_PATH, DEFAULT_DASHBOARD_PATH,
)


def generate_dashboard(stats_path: str = DEFAULT_STATS_PATH,
                       output_path: str = DEFAULT_DASHBOARD_PATH) -> bool:
    """Read all records and write a self-contained HTML dashboard."""
    records = read_all_records(stats_path)
    html = _build_html(records)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tmp_path = output_path + ".tmp"
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(html)
        os.replace(tmp_path, output_path)
        return True
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return False


def _build_html(records: list) -> str:
    """Assemble the complete HTML dashboard string."""
    overall = compute_aggregates(records)
    per_account = compute_per_account(records)
    per_bot = compute_per_bot(records)

    # Prepare chart data
    recent_50 = records[-50:] if len(records) > 50 else records

    # Run time over time (all records, last 200)
    chart_records = records[-200:] if len(records) > 200 else records
    run_times = [r.get("run_time_ms", 0) / 1000 for r in chart_records]
    run_labels = list(range(1, len(chart_records) + 1))
    run_success = [r.get("success", False) for r in chart_records]
    run_colors = ["#4ade80" if s else "#f87171" for s in run_success]

    # Gold per run
    gold_per_run = [r.get("gold_gained", 0) for r in chart_records]

    # Cumulative gold
    cum_gold = []
    total = 0
    for r in records:
        total += r.get("gold_gained", 0)
        cum_gold.append(total)

    # Strategy breakdown
    strategies = {}
    for r in records:
        s = r.get("strategy", "unknown")
        if s not in strategies:
            strategies[s] = {"wins": 0, "total": 0, "times": []}
        strategies[s]["total"] += 1
        if r.get("success"):
            strategies[s]["wins"] += 1
        if r.get("run_time_ms", 0) > 0:
            strategies[s]["times"].append(r["run_time_ms"] / 1000)
    strat_names = list(strategies.keys())
    strat_rates = [strategies[s]["wins"] / strategies[s]["total"] * 100 if strategies[s]["total"] else 0 for s in strat_names]
    strat_counts = [strategies[s]["total"] for s in strat_names]

    # Per-account items picked
    acct_labels = []
    acct_items = []
    acct_gold = []
    acct_greens = []
    for email, agg in sorted(per_account.items()):
        short = email.split("@")[0] if "@" in email else email
        acct_labels.append(short)
        acct_items.append(agg["total_items_picked"])
        acct_gold.append(agg["total_gold"])
        acct_greens.append(agg["total_greens"])

    # Green drops timeline
    green_events = []
    for i, r in enumerate(records):
        for g in r.get("green_drops", []):
            green_events.append({
                "run": i + 1,
                "name": g,
                "account": r.get("account_email", "").split("@")[0],
                "time": r.get("timestamp", ""),
            })

    # Hourly heatmap
    hourly = [0] * 24
    for r in records:
        ts = r.get("timestamp", "")
        if "T" in ts:
            try:
                hour = int(ts.split("T")[1][:2])
                hourly[hour] += 1
            except (ValueError, IndexError):
                pass

    # Recent runs table
    recent_rows = []
    for r in reversed(recent_50):
        recent_rows.append({
            "time": r.get("timestamp", "")[:19].replace("T", " "),
            "account": r.get("account_email", "").split("@")[0] if "@" in r.get("account_email", "") else r.get("character_name", ""),
            "bot": r.get("bot_name", ""),
            "strategy": r.get("strategy", ""),
            "result": "WIN" if r.get("success") else "FAIL",
            "duration": f"{r.get('run_time_ms', 0) / 1000:.0f}s",
            "gold": f"{r.get('gold_gained', 0):,}",
            "items": r.get("items_picked_up", 0),
            "greens": ", ".join(r.get("green_drops", [])) or "-",
        })

    data_json = json.dumps({
        "run_labels": run_labels,
        "run_times": run_times,
        "run_colors": run_colors,
        "gold_per_run": gold_per_run,
        "cum_gold": cum_gold,
        "cum_labels": list(range(1, len(cum_gold) + 1)),
        "strat_names": strat_names,
        "strat_rates": strat_rates,
        "strat_counts": strat_counts,
        "acct_labels": acct_labels,
        "acct_items": acct_items,
        "acct_gold": acct_gold,
        "acct_greens": acct_greens,
        "hourly": hourly,
        "green_events": green_events,
    })

    # Summary card values
    sr = f"{overall['success_rate'] * 100:.1f}"
    avg_t = f"{overall['avg_time_s']:.0f}"
    med_t = f"{overall['median_time_s']:.0f}"
    avg_g = f"{overall['avg_gold']:.0f}"
    med_g = f"{overall['median_gold']:.0f}"
    gph = f"{overall['gold_per_hour']:,.0f}"
    tot_g = f"{overall['total_gold']:,}"
    tot_greens = str(overall['total_greens'])
    green_rate = f"{overall['total_greens'] / overall['total_runs'] * 100:.1f}" if overall['total_runs'] else "0.0"
    items_run = f"{overall['items_per_run']:.1f}"

    # Per-bot table rows
    bot_rows_html = ""
    for bname, agg in sorted(per_bot.items()):
        bot_rows_html += f"""<tr>
            <td>{bname}</td><td>{agg['total_runs']}</td>
            <td>{agg['success_rate']*100:.1f}%</td>
            <td>{agg['avg_time_s']:.0f}s</td><td>{agg['median_time_s']:.0f}s</td>
            <td>{agg['avg_gold']:.0f}</td><td>{agg['total_gold']:,}</td>
            <td>{agg['total_greens']}</td>
        </tr>"""

    # Per-account table rows
    acct_rows_html = ""
    for email, agg in sorted(per_account.items()):
        short = email.split("@")[0] if "@" in email else email
        acct_rows_html += f"""<tr>
            <td>{short}</td><td>{agg['total_runs']}</td>
            <td>{agg['success_rate']*100:.1f}%</td>
            <td>{agg['avg_time_s']:.0f}s</td>
            <td>{agg['avg_gold']:.0f}</td><td>{agg['total_gold']:,}</td>
            <td>{agg['total_items_picked']}</td><td>{agg['total_greens']}</td>
        </tr>"""

    # Recent runs table rows
    recent_html = ""
    for row in recent_rows:
        result_class = "win" if row["result"] == "WIN" else "fail"
        recent_html += f"""<tr class="{result_class}">
            <td>{row['time']}</td><td>{row['account']}</td><td>{row['bot']}</td>
            <td>{row['strategy']}</td><td>{row['result']}</td><td>{row['duration']}</td>
            <td>{row['gold']}</td><td>{row['items']}</td><td>{row['greens']}</td>
        </tr>"""

    # Green drops log
    green_log_html = ""
    for ge in reversed(green_events[-20:]):
        green_log_html += f"""<tr>
            <td>#{ge['run']}</td><td>{ge['time'][:19].replace('T',' ')}</td>
            <td>{ge['account']}</td><td class="green-name">{ge['name']}</td>
        </tr>"""

    generated_at = records[-1]["timestamp"][:19].replace("T", " ") if records else "N/A"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="60">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Py4GW Farm Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {{
    --bg: #0f1117; --surface: #1a1d27; --surface2: #252830;
    --border: #2d3040; --text: #e4e4e7; --text2: #9ca3af;
    --green: #4ade80; --red: #f87171; --gold: #fbbf24;
    --blue: #60a5fa; --purple: #a78bfa; --cyan: #22d3ee;
    --ci-green: #B8C400; --ci-orange: #E86A10;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    background: var(--bg); color: var(--text);
    padding: 20px; line-height: 1.5;
}}
h1 {{ font-size: 1.8rem; margin-bottom: 4px; color: var(--ci-green); }}
.subtitle {{ color: var(--text2); font-size: 0.85rem; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.card {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
}}
.card .label {{ font-size: 0.75rem; color: var(--text2); text-transform: uppercase; letter-spacing: 0.5px; }}
.card .value {{ font-size: 1.6rem; font-weight: 700; margin-top: 4px; }}
.card .sub {{ font-size: 0.8rem; color: var(--text2); margin-top: 2px; }}
.v-green {{ color: var(--green); }}
.v-gold {{ color: var(--gold); }}
.v-blue {{ color: var(--blue); }}
.v-red {{ color: var(--red); }}
.v-purple {{ color: var(--purple); }}
.v-cyan {{ color: var(--cyan); }}
.charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
.chart-box {{
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 16px;
}}
.chart-box h3 {{ font-size: 0.9rem; color: var(--text2); margin-bottom: 10px; }}
.chart-box canvas {{ width: 100% !important; max-height: 260px; }}
.full-width {{ grid-column: 1 / -1; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
th {{ text-align: left; padding: 8px 10px; background: var(--surface2); color: var(--text2);
     font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 7px 10px; border-bottom: 1px solid var(--border); }}
tr.win td:nth-child(5) {{ color: var(--green); font-weight: 600; }}
tr.fail td:nth-child(5) {{ color: var(--red); font-weight: 600; }}
.green-name {{ color: var(--green); font-weight: 600; }}
.section {{ margin-bottom: 24px; }}
.section h2 {{ font-size: 1.1rem; margin-bottom: 12px; color: var(--text); }}
.heatmap {{ display: grid; grid-template-columns: repeat(24, 1fr); gap: 3px; }}
.heatmap-cell {{
    aspect-ratio: 1; border-radius: 4px; display: flex; align-items: center;
    justify-content: center; font-size: 0.65rem; color: var(--text2);
}}
@media (max-width: 900px) {{ .charts {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>

<h1>Py4GW Farm Dashboard</h1>
<div class="subtitle">Last updated: {generated_at} &bull; {overall['total_runs']} total runs &bull; Auto-refreshes every 60s</div>

<!-- Summary Cards -->
<div class="cards">
    <div class="card"><div class="label">Total Runs</div><div class="value v-blue">{overall['total_runs']}</div><div class="sub">{overall['wins']}W / {overall['losses']}L</div></div>
    <div class="card"><div class="label">Success Rate</div><div class="value v-green">{sr}%</div></div>
    <div class="card"><div class="label">Avg / Median Time</div><div class="value v-cyan">{avg_t}s</div><div class="sub">Median: {med_t}s</div></div>
    <div class="card"><div class="label">Avg / Median Gold</div><div class="value v-gold">{avg_g}</div><div class="sub">Median: {med_g}</div></div>
    <div class="card"><div class="label">Total Gold</div><div class="value v-gold">{tot_g}</div><div class="sub">{gph}/hr</div></div>
    <div class="card"><div class="label">Items / Run</div><div class="value v-purple">{items_run}</div><div class="sub">{overall['total_items_picked']} total</div></div>
    <div class="card"><div class="label">Green Drops</div><div class="value v-green">{tot_greens}</div><div class="sub">{green_rate}% drop rate</div></div>
</div>

<!-- Charts -->
<div class="charts">
    <div class="chart-box"><h3>Run Time (last 200)</h3><canvas id="chartTime"></canvas></div>
    <div class="chart-box"><h3>Gold Per Run (last 200)</h3><canvas id="chartGold"></canvas></div>
    <div class="chart-box"><h3>Cumulative Gold (all time)</h3><canvas id="chartCumGold"></canvas></div>
    <div class="chart-box"><h3>Strategy Success Rate</h3><canvas id="chartStrat"></canvas></div>
    <div class="chart-box"><h3>Items Picked Per Account</h3><canvas id="chartAcctItems"></canvas></div>
    <div class="chart-box"><h3>Gold Per Account</h3><canvas id="chartAcctGold"></canvas></div>
    <div class="chart-box full-width">
        <h3>Activity by Hour of Day</h3>
        <div class="heatmap" id="heatmap"></div>
    </div>
</div>

<!-- Per-Bot Table -->
<div class="section">
    <h2>Per-Bot Breakdown</h2>
    <div class="chart-box">
        <table>
            <thead><tr><th>Bot</th><th>Runs</th><th>Win%</th><th>Avg Time</th><th>Med Time</th><th>Avg Gold</th><th>Total Gold</th><th>Greens</th></tr></thead>
            <tbody>{bot_rows_html}</tbody>
        </table>
    </div>
</div>

<!-- Per-Account Table -->
<div class="section">
    <h2>Per-Account Breakdown</h2>
    <div class="chart-box">
        <table>
            <thead><tr><th>Account</th><th>Runs</th><th>Win%</th><th>Avg Time</th><th>Avg Gold</th><th>Total Gold</th><th>Items</th><th>Greens</th></tr></thead>
            <tbody>{acct_rows_html}</tbody>
        </table>
    </div>
</div>

<!-- Green Drops Log -->
<div class="section">
    <h2>Green Drops (last 20)</h2>
    <div class="chart-box">
        <table>
            <thead><tr><th>Run#</th><th>Time</th><th>Account</th><th>Item</th></tr></thead>
            <tbody>{green_log_html if green_log_html else '<tr><td colspan="4" style="color:var(--text2)">No green drops yet</td></tr>'}</tbody>
        </table>
    </div>
</div>

<!-- Recent Runs -->
<div class="section">
    <h2>Recent Runs (last 50)</h2>
    <div class="chart-box">
        <table>
            <thead><tr><th>Time</th><th>Account</th><th>Bot</th><th>Strategy</th><th>Result</th><th>Duration</th><th>Gold</th><th>Items</th><th>Greens</th></tr></thead>
            <tbody>{recent_html if recent_html else '<tr><td colspan="9" style="color:var(--text2)">No runs recorded yet</td></tr>'}</tbody>
        </table>
    </div>
</div>

<script>
const D = {data_json};
const chartOpts = {{
    responsive: true,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ mode: 'index' }} }},
    scales: {{
        x: {{ grid: {{ color: '#2d3040' }}, ticks: {{ color: '#9ca3af', maxTicksLimit: 15 }} }},
        y: {{ grid: {{ color: '#2d3040' }}, ticks: {{ color: '#9ca3af' }} }}
    }}
}};

// Run Time
new Chart(document.getElementById('chartTime'), {{
    type: 'bar', data: {{
        labels: D.run_labels,
        datasets: [{{ data: D.run_times, backgroundColor: D.run_colors, borderRadius: 2 }}]
    }}, options: {{ ...chartOpts, plugins: {{ ...chartOpts.plugins, tooltip: {{ callbacks: {{
        label: ctx => ctx.raw.toFixed(0) + 's'
    }} }} }} }}
}});

// Gold per run
new Chart(document.getElementById('chartGold'), {{
    type: 'bar', data: {{
        labels: D.run_labels,
        datasets: [{{ data: D.gold_per_run, backgroundColor: '#fbbf24aa', borderRadius: 2 }}]
    }}, options: chartOpts
}});

// Cumulative gold
new Chart(document.getElementById('chartCumGold'), {{
    type: 'line', data: {{
        labels: D.cum_labels,
        datasets: [{{ data: D.cum_gold, borderColor: '#fbbf24', backgroundColor: '#fbbf2422',
                      fill: true, tension: 0.3, pointRadius: 0 }}]
    }}, options: chartOpts
}});

// Strategy success rate
new Chart(document.getElementById('chartStrat'), {{
    type: 'bar', data: {{
        labels: D.strat_names,
        datasets: [{{
            data: D.strat_rates,
            backgroundColor: ['#4ade80', '#60a5fa', '#fbbf24', '#a78bfa', '#f87171', '#22d3ee'],
            borderRadius: 4
        }}]
    }}, options: {{
        ...chartOpts, indexAxis: 'y',
        scales: {{ x: {{ ...chartOpts.scales.x, max: 100, ticks: {{ ...chartOpts.scales.x.ticks, callback: v => v + '%' }} }}, y: chartOpts.scales.y }},
        plugins: {{ ...chartOpts.plugins, tooltip: {{ callbacks: {{ label: ctx => ctx.raw.toFixed(1) + '% (' + D.strat_counts[ctx.dataIndex] + ' runs)' }} }} }}
    }}
}});

// Account items
new Chart(document.getElementById('chartAcctItems'), {{
    type: 'bar', data: {{
        labels: D.acct_labels,
        datasets: [{{ data: D.acct_items, backgroundColor: '#a78bfaaa', borderRadius: 4 }}]
    }}, options: chartOpts
}});

// Account gold
new Chart(document.getElementById('chartAcctGold'), {{
    type: 'bar', data: {{
        labels: D.acct_labels,
        datasets: [{{ data: D.acct_gold, backgroundColor: '#fbbf24aa', borderRadius: 4 }}]
    }}, options: chartOpts
}});

// Heatmap
const hm = document.getElementById('heatmap');
const maxH = Math.max(...D.hourly, 1);
for (let h = 0; h < 24; h++) {{
    const cell = document.createElement('div');
    cell.className = 'heatmap-cell';
    const intensity = D.hourly[h] / maxH;
    const r = Math.round(74 + intensity * 100);
    const g = Math.round(222 * intensity);
    const b = Math.round(128 * intensity);
    cell.style.background = `rgba(${{r}}, ${{g}}, ${{b}}, ${{0.15 + intensity * 0.85}})`;
    cell.textContent = h + 'h';
    cell.title = D.hourly[h] + ' runs';
    hm.appendChild(cell);
}}
</script>
</body>
</html>"""


# Allow standalone execution
if __name__ == "__main__":
    generate_dashboard()
    print(f"Dashboard generated at: {DEFAULT_DASHBOARD_PATH}")
