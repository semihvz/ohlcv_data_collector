from __future__ import annotations

from dataclasses import dataclass

from .config import PCT_DENOMINATOR, STOP_PCT_NUMERATOR, TAKE_PROFIT_PCT_NUMERATOR
from .scale import mul_ratio_i, pct_bp


@dataclass(frozen=True)
class Candle:
    symbol: str
    open_time_ms: int
    close_time_ms: int
    open_i: int
    high_i: int
    low_i: int
    close_i: int
    volume_i: int
    quote_volume_i: int
    trade_count: int
    taker_buy_base_i: int
    taker_buy_quote_i: int


@dataclass(frozen=True)
class PositionResult:
    symbol: str
    attempt_no: int
    side: str
    entry_index: int
    entry_open_time_ms: int
    entry_time_ms: int
    entry_price_i: int
    stop_price_i: int
    take_profit_price_i: int
    exit_index: int
    exit_open_time_ms: int | None
    exit_time_ms: int | None
    exit_price_i: int | None
    outcome: str
    pnl_bp: int | None
    bars_held: int
    max_favorable_bp: int
    max_adverse_bp: int
    context_start_index: int
    context_bar_count: int

    @property
    def trade_uid(self) -> str:
        return f"{self.symbol}:{self.attempt_no:010d}:{self.side}"


@dataclass(frozen=True)
class AttemptResult:
    symbol: str
    attempt_no: int
    entry_index: int
    entry_open_time_ms: int
    entry_time_ms: int
    entry_price_i: int
    status: str
    latest_exit_open_time_ms: int | None
    latest_exit_time_ms: int | None


def _levels(entry_price_i: int, side: str) -> tuple[int, int]:
    if side == "LONG":
        stop_price_i = mul_ratio_i(entry_price_i, PCT_DENOMINATOR - STOP_PCT_NUMERATOR, PCT_DENOMINATOR)
        take_profit_price_i = mul_ratio_i(entry_price_i, PCT_DENOMINATOR + TAKE_PROFIT_PCT_NUMERATOR, PCT_DENOMINATOR)
    else:
        stop_price_i = mul_ratio_i(entry_price_i, PCT_DENOMINATOR + STOP_PCT_NUMERATOR, PCT_DENOMINATOR)
        take_profit_price_i = mul_ratio_i(entry_price_i, PCT_DENOMINATOR - TAKE_PROFIT_PCT_NUMERATOR, PCT_DENOMINATOR)
    return stop_price_i, take_profit_price_i


def scan_position(
    candles: list[Candle],
    symbol: str,
    attempt_no: int,
    entry_index: int,
    side: str,
    context_bars: int,
    require_full_context: bool,
) -> PositionResult:
    entry_candle = candles[entry_index]
    entry_price_i = entry_candle.close_i
    stop_price_i, take_profit_price_i = _levels(entry_price_i, side)
    first_exit_index = entry_index + 1
    context_start_index = entry_index - context_bars + 1
    if context_start_index < 0:
        if require_full_context:
            raise ValueError("entry_index does not have enough context bars")
        context_start_index = 0

    max_favorable_bp = 0
    max_adverse_bp = 0
    last_index = len(candles) - 1

    for exit_index in range(first_exit_index, len(candles)):
        candle = candles[exit_index]
        if side == "LONG":
            favorable_bp = pct_bp(candle.high_i - entry_price_i, entry_price_i)
            adverse_bp = pct_bp(candle.low_i - entry_price_i, entry_price_i)
            hit_stop = candle.low_i <= stop_price_i
            hit_take_profit = candle.high_i >= take_profit_price_i
        else:
            favorable_bp = pct_bp(entry_price_i - candle.low_i, entry_price_i)
            adverse_bp = pct_bp(entry_price_i - candle.high_i, entry_price_i)
            hit_stop = candle.high_i >= stop_price_i
            hit_take_profit = candle.low_i <= take_profit_price_i

        max_favorable_bp = max(max_favorable_bp, favorable_bp)
        max_adverse_bp = min(max_adverse_bp, adverse_bp)

        if hit_stop and hit_take_profit:
            return PositionResult(
                symbol=symbol,
                attempt_no=attempt_no,
                side=side,
                entry_index=entry_index,
                entry_open_time_ms=entry_candle.open_time_ms,
                entry_time_ms=entry_candle.close_time_ms,
                entry_price_i=entry_price_i,
                stop_price_i=stop_price_i,
                take_profit_price_i=take_profit_price_i,
                exit_index=exit_index,
                exit_open_time_ms=candle.open_time_ms,
                exit_time_ms=candle.close_time_ms,
                exit_price_i=None,
                outcome="BOTH_HIT_SAME_CANDLE",
                pnl_bp=None,
                bars_held=exit_index - first_exit_index + 1,
                max_favorable_bp=max_favorable_bp,
                max_adverse_bp=max_adverse_bp,
                context_start_index=context_start_index,
                context_bar_count=entry_index - context_start_index + 1,
            )

        if hit_take_profit:
            return PositionResult(
                symbol=symbol,
                attempt_no=attempt_no,
                side=side,
                entry_index=entry_index,
                entry_open_time_ms=entry_candle.open_time_ms,
                entry_time_ms=entry_candle.close_time_ms,
                entry_price_i=entry_price_i,
                stop_price_i=stop_price_i,
                take_profit_price_i=take_profit_price_i,
                exit_index=exit_index,
                exit_open_time_ms=candle.open_time_ms,
                exit_time_ms=candle.close_time_ms,
                exit_price_i=take_profit_price_i,
                outcome="TP",
                pnl_bp=TAKE_PROFIT_PCT_NUMERATOR * 100,
                bars_held=exit_index - first_exit_index + 1,
                max_favorable_bp=max_favorable_bp,
                max_adverse_bp=max_adverse_bp,
                context_start_index=context_start_index,
                context_bar_count=entry_index - context_start_index + 1,
            )

        if hit_stop:
            return PositionResult(
                symbol=symbol,
                attempt_no=attempt_no,
                side=side,
                entry_index=entry_index,
                entry_open_time_ms=entry_candle.open_time_ms,
                entry_time_ms=entry_candle.close_time_ms,
                entry_price_i=entry_price_i,
                stop_price_i=stop_price_i,
                take_profit_price_i=take_profit_price_i,
                exit_index=exit_index,
                exit_open_time_ms=candle.open_time_ms,
                exit_time_ms=candle.close_time_ms,
                exit_price_i=stop_price_i,
                outcome="SL",
                pnl_bp=-STOP_PCT_NUMERATOR * 100,
                bars_held=exit_index - first_exit_index + 1,
                max_favorable_bp=max_favorable_bp,
                max_adverse_bp=max_adverse_bp,
                context_start_index=context_start_index,
                context_bar_count=entry_index - context_start_index + 1,
            )

    return PositionResult(
        symbol=symbol,
        attempt_no=attempt_no,
        side=side,
        entry_index=entry_index,
        entry_open_time_ms=entry_candle.open_time_ms,
        entry_time_ms=entry_candle.close_time_ms,
        entry_price_i=entry_price_i,
        stop_price_i=stop_price_i,
        take_profit_price_i=take_profit_price_i,
        exit_index=last_index,
        exit_open_time_ms=None,
        exit_time_ms=None,
        exit_price_i=None,
        outcome="OPEN",
        pnl_bp=None,
        bars_held=max(0, last_index - first_exit_index + 1),
        max_favorable_bp=max_favorable_bp,
        max_adverse_bp=max_adverse_bp,
        context_start_index=context_start_index,
        context_bar_count=entry_index - context_start_index + 1,
    )


def simulate_symbol(
    symbol: str,
    candles: list[Candle],
    context_bars: int,
    require_full_context: bool,
) -> tuple[list[AttemptResult], list[PositionResult], list[PositionResult]]:
    attempts: list[AttemptResult] = []
    successful: list[PositionResult] = []
    failed: list[PositionResult] = []
    if len(candles) < 2:
        return attempts, successful, failed

    entry_index = context_bars - 1 if require_full_context else 0
    attempt_no = 1
    while entry_index + 1 < len(candles):
        long_result = scan_position(candles, symbol, attempt_no, entry_index, "LONG", context_bars, require_full_context)
        short_result = scan_position(candles, symbol, attempt_no, entry_index, "SHORT", context_bars, require_full_context)
        results = [long_result, short_result]

        if any(result.outcome == "OPEN" for result in results):
            attempts.append(
                AttemptResult(
                    symbol=symbol,
                    attempt_no=attempt_no,
                    entry_index=entry_index,
                    entry_open_time_ms=candles[entry_index].open_time_ms,
                    entry_time_ms=candles[entry_index].close_time_ms,
                    entry_price_i=candles[entry_index].close_i,
                    status="OPEN",
                    latest_exit_open_time_ms=None,
                    latest_exit_time_ms=None,
                )
            )
            break

        latest_exit = max(results, key=lambda result: result.exit_index)
        attempts.append(
            AttemptResult(
                symbol=symbol,
                attempt_no=attempt_no,
                entry_index=entry_index,
                entry_open_time_ms=candles[entry_index].open_time_ms,
                entry_time_ms=candles[entry_index].close_time_ms,
                entry_price_i=candles[entry_index].close_i,
                status="COMPLETE",
                latest_exit_open_time_ms=latest_exit.exit_open_time_ms,
                latest_exit_time_ms=latest_exit.exit_time_ms,
            )
        )

        for result in results:
            if result.outcome == "TP":
                successful.append(result)
            else:
                failed.append(result)

        next_entry_index = latest_exit.exit_index
        if next_entry_index <= entry_index:
            next_entry_index = entry_index + 1
        entry_index = next_entry_index
        attempt_no += 1

    return attempts, successful, failed
