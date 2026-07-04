# Multi-Exchange Futures R:R Data Platform

This project collects 1m futures candles, simulates sequential 1:2 R:R trades
on 1m candles, stores the results in DuckDB, and can generate deterministic
Brownian-bridge 1s synthetic candles from stored 1m candles.

## Exchanges

Supported futures exchanges:

- `binance` - native Binance USD-M Futures adapter
- `bybit` - CCXT futures adapter
- `okx` - CCXT futures adapter
- `bitget` - CCXT futures adapter
- `gateio` - CCXT futures adapter

Install dependencies:

```powershell
pip install -r requirements.txt
```

## Storage

Default DB:

```text
data/futures_rr_1m.duckdb
```

Main tables:

- `symbols`
- `candles_1m`
- `trade_attempts`
- `successful_trades`
- `failed_trades`
- `successful_trade_contexts`
- `failed_trade_contexts`
- `candles_1s_brownian`
- `symbol_progress`

The schema is multi-exchange. All market data and trade tables include
`exchange` and `symbol`.

Prices and volumes are stored as scaled integers. High-volume OHLCV fields use
DuckDB `HUGEINT` after scaling.

## Backfill

Smoke test:

```powershell
python -m rr_data_collector.cli --db data\smoke_1m.duckdb backfill --exchange binance --symbols BTCUSDT --start 2026-06-01 --end 2026-06-01T01:00:00Z
```

Full current USDT perpetual backfill for one exchange:

```powershell
python -m rr_data_collector.cli --db data\futures_rr_1m.duckdb backfill --exchange binance --all-usdt
```

Use another supported exchange:

```powershell
python -m rr_data_collector.cli --db data\bybit_1m.duckdb backfill --exchange bybit --all-usdt
```

## Sharded Backfill

Each shard DB stores at most 30 symbols by default.

```powershell
python -m rr_data_collector.cli shard-backfill --exchange binance --db-dir data\shards --all-usdt --symbols-per-db 30
```

Example output files:

```text
data\shards\futures_rr_1m_binance_001.duckdb
data\shards\futures_rr_1m_binance_002.duckdb
data\shards\shard_manifest.json
```

Run or resume one shard:

```powershell
python -m rr_data_collector.cli shard-backfill --exchange binance --db-dir data\shards --all-usdt --symbols-per-db 30 --shard 7
```

Aggregate shard stats:

```powershell
python -m rr_data_collector.cli shard-stats --exchange binance --db-dir data\shards
```

## Brownian 1s Candles

Generate deterministic Brownian-bridge 1s candles from stored 1m candles:

```powershell
python -m rr_data_collector.cli --db data\futures_rr_1m.duckdb generate-1s --exchange binance --symbols BTCUSDT --start 2026-06-01 --end 2026-06-01T01:00:00Z --seed 42
```

The generated rows are materialized into `candles_1s_brownian`.

The Brownian generator:

- preserves each 1m candle open and close,
- constrains the synthetic path inside the 1m high/low range,
- injects the 1m high and low into deterministic second candles,
- splits volume, quote volume, trade count, and taker volumes across 60 seconds,
- is deterministic for the same `exchange`, `symbol`, minute open time, and seed.

## Trade Rules

- Source candles: `1m`.
- Entry: at candle close.
- Per attempt: open both `LONG` and `SHORT` at the same close.
- Stop: `1%`.
- Take profit: `2%`.
- No new attempt is opened until both positions in the current attempt resolve.
- Next attempt opens at the close of the latest resolved candle.
- Default behavior requires a full 100-bar context before saving trades.

## Schema Note

The 1m multi-exchange schema is not compatible with the older 15m Binance-only
schema. Use a new DB path or reset the old DB explicitly:

```powershell
python -m rr_data_collector.cli --db data\futures_rr_1m.duckdb reset-db --yes
```
