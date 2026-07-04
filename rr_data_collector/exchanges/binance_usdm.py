from __future__ import annotations

import time
from typing import Any

import requests

from .base import ExchangeCandle, ExchangeSymbol


BINANCE_FAPI_BASE_URL = "https://fapi.binance.com"


class BinanceUsdmClient:
    exchange_id = "binance"

    def __init__(
        self,
        base_url: str = BINANCE_FAPI_BASE_URL,
        timeout_seconds: int = 30,
        request_sleep_seconds: float = 0.05,
        max_retries: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.request_sleep_seconds = request_sleep_seconds
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "rr-data-collector/1.0"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            if self.request_sleep_seconds:
                time.sleep(self.request_sleep_seconds)
            try:
                response = self.session.get(url, params=params, timeout=self.timeout_seconds)
                if response.status_code in {418, 429} or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(2**attempt, 30)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2**attempt, 30))
        if last_error:
            raise last_error
        raise RuntimeError(f"GET {url} failed")

    def usdt_perpetual_symbols(self) -> list[ExchangeSymbol]:
        rows = []
        for item in self._get("/fapi/v1/exchangeInfo").get("symbols", []):
            if item.get("quoteAsset") != "USDT":
                continue
            if item.get("contractType") != "PERPETUAL":
                continue
            if item.get("status") not in {"TRADING", "SETTLING"}:
                continue
            symbol = str(item["symbol"]).upper()
            rows.append(
                ExchangeSymbol(
                    exchange=self.exchange_id,
                    symbol=symbol,
                    exchange_symbol=symbol,
                    pair=item.get("pair"),
                    base_asset=item.get("baseAsset"),
                    quote_asset=item.get("quoteAsset"),
                    contract_type=item.get("contractType"),
                    status=item.get("status"),
                    onboard_time_ms=item.get("onboardDate"),
                    delivery_time_ms=item.get("deliveryDate"),
                    price_precision=item.get("pricePrecision"),
                    quantity_precision=item.get("quantityPrecision"),
                )
            )
        return sorted(rows, key=lambda row: row.symbol)

    def klines_page(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int = 1500,
    ) -> list[ExchangeCandle]:
        rows = self._get(
            "/fapi/v1/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": limit,
            },
        )
        return [
            ExchangeCandle(
                open_time_ms=int(row[0]),
                close_time_ms=int(row[6]),
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
                quote_volume=row[7],
                trade_count=int(row[8]),
                taker_buy_base=row[9],
                taker_buy_quote=row[10],
            )
            for row in rows
        ]
