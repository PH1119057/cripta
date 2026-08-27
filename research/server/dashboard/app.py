from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
import psycopg
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

DATA_ROOT = Path(os.environ.get("CRIPTA_DATA_ROOT", "/data/cripta"))
APP_ROOT = Path(os.environ.get("CRIPTA_APP_ROOT", "/srv/cripta"))
PERIOD = "20260518_20260816"
STATE = DATA_ROOT / "datasets" / "raw" / PERIOD / "download_state.json"
EXPANSION_STATE = DATA_ROOT / "datasets" / "raw" / PERIOD / "download_state_expansion_20260823.json"
REPORT_ROOT = Path(os.environ.get("CRIPTA_REPORT_ROOT", "/srv/cripta-share/reports"))
CONNECTIVITY_STATE = Path("/var/lib/cripta/connectivity/status.json")
PRIVATE_API_STATE = Path("/var/lib/cripta/connectivity/private_api.json")
SAFETY_STATE = Path("/var/lib/cripta/safety/latest.json")
BACKUP_STATE = Path("/var/lib/cripta/backup/latest.json")
PRIVATE_RUNTIME_STATE = Path("/var/lib/cripta/private_runtime/status.json")
HEALTH_STATE = Path("/var/lib/cripta/health/status.json")
ALLOWED_SERVICES = (
    "cripta-dashboard.service",
    "cripta-download-frozen.service",
    "cripta-download-expansion.service",
    "cripta-job-intake.service",
    "cripta-job-runner.service",
    "cripta-bybit-latency.service",
    "cripta-safety-observer.service",
    "cripta-private-runtime.service",
    "cripta-health-monitor.service",
    "nginx.service",
    "postgresql.service",
)
_cache: tuple[float, dict[str, object]] | None = None
_ticker_cache: tuple[float, dict[str, dict[str, object]]] | None = None

TRADING_UNIVERSE = (
    "AAVEUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT", "BCHUSDT",
    "BNBUSDT", "DOTUSDT", "HBARUSDT", "INJUSDT", "LINKUSDT", "LTCUSDT",
    "NEARUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT", "TRXUSDT", "UNIUSDT",
    "XLMUSDT", "XRPUSDT",
)
INDICATORS = ("BTCUSDT", "ETHUSDT")
EXCLUDED_MEMES = ("1000PEPEUSDT", "DOGEUSDT")


def live_tickers() -> dict[str, dict[str, object]]:
    global _ticker_cache
    now = time.monotonic()
    if _ticker_cache and now - _ticker_cache[0] < 30:
        return _ticker_cache[1]
    with urllib.request.urlopen("https://api.bybit.kz/v5/market/tickers?category=linear", timeout=8) as response:
        payload = json.load(response)
    result: dict[str, dict[str, object]] = {}
    for item in payload.get("result", {}).get("list", []):
        symbol = item.get("symbol")
        if symbol not in {*TRADING_UNIVERSE, *INDICATORS, *EXCLUDED_MEMES}:
            continue
        bid = float(item.get("bid1Price") or 0)
        ask = float(item.get("ask1Price") or 0)
        middle = (bid + ask) / 2 if bid and ask else 0
        result[symbol] = {
            "turnover24h": float(item.get("turnover24h") or 0),
            "open_interest_value": float(item.get("openInterestValue") or 0),
            "funding_rate_pct": float(item.get("fundingRate") or 0) * 100,
            "spread_bps": ((ask - bid) / middle * 10_000) if middle else None,
            "last_price": item.get("lastPrice") or "",
        }
    _ticker_cache = (now, result)
    return result


def traffic_light(symbol: str, ticker: dict[str, object] | None) -> tuple[str, str]:
    if symbol in INDICATORS:
        return "red", "индикатор рынка, торговля запрещена"
    if symbol in EXCLUDED_MEMES:
        return "red", "мем-монета исключена из торговли"
    if not ticker:
        return "red", "нет свежей котировки Bybit"
    turnover = float(ticker["turnover24h"])
    oi = float(ticker["open_interest_value"])
    spread = ticker["spread_bps"]
    funding = abs(float(ticker["funding_rate_pct"]))
    if turnover < 5_000_000 or oi < 2_000_000 or spread is None or spread > 20 or funding > 0.20:
        return "red", "критический порог ликвидности, спреда, OI или funding"
    warnings = []
    if turnover < 25_000_000: warnings.append("оборот < $25 млн")
    if oi < 10_000_000: warnings.append("OI < $10 млн")
    if spread > 8: warnings.append("спред > 8 б.п.")
    if funding > 0.05: warnings.append("|funding| > 0,05%")
    if warnings:
        return "yellow", "; ".join(warnings)
    return "green", "проходит сегодняшние операционные пороги"


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=8, check=False)
    return (result.stdout or result.stderr).strip()


def directory_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    output = command("du", "-sb", str(path)).split(maxsplit=1)
    return int(output[0]) if output and output[0].isdigit() else 0


def service_state(name: str) -> str:
    value = command("systemctl", "is-active", name)
    return value.splitlines()[0] if value else "unknown"


def bot_control_state() -> dict[str, object]:
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        rows = connection.execute("""SELECT id,name,strategy,mode,desired_state,actual_state,executable,
            mainnet_approved,symbols_json,stats_json,updated_at_epoch FROM control.bots ORDER BY id""").fetchall()
        bots = [{
            "id": row[0], "name": row[1], "strategy": row[2], "mode": row[3],
            "desired_state": row[4], "actual_state": row[5], "executable": row[6],
            "mainnet_approved": bool(row[7]), "symbols": json.loads(row[8]),
            "stats": json.loads(row[9]), "updated_at_epoch": row[10],
        } for row in rows]
        events = [{"at_epoch": row[0] // 1000, "bot_id": row[1], "action": row[2], "result": row[3]}
                  for row in connection.execute("SELECT at_epoch_ms,bot_id,action,result FROM control.bot_events ORDER BY id DESC LIMIT 100")]
        gates = {row[0]: {"enabled": bool(row[1]), "reason": row[2]} for row in connection.execute("SELECT mode,enabled,reason FROM control.execution_gates")}
    return {"execution_gate": "live-trading-locked" if not gates.get("mainnet", {}).get("enabled") else "mainnet-enabled", "gates": gates, "bots": bots, "events": events}


def opportunity_state() -> dict[str, object]:
    with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
        counts = {row[0]: row[1] for row in connection.execute("SELECT state,count(*) FROM monitoring.opportunities GROUP BY state")}
        rows = connection.execute("""SELECT signal_id,bot_id,strategy_version,symbol,direction,decision,
            decision_reason,traffic_light,state,max_favorable_pct,max_adverse_pct,first_hits_json,samples,
            signal_at_epoch_ms FROM monitoring.opportunities ORDER BY signal_at_epoch_ms DESC LIMIT 100""").fetchall()
    items = [{"signal_id": row[0], "bot_id": row[1], "strategy_version": row[2], "symbol": row[3],
              "direction": row[4], "decision": row[5], "reason": row[6], "traffic_light": row[7],
              "state": row[8], "mfe_pct": row[9], "mae_pct": row[10], "hits": json.loads(row[11]),
              "samples": row[12], "signal_at_epoch_ms": row[13]} for row in rows]
    return {"counts": counts, "items": items}


def snapshot() -> dict[str, object]:
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < 10:
        return _cache[1]
    download = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    expansion_download = json.loads(EXPANSION_STATE.read_text(encoding="utf-8")) if EXPANSION_STATE.exists() else {}
    active_download = expansion_download or download
    connectivity = json.loads(CONNECTIVITY_STATE.read_text(encoding="utf-8")) if CONNECTIVITY_STATE.exists() else {"state": "not-started"}
    private_api = json.loads(PRIVATE_API_STATE.read_text(encoding="utf-8")) if PRIVATE_API_STATE.exists() else {"state": "not-checked"}
    try:
        bots = bot_control_state()
    except Exception as exc:
        bots = {"bots": [], "events": [], "execution_gate": "database-error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        opportunities = opportunity_state()
    except Exception as exc:
        opportunities = {"counts": {}, "items": [], "error": f"{type(exc).__name__}: {exc}"}
    safety = json.loads(SAFETY_STATE.read_text(encoding="utf-8")) if SAFETY_STATE.exists() else {"state": "not-started"}
    backup = json.loads(BACKUP_STATE.read_text(encoding="utf-8")) if BACKUP_STATE.exists() else {"state": "not-started"}
    private_runtime = json.loads(PRIVATE_RUNTIME_STATE.read_text(encoding="utf-8")) if PRIVATE_RUNTIME_STATE.exists() else {"private": {"state": "not-started"}, "trade": {"state": "not-started"}}
    health = json.loads(HEALTH_STATE.read_text(encoding="utf-8")) if HEALTH_STATE.exists() else {"state": "unknown", "issues": []}
    raw = DATA_ROOT / "datasets" / "raw" / PERIOD
    symbols = []
    roles = {**{symbol: "trading" for symbol in TRADING_UNIVERSE}, **{symbol: "indicator" for symbol in INDICATORS}, **{symbol: "excluded_meme" for symbol in EXCLUDED_MEMES}}
    tickers = live_tickers()
    for symbol in sorted(roles):
        root = raw / symbol
        trades = root / "public_trades"
        books = root / "orderbook"
        ticker = tickers.get(symbol)
        light, reason = traffic_light(symbol, ticker)
        symbols.append({
            "symbol": symbol,
            "role": roles[symbol],
            "public_trade_files": len(list(trades.glob("*.csv.gz"))) if trades.exists() else 0,
            "orderbook_files": len(list(books.glob("*.data.zip"))) if books.exists() else 0,
            "bytes": directory_bytes(root),
            "traffic_light": light,
            "traffic_reason": reason,
            "market": ticker or {},
        })
    order = {"green": 0, "yellow": 1, "red": 2}
    symbols.sort(key=lambda x: (order[x["traffic_light"]], -float(x["market"].get("turnover24h", 0))))
    disk = shutil.disk_usage(DATA_ROOT)
    research = APP_ROOT / "research"
    scripts = sorted(p.name for p in research.glob("*") if p.is_file()) if research.exists() else []
    current_root = APP_ROOT / "current"
    current_code = {
        "release": current_root.resolve().name if current_root.exists() else "",
        "bytes": directory_bytes(current_root.resolve()) if current_root.exists() else 0,
        "source_modules": len(list((current_root / "src").glob("**/*.py"))) if current_root.exists() else 0,
        "research_scripts": len(list((current_root / "scripts").glob("**/*.py"))) if current_root.exists() else 0,
        "tests": len(list((current_root / "tests").glob("**/*.py"))) if current_root.exists() else 0,
        "status": "baseline-not-activated" if current_root.exists() else "missing",
    }
    jobs: dict[str, object] = {}
    job_items = []
    for state in ("queued", "running", "completed", "failed"):
        path = DATA_ROOT / "jobs" / state
        entries = sorted((p for p in path.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True) if path.exists() else []
        jobs[state] = len(entries)
        for entry in entries[:25]:
            status_path = entry / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
            manifest = status.get("manifest", {})
            job_items.append({
                "job_id": entry.name,
                "state": state,
                "title": manifest.get("title", ""),
                "line": manifest.get("line", ""),
                "symbols": manifest.get("dataset", {}).get("symbols", []),
                "dependencies": manifest.get("dependencies", []),
                "duration_seconds": status.get("duration_seconds"),
                "exit_code": status.get("exit_code"),
                "error": status.get("error", ""),
                "report_path": status.get("report_path", ""),
            })
    jobs["items"] = job_items
    legacy_root = DATA_ROOT / "legacy"
    legacy_items = []
    if legacy_root.exists():
        for item in sorted((p for p in legacy_root.iterdir() if p.is_dir()), key=lambda p: p.name):
            reports = item / "reports"
            modules = item / "src" / "bybit_workbench" / "research"
            legacy_items.append({
                "name": item.name,
                "bytes": directory_bytes(item),
                "files": len(command("find", str(item), "-type", "f", "-printf", ".")),
                "report_roots": len([p for p in reports.iterdir() if p.is_dir()]) if reports.exists() else 0,
                "research_modules": len(list(modules.glob("*.py"))) if modules.exists() else 0,
                "reports": sorted(p.name for p in reports.iterdir() if p.is_dir()) if reports.exists() else [],
                "modules": sorted(p.name for p in modules.glob("*.py")) if modules.exists() else [],
            })
    payload: dict[str, object] = {
        "generated_at_epoch": int(time.time()),
        "host": command("hostname"),
        "period": PERIOD,
        "evaluation_start": download.get("evaluation_start"),
        "evaluation_end": download.get("evaluation_end"),
        "download": active_download,
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free},
        "symbols": symbols,
        "market_selection": {
            "updated_every_seconds": 30,
            "green": "оборот ≥ $25 млн; OI ≥ $10 млн; спред ≤ 8 б.п.; |funding| ≤ 0,05%",
            "yellow": "не критично, но хотя бы один зелёный порог не выполнен",
            "red": "индикатор/мем/нет котировки либо оборот < $5 млн, OI < $2 млн, спред > 20 б.п., |funding| > 0,20%",
        },
        "jobs": jobs,
        "legacy": legacy_items,
        "scripts": scripts,
        "current_code": current_code,
        "connectivity": connectivity,
        "private_api": private_api,
        "bots": bots,
        "opportunities": opportunities,
        "safety": safety,
        "backup": backup,
        "private_runtime": private_runtime,
        "health": health,
        "services": {name: service_state(name) for name in ALLOWED_SERVICES},
        "technologies": {
            "os": command("bash", "-lc", ". /etc/os-release; printf '%s' \"$PRETTY_NAME\""),
            "python": command("python3", "--version"),
            "nginx": command("nginx", "-v"),
            "postgresql": command("psql", "--version"),
            "service_manager": "systemd",
            "data_filesystem": command("findmnt", "-n", "-o", "FSTYPE", str(DATA_ROOT)),
        },
    }
    _cache = (now, payload)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "CriptaDashboard/0.1"

    def send_body(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self.send_body(200, b'{"status":"ok"}\n', "application/json; charset=utf-8")
        elif path == "/api/status":
            body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
            self.send_body(200, body, "application/json; charset=utf-8")
        elif path == "/":
            body = (Path(__file__).parent / "index.html").read_bytes()
            self.send_body(200, body, "text/html; charset=utf-8")
        elif path.startswith("/reports/"):
            self.send_report_path(path.removeprefix("/reports/"))
        else:
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/bots/action":
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("invalid body size")
            request = json.loads(self.rfile.read(length))
            bot_id = str(request.get("bot_id", ""))
            action = str(request.get("action", ""))
            if action not in {"start", "stop"}:
                raise ValueError("unknown action")
            with psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql") as connection:
                bot = connection.execute("SELECT executable,mode,mainnet_approved FROM control.bots WHERE id=%s", (bot_id,)).fetchone()
                if not bot:
                    raise ValueError("unknown bot")
                executable, mode, mainnet_approved = bot
                if action == "start" and not executable:
                    self.send_body(409, json.dumps({"error": "Сначала назначьте проверенный исполняемый модуль"}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                    return
                if action == "start" and mode == "mainnet" and not mainnet_approved:
                    self.send_body(409, json.dumps({"error": "Mainnet-допуск заблокирован"}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                    return
                now_ms = int(time.time() * 1000)
                connection.execute("UPDATE control.bots SET desired_state=%s,updated_at_epoch=%s WHERE id=%s", ("running" if action == "start" else "stopped", now_ms // 1000, bot_id))
                connection.execute("INSERT INTO control.bot_events(at_epoch_ms,bot_id,action,result,details_json) VALUES(%s,%s,%s,'requested','{}')", (now_ms, bot_id, action))
                connection.commit()
            global _cache
            _cache = None
            self.send_body(202, json.dumps({"status": "accepted"}).encode(), "application/json; charset=utf-8")
        except (ValueError, json.JSONDecodeError, OSError, psycopg.Error) as exc:
            self.send_body(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def send_report_path(self, relative: str) -> None:
        root = REPORT_ROOT.resolve()
        target = (root / unquote(relative)).resolve()
        if not target.is_relative_to(root) or not target.exists():
            self.send_body(404, b"not found\n", "text/plain; charset=utf-8")
            return
        if target.is_dir():
            rows = []
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                rel = item.relative_to(root).as_posix()
                suffix = "/" if item.is_dir() else ""
                rows.append(f'<li><a href="/reports/{quote(rel)}">{escape(item.name)}{suffix}</a></li>')
            parent = target.parent if target != root else None
            back = ""
            if parent and parent.is_relative_to(root):
                back_rel = parent.relative_to(root).as_posix()
                back = f'<p><a href="/reports/{quote(back_rel)}">← Назад</a></p>'
            body = ("<!doctype html><meta charset=utf-8><title>Cripta reports</title>"
                    "<style>body{background:#091017;color:#e7eef5;font:15px system-ui;padding:30px}a{color:#55b5ff}li{margin:9px}</style>"
                    f"<h1>{escape(target.name)}</h1>{back}<ul>{''.join(rows)}</ul>").encode("utf-8")
            self.send_body(200, body, "text/html; charset=utf-8")
            return
        body = target.read_bytes()
        self.send_body(200, body, guess_type(target.name)[0] or "application/octet-stream")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.client_address[0]} {fmt % args}", flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    server.serve_forever()
