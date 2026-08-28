"""RSI / SMA math (the signal primitives)."""

def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None when there is not enough data.

    RSI = 100 - 100/(1+RS), where RS = avg gain / avg loss over `period`
    bars. Value is 0..100: high = overbought (recent bars mostly up),
    low = oversold. The bot BUYS oversold dips (RSI <= entry_rsi) and can
    exit overbought holds (RSI >= exit_rsi).
    """
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """RSI for every prefix of `closes`, computed in ONE forward pass.

    rsi_series[i] == rsi(closes[:i+1]) for every i (exactly), so the sweep can
    evaluate signals at any bar in O(1) instead of recomputing RSI from scratch
    at every step.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    for i in range(period, n):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` values (None if too short)."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period
