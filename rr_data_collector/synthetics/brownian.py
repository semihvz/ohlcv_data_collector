from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

from ..config import PRICE_SCALE
from ..simulator import Candle


BROWNIAN_MODEL_VERSION = "brownian_bridge_v1"


@dataclass(frozen=True)
class BrownianOptions:
    seed: int = 42
    seconds_per_minute: int = 60


class BrownianSecondGenerator:
    def __init__(self, options: BrownianOptions | None = None) -> None:
        self.options = options or BrownianOptions()

    def generate_for_candles(self, exchange: str, symbol: str, candles: list[Candle]) -> list[dict[str, int]]:
        rows: list[dict[str, int]] = []
        for candle in candles:
            rows.extend(self.generate_for_candle(exchange, symbol, candle))
        return rows

    def generate_for_candle(self, exchange: str, symbol: str, candle: Candle) -> list[dict[str, int]]:
        seconds = self.options.seconds_per_minute
        rng = random.Random(_stable_seed(self.options.seed, exchange, symbol, candle.open_time_ms))
        prices = self._price_path(candle, seconds, rng)
        high_second = rng.randrange(seconds)
        low_second = rng.randrange(seconds)
        if seconds > 1:
            while low_second == high_second:
                low_second = rng.randrange(seconds)

        volume_parts = _split_integer(int(candle.volume_i), seconds, rng)
        quote_volume_parts = _split_integer(int(candle.quote_volume_i), seconds, rng)
        trade_count_parts = _split_integer(max(0, int(candle.trade_count)), seconds, rng)
        taker_base_parts = _split_integer(int(candle.taker_buy_base_i), seconds, rng)
        taker_quote_parts = _split_integer(int(candle.taker_buy_quote_i), seconds, rng)

        rows = []
        for index in range(seconds):
            open_i = prices[index]
            close_i = prices[index + 1]
            high_i = max(open_i, close_i)
            low_i = min(open_i, close_i)
            if index == high_second:
                high_i = max(high_i, int(candle.high_i))
            if index == low_second:
                low_i = min(low_i, int(candle.low_i))
            open_time_ms = candle.open_time_ms + index * 1000
            rows.append(
                {
                    "minute_open_time_ms": candle.open_time_ms,
                    "open_time_ms": open_time_ms,
                    "close_time_ms": open_time_ms + 999,
                    "open_i": open_i,
                    "high_i": high_i,
                    "low_i": low_i,
                    "close_i": close_i,
                    "volume_i": volume_parts[index],
                    "quote_volume_i": quote_volume_parts[index],
                    "trade_count": trade_count_parts[index],
                    "taker_buy_base_i": taker_base_parts[index],
                    "taker_buy_quote_i": taker_quote_parts[index],
                }
            )
        return rows

    def _price_path(self, candle: Candle, seconds: int, rng: random.Random) -> list[int]:
        open_price = max(int(candle.open_i) / PRICE_SCALE, 1e-12)
        close_price = max(int(candle.close_i) / PRICE_SCALE, 1e-12)
        high_i = int(candle.high_i)
        low_i = int(candle.low_i)
        low_log = math.log(max(low_i / PRICE_SCALE, 1e-12))
        high_log = math.log(max(high_i / PRICE_SCALE, 1e-12))
        start_log = math.log(open_price)
        end_log = math.log(close_price)
        range_log = max(high_log - low_log, 1e-9)
        sigma = range_log / 2.5

        increments = [rng.gauss(0.0, sigma / math.sqrt(seconds)) for _ in range(seconds)]
        walk = [0.0]
        for inc in increments:
            walk.append(walk[-1] + inc)
        terminal = walk[-1]

        values = []
        for index in range(seconds + 1):
            t = index / seconds
            bridge = walk[index] - t * terminal
            value = (1 - t) * start_log + t * end_log + bridge
            values.append(min(high_log, max(low_log, value)))

        prices = [int(round(math.exp(value) * PRICE_SCALE)) for value in values]
        prices[0] = int(candle.open_i)
        prices[-1] = int(candle.close_i)
        return prices


def _stable_seed(seed: int, exchange: str, symbol: str, open_time_ms: int) -> int:
    payload = f"{seed}:{exchange}:{symbol}:{open_time_ms}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big")


def _split_integer(total: int, parts: int, rng: random.Random) -> list[int]:
    if parts <= 0:
        return []
    if total <= 0:
        return [0] * parts
    weights = [rng.expovariate(1.0) for _ in range(parts)]
    weight_sum = sum(weights)
    raw = [int(total * weight / weight_sum) for weight in weights]
    remainder = total - sum(raw)
    for _ in range(remainder):
        raw[rng.randrange(parts)] += 1
    return raw
