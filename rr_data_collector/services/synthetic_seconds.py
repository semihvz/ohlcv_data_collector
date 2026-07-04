from __future__ import annotations

from dataclasses import dataclass

from ..config import CollectorConfig
from ..db import RRDatabase
from ..synthetics import BROWNIAN_MODEL_VERSION, BrownianSecondGenerator
from ..synthetics.brownian import BrownianOptions


@dataclass(frozen=True)
class GenerateSecondsOptions:
    symbols: list[str]
    start_ms: int | None
    end_ms: int | None
    seed: int


class SyntheticSecondService:
    def __init__(self, config: CollectorConfig) -> None:
        self.config = config
        self.db = RRDatabase(
            config.db_path,
            memory_limit=config.duckdb_memory_limit,
            threads=config.duckdb_threads,
            temp_directory=config.duckdb_temp_directory,
            insert_chunk_size=config.insert_chunk_size,
        )

    def close(self) -> None:
        self.db.close()

    def generate_brownian_1s(self, options: GenerateSecondsOptions) -> dict[str, object]:
        self.db.init_schema()
        generator = BrownianSecondGenerator(BrownianOptions(seed=options.seed))
        results = []
        total_minutes = 0
        total_seconds = 0
        for symbol in options.symbols:
            candles = self.db.load_minute_candles(self.config.exchange, symbol, options.start_ms, options.end_ms)
            rows = generator.generate_for_candles(self.config.exchange, symbol, candles)
            inserted = self.db.replace_brownian_seconds(
                exchange=self.config.exchange,
                symbol=symbol,
                model_version=BROWNIAN_MODEL_VERSION,
                seed=options.seed,
                rows=rows,
            )
            self.db.checkpoint()
            total_minutes += len(candles)
            total_seconds += inserted
            results.append({"symbol": symbol, "minute_candles": len(candles), "second_candles": inserted})
        return {
            "exchange": self.config.exchange,
            "db": self.config.db_path,
            "model_version": BROWNIAN_MODEL_VERSION,
            "seed": options.seed,
            "symbols": len(options.symbols),
            "minute_candles": total_minutes,
            "second_candles": total_seconds,
            "results": results,
        }
