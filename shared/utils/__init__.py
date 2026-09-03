from datetime import datetime, timezone
from typing import Union


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_timestamp(ts: Union[int, float, datetime, str]) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(ts, (int, float)):
        # If timestamp is in milliseconds (> 10**11)
        if ts > 1e11:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        # Parse ISO format
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    raise ValueError(f"Unsupported timestamp format: {ts}")


def calculate_slippage(price: float, slippage_bps: float, is_buy: bool) -> float:
    # 1 bps = 0.0001 (0.01%)
    impact = price * (slippage_bps / 10000.0)
    return price + impact if is_buy else price - impact


def calculate_fee(price: float, quantity: float, fee_rate: float) -> float:
    return price * quantity * fee_rate
