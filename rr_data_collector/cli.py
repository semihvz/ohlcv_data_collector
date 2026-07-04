from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    DEFAULT_DB_PATH,
    DEFAULT_DUCKDB_MEMORY_LIMIT,
    DEFAULT_EXCHANGE,
    DEFAULT_DUCKDB_THREADS,
    DEFAULT_INSERT_CHUNK_SIZE,
    DEFAULT_SHARD_DB_DIR,
    DEFAULT_SHARD_DB_PREFIX,
    DEFAULT_SYMBOLS_PER_DB,
    CollectorConfig,
)
from .exchanges import SUPPORTED_EXCHANGES
from .gui import run_gui
from .pipeline import BackfillOptions, CollectorService
from .services.synthetic_seconds import GenerateSecondsOptions, SyntheticSecondService
from .shard_manager import ShardBackfillOptions, ShardManager
from .time_utils import parse_time_ms


def parse_symbols(value: str | None) -> list[str] | None:
    if not value:
        return None
    symbols = []
    seen = set()
    for item in value.split(","):
        symbol = item.strip().upper()
        if not symbol or symbol in seen:
            continue
        symbols.append(symbol)
        seen.add(symbol)
    return symbols


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be 1 or greater")
    return parsed


def build_config(args: argparse.Namespace, db_path: str) -> CollectorConfig:
    return CollectorConfig(
        db_path=db_path,
        exchange=getattr(args, "exchange", DEFAULT_EXCHANGE),
        require_full_context=not args.allow_partial_context,
        request_sleep_seconds=args.request_sleep,
        duckdb_memory_limit=args.duckdb_memory_limit,
        duckdb_threads=args.duckdb_threads,
        duckdb_temp_directory=args.duckdb_temp_directory,
        insert_chunk_size=args.insert_chunk_size,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Binance USD-M Futures sequential R:R trade data into DuckDB.")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="DuckDB database path.")
    parser.add_argument("--request-sleep", type=float, default=0.05, help="Sleep between Binance API calls.")
    parser.add_argument("--duckdb-memory-limit", default=DEFAULT_DUCKDB_MEMORY_LIMIT, help="DuckDB memory limit, e.g. 2GB.")
    parser.add_argument("--duckdb-threads", type=int, default=DEFAULT_DUCKDB_THREADS, help="DuckDB worker threads.")
    parser.add_argument("--duckdb-temp-directory", help="Directory where DuckDB can spill temp data.")
    parser.add_argument("--insert-chunk-size", type=int, default=DEFAULT_INSERT_CHUNK_SIZE, help="Rows per executemany chunk.")
    parser.add_argument(
        "--allow-partial-context",
        action="store_true",
        help="Allow first trades with fewer than 100 context bars. Default requires full 100-bar context.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create database schema only.")
    reset = subparsers.add_parser("reset-db", help="Drop and recreate all collector tables. Deletes collected data.")
    reset.add_argument("--yes", action="store_true", help="Required confirmation.")

    backfill = subparsers.add_parser("backfill", help="Download klines and rebuild sequential trade tables.")
    backfill.add_argument("--exchange", choices=SUPPORTED_EXCHANGES, default=DEFAULT_EXCHANGE, help="Futures exchange.")
    backfill.add_argument("--all-usdt", action="store_true", help="Process all current USDT perpetual symbols.")
    backfill.add_argument("--symbols", help="Comma-separated symbol list, e.g. BTCUSDT,ETHUSDT.")
    backfill.add_argument("--start", help="Override download start time, e.g. 2024-01-01 or 2024-01-01T00:00:00Z.")
    backfill.add_argument("--end", help="Override download end time.")
    backfill.add_argument("--limit-symbols", type=int, help="Limit selected symbols; useful for dry runs.")
    backfill.add_argument("--max-pages-per-symbol", type=int, help="Limit kline API pages per symbol; useful for smoke tests.")
    backfill.add_argument("--no-download", action="store_true", help="Rebuild trades from existing klines only.")
    backfill.add_argument("--skip-trade-rebuild", action="store_true", help="Only download klines.")

    shard_backfill = subparsers.add_parser(
        "shard-backfill",
        help="Backfill symbols into multiple DuckDB files, with a fixed maximum symbols per DB.",
    )
    shard_backfill.add_argument("--exchange", choices=SUPPORTED_EXCHANGES, default=DEFAULT_EXCHANGE, help="Futures exchange.")
    shard_backfill.add_argument("--db-dir", default=DEFAULT_SHARD_DB_DIR, help="Directory for shard DuckDB files.")
    shard_backfill.add_argument("--db-prefix", default=DEFAULT_SHARD_DB_PREFIX, help="Shard DB filename prefix.")
    shard_backfill.add_argument(
        "--symbols-per-db",
        type=positive_int,
        default=DEFAULT_SYMBOLS_PER_DB,
        help="Maximum symbols stored in each shard DB.",
    )
    shard_backfill.add_argument("--shard", type=positive_int, help="Run only one 1-based shard number.")
    shard_backfill.add_argument("--all-usdt", action="store_true", help="Process all current USDT perpetual symbols.")
    shard_backfill.add_argument("--symbols", help="Comma-separated symbol list, e.g. BTCUSDT,ETHUSDT.")
    shard_backfill.add_argument("--start", help="Override download start time, e.g. 2024-01-01 or 2024-01-01T00:00:00Z.")
    shard_backfill.add_argument("--end", help="Override download end time.")
    shard_backfill.add_argument("--limit-symbols", type=int, help="Limit selected symbols; useful for dry runs.")
    shard_backfill.add_argument(
        "--max-pages-per-symbol",
        type=int,
        help="Limit kline API pages per symbol; useful for smoke tests.",
    )
    shard_backfill.add_argument("--no-download", action="store_true", help="Rebuild trades from existing klines only.")
    shard_backfill.add_argument("--skip-trade-rebuild", action="store_true", help="Only download klines.")

    shard_stats = subparsers.add_parser("shard-stats", help="Print aggregate row counts across shard DB files.")
    shard_stats.add_argument("--exchange", choices=SUPPORTED_EXCHANGES, default=DEFAULT_EXCHANGE, help="Futures exchange.")
    shard_stats.add_argument("--db-dir", default=DEFAULT_SHARD_DB_DIR, help="Directory for shard DuckDB files.")
    shard_stats.add_argument("--db-prefix", default=DEFAULT_SHARD_DB_PREFIX, help="Shard DB filename prefix.")
    shard_stats.add_argument(
        "--symbols-per-db",
        type=positive_int,
        default=DEFAULT_SYMBOLS_PER_DB,
        help="Expected maximum symbols stored in each shard DB.",
    )

    generate_1s = subparsers.add_parser(
        "generate-1s",
        help="Generate deterministic Brownian-bridge 1s candles from stored 1m candles.",
    )
    generate_1s.add_argument("--exchange", choices=SUPPORTED_EXCHANGES, default=DEFAULT_EXCHANGE, help="Futures exchange.")
    generate_1s.add_argument("--symbols", required=True, help="Comma-separated symbol list, e.g. BTCUSDT,ETHUSDT.")
    generate_1s.add_argument("--start", help="Start time, e.g. 2026-06-01 or 2026-06-01T00:00:00Z.")
    generate_1s.add_argument("--end", help="End time.")
    generate_1s.add_argument("--seed", type=int, default=42, help="Deterministic Brownian generator seed.")

    gui = subparsers.add_parser("gui", help="Start the local browser GUI.")
    gui.add_argument("--host", default="127.0.0.1", help="GUI host.")
    gui.add_argument("--port", type=positive_int, default=8765, help="GUI port.")
    gui.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")

    subparsers.add_parser("stats", help="Print table row counts.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = build_config(args, args.db)

    if args.command in {"init-db", "reset-db", "stats", "backfill"}:
        service = CollectorService(config)
        try:
            if args.command == "init-db":
                service.init_db()
                print(json.dumps({"db": str(Path(args.db)), "status": "initialized"}, indent=2))
                return 0
            if args.command == "reset-db":
                if not args.yes:
                    raise SystemExit("reset-db deletes collected data; rerun with --yes to confirm.")
                service.reset_db()
                print(json.dumps({"db": str(Path(args.db)), "status": "reset"}, indent=2))
                return 0
            if args.command == "stats":
                print(json.dumps(service.stats(), indent=2))
                return 0
            if args.command == "backfill":
                options = BackfillOptions(
                    symbols=parse_symbols(args.symbols),
                    all_usdt=bool(args.all_usdt),
                    start_ms=parse_time_ms(args.start),
                    end_ms=parse_time_ms(args.end),
                    limit_symbols=args.limit_symbols,
                    max_pages_per_symbol=args.max_pages_per_symbol,
                    no_download=bool(args.no_download),
                    skip_trade_rebuild=bool(args.skip_trade_rebuild),
                )
                result = service.backfill(options)
                result["db"] = str(Path(args.db))
                print(json.dumps(result, indent=2))
                return 0
        finally:
            service.close()

    if args.command == "shard-backfill":
        manager = ShardManager(
            base_config=config,
            db_dir=args.db_dir,
            db_prefix=args.db_prefix,
            symbols_per_db=args.symbols_per_db,
        )
        result = manager.backfill(
            ShardBackfillOptions(
                symbols=parse_symbols(args.symbols),
                all_usdt=bool(args.all_usdt),
                start_ms=parse_time_ms(args.start),
                end_ms=parse_time_ms(args.end),
                limit_symbols=args.limit_symbols,
                max_pages_per_symbol=args.max_pages_per_symbol,
                shard=args.shard,
                no_download=bool(args.no_download),
                skip_trade_rebuild=bool(args.skip_trade_rebuild),
            )
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "shard-stats":
        manager = ShardManager(
            base_config=config,
            db_dir=args.db_dir,
            db_prefix=args.db_prefix,
            symbols_per_db=args.symbols_per_db,
        )
        print(json.dumps(manager.stats(), indent=2))
        return 0

    if args.command == "generate-1s":
        service = SyntheticSecondService(config)
        try:
            result = service.generate_brownian_1s(
                GenerateSecondsOptions(
                    symbols=parse_symbols(args.symbols) or [],
                    start_ms=parse_time_ms(args.start),
                    end_ms=parse_time_ms(args.end),
                    seed=args.seed,
                )
            )
            print(json.dumps(result, indent=2))
            return 0
        finally:
            service.close()

    if args.command == "gui":
        run_gui(host=args.host, port=args.port, open_browser=not args.no_open)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
