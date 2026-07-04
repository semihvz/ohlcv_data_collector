from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_LISTING_START_MS, CollectorConfig
from .db import RRDatabase
from .exchanges import build_exchange_client
from .simulator import simulate_symbol
from .time_utils import iso_from_ms, utc_now_ms


@dataclass(frozen=True)
class BackfillOptions:
    symbols: list[str] | None
    all_usdt: bool
    start_ms: int | None
    end_ms: int | None
    limit_symbols: int | None
    max_pages_per_symbol: int | None
    no_download: bool
    skip_trade_rebuild: bool


class CollectorService:
    def __init__(self, config: CollectorConfig) -> None:
        self.config = config
        self.db = RRDatabase(
            config.db_path,
            memory_limit=config.duckdb_memory_limit,
            threads=config.duckdb_threads,
            temp_directory=config.duckdb_temp_directory,
            insert_chunk_size=config.insert_chunk_size,
        )
        self.client = build_exchange_client(
            config.exchange,
            timeout_seconds=config.request_timeout_seconds,
            request_sleep_seconds=config.request_sleep_seconds,
        )

    def close(self) -> None:
        self.db.close()

    def init_db(self) -> None:
        self.db.init_schema()
        from .config import SCHEMA_VERSION

        self.db.upsert_metadata("schema", SCHEMA_VERSION)
        self.db.upsert_metadata("exchange", self.config.exchange)
        self.db.upsert_metadata("interval", self.config.interval)
        self.db.upsert_metadata("context_bars", str(self.config.context_bars))
        self.db.upsert_metadata(
            "storage_note",
            "1m multi-exchange futures storage, scaled integer prices/volumes, context ranges",
        )
        self.db.checkpoint()

    def reset_db(self) -> None:
        self.db.reset_schema()
        self.init_db()

    def resolve_symbols(self, options: BackfillOptions) -> list[str]:
        exchange_symbols = self.client.usdt_perpetual_symbols()
        by_symbol = {item.symbol: item for item in exchange_symbols}

        if options.symbols:
            selected = [symbol.upper().strip() for symbol in options.symbols if symbol.strip()]
        else:
            selected = sorted(by_symbol)

        if not options.all_usdt and not options.symbols:
            selected = selected[:1]
        if options.limit_symbols is not None:
            selected = selected[: options.limit_symbols]

        self.db.replace_symbols(self.config.exchange, [by_symbol[symbol] for symbol in selected if symbol in by_symbol])
        for symbol in selected:
            if symbol not in by_symbol:
                self.db.upsert_minimal_symbol(self.config.exchange, symbol, options.start_ms or DEFAULT_LISTING_START_MS)
        return selected

    def backfill(self, options: BackfillOptions) -> dict[str, int]:
        self.init_db()
        symbols = self.resolve_symbols(options)
        end_ms = options.end_ms or utc_now_ms()

        completed = 0
        failed = 0
        for index, symbol in enumerate(symbols, start=1):
            print(f"[{index}/{len(symbols)}] {symbol}", flush=True)
            try:
                if not options.no_download:
                    inserted = self.download_symbol_klines(symbol, options.start_ms, end_ms, options.max_pages_per_symbol)
                    print(f"  candles inserted: {inserted}", flush=True)
                if not options.skip_trade_rebuild:
                    attempts, successful, failed_trades = self.rebuild_symbol_trades(symbol)
                    print(
                        f"  attempts={attempts} successful={successful} failed={failed_trades}",
                        flush=True,
                    )
                completed += 1
            except Exception as exc:
                failed += 1
                self.db.update_progress(self.config.exchange, symbol, "ERROR", error_message=str(exc))
                print(f"  ERROR: {exc}", flush=True)
            self.db.checkpoint()

        return {"symbols": len(symbols), "completed": completed, "failed": failed}

    def download_symbol_klines(
        self,
        symbol: str,
        start_override_ms: int | None,
        end_ms: int,
        max_pages: int | None,
    ) -> int:
        start_ms = self._download_start_ms(symbol, start_override_ms)
        if start_ms >= end_ms:
            return 0

        inserted_total = 0
        page_count = 0
        while start_ms < end_ms:
            if max_pages is not None and page_count >= max_pages:
                break

            rows = self.client.klines_page(
                symbol=symbol,
                interval=self.config.interval,
                start_ms=start_ms,
                end_ms=end_ms,
                limit=1500,
            )
            page_count += 1
            if not rows:
                break

            closed_rows = [row for row in rows if row.close_time_ms <= end_ms]
            inserted_total += self.db.insert_candles(self.config.exchange, symbol, closed_rows)
            last_open = rows[-1].open_time_ms
            next_start = last_open + self.config.interval_ms
            if next_start <= start_ms:
                break
            start_ms = next_start

            first_open = closed_rows[0].open_time_ms if closed_rows else rows[0].open_time_ms
            display_last_open = closed_rows[-1].open_time_ms if closed_rows else last_open
            print(
                f"    page {page_count}: {len(closed_rows)} rows "
                f"{iso_from_ms(first_open)} -> {iso_from_ms(display_last_open)}",
                flush=True,
            )

            if len(rows) < 1500:
                break

        return inserted_total

    def _download_start_ms(self, symbol: str, start_override_ms: int | None) -> int:
        stored_max = self.db.max_candle_open_time_ms(self.config.exchange, symbol)
        onboard_ms = self.db.symbol_onboard_ms(self.config.exchange, symbol)
        if start_override_ms is not None:
            return start_override_ms
        if stored_max is not None:
            return stored_max + self.config.interval_ms
        return onboard_ms or DEFAULT_LISTING_START_MS

    def rebuild_symbol_trades(self, symbol: str) -> tuple[int, int, int]:
        candles = self.db.load_candles(self.config.exchange, symbol)
        if len(candles) < 2:
            self.db.update_progress(self.config.exchange, symbol, "NO_CANDLES")
            return 0, 0, 0

        attempts, successful, failed = simulate_symbol(
            symbol=symbol,
            candles=candles,
            context_bars=self.config.context_bars,
            require_full_context=self.config.require_full_context,
        )
        self.db.replace_symbol_trades(self.config.exchange, symbol, candles, attempts, successful, failed)
        status = "OK" if attempts else "NO_TRADES"
        self.db.update_progress(self.config.exchange, symbol, status, len(attempts), len(successful), len(failed))
        return len(attempts), len(successful), len(failed)

    def stats(self) -> dict[str, int]:
        self.init_db()
        return self.db.stats()
