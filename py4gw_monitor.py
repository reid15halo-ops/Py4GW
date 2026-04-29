#!/usr/bin/env python3
"""
Py4GW Bot Monitor - Ueberwacht YAVB und andere Py4GW Bots

Verwendung:
    python py4gw_monitor.py --status     Einmaliger Status-Report
    python py4gw_monitor.py --watch      Kontinuierliche Ueberwachung (alle 10s)
    python py4gw_monitor.py --logs       Zeigt aktuelle Log-Eintraege
    python py4gw_monitor.py --detail     Detaillierte Account-Uebersicht
"""

import json
import os
import sys
import time
import argparse
import subprocess
from datetime import datetime
from typing import Dict, List, Optional, Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "accounts.json")
INJECTION_LOG = os.path.join(SCRIPT_DIR, "Py4GW_injection_log.txt")
PY4GW_DIR = SCRIPT_DIR


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"


def run_ps(cmd: str) -> str:
    """Führt einen PowerShell-Befehl aus und gibt die Ausgabe zurück."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    return result.stdout.strip()


def get_gw_processes() -> List[Dict[str, Any]]:
    """Findet alle laufenden Guild Wars Prozesse."""
    cmd = r"""
    Get-Process | Where-Object { $_.ProcessName -eq "Gw" } | 
    Select-Object Id, ProcessName, MainWindowTitle, @{Name="WorkingSetMB";Expression={[math]::Round($_.WorkingSet64 / 1MB, 1)}}, 
    @{Name="CPUTime";Expression={$_.TotalProcessorTime.ToString("hh\:mm\:ss")}}, 
    @{Name="StartTime";Expression={$_.StartTime.ToString("yyyy-MM-dd HH:mm:ss")}} | 
    ConvertTo-Json -Depth 3
    """
    out = run_ps(cmd)
    if not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            return [data]
        return data
    except json.JSONDecodeError:
        return []


def get_py4gw_launchers() -> List[Dict[str, Any]]:
    """Findet alle Py4GW Launcher Prozesse."""
    cmd = r"""
    Get-Process | Where-Object { $_.ProcessName -like "*Py4GW*" } | 
    Select-Object Id, ProcessName, MainWindowTitle, @{Name="StartTime";Expression={$_.StartTime.ToString("yyyy-MM-dd HH:mm:ss")}} | 
    ConvertTo-Json -Depth 3
    """
    out = run_ps(cmd)
    if not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            return [data]
        return data
    except json.JSONDecodeError:
        return []


def load_accounts() -> Dict[str, List[Dict[str, Any]]]:
    """Lädt die accounts.json."""
    if not os.path.exists(ACCOUNTS_FILE):
        return {}
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def get_last_log_lines(filepath: str, n: int = 20) -> List[str]:
    """Liest die letzten n Zeilen einer Datei."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return [line.rstrip() for line in lines[-n:]]
    except IOError:
        return []


def match_account_to_process(account: Dict[str, Any], processes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Ordnet einen Account einem laufenden GW-Prozess zu (anhand Fenstertitel/Charaktername)."""
    char_name = account.get("character_name", "")
    for proc in processes:
        title = proc.get("MainWindowTitle", "")
        if char_name and title and char_name.lower() in title.lower():
            return proc
    return None


def format_runtime(start_time_str: str) -> str:
    """Berechnet die Laufzeit seit Start."""
    try:
        start = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        delta = datetime.now() - start
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except (ValueError, TypeError):
        return "?"


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*60}{Colors.RESET}")


def print_status():
    """Gibt einen einmaligen Status-Report aus."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{Colors.BOLD}Py4GW Bot Monitor{Colors.RESET} - {Colors.DIM}{now}{Colors.RESET}")
    
    accounts = load_accounts()
    gw_procs = get_gw_processes()
    launchers = get_py4gw_launchers()
    
    print_header("LAUFENDE PROZESSE")
    
    if launchers:
        print(f"\n{Colors.GREEN}[+] Py4GW Launcher:{Colors.RESET}")
        for launcher in launchers:
            title = launcher.get("MainWindowTitle", "kein Titel")
            pid = launcher.get("Id", "?")
            started = launcher.get("StartTime", "?")
            runtime = format_runtime(started) if started != "?" else "?"
            print(f"   PID {pid}: {Colors.BOLD}{title}{Colors.RESET} (laeuft seit {runtime})")
    else:
        print(f"\n{Colors.RED}✗ Kein Py4GW Launcher aktiv{Colors.RESET}")
    
        print(f"\n{Colors.GREEN}[+] Py4GW Launcher:{Colors.RESET}")
    if gw_procs:
        for proc in gw_procs:
            pid = proc.get("Id", "?")
            title = proc.get("MainWindowTitle", "Unbenannt")
            mem = proc.get("WorkingSetMB", "?")
            started = proc.get("StartTime", "?")
            runtime = format_runtime(started) if started != "?" else "?"
            print(f"   PID {Colors.YELLOW}{pid}{Colors.RESET}: {Colors.BOLD}{title}{Colors.RESET}")
            print(f"      RAM: {mem} MB | Laufzeit: {runtime}")
    else:
        print(f"   {Colors.RED}✗ Keine Guild Wars Prozesse gefunden{Colors.RESET}")
    
    print_header("ACCOUNT-UEBERSICHT")
    
    total_accounts = 0
    running_accounts = 0
    
    for team_name, team_accounts in accounts.items():
        if not team_accounts:
            continue
        print(f"\n{Colors.BOLD}{Colors.MAGENTA}Team: {team_name}{Colors.RESET} ({len(team_accounts)} Accounts)")
        for acc in team_accounts:
            total_accounts += 1
            char_name = acc.get("character_name", "Unbekannt")
            gw_path = acc.get("gw_path", "")
            inject = acc.get("inject_py4gw", False)
            
            matched_proc = match_account_to_process(acc, gw_procs)
            if matched_proc:
                running_accounts += 1
                pid = matched_proc.get("Id", "?")
                mem = matched_proc.get("WorkingSetMB", "?")
                status_color = Colors.GREEN
                status_icon = "[+]"
                status_text = f"AKTIV (PID {pid}, {mem} MB)"
            else:
                status_color = Colors.RED
                status_icon = "[-]"
                status_text = "OFFLINE"
            
            inject_str = f"{Colors.BLUE}[Py4GW]{Colors.RESET}" if inject else ""
            print(f"  {status_color}{status_icon}{Colors.RESET} {Colors.BOLD}{char_name}{Colors.RESET} {inject_str}")
            print(f"     Status: {status_color}{status_text}{Colors.RESET}")
            print(f"     Pfad: {Colors.DIM}{gw_path}{Colors.RESET}")
    
    print(f"\n{Colors.BOLD}Zusammenfassung:{Colors.RESET} {running_accounts}/{total_accounts} Accounts aktiv | {len(gw_procs)} GW-Prozesse | {len(launchers)} Launcher")
    
    # Logs
    print_header("LETZTE INJEKTIONS-LOGS")
    log_lines = get_last_log_lines(INJECTION_LOG, 10)
    if log_lines:
        for line in log_lines:
            if "Terminating" in line:
                print(f"  {Colors.RED}{line}{Colors.RESET}")
            elif "initialization completed" in line:
                print(f"  {Colors.GREEN}{line}{Colors.RESET}")
            else:
                print(f"  {Colors.DIM}{line}{Colors.RESET}")
    else:
        print(f"  {Colors.RED}Keine Log-Datei gefunden{Colors.RESET}")


def print_detail():
    """Detaillierte Account-Informationen."""
    accounts = load_accounts()
    gw_procs = get_gw_processes()
    
    print_header("DETAILLIERTE ACCOUNT-INFORMATIONEN")
    
    for team_name, team_accounts in accounts.items():
        for acc in team_accounts:
            char_name = acc.get("character_name", "Unbekannt")
            matched = match_account_to_process(acc, gw_procs)
            
            print(f"\n{Colors.BOLD}{Colors.CYAN}{char_name}{Colors.RESET}")
            print(f"  E-Mail:     {acc.get('email', 'n/a')}")
            print(f"  GW-Pfad:    {acc.get('gw_path', 'n/a')}")
            print(f"  Client-Name:{acc.get('gw_client_name', 'n/a')}")
            print(f"  Inject Py4GW:{Colors.GREEN if acc.get('inject_py4gw') else Colors.RED}{acc.get('inject_py4gw')}{Colors.RESET}")
            print(f"  Admin:      {acc.get('run_as_admin', False)}")
            print(f"  Fenster:    {acc.get('width', '?')}x{acc.get('height', '?')} @ ({acc.get('top_left', ['?', '?'])[0]}, {acc.get('top_left', ['?', '?'])[1]})")
            
            if matched:
                print(f"  {Colors.GREEN}[+] LAEUFT:{Colors.RESET} PID={matched.get('Id')}, Titel='{matched.get('MainWindowTitle')}'")
                print(f"          RAM={matched.get('WorkingSetMB')} MB, CPU-Time={matched.get('CPUTime', '?')}")
            else:
                print(f"  {Colors.RED}[-] OFFLINE{Colors.RESET}")


def watch_mode(interval: int = 10):
    """Kontinuierliche Ueberwachung."""
    print(f"{Colors.BOLD}Py4GW Monitor Watch-Mode{Colors.RESET}")
    print(f"Aktualisierung alle {interval} Sekunden. Druecke Ctrl+C zum Beenden.\n")
    
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print_status()
            print(f"\n{Colors.DIM}[{datetime.now().strftime('%H:%M:%S')}] Naechste Aktualisierung in {interval}s... (Ctrl+C = Stop){Colors.RESET}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Monitor beendet.{Colors.RESET}")


def print_logs(lines: int = 30):
    """Zeigt Log-Einträge an."""
    print_header("PY4GW INJEKTIONS-LOG")
    log_lines = get_last_log_lines(INJECTION_LOG, lines)
    if log_lines:
        for line in log_lines:
            print(line)
    else:
        print(f"{Colors.RED}Keine Log-Datei gefunden unter:{Colors.RESET}")
        print(f"  {INJECTION_LOG}")
    
    # Auch nach Bot-spezifischen Logs suchen
    print_header("BOT-SPEZIFISCHE LOGS")
    log_files = []
    for root, dirs, files in os.walk(os.path.join(PY4GW_DIR, "Bots")):
        for f in files:
            if f.endswith(".log"):
                log_files.append(os.path.join(root, f))
    
    if log_files:
        for log_file in log_files:
            print(f"\n{Colors.CYAN}{log_file}{Colors.RESET}")
            lines_content = get_last_log_lines(log_file, 5)
            for line in lines_content:
                print(f"  {line}")
    else:
        print(f"{Colors.DIM}Keine Bot-Log-Dateien gefunden.{Colors.RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Py4GW Bot Monitor - Ueberwacht YAVB und andere Py4GW Bots",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python py4gw_monitor.py --status
  python py4gw_monitor.py --watch --interval 5
  python py4gw_monitor.py --logs --lines 50
  python py4gw_monitor.py --detail
        """
    )
    parser.add_argument("--status", action="store_true", help="Einmaliger Status-Report (Standard)")
    parser.add_argument("--watch", action="store_true", help="Kontinuierliche Ueberwachung")
    parser.add_argument("--interval", type=int, default=10, help="Aktualisierungsintervall in Sekunden (Default: 10)")
    parser.add_argument("--logs", action="store_true", help="Zeigt aktuelle Log-Einträge")
    parser.add_argument("--lines", type=int, default=30, help="Anzahl der Log-Zeilen (Default: 30)")
    parser.add_argument("--detail", action="store_true", help="Detaillierte Account-Uebersicht")
    
    args = parser.parse_args()
    
    if args.watch:
        watch_mode(args.interval)
    elif args.logs:
        print_logs(args.lines)
    elif args.detail:
        print_detail()
    else:
        print_status()


if __name__ == "__main__":
    main()
