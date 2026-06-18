#!/usr/bin/env python3
"""
Build manually refreshed research panels for the local RPS dashboard.

This script reads the already-generated RPS report CSVs and writes a cached
`research_panels.json` file into the selected report directory. Network calls
for company profiles only happen when this script is invoked explicitly.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


POLYGON_API = "https://api.polygon.io"
PANEL_FILENAME = "research_panels.json"
RPS_FIELDS = ("rps30", "rps50", "rps120", "rps250")
DEFAULT_MANUAL_LEADERS = ("NVDA", "AVGO", "PLTR")


def load_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def polygon_api_key(env_file: Path) -> str | None:
    values = load_env_values(env_file)
    return values.get("polygon_key") or values.get("POLYGON_API_KEY") or values.get("POLYGON_KEY")


def latest_report_dir(report_root: Path) -> Path | None:
    if not report_root.exists():
        return None
    dirs = [path for path in report_root.iterdir() if path.is_dir()]
    report_dirs = [path for path in dirs if (path / "us_1d_watchlist.csv").exists()]
    return max(report_dirs or dirs, default=None, key=lambda path: path.name)


def report_dir_for_date(report_root: Path, report_date: str | None) -> Path:
    if report_date:
        return report_root / report_date
    directory = latest_report_dir(report_root)
    if directory is None:
        raise FileNotFoundError(f"No report directory found in {report_root}")
    return directory


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def num(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def round1(value: float) -> float:
    return round(float(value), 1)


def ab_core_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if str(row.get("tier", "")).upper() in {"A", "B"} and truthy(row.get("core_watchlist"))
    ]


def metadata_dir(data_dir: Path) -> Path:
    return data_dir / "metadata"


def load_profile_cache(data_dir: Path) -> dict[str, dict[str, str]]:
    path = metadata_dir(data_dir) / "us_company_profiles.csv"
    profiles: dict[str, dict[str, str]] = {}
    for row in read_csv_rows(path):
        symbol = str(row.get("symbol", "")).upper()
        if symbol:
            profiles[symbol] = row
    return profiles


def save_profile_cache(data_dir: Path, profiles: dict[str, dict[str, str]]) -> None:
    path = metadata_dir(data_dir) / "us_company_profiles.csv"
    rows = [profiles[symbol] for symbol in sorted(profiles)]
    fields = [
        "symbol",
        "name",
        "sector",
        "industry",
        "sic_description",
        "description",
        "market_cap",
        "source",
        "updated_at",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def polygon_ticker_details(symbol: str, api_key: str) -> dict:
    url = (
        f"{POLYGON_API}/v3/reference/tickers/{urllib.parse.quote(symbol)}?"
        + urllib.parse.urlencode({"apiKey": api_key})
    )
    request = urllib.request.Request(url, headers={"User-Agent": "rps-panel-builder/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return payload.get("results") or {}


def profile_from_polygon(symbol: str, details: dict) -> dict[str, str]:
    return {
        "symbol": symbol.upper(),
        "name": str(details.get("name") or ""),
        "sector": str(details.get("sector") or ""),
        "industry": str(details.get("industry") or ""),
        "sic_description": str(details.get("sic_description") or ""),
        "description": str(details.get("description") or "").replace("\n", " "),
        "market_cap": str(details.get("market_cap") or ""),
        "source": "polygon",
        "updated_at": datetime.now(UTC).isoformat(),
    }


def ensure_profiles(
    symbols: list[str],
    data_dir: Path,
    api_key: str | None,
    fetch_profiles: bool,
    request_sleep: float,
) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    profiles = load_profile_cache(data_dir)
    missing = [symbol for symbol in symbols if symbol not in profiles]
    fetched = 0
    errors = 0
    if fetch_profiles and api_key:
        for symbol in missing:
            try:
                profiles[symbol] = profile_from_polygon(symbol, polygon_ticker_details(symbol, api_key))
                fetched += 1
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                errors += 1
            if request_sleep > 0:
                time.sleep(request_sleep)
        if fetched:
            save_profile_cache(data_dir, profiles)
    return profiles, {"missingBeforeFetch": len(missing), "fetched": fetched, "errors": errors}


def load_theme_overrides(data_dir: Path) -> dict[str, str]:
    path = metadata_dir(data_dir) / "us_theme_overrides.csv"
    overrides: dict[str, str] = {}
    for row in read_csv_rows(path):
        symbol = str(row.get("symbol", "")).upper()
        theme = str(row.get("theme", "")).strip()
        if symbol and theme:
            overrides[symbol] = theme
    return overrides


def infer_theme(symbol: str, profile: dict[str, str], overrides: dict[str, str]) -> str:
    if symbol in overrides:
        return overrides[symbol]
    text = " ".join(
        [
            symbol,
            profile.get("name", ""),
            profile.get("sector", ""),
            profile.get("industry", ""),
            profile.get("sic_description", ""),
            profile.get("description", ""),
        ]
    ).lower()
    if any(term in text for term in ("bitcoin", "crypto", "mining", "blockchain")):
        return "比特币矿工/加密基础设施"
    if "cyber" in text or "security" in text or "firewall" in text:
        return "网络安全/网络基础设施"
    if any(term in text for term in ("cloud", "developer", "devops", "software-as-a-service", "data processing")):
        return "云/开发者基础设施"
    if "memory" in text or "nand" in text or "storage device" in text or "hard disk" in text:
        return "存储/硬盘"
    if "semiconductor" in text or "integrated circuit" in text or "chip" in text:
        if any(term in text for term in ("equipment", "machinery", "wafer", "fabrication", "eda", "test", "assembly", "packaging")):
            return "半导体设备/EDA/封测"
        return "AI/数据中心芯片"
    if any(term in text for term in ("hospital", "medical service", "health insurer", "managed care", "healthcare services")):
        return "医疗服务/管理式医疗"
    if any(term in text for term in ("pharmaceutical", "biopharmaceutical", "biotech", "therapeutic", "drug")):
        return "生物科技/制药"
    if any(term in text for term in ("medical instrument", "diagnostic", "life science", "surgical")):
        return "医疗器械/生命科学工具"
    if any(term in text for term in ("power", "energy", "electrical", "fuel cell", "electrification")):
        return "电力/能源基础设施"
    if any(term in text for term in ("aerospace", "defense", "sensor", "automation", "electronic components")):
        return "工业自动化/传感器"
    if any(term in text for term in ("transport", "trucking", "logistics", "shipping")):
        return "物流/运输"
    if any(term in text for term in ("hotel", "travel", "airline", "leisure")):
        return "旅行/休闲"
    if any(term in text for term in ("broker", "exchange", "financial services")):
        return "金融/交易基础设施"
    if any(term in text for term in ("advertising", "media", "digital")):
        return "广告技术/数字媒体"
    if any(term in text for term in ("construction", "infrastructure")):
        return "建筑/基础设施"
    return "其他/待确认"


def median_for(rows: list[dict], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) not in ("", None)]
    return round1(statistics.median(values)) if values else 0.0


def average_for(rows: list[dict], key: str) -> float:
    values = [float(row[key]) for row in rows if row.get(key) not in ("", None)]
    return round1(sum(values) / len(values)) if values else 0.0


def build_theme_panel(rows: list[dict[str, str]], profiles: dict[str, dict[str, str]], overrides: dict[str, str]) -> dict:
    detail_rows = []
    for row in ab_core_rows(rows):
        symbol = str(row.get("symbol", "")).upper()
        profile = profiles.get(symbol, {})
        detail = {
            "symbol": symbol,
            "theme": infer_theme(symbol, profile, overrides),
            "industry": profile.get("industry") or profile.get("sic_description") or "未分类",
            "name": profile.get("name", ""),
            "tier": row.get("tier", ""),
            **{field: round1(num(row, field)) for field in RPS_FIELDS},
            "rpsMax": round1(num(row, "rps_max")),
        }
        detail_rows.append(detail)

    grouped: dict[str, list[dict]] = {}
    for detail in detail_rows:
        grouped.setdefault(detail["theme"], []).append(detail)

    summary_rows = []
    for theme, items in grouped.items():
        sorted_items = sorted(items, key=lambda item: item["rpsMax"], reverse=True)
        summary_rows.append(
            {
                "theme": theme,
                "count": len(items),
                "aCount": sum(1 for item in items if str(item["tier"]).upper() == "A"),
                "bCount": sum(1 for item in items if str(item["tier"]).upper() == "B"),
                "avgRps30": average_for(items, "rps30"),
                "avgRps50": average_for(items, "rps50"),
                "avgRps120": average_for(items, "rps120"),
                "avgRps250": average_for(items, "rps250"),
                "medianRps30": median_for(items, "rps30"),
                "medianRps50": median_for(items, "rps50"),
                "medianRps120": median_for(items, "rps120"),
                "medianRps250": median_for(items, "rps250"),
                "topSymbols": ", ".join(item["symbol"] for item in sorted_items[:10]),
            }
        )
    summary_rows.sort(key=lambda item: (item["count"], item["avgRps50"]), reverse=True)
    detail_rows.sort(key=lambda item: (item["theme"], -item["rpsMax"], item["symbol"]))
    return {"rows": summary_rows, "detailRows": detail_rows}


def current_leader_rows(rows: list[dict[str, str]], profiles: dict[str, dict[str, str]], overrides: dict[str, str]) -> list[dict]:
    leaders = []
    for row in ab_core_rows(rows):
        if num(row, "rps_max") < 97 or num(row, "rps50") < 90 or num(row, "rps120") < 85:
            continue
        symbol = str(row.get("symbol", "")).upper()
        profile = profiles.get(symbol, {})
        leaders.append(
            {
                "symbol": symbol,
                "theme": infer_theme(symbol, profile, overrides),
                "tier": row.get("tier", ""),
                "rpsMax": round1(num(row, "rps_max")),
                "rps30": round1(num(row, "rps30")),
                "rps50": round1(num(row, "rps50")),
                "rps120": round1(num(row, "rps120")),
                "rps250": round1(num(row, "rps250")),
                "status": "现任领导股",
                "reason": "A/B + Core，且多周期 RPS 维持高位",
            }
        )
    leaders.sort(key=lambda item: item["rpsMax"], reverse=True)
    return leaders[:60]


def load_manual_leaders(data_dir: Path) -> list[dict[str, str]]:
    path = metadata_dir(data_dir) / "us_manual_leaders.csv"
    manual = []
    if path.exists():
        for row in read_csv_rows(path):
            symbol = str(row.get("symbol", "")).upper()
            if symbol:
                manual.append({"symbol": symbol, "note": row.get("note", "手动观察")})
    else:
        manual = [{"symbol": symbol, "note": "默认老领导股观察"} for symbol in DEFAULT_MANUAL_LEADERS]
    return manual


def technical_status(data_dir: Path, symbol: str) -> dict[str, object]:
    rows = read_csv_rows(data_dir / "us_1d" / f"{symbol}.csv")
    if not rows:
        return {"close": None, "ma50": None, "drawdownPct": None, "status": "缺数据"}
    closes = [num(row, "close") for row in rows if row.get("close")]
    if not closes:
        return {"close": None, "ma50": None, "drawdownPct": None, "status": "缺数据"}
    close = closes[-1]
    recent_50 = closes[-50:] if len(closes) >= 50 else closes
    ma50 = sum(recent_50) / len(recent_50)
    high_window = closes[-252:] if len(closes) >= 252 else closes
    high = max(high_window)
    drawdown = (close / high - 1) * 100 if high else 0.0
    if close < ma50 * 0.98 or drawdown <= -25:
        status = "破位"
    elif close < ma50 or drawdown <= -15:
        status = "黄灯"
    else:
        status = "健康"
    return {"close": round1(close), "ma50": round1(ma50), "drawdownPct": round1(drawdown), "status": status}


def previous_leader_candidates(report_root: Path, current_date: str, current_symbols: set[str]) -> dict[str, dict]:
    candidates: dict[str, dict] = {}
    for directory in sorted([path for path in report_root.iterdir() if path.is_dir() and path.name < current_date], reverse=True)[:20]:
        for row in read_csv_rows(directory / "us_1d_watchlist.csv"):
            symbol = str(row.get("symbol", "")).upper()
            if not symbol or symbol in current_symbols:
                continue
            if str(row.get("tier", "")).upper() in {"A", "B"} and truthy(row.get("core_watchlist")) and num(row, "rps_max") >= 97:
                candidates.setdefault(
                    symbol,
                    {
                        "symbol": symbol,
                        "lastLeaderDate": directory.name,
                        "lastRpsMax": round1(num(row, "rps_max")),
                        "source": "自动",
                    },
                )
    return candidates


def build_leadership_panel(
    rows: list[dict[str, str]],
    data_dir: Path,
    report_root: Path,
    report_date: str,
    profiles: dict[str, dict[str, str]],
    overrides: dict[str, str],
) -> dict:
    current = current_leader_rows(rows, profiles, overrides)
    current_symbols = {row["symbol"] for row in current}
    former = previous_leader_candidates(report_root, report_date, current_symbols)
    for row in load_manual_leaders(data_dir):
        symbol = row["symbol"]
        if symbol not in current_symbols:
            former.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "lastLeaderDate": "",
                    "lastRpsMax": "",
                    "source": "手动",
                    "note": row.get("note", ""),
                },
            )

    former_rows = []
    for symbol, item in former.items():
        profile = profiles.get(symbol, {})
        status = technical_status(data_dir, symbol)
        former_rows.append(
            {
                "symbol": symbol,
                "theme": infer_theme(symbol, profile, overrides),
                "status": status["status"],
                "close": status["close"],
                "ma50": status["ma50"],
                "drawdownPct": status["drawdownPct"],
                "lastLeaderDate": item.get("lastLeaderDate", ""),
                "lastRpsMax": item.get("lastRpsMax", ""),
                "source": item.get("source", ""),
                "note": item.get("note", ""),
            }
        )
    former_rows.sort(key=lambda row: (row["status"] == "破位", row["status"] == "黄灯", row["symbol"]), reverse=True)
    return {"current": current, "former": former_rows}


def build_macro_regime(report_dir: Path) -> dict:
    rows = read_csv_rows(report_dir / "macro_1d_watchlist.csv")
    by_symbol = {str(row.get("symbol", "")).upper(): row for row in rows}

    def rps(symbol: str) -> float:
        return num(by_symbol.get(symbol, {}), "rps_max")

    score = 0
    indicators = []
    qqq = rps("QQQ")
    spy = rps("SPY")
    iwm = rps("IWM")
    hyg = rps("HYG")
    uup = rps("UUP")
    tlt = rps("TLT")
    gld = rps("GLD")
    btc = rps("BTCUSDT")

    if qqq >= 70:
        score += 2
        indicators.append({"name": "科技成长", "value": qqq, "signal": "顺风", "detail": "QQQ 相对强度靠前"})
    elif qqq <= 40:
        score -= 2
        indicators.append({"name": "科技成长", "value": qqq, "signal": "逆风", "detail": "QQQ 相对强度偏弱"})
    if iwm and spy and iwm + 15 < spy:
        score -= 1
        indicators.append({"name": "市场宽度", "value": round1(iwm - spy), "signal": "分化", "detail": "IWM 明显弱于 SPY"})
    if hyg >= 60:
        score += 1
        indicators.append({"name": "信用风险", "value": hyg, "signal": "稳定", "detail": "HYG 相对强度不弱"})
    elif hyg and hyg <= 40:
        score -= 2
        indicators.append({"name": "信用风险", "value": hyg, "signal": "警戒", "detail": "高收益债走弱"})
    defensive = max(uup, tlt, gld)
    if defensive >= 80:
        score -= 2
        indicators.append({"name": "防御资产", "value": defensive, "signal": "警戒", "detail": "美元/长债/黄金中有资产明显走强"})
    if btc >= 80:
        score += 1
        indicators.append({"name": "风险偏好", "value": btc, "signal": "活跃", "detail": "BTC 相对强度靠前"})

    if score >= 3:
        regime = "风险偏好"
    elif score >= 0:
        regime = "中性"
    elif score >= -2:
        regime = "风险警戒"
    else:
        regime = "风险回避"
    strongest = sorted(rows, key=lambda row: num(row, "rps_max"), reverse=True)[:3]
    strongest_text = "、".join(row.get("macro_group") or row.get("symbol", "") for row in strongest) or "暂无"
    if strongest:
        indicators.append(
            {
                "name": "最强宏观资产",
                "value": round1(num(strongest[0], "rps_max")),
                "signal": strongest[0].get("symbol", ""),
                "detail": strongest[0].get("macro_group") or strongest[0].get("asset_name") or strongest[0].get("symbol", ""),
            }
        )
    if not indicators:
        indicators.append({"name": "宏观数据", "value": "", "signal": "缺少", "detail": "尚未生成宏观 RPS 表"})
    return {
        "regime": regime,
        "score": score,
        "summary": f"当前宏观相对强度靠前的是 {strongest_text}；面板基于本地 Macro RPS，外部利率/流动性数据后续可继续接入。",
        "indicators": indicators,
        "rows": rows,
    }


def build_panels(
    data_dir: Path,
    report_root: Path,
    report_date: str | None = None,
    env_file: Path | None = None,
    fetch_profiles: bool = True,
    request_sleep: float = 0.0,
) -> dict:
    report_dir = report_dir_for_date(report_root, report_date)
    watchlist_path = report_dir / "us_1d_watchlist.csv"
    rows = read_csv_rows(watchlist_path)
    selected = ab_core_rows(rows)
    symbols = sorted({str(row.get("symbol", "")).upper() for row in selected if row.get("symbol")})
    manual_symbols = [row["symbol"] for row in load_manual_leaders(data_dir)]
    api_key = polygon_api_key(env_file) if env_file else None
    profiles, profile_stats = ensure_profiles(
        sorted(set(symbols + manual_symbols)),
        data_dir,
        api_key,
        fetch_profiles=fetch_profiles,
        request_sleep=request_sleep,
    )
    overrides = load_theme_overrides(data_dir)
    market_dates = sorted({str(row.get("date", ""))[:10] for row in rows if row.get("date")})
    payload = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "reportDate": report_dir.name,
        "marketDate": market_dates[-1] if market_dates else None,
        "source": {
            "watchlistPath": str(watchlist_path),
            "watchlistCount": len(rows),
            "abCoreCount": len(selected),
            **profile_stats,
        },
        "themePanel": build_theme_panel(rows, profiles, overrides),
        "leadership": build_leadership_panel(rows, data_dir, report_root, report_dir.name, profiles, overrides),
        "macroRegime": build_macro_regime(report_dir),
    }
    output_path = report_dir / PANEL_FILENAME
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    write_csv_rows(report_dir / "us_theme_panel.csv", payload["themePanel"]["rows"])
    write_csv_rows(report_dir / "us_theme_detail.csv", payload["themePanel"]["detailRows"])
    write_csv_rows(report_dir / "leadership_current.csv", payload["leadership"]["current"])
    write_csv_rows(report_dir / "leadership_former.csv", payload["leadership"]["former"])
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/rps_pp"))
    parser.add_argument("--report-root", type=Path, default=Path("reports/rps_pp"))
    parser.add_argument("--report-date")
    parser.add_argument("--env-file", type=Path, default=Path("docs/strategies/.env"))
    parser.add_argument("--request-sleep", type=float, default=0.0)
    parser.add_argument("--no-fetch-profiles", action="store_true")
    args = parser.parse_args()
    payload = build_panels(
        data_dir=args.data_dir,
        report_root=args.report_root,
        report_date=args.report_date,
        env_file=args.env_file,
        fetch_profiles=not args.no_fetch_profiles,
        request_sleep=args.request_sleep,
    )
    print(json.dumps({"generated_at": payload["generatedAt"], "report_date": payload["reportDate"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
