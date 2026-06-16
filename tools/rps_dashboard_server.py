#!/usr/bin/env python3
"""
Local dashboard server for RPS + pocket pivot reports.

The server binds to 127.0.0.1 only. The refresh endpoint runs the fixed daily
runner command; it does not accept arbitrary shell commands from the browser.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = PROJECT_ROOT / "reports" / "rps_pp"
STATIC_ROOT = PROJECT_ROOT / "rps-dashboard"
REFRESH_ACTIONS = {
    "us-backfill": ("us", "backfill"),
    "crypto-backfill": ("crypto", "backfill"),
    "macro-backfill": ("macro", "backfill"),
    "us-repair-missing": ("us", "repair-missing"),
    "us-rank": ("us", "scan-only"),
    "crypto-rank": ("crypto", "scan-only"),
    "macro-rank": ("macro", "scan-only"),
}


def python_can_import(executable: str, module: str) -> bool:
    try:
        result = subprocess.run(
            [executable, "-c", f"import {module}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0


def runner_python_candidates() -> list[str]:
    candidates = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        str(PROJECT_ROOT / ".venv" / "bin" / "python3"),
        sys.executable,
        shutil.which("python3") or "",
        shutil.which("python") or "",
        str(Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "bin" / "python3"),
    ]
    deduped = []
    seen = set()
    for candidate in candidates:
        if candidate and candidate not in seen and Path(candidate).exists():
            seen.add(candidate)
            deduped.append(candidate)
    return deduped


def resolve_runner_python(
    args: argparse.Namespace,
    candidates: list[str] | None = None,
    can_import=python_can_import,
) -> str:
    explicit = getattr(args, "runner_python", None) or os.environ.get("RPS_RUNNER_PYTHON")
    if explicit:
        return str(explicit)
    for candidate in candidates or runner_python_candidates():
        if can_import(candidate, "pandas"):
            return candidate
    return sys.executable


def latest_report_dir(report_root: Path = DEFAULT_REPORT_ROOT) -> Path | None:
    if not report_root.exists():
        return None
    dirs = [path for path in report_root.iterdir() if path.is_dir()]
    return max(dirs, default=None, key=lambda path: path.name)


def available_report_dates(report_root: Path = DEFAULT_REPORT_ROOT) -> list[str]:
    if not report_root.exists():
        return []
    return sorted(path.name for path in report_root.iterdir() if path.is_dir())


def report_dir_for_date(report_root: Path, report_date: str | None) -> Path | None:
    if report_date:
        directory = report_root / report_date
        return directory if directory.exists() and directory.is_dir() else None
    return latest_report_dir(report_root)


def read_csv_records(path: Path, limit: int = 500) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    def tier_rank(row: dict[str, str]) -> int:
        return {"A": 0, "B": 1, "C": 2}.get(str(row.get("tier", "Z")).upper(), 9)

    rows.sort(key=lambda row: (row.get("date", ""), -tier_rank(row), float(row.get("rps_max") or 0)), reverse=True)
    return rows[:limit]


def count_csv_records(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def read_universe_symbols(data_dir: Path) -> list[str]:
    path = data_dir / "universe" / "polygon_common_stocks.csv"
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        rows = csv.DictReader(handle)
        return sorted({(row.get("Ticker") or row.get("ticker") or "").upper() for row in rows if row})


def csv_history_summary(directory: Path, min_bars: int = 250) -> dict:
    csv_paths = sorted(directory.glob("*.csv")) if directory.exists() else []
    latest_dates = []
    short_count = 0
    rankable_count = 0
    for path in csv_paths:
        try:
            with path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
        except Exception:
            rows = []
        if len(rows) >= min_bars:
            rankable_count += 1
        else:
            short_count += 1
        if rows and rows[-1].get("date"):
            latest_dates.append(rows[-1]["date"][:10])
    return {
        "localCsvCount": len(csv_paths),
        "shortHistoryCount": short_count,
        "rankableHistoryCount": rankable_count,
        "latestDate": max(latest_dates) if latest_dates else None,
    }


def macro_history_summary(directory: Path, min_bars: int = 120) -> dict:
    summary = csv_history_summary(directory, min_bars=min_bars)
    csv_paths = sorted(directory.glob("*.csv")) if directory.exists() else []
    common_dates: set[str] | None = None
    for path in csv_paths:
        try:
            with path.open(newline="") as handle:
                dates = {row.get("date", "")[:10] for row in csv.DictReader(handle) if row.get("date")}
        except Exception:
            dates = set()
        common_dates = dates if common_dates is None else common_dates & dates
    if common_dates:
        summary["latestDate"] = max(common_dates)
    return summary


def health_status(missing_count: int, universe_count: int) -> str:
    if universe_count <= 0:
        return "unknown"
    missing_ratio = missing_count / universe_count
    if missing_ratio < 0.02:
        return "ok"
    if missing_ratio < 0.10:
        return "warn"
    return "alert"


def data_health_payload(data_dir: Path) -> dict:
    universe_symbols = read_universe_symbols(data_dir)
    us_summary = csv_history_summary(data_dir / "us_1d", min_bars=250)
    local_us_symbols = {path.stem.upper() for path in (data_dir / "us_1d").glob("*.csv")} if (data_dir / "us_1d").exists() else set()
    universe_set = set(universe_symbols)
    missing_count = len(universe_set - local_us_symbols) if universe_set else 0
    us = {
        "universeCount": len(universe_symbols),
        "missingCsvCount": missing_count,
        "status": health_status(missing_count, len(universe_symbols)),
        **us_summary,
    }
    crypto = {
        **csv_history_summary(data_dir / "crypto_4h", min_bars=180),
    }
    crypto["status"] = "ok" if crypto["localCsvCount"] else "unknown"
    macro = {
        **macro_history_summary(data_dir / "macro_1d", min_bars=120),
    }
    macro["status"] = "ok" if macro["localCsvCount"] else "unknown"
    return {"us": us, "crypto": crypto, "macro": macro}


def table_payload(report_root: Path = DEFAULT_REPORT_ROOT, limit: int = 500, report_date: str | None = None) -> dict:
    directory = report_dir_for_date(report_root, report_date)
    if directory is None:
        return {"reportDate": report_date, "marketDate": None, "availableReports": available_report_dates(report_root), "tables": {}, "summary": {}}

    table_names = [
        "us_1d_watchlist",
        "us_1d_signals",
        "crypto_4h_watchlist",
        "crypto_4h_signals",
        "macro_1d_watchlist",
        "macro_1d_signals",
    ]
    tables = {}
    summary = {}
    market_dates = []
    for name in table_names:
        path = directory / f"{name}.csv"
        rows = read_csv_records(path, limit=limit)
        tables[name] = {"path": str(path), "rows": rows}
        summary[name] = count_csv_records(path)
        market_dates.extend(row.get("date", "")[:10] for row in rows if row.get("date"))
    return {
        "reportDate": directory.name,
        "marketDate": max(market_dates) if market_dates else None,
        "availableReports": available_report_dates(report_root),
        "tables": tables,
        "summary": summary,
    }


class RefreshState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.exit_code: int | None = None
        self.error: str | None = None
        self.logs: deque[str] = deque(maxlen=300)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "exitCode": self.exit_code,
                "error": self.error,
                "logs": list(self.logs),
            }

    def append_log(self, line: str) -> None:
        with self.lock:
            self.logs.append(line.rstrip())

    def start(self) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.started_at = datetime.now(UTC).isoformat()
            self.finished_at = None
            self.exit_code = None
            self.error = None
            self.logs.clear()
            return True

    def finish(self, exit_code: int, error: str | None = None) -> None:
        with self.lock:
            self.running = False
            self.finished_at = datetime.now(UTC).isoformat()
            self.exit_code = exit_code
            self.error = error


def refresh_command(args: argparse.Namespace) -> list[str]:
    command = [
        resolve_runner_python(args),
        str(PROJECT_ROOT / "tools" / "rps_daily_runner.py"),
        "--markets",
        args.markets,
        "--operation",
        args.operation,
        "--data-dir",
        str(args.data_dir),
        "--output-dir",
        str(args.report_root),
        "--env-file",
        str(args.env_file),
    ]
    should_include_update_flags = args.operation in {"update-and-scan", "backfill", "repair-missing"}
    if should_include_update_flags and "us" in {item.strip() for item in args.markets.split(",")}:
        command.extend(
            [
                "--us-universe",
                args.us_universe,
                "--us-provider",
                args.us_provider,
                "--us-request-sleep",
                str(args.us_request_sleep),
                "--us-lookback-days",
                str(args.us_lookback_days),
            ]
        )
        if args.force_backfill:
            command.append("--force-backfill")
    selected_markets = {item.strip() for item in args.markets.split(",")}
    if "crypto" in selected_markets:
        command.extend(["--crypto-timeframes", args.crypto_timeframes])
    if should_include_update_flags and "crypto" in selected_markets:
        command.extend(
            [
                "--crypto-limit",
                str(args.crypto_limit),
                "--crypto-lookback-days",
                str(args.crypto_lookback_days),
            ]
        )
    if should_include_update_flags and "macro" in {item.strip() for item in args.markets.split(",")}:
        command.extend(
            [
                "--macro-lookback-days",
                str(args.macro_lookback_days),
                "--us-request-sleep",
                str(args.us_request_sleep),
            ]
        )
    return command


def refresh_args_for_action(args: argparse.Namespace, action: str | None) -> argparse.Namespace:
    if action is None:
        return args
    if action not in REFRESH_ACTIONS:
        raise ValueError(f"Unsupported refresh action: {action}")
    market, operation = REFRESH_ACTIONS[action]
    scoped = copy.copy(args)
    scoped.markets = market
    scoped.operation = operation
    return scoped


def run_refresh(args: argparse.Namespace, state: RefreshState) -> None:
    if not state.start():
        return
    command = refresh_command(args)
    state.append_log("$ " + " ".join(command))
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            state.append_log(line)
        exit_code = process.wait()
        state.finish(exit_code, None if exit_code == 0 else f"refresh exited with {exit_code}")
    except Exception as error:
        state.append_log(f"error: {error}")
        state.finish(1, str(error))


def content_type(path: Path) -> str:
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "application/javascript; charset=utf-8"
    if path.suffix == ".csv":
        return "text/csv; charset=utf-8"
    return "application/octet-stream"


def make_handler(args: argparse.Namespace, state: RefreshState):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, payload: dict, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/status":
                self.send_json(state.snapshot())
                return
            if parsed.path == "/api/tables":
                params = parse_qs(parsed.query)
                limit = int(params.get("limit", ["500"])[0])
                report_date = params.get("date", [None])[0]
                self.send_json(table_payload(args.report_root, limit=limit, report_date=report_date))
                return
            if parsed.path == "/api/health":
                self.send_json(data_health_payload(args.data_dir))
                return
            if parsed.path == "/api/reports":
                self.send_json({"reports": available_report_dates(args.report_root)})
                return
            if parsed.path == "/api/download":
                params = parse_qs(parsed.query)
                file_path = Path(params.get("path", [""])[0])
                try:
                    resolved = file_path.resolve()
                    if not str(resolved).startswith(str(args.report_root.resolve())):
                        raise ValueError("outside report directory")
                    data = resolved.read_bytes()
                except Exception:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type(resolved))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            static_path = STATIC_ROOT / ("index.html" if parsed.path == "/" else parsed.path.lstrip("/"))
            if not static_path.exists() or not static_path.is_file():
                self.send_error(404)
                return
            data = static_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type(static_path))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/refresh":
                self.send_error(404)
                return
            if state.snapshot()["running"]:
                self.send_json({"started": False, "reason": "already running"}, status=409)
                return
            params = parse_qs(parsed.query)
            action = params.get("action", [None])[0]
            try:
                refresh_args = refresh_args_for_action(args, action)
            except ValueError as error:
                self.send_json({"started": False, "error": str(error)}, status=400)
                return
            thread = threading.Thread(target=run_refresh, args=(refresh_args, state), daemon=True)
            thread.start()
            self.send_json({"started": True})

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--markets", default="us")
    parser.add_argument("--operation", choices=("update-and-scan", "backfill", "scan-only", "repair-missing"), default="update-and-scan")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data" / "rps_pp")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / "docs" / "strategies" / ".env")
    parser.add_argument("--us-universe", choices=("iwv", "polygon-common", "sptm", "config", "nasdaq"), default="polygon-common")
    parser.add_argument("--us-provider", choices=("polygon-grouped", "polygon", "yahoo"), default="polygon-grouped")
    parser.add_argument("--us-request-sleep", type=float, default=12.0)
    parser.add_argument("--us-lookback-days", type=int, default=420)
    parser.add_argument("--force-backfill", action="store_true")
    parser.add_argument("--crypto-limit", type=int, default=200)
    parser.add_argument("--crypto-timeframes", default="4h")
    parser.add_argument("--crypto-lookback-days", type=int, default=420)
    parser.add_argument("--macro-lookback-days", type=int, default=420)
    parser.add_argument(
        "--runner-python",
        help="Python executable used for refresh jobs. Defaults to the first local Python that can import pandas.",
    )
    args = parser.parse_args()

    state = RefreshState()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(args, state))
    print(f"RPS dashboard: http://{args.host}:{args.port}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
