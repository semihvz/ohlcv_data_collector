from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import (
    CollectorConfig,
    DEFAULT_LISTING_START_MS,
    DEFAULT_SHARD_DB_PREFIX,
    DEFAULT_SYMBOLS_PER_DB,
    SHARD_MANIFEST_FILENAME,
)
from .exchanges import build_exchange_client
from .pipeline import BackfillOptions, CollectorService
from .time_utils import utc_now_ms


@dataclass(frozen=True)
class ShardBackfillOptions:
    symbols: list[str] | None
    all_usdt: bool
    start_ms: int | None
    end_ms: int | None
    limit_symbols: int | None
    max_pages_per_symbol: int | None
    shard: int | None
    no_download: bool
    skip_trade_rebuild: bool


class ShardManager:
    def __init__(
        self,
        base_config: CollectorConfig,
        db_dir: str,
        db_prefix: str = DEFAULT_SHARD_DB_PREFIX,
        symbols_per_db: int = DEFAULT_SYMBOLS_PER_DB,
    ) -> None:
        if symbols_per_db < 1:
            raise ValueError("symbols_per_db must be 1 or greater")
        self.base_config = base_config
        self.db_dir = Path(db_dir)
        self.db_prefix = db_prefix
        self.symbols_per_db = symbols_per_db
        self.client = build_exchange_client(
            base_config.exchange,
            timeout_seconds=base_config.request_timeout_seconds,
            request_sleep_seconds=base_config.request_sleep_seconds,
        )

    @property
    def manifest_path(self) -> Path:
        return self.db_dir / SHARD_MANIFEST_FILENAME

    def backfill(self, options: ShardBackfillOptions) -> dict[str, Any]:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        selected = self.resolve_symbols(options)
        shards = self.build_shards(selected)
        manifest = self.write_manifest(shards)

        shards_to_run = shards
        if options.shard is not None:
            if options.shard < 1 or options.shard > len(shards):
                raise ValueError(f"shard must be between 1 and {len(shards)}")
            shards_to_run = [shards[options.shard - 1]]

        completed = 0
        failed = 0
        results = []
        for shard in shards_to_run:
            shard_no = int(shard["shard"])
            shard_symbols = list(shard["symbols"])
            db_path = self.db_dir / str(shard["db"])
            print(
                f"[shard {shard_no}/{len(shards)}] {db_path} "
                f"symbols={len(shard_symbols)}",
                flush=True,
            )
            service = CollectorService(replace(self.base_config, db_path=str(db_path)))
            try:
                service.init_db()
                service.db.prune_to_symbols(self.base_config.exchange, shard_symbols)
                result = service.backfill(
                    BackfillOptions(
                        symbols=shard_symbols,
                        all_usdt=False,
                        start_ms=options.start_ms,
                        end_ms=options.end_ms,
                        limit_symbols=None,
                        max_pages_per_symbol=options.max_pages_per_symbol,
                        no_download=options.no_download,
                        skip_trade_rebuild=options.skip_trade_rebuild,
                    )
                )
            finally:
                service.close()

            completed += int(result["completed"])
            failed += int(result["failed"])
            results.append(
                {
                    "shard": shard_no,
                    "db": str(db_path),
                    "symbols": len(shard_symbols),
                    "completed": int(result["completed"]),
                    "failed": int(result["failed"]),
                }
            )

        return {
            "db_dir": str(self.db_dir),
            "exchange": self.base_config.exchange,
            "manifest": str(self.manifest_path),
            "symbols_per_db": self.symbols_per_db,
            "total_symbols": len(selected),
            "total_shards": len(shards),
            "processed_shards": len(shards_to_run),
            "completed": completed,
            "failed": failed,
            "manifest_written_at_ms": manifest["updated_at_ms"],
            "results": results,
        }

    def stats(self) -> dict[str, Any]:
        manifest = self.read_manifest()
        totals: dict[str, int] = {}
        shard_stats = []

        for shard in manifest["shards"]:
            db_path = self.db_dir / str(shard["db"])
            row: dict[str, Any] = {
                "shard": int(shard["shard"]),
                "db": str(db_path),
                "symbols_in_manifest": len(shard["symbols"]),
            }
            if not db_path.exists():
                row["status"] = "MISSING"
                shard_stats.append(row)
                continue

            service = CollectorService(replace(self.base_config, db_path=str(db_path)))
            try:
                stats = service.stats()
                row["status"] = "OK"
                row["stats"] = stats
                for key, value in stats.items():
                    totals[key] = totals.get(key, 0) + int(value)
            except Exception as exc:
                row["status"] = "ERROR"
                row["error_message"] = str(exc)
            finally:
                service.close()
            shard_stats.append(row)

        return {
            "db_dir": str(self.db_dir),
            "exchange": self.base_config.exchange,
            "manifest": str(self.manifest_path),
            "symbols_per_db": int(manifest["symbols_per_db"]),
            "total_symbols": int(manifest["total_symbols"]),
            "total_shards": len(manifest["shards"]),
            "totals": totals,
            "shards": shard_stats,
        }

    def resolve_symbols(self, options: ShardBackfillOptions) -> list[str]:
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

        for symbol in selected:
            if symbol not in by_symbol and options.start_ms is None:
                print(
                    f"warning: {symbol} is not in current exchangeInfo; "
                    f"using default listing start {DEFAULT_LISTING_START_MS}",
                    flush=True,
                )
        return selected

    def build_shards(self, symbols: list[str]) -> list[dict[str, Any]]:
        shards = []
        for start in range(0, len(symbols), self.symbols_per_db):
            chunk = symbols[start : start + self.symbols_per_db]
            shard_no = len(shards) + 1
            shards.append(
                {
                    "shard": shard_no,
                    "db": self.shard_db_name(shard_no),
                    "symbol_count": len(chunk),
                    "symbols": chunk,
                }
            )
        return shards

    def shard_db_name(self, shard_no: int) -> str:
        return f"{self.db_prefix}_{self.base_config.exchange}_{shard_no:03d}.duckdb"

    def write_manifest(self, shards: list[dict[str, Any]]) -> dict[str, Any]:
        manifest = {
            "version": 1,
            "updated_at_ms": utc_now_ms(),
            "exchange": self.base_config.exchange,
            "symbols_per_db": self.symbols_per_db,
            "db_prefix": self.db_prefix,
            "total_symbols": sum(int(shard["symbol_count"]) for shard in shards),
            "total_shards": len(shards),
            "shards": shards,
        }
        self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Shard manifest not found: {self.manifest_path}")
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))
