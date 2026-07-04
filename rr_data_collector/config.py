from __future__ import annotations

from dataclasses import dataclass


INTERVAL = "1m"
INTERVAL_MS = 60 * 1000
PRICE_SCALE = 100_000_000
VOLUME_SCALE = 100_000_000
STOP_PCT_NUMERATOR = 1
TAKE_PROFIT_PCT_NUMERATOR = 2
PCT_DENOMINATOR = 100
CONTEXT_BARS = 100
DEFAULT_EXCHANGE = "binance"
DEFAULT_DB_PATH = "data/futures_rr_1m.duckdb"
BINANCE_FAPI_BASE_URL = "https://fapi.binance.com"
DEFAULT_LISTING_START_MS = 1567969200000  # 2019-09-08 17:00:00 UTC
SCHEMA_VERSION = "rr_data_collector_v4_1m_multi_exchange"
DEFAULT_DUCKDB_MEMORY_LIMIT = "2GB"
DEFAULT_DUCKDB_THREADS = 1
DEFAULT_INSERT_CHUNK_SIZE = 50_000
DEFAULT_SHARD_DB_DIR = "data/shards"
DEFAULT_SHARD_DB_PREFIX = "futures_rr_1m"
DEFAULT_SYMBOLS_PER_DB = 30
SHARD_MANIFEST_FILENAME = "shard_manifest.json"


@dataclass(frozen=True)
class CollectorConfig:
    db_path: str = DEFAULT_DB_PATH
    exchange: str = DEFAULT_EXCHANGE
    interval: str = INTERVAL
    interval_ms: int = INTERVAL_MS
    context_bars: int = CONTEXT_BARS
    require_full_context: bool = True
    request_sleep_seconds: float = 0.05
    request_timeout_seconds: int = 30
    duckdb_memory_limit: str = DEFAULT_DUCKDB_MEMORY_LIMIT
    duckdb_threads: int = DEFAULT_DUCKDB_THREADS
    duckdb_temp_directory: str | None = None
    insert_chunk_size: int = DEFAULT_INSERT_CHUNK_SIZE
