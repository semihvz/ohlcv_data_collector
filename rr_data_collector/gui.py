from __future__ import annotations

import json
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from typing import Any
from urllib.parse import urlparse

from .config import DEFAULT_DB_PATH, DEFAULT_EXCHANGE, CollectorConfig
from .exchanges import SUPPORTED_EXCHANGES, build_exchange_client
from .pipeline import CollectorService
from .time_utils import iso_from_ms, parse_time_ms, utc_now_ms


class GuiJobManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.status: dict[str, Any] = self._idle_status()
        self.logs: list[str] = []
        self.recent_candles: list[dict[str, Any]] = []

    def _idle_status(self) -> dict[str, Any]:
        return {
            "running": False,
            "stopping": False,
            "exchange": DEFAULT_EXCHANGE,
            "db_path": DEFAULT_DB_PATH,
            "total": 0,
            "downloaded": 0,
            "rebuilt": 0,
            "failed": 0,
            "current": None,
            "message": "Idle",
            "started_at": None,
            "finished_at": None,
        }

    def list_symbols(self, exchange: str) -> list[dict[str, Any]]:
        client = build_exchange_client(exchange)
        return [
            {
                "symbol": item.symbol,
                "base_asset": item.base_asset,
                "quote_asset": item.quote_asset,
                "status": item.status,
            }
            for item in client.usdt_perpetual_symbols()
        ]

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.status["running"]:
                raise RuntimeError("A download job is already running.")
            self.stop_event.clear()
            self.logs = []
            self.recent_candles = []
            self.status = self._idle_status()
            self.status.update(
                {
                    "running": True,
                    "stopping": False,
                    "exchange": payload.get("exchange", DEFAULT_EXCHANGE),
                    "db_path": payload.get("db_path") or DEFAULT_DB_PATH,
                    "message": "Starting",
                    "started_at": iso_from_ms(utc_now_ms()),
                    "finished_at": None,
                }
            )
            self.thread = threading.Thread(target=self._run_job, args=(payload,), daemon=True)
            self.thread.start()
            return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            self.stop_event.set()
            self.status["stopping"] = True
            if self.status["running"]:
                self.status["message"] = "Stopping after in-flight requests finish"
            self._log("Stop requested")
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "status": dict(self.status),
                "logs": list(self.logs[-300:]),
                "recent_candles": list(self.recent_candles[-200:]),
            }

    def _set_status(self, **updates: Any) -> None:
        with self.lock:
            self.status.update(updates)

    def _log(self, message: str) -> None:
        stamped = f"{time.strftime('%H:%M:%S')} {message}"
        with self.lock:
            self.logs.append(stamped)
            if len(self.logs) > 1000:
                self.logs = self.logs[-1000:]

    def _record_candles(self, symbol: str, rows: list[Any]) -> None:
        if not rows:
            return
        entries = [
            {
                "symbol": symbol,
                "open_time": iso_from_ms(row.open_time_ms),
                "open": str(row.open),
                "high": str(row.high),
                "low": str(row.low),
                "close": str(row.close),
                "volume": str(row.volume),
                "quote_volume": str(row.quote_volume),
            }
            for row in rows
        ]
        with self.lock:
            self.recent_candles.extend(entries)
            if len(self.recent_candles) > 500:
                self.recent_candles = self.recent_candles[-500:]

    def _finish(self, message: str) -> None:
        with self.lock:
            self.status["running"] = False
            self.status["stopping"] = False
            self.status["message"] = message
            self.status["finished_at"] = iso_from_ms(utc_now_ms())

    def _run_job(self, payload: dict[str, Any]) -> None:
        service: CollectorService | None = None
        try:
            exchange = str(payload.get("exchange") or DEFAULT_EXCHANGE)
            db_path = str(payload.get("db_path") or DEFAULT_DB_PATH)
            workers = max(1, int(payload.get("workers") or 1))
            limit_symbols = _optional_int(payload.get("limit_symbols"))
            max_pages = _optional_int(payload.get("max_pages_per_symbol"))
            start_ms = parse_time_ms(payload.get("start") or None)
            end_ms = parse_time_ms(payload.get("end") or None) or utc_now_ms()
            selected_symbols = _normalize_symbols(payload.get("symbols") or [])
            mode = str(payload.get("mode") or "limit")
            skip_rebuild = bool(payload.get("skip_trade_rebuild", True))

            config = CollectorConfig(db_path=db_path, exchange=exchange)
            service = CollectorService(config)
            service.init_db()

            self._log(f"Loading {exchange} USDT perpetual symbols")
            exchange_symbols = service.client.usdt_perpetual_symbols()
            by_symbol = {item.symbol: item for item in exchange_symbols}

            if mode == "selected":
                symbols = selected_symbols
            else:
                symbols = sorted(by_symbol)
                if limit_symbols is not None:
                    symbols = symbols[:limit_symbols]

            if not symbols:
                self._finish("No symbols selected")
                return

            service.db.replace_symbols(exchange, [by_symbol[symbol] for symbol in symbols if symbol in by_symbol])
            for symbol in symbols:
                if symbol not in by_symbol:
                    service.db.upsert_minimal_symbol(exchange, symbol, start_ms)

            self._set_status(total=len(symbols), message="Downloading", current=None)
            self._log(f"Selected {len(symbols)} symbols, workers={workers}")
            completed_downloads, failed_downloads = self._download_parallel(
                service=service,
                symbols=symbols,
                start_ms=start_ms,
                end_ms=end_ms,
                max_pages=max_pages,
                workers=workers,
            )

            if not skip_rebuild and not self.stop_event.is_set():
                self._set_status(message="Rebuilding trades")
                for index, symbol in enumerate(completed_downloads, start=1):
                    if self.stop_event.is_set():
                        break
                    self._set_status(current=symbol)
                    attempts, successful, failed = service.rebuild_symbol_trades(symbol)
                    self._set_status(rebuilt=index)
                    self._log(
                        f"{symbol} rebuild attempts={attempts} successful={successful} failed={failed}"
                    )

            service.db.checkpoint()
            if self.stop_event.is_set():
                self._finish("Stopped")
            else:
                self._finish(f"Completed: {len(completed_downloads)} downloaded, {failed_downloads} failed")
        except Exception as exc:
            self._log(f"ERROR: {exc}")
            self._finish(f"Error: {exc}")
        finally:
            if service is not None:
                service.close()

    def _download_parallel(
        self,
        service: CollectorService,
        symbols: list[str],
        start_ms: int | None,
        end_ms: int,
        max_pages: int | None,
        workers: int,
    ) -> tuple[list[str], int]:
        symbol_queue: Queue[str] = Queue()
        event_queue: Queue[dict[str, Any]] = Queue(maxsize=max(8, workers * 4))
        completed: list[str] = []
        failed = 0

        starts = {symbol: service._download_start_ms(symbol, start_ms) for symbol in symbols}
        for symbol in symbols:
            symbol_queue.put(symbol)

        def worker() -> None:
            client = build_exchange_client(
                service.config.exchange,
                timeout_seconds=service.config.request_timeout_seconds,
                request_sleep_seconds=service.config.request_sleep_seconds,
            )
            while True:
                try:
                    symbol = symbol_queue.get_nowait()
                except Empty:
                    return
                if self.stop_event.is_set():
                    event_queue.put({"type": "stopped", "symbol": symbol})
                    symbol_queue.task_done()
                    continue
                try:
                    inserted_pages = 0
                    cursor = starts[symbol]
                    if cursor >= end_ms:
                        event_queue.put({"type": "done", "symbol": symbol, "inserted": 0})
                        continue
                    while cursor < end_ms and not self.stop_event.is_set():
                        if max_pages is not None and inserted_pages >= max_pages:
                            break
                        rows = client.klines_page(
                            symbol=symbol,
                            interval=service.config.interval,
                            start_ms=cursor,
                            end_ms=end_ms,
                            limit=1500,
                        )
                        inserted_pages += 1
                        if not rows:
                            break
                        closed_rows = [row for row in rows if row.close_time_ms <= end_ms]
                        event_queue.put(
                            {
                                "type": "page",
                                "symbol": symbol,
                                "rows": closed_rows,
                                "first": closed_rows[0].open_time_ms if closed_rows else rows[0].open_time_ms,
                                "last": closed_rows[-1].open_time_ms if closed_rows else rows[-1].open_time_ms,
                            }
                        )
                        next_cursor = rows[-1].open_time_ms + service.config.interval_ms
                        if next_cursor <= cursor or len(rows) < 1500:
                            break
                        cursor = next_cursor
                    event_queue.put({"type": "done", "symbol": symbol})
                except Exception as exc:
                    event_queue.put({"type": "error", "symbol": symbol, "error": str(exc)})
                finally:
                    symbol_queue.task_done()

        executor_workers = min(workers, len(symbols))
        with ThreadPoolExecutor(max_workers=executor_workers) as executor:
            futures = [executor.submit(worker) for _ in range(executor_workers)]
            finished_symbols = 0
            while finished_symbols < len(symbols):
                event = event_queue.get()
                symbol = str(event["symbol"])
                if event["type"] == "page":
                    rows = event["rows"]
                    inserted = service.db.insert_candles(service.config.exchange, symbol, rows)
                    self._record_candles(symbol, rows[-20:])
                    self._set_status(current=symbol)
                    self._log(
                        f"{symbol} inserted={inserted} "
                        f"{iso_from_ms(event['first'])} -> {iso_from_ms(event['last'])}"
                    )
                    continue
                finished_symbols += 1
                if event["type"] == "done":
                    completed.append(symbol)
                    self._set_status(downloaded=len(completed))
                    self._log(f"{symbol} download done")
                elif event["type"] == "error":
                    failed += 1
                    self._set_status(failed=failed)
                    service.db.update_progress(
                        service.config.exchange,
                        symbol,
                        "ERROR",
                        error_message=str(event.get("error")),
                    )
                    self._log(f"{symbol} ERROR {event.get('error')}")
                else:
                    self._log(f"{symbol} skipped")
                service.db.checkpoint()

            for future in futures:
                future.result()
        return completed, failed


MANAGER = GuiJobManager()


class GuiRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_html(INDEX_HTML)
            return
        if path == "/api/status":
            self._send_json(MANAGER.snapshot())
            return
        if path == "/api/exchanges":
            self._send_json({"exchanges": list(SUPPORTED_EXCHANGES)})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/symbols":
                symbols = MANAGER.list_symbols(str(payload.get("exchange") or DEFAULT_EXCHANGE))
                self._send_json({"symbols": symbols})
                return
            if path == "/api/start":
                self._send_json(MANAGER.start(payload))
                return
            if path == "/api/stop":
                self._send_json(MANAGER.stop())
                return
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_gui(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer((host, port), GuiRequestHandler)
    url = f"http://{host}:{port}"
    print(f"GUI running at {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _normalize_symbols(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").split(",")
    symbols = []
    seen = set()
    for item in raw:
        symbol = str(item).strip().upper()
        if symbol and symbol not in seen:
            symbols.append(symbol)
            seen.add(symbol)
    return symbols


INDEX_HTML = r"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OHLCV Collector</title>
  <style>
    :root {
      --bg: #f4f1e8;
      --panel: #fffdf6;
      --ink: #1e2521;
      --muted: #637067;
      --line: #d8d1c0;
      --accent: #0f766e;
      --accent-2: #b45309;
      --danger: #b91c1c;
      --good: #166534;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(135deg, rgba(15,118,110,.09), transparent 35%),
        linear-gradient(315deg, rgba(180,83,9,.12), transparent 40%),
        var(--bg);
      color: var(--ink);
      font-family: Cambria, Georgia, "Times New Roman", serif;
    }
    main {
      width: min(1240px, calc(100vw - 32px));
      margin: 24px auto;
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 16px;
    }
    section, aside {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 50px rgba(40, 32, 12, .08);
    }
    aside { padding: 18px; }
    section { padding: 16px; min-width: 0; }
    h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: 0; }
    h2 { font-size: 17px; margin: 0 0 12px; }
    label { display: block; color: var(--muted); font-size: 13px; margin: 12px 0 5px; }
    select, input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--ink);
      padding: 9px 10px;
      font: inherit;
      min-height: 38px;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .buttons { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
    button {
      border: 0;
      border-radius: 6px;
      padding: 10px 12px;
      min-height: 38px;
      cursor: pointer;
      background: var(--accent);
      color: white;
      font-weight: 700;
      font-family: inherit;
    }
    button.secondary { background: #334155; }
    button.danger { background: var(--danger); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .toolbar { display: flex; gap: 8px; align-items: center; margin-bottom: 12px; }
    .toolbar input { max-width: 280px; }
    .symbols {
      height: 460px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: white;
    }
    .symbol {
      display: grid;
      grid-template-columns: 28px 1fr auto;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      border-bottom: 1px solid #eee8d8;
      font-family: Consolas, "Courier New", monospace;
      font-size: 13px;
    }
    .symbol span:last-child { color: var(--muted); font-family: Cambria, Georgia, serif; }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
    .metric { background: #f8f5ea; border: 1px solid var(--line); border-radius: 8px; padding: 10px; }
    .metric b { display: block; font-size: 22px; }
    .metric span { color: var(--muted); font-size: 12px; }
    .log {
      height: 260px;
      overflow: auto;
      background: #15201d;
      color: #d6f3e7;
      border-radius: 8px;
      padding: 12px;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
      white-space: pre-wrap;
    }
    .live-wrap {
      max-height: 300px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: white;
      margin-bottom: 16px;
    }
    .live-table {
      width: 100%;
      border-collapse: collapse;
      font-family: Consolas, "Courier New", monospace;
      font-size: 12px;
    }
    .live-table th,
    .live-table td {
      border-bottom: 1px solid #eee8d8;
      padding: 7px 8px;
      text-align: right;
      white-space: nowrap;
    }
    .live-table th {
      position: sticky;
      top: 0;
      background: #f8f5ea;
      color: var(--muted);
      z-index: 1;
    }
    .live-table th:first-child,
    .live-table td:first-child,
    .live-table th:nth-child(2),
    .live-table td:nth-child(2) {
      text-align: left;
    }
    .empty-live {
      color: var(--muted);
      padding: 12px;
      font-size: 13px;
    }
    .status { color: var(--muted); margin: 4px 0 12px; }
    .mode { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
    .mode label {
      margin: 0;
      padding: 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #faf7ec;
      color: var(--ink);
    }
    .mode input { width: auto; min-height: 0; margin-right: 6px; }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; width: min(100vw - 20px, 760px); }
      .metrics { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <main>
    <aside>
      <h1>OHLCV Collector</h1>
      <p class="status" id="statusText">Idle</p>
      <label>Borsa</label>
      <select id="exchange"></select>
      <label>DuckDB yolu</label>
      <input id="dbPath" value="data/futures_rr_1m.duckdb">
      <div class="row">
        <div><label>Baslangic</label><input id="start" placeholder="2026-06-01"></div>
        <div><label>Bitis</label><input id="end" placeholder="2026-06-01T01:00:00Z"></div>
      </div>
      <div class="row">
        <div><label>Ilk N sembol</label><input id="limitSymbols" type="number" min="1" value="10"></div>
        <div><label>Worker</label><input id="workers" type="number" min="1" max="32" value="1"></div>
      </div>
      <div class="row">
        <div><label>Max sayfa/sembol</label><input id="maxPages" type="number" min="1" placeholder="bos = sinirsiz"></div>
        <div><label>Trade rebuild</label><select id="skipRebuild"><option value="true">Atla</option><option value="false">Calistir</option></select></div>
      </div>
      <div class="mode">
        <label><input type="radio" name="mode" value="limit" checked> Ilk N sembol</label>
        <label><input type="radio" name="mode" value="selected"> Secili semboller</label>
      </div>
      <div class="buttons">
        <button id="loadSymbols">USDT Paritelerini Listele</button>
        <button id="startJob">Indir</button>
        <button id="stopJob" class="danger">Durdur</button>
      </div>
    </aside>
    <section>
      <div class="metrics">
        <div class="metric"><b id="mTotal">0</b><span>toplam</span></div>
        <div class="metric"><b id="mDownloaded">0</b><span>indirilen</span></div>
        <div class="metric"><b id="mRebuilt">0</b><span>rebuild</span></div>
        <div class="metric"><b id="mFailed">0</b><span>hata</span></div>
      </div>
      <div class="toolbar">
        <input id="filter" placeholder="Sembol ara">
        <button class="secondary" id="selectVisible">Gorunenleri Sec</button>
        <button class="secondary" id="clearSelected">Secimi Temizle</button>
        <span class="status" id="selectedCount">0 secili</span>
      </div>
      <div class="symbols" id="symbols"></div>
      <h2 style="margin-top:16px">Canli Mum Akisi</h2>
      <div class="live-wrap">
        <table class="live-table">
          <thead>
            <tr>
              <th>Sembol</th>
              <th>Zaman</th>
              <th>Open</th>
              <th>High</th>
              <th>Low</th>
              <th>Close</th>
              <th>Volume</th>
            </tr>
          </thead>
          <tbody id="recentCandles">
            <tr><td colspan="7"><div class="empty-live">Henuz veri yok</div></td></tr>
          </tbody>
        </table>
      </div>
      <h2 style="margin-top:16px">Islem Gunlugu</h2>
      <div class="log" id="log"></div>
    </section>
  </main>
<script>
const $ = id => document.getElementById(id);
let symbols = [];
let selected = new Set();

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? {'Content-Type':'application/json'} : {},
    body: body ? JSON.stringify(body) : undefined
  });
  const data = await res.json();
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
}

function renderSymbols() {
  const q = $('filter').value.trim().toUpperCase();
  const box = $('symbols');
  box.innerHTML = '';
  for (const item of symbols) {
    if (q && !item.symbol.includes(q)) continue;
    const row = document.createElement('label');
    row.className = 'symbol';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selected.has(item.symbol);
    cb.onchange = () => {
      cb.checked ? selected.add(item.symbol) : selected.delete(item.symbol);
      updateSelectedCount();
    };
    row.appendChild(cb);
    const name = document.createElement('span');
    name.textContent = item.symbol;
    row.appendChild(name);
    const meta = document.createElement('span');
    meta.textContent = item.status || '';
    row.appendChild(meta);
    box.appendChild(row);
  }
  updateSelectedCount();
}

function updateSelectedCount() {
  $('selectedCount').textContent = `${selected.size} secili`;
}

function renderRecentCandles(rows) {
  const body = $('recentCandles');
  body.innerHTML = '';
  if (!rows.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 7;
    const div = document.createElement('div');
    div.className = 'empty-live';
    div.textContent = 'Henuz veri yok';
    td.appendChild(div);
    tr.appendChild(td);
    body.appendChild(tr);
    return;
  }
  for (const item of rows.slice().reverse().slice(0, 80)) {
    const tr = document.createElement('tr');
    for (const key of ['symbol', 'open_time', 'open', 'high', 'low', 'close', 'volume']) {
      const td = document.createElement('td');
      td.textContent = item[key] ?? '';
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function payload() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  return {
    exchange: $('exchange').value,
    db_path: $('dbPath').value,
    start: $('start').value,
    end: $('end').value,
    limit_symbols: $('limitSymbols').value,
    max_pages_per_symbol: $('maxPages').value,
    workers: $('workers').value,
    skip_trade_rebuild: $('skipRebuild').value === 'true',
    mode,
    symbols: [...selected]
  };
}

async function refreshStatus() {
  const data = await api('/api/status');
  const s = data.status;
  $('statusText').textContent = `${s.message}${s.current ? ' | ' + s.current : ''}`;
  $('mTotal').textContent = s.total || 0;
  $('mDownloaded').textContent = s.downloaded || 0;
  $('mRebuilt').textContent = s.rebuilt || 0;
  $('mFailed').textContent = s.failed || 0;
  renderRecentCandles(data.recent_candles || []);
  $('log').textContent = (data.logs || []).join('\n');
  $('log').scrollTop = $('log').scrollHeight;
  $('startJob').disabled = !!s.running;
  $('stopJob').disabled = !s.running;
}

$('loadSymbols').onclick = async () => {
  $('statusText').textContent = 'Semboller yukleniyor';
  const data = await api('/api/symbols', {exchange: $('exchange').value});
  symbols = data.symbols || [];
  selected.clear();
  renderSymbols();
  $('statusText').textContent = `${symbols.length} USDT perpetual listelendi`;
};
$('startJob').onclick = async () => { await api('/api/start', payload()); await refreshStatus(); };
$('stopJob').onclick = async () => { await api('/api/stop', {}); await refreshStatus(); };
$('filter').oninput = renderSymbols;
$('selectVisible').onclick = () => {
  const q = $('filter').value.trim().toUpperCase();
  for (const item of symbols) if (!q || item.symbol.includes(q)) selected.add(item.symbol);
  renderSymbols();
};
$('clearSelected').onclick = () => { selected.clear(); renderSymbols(); };

(async function init() {
  const data = await api('/api/exchanges');
  $('exchange').innerHTML = data.exchanges.map(e => `<option value="${e}">${e}</option>`).join('');
  await refreshStatus();
  setInterval(refreshStatus, 1000);
})();
</script>
</body>
</html>
"""
