from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from .config import INTERVAL_MS, SCHEMA_VERSION
from .exchanges.base import ExchangeCandle, ExchangeSymbol
from .scale import price_to_i, volume_to_i
from .simulator import AttemptResult, Candle, PositionResult
from .time_utils import utc_now_ms


class RRDatabase:
    def __init__(
        self,
        path: str,
        memory_limit: str = "2GB",
        threads: int = 1,
        temp_directory: str | None = None,
        insert_chunk_size: int = 50_000,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.insert_chunk_size = insert_chunk_size
        self.con = duckdb.connect(str(self.path))
        self.con.execute("SET preserve_insertion_order = false")
        self.con.execute(f"SET memory_limit = '{_sql_string(memory_limit)}'")
        self.con.execute(f"SET threads = {int(threads)}")
        if temp_directory is None:
            temp_path = self.path.parent / f"{self.path.stem}_duckdb_tmp"
        else:
            temp_path = Path(temp_directory)
        temp_path.mkdir(parents=True, exist_ok=True)
        self.con.execute(f"SET temp_directory = '{_sql_string(str(temp_path))}'")

    def close(self) -> None:
        self.con.close()

    def init_schema(self) -> None:
        self._assert_compatible_schema()
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS symbols (
                exchange VARCHAR,
                symbol VARCHAR,
                exchange_symbol VARCHAR,
                pair VARCHAR,
                base_asset VARCHAR,
                quote_asset VARCHAR,
                contract_type VARCHAR,
                status VARCHAR,
                onboard_time_ms BIGINT,
                delivery_time_ms BIGINT,
                price_precision SMALLINT,
                quantity_precision SMALLINT,
                updated_at_ms BIGINT
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS candles_1m (
                exchange VARCHAR,
                symbol VARCHAR,
                open_time_ms BIGINT,
                close_time_ms BIGINT,
                open_i BIGINT,
                high_i BIGINT,
                low_i BIGINT,
                close_i BIGINT,
                volume_i HUGEINT,
                quote_volume_i HUGEINT,
                trade_count INTEGER,
                taker_buy_base_i HUGEINT,
                taker_buy_quote_i HUGEINT
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_attempts (
                exchange VARCHAR,
                symbol VARCHAR,
                attempt_no INTEGER,
                entry_index INTEGER,
                entry_open_time_ms BIGINT,
                entry_time_ms BIGINT,
                entry_price_i BIGINT,
                status VARCHAR,
                latest_exit_open_time_ms BIGINT,
                latest_exit_time_ms BIGINT
            )
            """
        )
        for table_name in ("successful_trades", "failed_trades"):
            self.con.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    trade_uid VARCHAR,
                    exchange VARCHAR,
                    symbol VARCHAR,
                    attempt_no INTEGER,
                    side VARCHAR,
                    entry_index INTEGER,
                    entry_open_time_ms BIGINT,
                    entry_time_ms BIGINT,
                    entry_price_i BIGINT,
                    stop_price_i BIGINT,
                    take_profit_price_i BIGINT,
                    exit_index INTEGER,
                    exit_open_time_ms BIGINT,
                    exit_time_ms BIGINT,
                    exit_price_i BIGINT,
                    outcome VARCHAR,
                    pnl_bp INTEGER,
                    bars_held INTEGER,
                    max_favorable_bp INTEGER,
                    max_adverse_bp INTEGER,
                    context_start_index INTEGER,
                    context_bar_count SMALLINT
                )
                """
            )
        for table_name in ("successful_trade_contexts", "failed_trade_contexts"):
            self.con.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    trade_uid VARCHAR,
                    exchange VARCHAR,
                    symbol VARCHAR,
                    start_open_time_ms BIGINT,
                    end_open_time_ms BIGINT,
                    entry_open_time_ms BIGINT,
                    bar_count SMALLINT
                )
                """
            )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS candles_1s_brownian (
                exchange VARCHAR,
                symbol VARCHAR,
                model_version VARCHAR,
                seed BIGINT,
                minute_open_time_ms BIGINT,
                open_time_ms BIGINT,
                close_time_ms BIGINT,
                open_i BIGINT,
                high_i BIGINT,
                low_i BIGINT,
                close_i BIGINT,
                volume_i HUGEINT,
                quote_volume_i HUGEINT,
                trade_count INTEGER,
                taker_buy_base_i HUGEINT,
                taker_buy_quote_i HUGEINT
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS symbol_progress (
                exchange VARCHAR,
                symbol VARCHAR,
                candle_rows BIGINT,
                candle_last_open_time_ms BIGINT,
                attempts BIGINT,
                successful_trades BIGINT,
                failed_trades BIGINT,
                status VARCHAR,
                error_message VARCHAR,
                updated_at_ms BIGINT
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key VARCHAR,
                value VARCHAR,
                updated_at_ms BIGINT
            )
            """
        )
        self._create_views()

    def reset_schema(self) -> None:
        for view_name in ("successful_trade_context_ohlc", "failed_trade_context_ohlc"):
            self.con.execute(f"DROP VIEW IF EXISTS {view_name}")
        for table_name in (
            "candles_1s_brownian",
            "successful_trade_context_bars",
            "failed_trade_context_bars",
            "successful_trade_contexts",
            "failed_trade_contexts",
            "successful_trades",
            "failed_trades",
            "trade_attempts",
            "symbol_progress",
            "candles_1m",
            "klines_15m",
            "symbols",
            "metadata",
        ):
            self.con.execute(f"DROP TABLE IF EXISTS {table_name}")

    def _assert_compatible_schema(self) -> None:
        existing_schema = self._metadata_value("schema") if self._table_exists("metadata") else None
        has_known_tables = any(
            self._table_exists(table_name)
            for table_name in (
                "symbols",
                "candles_1m",
                "klines_15m",
                "trade_attempts",
                "successful_trades",
                "failed_trades",
            )
        )
        if has_known_tables and existing_schema != SCHEMA_VERSION:
            raise RuntimeError(
                "Existing database uses an incompatible schema. Use a new DB path for the 1m multi-exchange "
                "collector or run `python -m rr_data_collector.cli --db <path> reset-db --yes`."
            )

    def _table_exists(self, table_name: str) -> bool:
        row = self.con.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_name = ?
            """,
            [table_name],
        ).fetchone()
        return bool(row and row[0])

    def _metadata_value(self, key: str) -> str | None:
        row = self.con.execute("SELECT value FROM metadata WHERE key = ? LIMIT 1", [key]).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    def _create_views(self) -> None:
        for prefix in ("successful", "failed"):
            self.con.execute(
                f"""
                CREATE OR REPLACE VIEW {prefix}_trade_context_ohlc AS
                SELECT
                    ctx.trade_uid,
                    CAST((c.open_time_ms - ctx.start_open_time_ms) / {INTERVAL_MS} AS SMALLINT) AS seq,
                    CAST((c.open_time_ms - ctx.entry_open_time_ms) / {INTERVAL_MS} AS SMALLINT) AS bar_offset_from_entry,
                    c.exchange,
                    c.symbol,
                    c.open_time_ms,
                    c.close_time_ms,
                    c.open_i,
                    c.high_i,
                    c.low_i,
                    c.close_i,
                    c.volume_i,
                    c.quote_volume_i,
                    c.trade_count,
                    c.taker_buy_base_i,
                    c.taker_buy_quote_i
                FROM {prefix}_trade_contexts ctx
                JOIN candles_1m c
                  ON c.exchange = ctx.exchange
                 AND c.symbol = ctx.symbol
                 AND c.open_time_ms BETWEEN ctx.start_open_time_ms AND ctx.end_open_time_ms
                """
            )

    def upsert_metadata(self, key: str, value: str) -> None:
        self.con.execute("DELETE FROM metadata WHERE key = ?", [key])
        self.con.execute("INSERT INTO metadata VALUES (?, ?, ?)", [key, value, utc_now_ms()])

    def replace_symbols(self, exchange: str, symbols: list[ExchangeSymbol]) -> None:
        self.con.execute("DELETE FROM symbols WHERE exchange = ?", [exchange])
        self.upsert_symbols(exchange, symbols)

    def upsert_symbols(self, exchange: str, symbols: list[ExchangeSymbol]) -> None:
        now_ms = utc_now_ms()
        rows = [
            (
                exchange,
                item.symbol,
                item.exchange_symbol,
                item.pair,
                item.base_asset,
                item.quote_asset,
                item.contract_type,
                item.status,
                item.onboard_time_ms,
                item.delivery_time_ms,
                item.price_precision,
                item.quantity_precision,
                now_ms,
            )
            for item in symbols
        ]
        if rows:
            self.con.executemany(
                "DELETE FROM symbols WHERE exchange = ? AND symbol = ?",
                [(exchange, row[1]) for row in rows],
            )
            self._executemany_chunked(
                """
                INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def upsert_minimal_symbol(self, exchange: str, symbol: str, onboard_time_ms: int | None) -> None:
        self.con.execute("DELETE FROM symbols WHERE exchange = ? AND symbol = ?", [exchange, symbol])
        self.con.execute(
            """
            INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                exchange,
                symbol,
                symbol,
                symbol,
                symbol.removesuffix("USDT") if symbol.endswith("USDT") else None,
                "USDT" if symbol.endswith("USDT") else None,
                "PERPETUAL",
                "UNKNOWN",
                onboard_time_ms,
                None,
                None,
                None,
                utc_now_ms(),
            ],
        )

    def prune_to_symbols(self, exchange: str, symbols: list[str]) -> None:
        tables = (
            "candles_1s_brownian",
            "successful_trade_contexts",
            "failed_trade_contexts",
            "successful_trades",
            "failed_trades",
            "trade_attempts",
            "symbol_progress",
            "candles_1m",
            "symbols",
        )
        for table in tables:
            if not self._table_exists(table):
                continue
            if not symbols:
                self.con.execute(f"DELETE FROM {table} WHERE exchange = ?", [exchange])
                continue
            placeholders = ", ".join("?" for _ in symbols)
            self.con.execute(
                f"DELETE FROM {table} WHERE exchange = ? AND symbol NOT IN ({placeholders})",
                [exchange, *symbols],
            )

    def symbol_onboard_ms(self, exchange: str, symbol: str) -> int | None:
        row = self.con.execute(
            "SELECT onboard_time_ms FROM symbols WHERE exchange = ? AND symbol = ?",
            [exchange, symbol],
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def max_candle_open_time_ms(self, exchange: str, symbol: str) -> int | None:
        row = self.con.execute(
            "SELECT max(open_time_ms) FROM candles_1m WHERE exchange = ? AND symbol = ?",
            [exchange, symbol],
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def insert_candles(self, exchange: str, symbol: str, rows: list[ExchangeCandle]) -> int:
        if not rows:
            return 0
        min_open = min(row.open_time_ms for row in rows)
        max_open = max(row.open_time_ms for row in rows)
        existing = {
            int(row[0])
            for row in self.con.execute(
                """
                SELECT open_time_ms
                FROM candles_1m
                WHERE exchange = ?
                  AND symbol = ?
                  AND open_time_ms BETWEEN ? AND ?
                """,
                [exchange, symbol, min_open, max_open],
            ).fetchall()
        }
        converted = []
        for row in rows:
            if row.open_time_ms in existing:
                continue
            converted.append(
                (
                    exchange,
                    symbol,
                    row.open_time_ms,
                    row.close_time_ms,
                    price_to_i(row.open),
                    price_to_i(row.high),
                    price_to_i(row.low),
                    price_to_i(row.close),
                    volume_to_i(row.volume),
                    volume_to_i(row.quote_volume),
                    int(row.trade_count or 0),
                    volume_to_i(row.taker_buy_base),
                    volume_to_i(row.taker_buy_quote),
                )
            )
        if not converted:
            return 0
        self._executemany_chunked(
            """
            INSERT INTO candles_1m VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            converted,
        )
        return len(converted)

    def load_candles(self, exchange: str, symbol: str) -> list[Candle]:
        rows = self.con.execute(
            """
            SELECT
                symbol,
                open_time_ms,
                close_time_ms,
                open_i,
                high_i,
                low_i,
                close_i,
                volume_i,
                quote_volume_i,
                trade_count,
                taker_buy_base_i,
                taker_buy_quote_i
            FROM candles_1m
            WHERE exchange = ?
              AND symbol = ?
            ORDER BY open_time_ms
            """,
            [exchange, symbol],
        ).fetchall()
        return [Candle(*row) for row in rows]

    def load_minute_candles(
        self,
        exchange: str,
        symbol: str,
        start_ms: int | None,
        end_ms: int | None,
    ) -> list[Candle]:
        clauses = ["exchange = ?", "symbol = ?"]
        params: list[Any] = [exchange, symbol]
        if start_ms is not None:
            clauses.append("open_time_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            clauses.append("open_time_ms < ?")
            params.append(end_ms)
        rows = self.con.execute(
            f"""
            SELECT
                symbol,
                open_time_ms,
                close_time_ms,
                open_i,
                high_i,
                low_i,
                close_i,
                volume_i,
                quote_volume_i,
                trade_count,
                taker_buy_base_i,
                taker_buy_quote_i
            FROM candles_1m
            WHERE {" AND ".join(clauses)}
            ORDER BY open_time_ms
            """,
            params,
        ).fetchall()
        return [Candle(*row) for row in rows]

    def replace_symbol_trades(
        self,
        exchange: str,
        symbol: str,
        candles: list[Candle],
        attempts: list[AttemptResult],
        successful: list[PositionResult],
        failed: list[PositionResult],
    ) -> None:
        self.con.execute("BEGIN TRANSACTION")
        try:
            for table in (
                "successful_trade_contexts",
                "failed_trade_contexts",
                "successful_trades",
                "failed_trades",
                "trade_attempts",
            ):
                self.con.execute(f"DELETE FROM {table} WHERE exchange = ? AND symbol = ?", [exchange, symbol])

            if attempts:
                self._executemany_chunked(
                    """
                    INSERT INTO trade_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            exchange,
                            item.symbol,
                            item.attempt_no,
                            item.entry_index,
                            item.entry_open_time_ms,
                            item.entry_time_ms,
                            item.entry_price_i,
                            item.status,
                            item.latest_exit_open_time_ms,
                            item.latest_exit_time_ms,
                        )
                        for item in attempts
                    ],
                )

            self._insert_position_results(exchange, "successful_trades", successful)
            self._insert_position_results(exchange, "failed_trades", failed)
            self._insert_contexts(exchange, "successful_trade_contexts", candles, successful)
            self._insert_contexts(exchange, "failed_trade_contexts", candles, failed)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise

    def _insert_position_results(self, exchange: str, table_name: str, results: list[PositionResult]) -> None:
        if not results:
            return
        rows = [
            (
                _trade_uid(exchange, result),
                exchange,
                result.symbol,
                result.attempt_no,
                result.side,
                result.entry_index,
                result.entry_open_time_ms,
                result.entry_time_ms,
                result.entry_price_i,
                result.stop_price_i,
                result.take_profit_price_i,
                result.exit_index,
                result.exit_open_time_ms,
                result.exit_time_ms,
                result.exit_price_i,
                result.outcome,
                result.pnl_bp,
                result.bars_held,
                result.max_favorable_bp,
                result.max_adverse_bp,
                result.context_start_index,
                result.context_bar_count,
            )
            for result in results
        ]
        self._executemany_chunked(
            f"""
            INSERT INTO {table_name} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _insert_contexts(
        self,
        exchange: str,
        table_name: str,
        candles: list[Candle],
        results: list[PositionResult],
    ) -> None:
        rows = []
        for result in results:
            start_candle = candles[result.context_start_index]
            entry_candle = candles[result.entry_index]
            rows.append(
                (
                    _trade_uid(exchange, result),
                    exchange,
                    result.symbol,
                    start_candle.open_time_ms,
                    entry_candle.open_time_ms,
                    entry_candle.open_time_ms,
                    result.context_bar_count,
                )
            )
        if rows:
            self._executemany_chunked(f"INSERT INTO {table_name} VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def update_progress(
        self,
        exchange: str,
        symbol: str,
        status: str,
        attempts: int = 0,
        successful: int = 0,
        failed: int = 0,
        error_message: str | None = None,
    ) -> None:
        candle_stats = self.con.execute(
            """
            SELECT count(*), max(open_time_ms)
            FROM candles_1m
            WHERE exchange = ?
              AND symbol = ?
            """,
            [exchange, symbol],
        ).fetchone()
        self.con.execute("DELETE FROM symbol_progress WHERE exchange = ? AND symbol = ?", [exchange, symbol])
        self.con.execute(
            """
            INSERT INTO symbol_progress VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                exchange,
                symbol,
                int(candle_stats[0] or 0),
                int(candle_stats[1]) if candle_stats[1] is not None else None,
                attempts,
                successful,
                failed,
                status,
                error_message,
                utc_now_ms(),
            ],
        )

    def replace_brownian_seconds(
        self,
        exchange: str,
        symbol: str,
        model_version: str,
        seed: int,
        rows: list[dict[str, Any]],
    ) -> int:
        if not rows:
            return 0
        min_minute = min(int(row["minute_open_time_ms"]) for row in rows)
        max_minute = max(int(row["minute_open_time_ms"]) for row in rows)
        self.con.execute(
            """
            DELETE FROM candles_1s_brownian
            WHERE exchange = ?
              AND symbol = ?
              AND model_version = ?
              AND seed = ?
              AND minute_open_time_ms BETWEEN ? AND ?
            """,
            [exchange, symbol, model_version, seed, min_minute, max_minute],
        )
        converted = [
            (
                exchange,
                symbol,
                model_version,
                seed,
                int(row["minute_open_time_ms"]),
                int(row["open_time_ms"]),
                int(row["close_time_ms"]),
                int(row["open_i"]),
                int(row["high_i"]),
                int(row["low_i"]),
                int(row["close_i"]),
                int(row["volume_i"]),
                int(row["quote_volume_i"]),
                int(row["trade_count"]),
                int(row["taker_buy_base_i"]),
                int(row["taker_buy_quote_i"]),
            )
            for row in rows
        ]
        self._executemany_chunked(
            """
            INSERT INTO candles_1s_brownian VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            converted,
        )
        return len(converted)

    def stats(self) -> dict[str, int]:
        tables = [
            "symbols",
            "candles_1m",
            "candles_1s_brownian",
            "trade_attempts",
            "successful_trades",
            "failed_trades",
            "successful_trade_contexts",
            "failed_trade_contexts",
        ]
        return {
            table: int(self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
            if self._table_exists(table)
        }

    def checkpoint(self) -> None:
        self.con.execute("CHECKPOINT")

    def _executemany_chunked(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        for start in range(0, len(rows), self.insert_chunk_size):
            self.con.executemany(sql, rows[start : start + self.insert_chunk_size])


def _trade_uid(exchange: str, result: PositionResult) -> str:
    return f"{exchange}:{result.symbol}:{result.attempt_no:010d}:{result.side}"


def _sql_string(value: str) -> str:
    return value.replace("'", "''")
