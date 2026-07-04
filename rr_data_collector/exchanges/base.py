from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExchangeSymbol:
    exchange: str
    symbol: str
    exchange_symbol: str
    pair: str | None
    base_asset: str | None
    quote_asset: str | None
    contract_type: str | None
    status: str | None
    onboard_time_ms: int | None
    delivery_time_ms: int | None
    price_precision: int | None
    quantity_precision: int | None


@dataclass(frozen=True)
class ExchangeCandle:
    open_time_ms: int
    close_time_ms: int
    open: str | int | float
    high: str | int | float
    low: str | int | float
    close: str | int | float
    volume: str | int | float
    quote_volume: str | int | float = 0
    trade_count: int = 0
    taker_buy_base: str | int | float = 0
    taker_buy_quote: str | int | float = 0


class FuturesExchangeClient(Protocol):
    exchange_id: str

    def usdt_perpetual_symbols(self) -> list[ExchangeSymbol]:
        ...

    def klines_page(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[ExchangeCandle]:
        ...
