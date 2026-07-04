from __future__ import annotations

from .base import FuturesExchangeClient
from .binance_usdm import BinanceUsdmClient
from .ccxt_futures import CcxtFuturesClient


SUPPORTED_EXCHANGES = ("binance", "bybit", "okx", "bitget", "gateio")


def canonical_exchange_id(exchange: str) -> str:
    normalized = exchange.lower().strip()
    aliases = {
        "binanceusdm": "binance",
        "binance_usdm": "binance",
        "gate": "gateio",
        "gate-io": "gateio",
    }
    return aliases.get(normalized, normalized)


def build_exchange_client(
    exchange: str,
    timeout_seconds: int = 30,
    request_sleep_seconds: float = 0.05,
) -> FuturesExchangeClient:
    exchange_id = canonical_exchange_id(exchange)
    if exchange_id == "binance":
        return BinanceUsdmClient(
            timeout_seconds=timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
        )
    if exchange_id == "bybit":
        return CcxtFuturesClient(
            "bybit",
            timeout_seconds=timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            options={"defaultType": "swap"},
        )
    if exchange_id == "okx":
        return CcxtFuturesClient(
            "okx",
            timeout_seconds=timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            options={"defaultType": "swap"},
        )
    if exchange_id == "bitget":
        return CcxtFuturesClient(
            "bitget",
            timeout_seconds=timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            options={"defaultType": "swap"},
        )
    if exchange_id == "gateio":
        return CcxtFuturesClient(
            "gate",
            timeout_seconds=timeout_seconds,
            request_sleep_seconds=request_sleep_seconds,
            options={"defaultType": "swap"},
        )
    raise ValueError(f"Unsupported exchange: {exchange}. Supported: {', '.join(SUPPORTED_EXCHANGES)}")
