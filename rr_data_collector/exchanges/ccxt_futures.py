from __future__ import annotations

import re
import time
from typing import Any

from .base import ExchangeCandle, ExchangeSymbol


def _require_ccxt() -> Any:
    try:
        import ccxt  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "This exchange requires ccxt. Install dependencies with `pip install -r requirements.txt`."
        ) from exc
    return ccxt


class CcxtFuturesClient:
    def __init__(
        self,
        exchange_id: str,
        timeout_seconds: int = 30,
        request_sleep_seconds: float = 0.05,
        options: dict[str, Any] | None = None,
    ) -> None:
        ccxt = _require_ccxt()
        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"ccxt exchange is not available: {exchange_id}")
        self.exchange_id = exchange_id
        self.request_sleep_seconds = request_sleep_seconds
        klass = getattr(ccxt, exchange_id)
        self.exchange = klass(
            {
                "enableRateLimit": True,
                "timeout": timeout_seconds * 1000,
                "options": options or {"defaultType": "swap"},
            }
        )
        self._markets_by_symbol: dict[str, dict[str, Any]] = {}

    def usdt_perpetual_symbols(self) -> list[ExchangeSymbol]:
        markets = self.exchange.load_markets()
        symbols: list[ExchangeSymbol] = []
        self._markets_by_symbol = {}
        for market in markets.values():
            if not self._is_usdt_perpetual(market):
                continue
            normalized = _normalize_symbol(market)
            self._markets_by_symbol[normalized] = market
            symbols.append(
                ExchangeSymbol(
                    exchange=self.exchange_id,
                    symbol=normalized,
                    exchange_symbol=str(market.get("symbol") or market.get("id") or normalized),
                    pair=str(market.get("symbol") or normalized),
                    base_asset=market.get("base"),
                    quote_asset=market.get("quote"),
                    contract_type="PERPETUAL",
                    status="TRADING" if market.get("active", True) else "INACTIVE",
                    onboard_time_ms=None,
                    delivery_time_ms=None,
                    price_precision=_precision(market, "price"),
                    quantity_precision=_precision(market, "amount"),
                )
            )
        return sorted(symbols, key=lambda row: row.symbol)

    def klines_page(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int = 1000,
    ) -> list[ExchangeCandle]:
        if not self._markets_by_symbol:
            self.usdt_perpetual_symbols()
        market = self._markets_by_symbol.get(symbol.upper())
        if market is None:
            raise ValueError(f"{self.exchange_id} does not expose a USDT perpetual symbol named {symbol}")
        if self.request_sleep_seconds:
            time.sleep(self.request_sleep_seconds)
        rows = self.exchange.fetch_ohlcv(str(market["symbol"]), timeframe=interval, since=start_ms, limit=limit)
        candles = []
        interval_ms = _interval_ms(interval)
        for row in rows:
            open_time_ms = int(row[0])
            close_time_ms = open_time_ms + interval_ms - 1
            if open_time_ms >= end_ms:
                continue
            candles.append(
                ExchangeCandle(
                    open_time_ms=open_time_ms,
                    close_time_ms=close_time_ms,
                    open=row[1],
                    high=row[2],
                    low=row[3],
                    close=row[4],
                    volume=row[5],
                )
            )
        return candles

    @staticmethod
    def _is_usdt_perpetual(market: dict[str, Any]) -> bool:
        if market.get("quote") != "USDT" and market.get("settle") != "USDT":
            return False
        if not market.get("active", True):
            return False
        if market.get("linear") is False:
            return False
        if market.get("swap") is True:
            return True
        if str(market.get("type", "")).lower() == "swap":
            return True
        return False


def _normalize_symbol(market: dict[str, Any]) -> str:
    base = str(market.get("base") or "").upper()
    quote = str(market.get("quote") or market.get("settle") or "USDT").upper()
    if base and quote:
        return f"{base}{quote}"
    raw = str(market.get("id") or market.get("symbol") or "").upper()
    return re.sub(r"[^A-Z0-9]", "", raw)


def _precision(market: dict[str, Any], key: str) -> int | None:
    value = market.get("precision", {}).get(key)
    return int(value) if value is not None else None


def _interval_ms(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1]) * 60_000
    if interval.endswith("h"):
        return int(interval[:-1]) * 60 * 60_000
    raise ValueError(f"Unsupported interval: {interval}")
