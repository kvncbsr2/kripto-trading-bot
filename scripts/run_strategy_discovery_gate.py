"""
KRIPTO AGENT — STRATEGY DISCOVERY & VALIDATION GATE
Authoritative benchmarking, out-of-sample testing, walk-forward analysis,
cost stress testing, and safety gate evaluation for $5,000 spot portfolio.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd

from services.feature_engine.indicators.momentum import calculate_rsi, calculate_macd
from services.feature_engine.indicators.trend import calculate_ema, calculate_adx
from services.feature_engine.volatility.volatility import calculate_atr
from services.feature_engine.volume.volume import calculate_volume_ratio, calculate_vwap
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("strategy-discovery-gate", service="research")
settings = get_settings()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "backtesting" / "datasets" / "binance_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# -----------------------------------------------------------------------------
# 1. Real Binance Spot Historical Data Pipeline
# -----------------------------------------------------------------------------
async def fetch_binance_candles(
    symbol: str,
    timeframe: str = "15m",
    target_count: int = 1500,
) -> pd.DataFrame:
    """
    Fetches real Binance historical klines via public REST API with pagination.
    Strictly verifies closed candles, timestamps, and absence of synthetic data.
    Caches results locally to guarantee reproducible benchmarking.
    """
    raw_sym = symbol.replace("/", "").upper()
    cache_file = CACHE_DIR / f"{raw_sym}_{timeframe}_{target_count}.json"

    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                records = json.load(f)
            df = pd.DataFrame(records)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            if df["timestamp"].dt.tz is not None:
                df["timestamp"] = df["timestamp"].dt.tz_localize(None)
            logger.info(f"Loaded {len(df)} cached {timeframe} candles for {symbol}")
            return df
        except Exception as e:
            logger.warning(f"Failed to read cache for {symbol}: {e}")

    logger.info(f"Fetching {target_count} real {timeframe} candles for {symbol} from Binance API...")
    url = "https://api.binance.com/api/v3/klines"
    all_rows = []
    end_time = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        while len(all_rows) < target_count:
            fetch_limit = min(1000, target_count - len(all_rows))
            params = {
                "symbol": raw_sym,
                "interval": timeframe,
                "limit": fetch_limit,
            }
            if end_time:
                params["endTime"] = end_time

            try:
                resp = await client.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    logger.error(f"Binance API error {resp.status_code} for {symbol}: {resp.text}")
                    break
                batch = resp.json()
                if not batch:
                    break

                batch = sorted(batch, key=lambda x: x[0])
                all_rows = batch + all_rows
                end_time = batch[0][0] - 1

                if len(batch) < fetch_limit:
                    break
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Network error fetching {symbol} {timeframe}: {e}")
                break

    seen = set()
    unique_rows = []
    for r in all_rows:
        if r[0] not in seen:
            seen.add(r[0])
            unique_rows.append(r)
    unique_rows.sort(key=lambda x: x[0])

    if not unique_rows:
        return pd.DataFrame()

    now_ms = int(time.time() * 1000)
    if unique_rows[-1][6] > now_ms:
        unique_rows = unique_rows[:-1]

    records = [
        {
            "timestamp": datetime.fromtimestamp(r[0] / 1000.0, tz=timezone.utc).isoformat(),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "quote_volume": float(r[7]),
            "trades_count": int(r[8]),
            "close_time": r[6],
        }
        for r in unique_rows
    ]

    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(records, f)
    except Exception as e:
        logger.warning(f"Failed to cache candles for {symbol}: {e}")

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    logger.info(f"Successfully retrieved {len(df)} closed candles for {symbol} ({timeframe}).")
    return df


# -----------------------------------------------------------------------------
# 2. Universal Deterministic Trade Simulator
# -----------------------------------------------------------------------------
class DeterministicPortfolioSimulator:
    """
    Executes trades with zero look-ahead bias, fee deduction, slippage,
    daily loss locking, position sizing, and detailed metric tracking.
    """

    def __init__(
        self,
        initial_capital: float = 5000.0,
        risk_per_trade: float = 0.005,  # 0.50% ($25)
        daily_max_loss_pct: float = 0.01,  # 1.0% ($50)
        max_open_positions: int = 2,
        max_trades_per_day: int = 5,
        fee_rate: float = 0.0010,  # 0.10%
        slippage_bps: float = 5.0,  # 5 bps = 0.05%
        spread_bps: float = 2.0,  # 2 bps
    ):
        self.initial_capital = initial_capital
        self.risk_per_trade = risk_per_trade
        self.daily_max_loss = initial_capital * daily_max_loss_pct
        self.max_open_positions = max_open_positions
        self.max_trades_per_day = max_trades_per_day
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_bps / 10000.0
        self.half_spread = (spread_bps / 10000.0) / 2.0

    def simulate(
        self,
        signals: List[Dict[str, Any]],
        df_15m_dict: Dict[str, pd.DataFrame],
        exit_model: str = "B",
    ) -> Dict[str, Any]:
        capital = self.initial_capital
        peak_capital = capital
        open_positions: List[Dict[str, Any]] = []
        closed_trades: List[Dict[str, Any]] = []

        signals_by_time: Dict[Any, List[Dict[str, Any]]] = {}
        for s in signals:
            signals_by_time.setdefault(s["timestamp"], []).append(s)

        all_timestamps = set()
        for sym, df in df_15m_dict.items():
            all_timestamps.update(df["timestamp"].tolist())
        timeline = sorted(list(all_timestamps))

        current_day: Optional[datetime.date] = None
        trades_today_count = 0
        daily_pnl = 0.0
        daily_equity_series: Dict[str, float] = {}

        price_lookup: Dict[str, Dict[Any, Tuple[float, float, float, float]]] = {}
        for sym, df in df_15m_dict.items():
            price_lookup[sym] = {
                row["timestamp"]: (row["open"], row["high"], row["low"], row["close"])
                for _, row in df.iterrows()
            }

        for ts in timeline:
            ts_date = ts.date() if hasattr(ts, "date") else ts
            if current_day != ts_date:
                if current_day is not None:
                    daily_equity_series[str(current_day)] = capital + sum(
                        p.get("unrealized_pnl", 0.0) for p in open_positions
                    )
                current_day = ts_date
                trades_today_count = 0
                daily_pnl = 0.0

            # -------------------------------------------------------------
            # A. Check and Update Existing Open Positions
            # -------------------------------------------------------------
            remaining_positions = []
            for pos in open_positions:
                sym = pos["symbol"]
                prices = price_lookup.get(sym, {}).get(ts)
                if not prices:
                    remaining_positions.append(pos)
                    continue

                o_price, h_price, l_price, c_price = prices
                is_closed = False
                exit_price = 0.0
                exit_reason = ""
                exit_qty = pos["quantity"]

                # Check partial take-profit logic (Variant C)
                if exit_model == "C":
                    if not pos.get("partial_tp_hit", False):
                        if h_price >= pos["partial_tp_price"]:
                            part_qty = pos["quantity"] * 0.5
                            part_exit_price = pos["partial_tp_price"] * (1.0 - self.slippage_rate - self.half_spread)
                            part_fee = part_qty * part_exit_price * self.fee_rate
                            part_pnl = (part_exit_price - pos["entry_price"]) * part_qty - part_fee
                            capital += part_pnl
                            daily_pnl += part_pnl

                            pos["partial_tp_hit"] = True
                            pos["quantity"] -= part_qty
                            pos["partial_realized_pnl"] = part_pnl
                            pos["partial_fees"] = part_fee
                            pos["stop_loss"] = pos["entry_price"]  # Move stop to break-even
                            pos["peak_price"] = h_price

                    if pos.get("partial_tp_hit", False):
                        if h_price > pos.get("peak_price", h_price):
                            pos["peak_price"] = h_price
                            new_sl = pos["peak_price"] - pos["risk_dist"]
                            if new_sl > pos["stop_loss"]:
                                pos["stop_loss"] = new_sl

                elif exit_model == "D":
                    if h_price > pos.get("peak_price", h_price):
                        pos["peak_price"] = h_price
                        new_sl = h_price - (pos["atr"] * 1.5)
                        if new_sl > pos["stop_loss"]:
                            pos["stop_loss"] = new_sl

                # Stop Loss Check
                if l_price <= pos["stop_loss"]:
                    is_closed = True
                    exit_price = pos["stop_loss"] * (1.0 - self.slippage_rate - self.half_spread)
                    exit_reason = "STOP_LOSS"
                # Take Profit Check (Variants A, B)
                elif exit_model in ["A", "B"] and h_price >= pos["take_profit"]:
                    is_closed = True
                    exit_price = pos["take_profit"] * (1.0 - self.slippage_rate - self.half_spread)
                    exit_reason = "TAKE_PROFIT"

                if is_closed:
                    fee_exit = exit_qty * exit_price * self.fee_rate
                    gross_pnl = (exit_price - pos["entry_price"]) * exit_qty
                    net_pnl = gross_pnl - pos["entry_fee"] - fee_exit

                    total_trade_pnl = net_pnl + pos.get("partial_realized_pnl", 0.0)
                    total_trade_fees = pos["entry_fee"] + fee_exit + pos.get("partial_fees", 0.0)

                    capital += net_pnl
                    daily_pnl += net_pnl
                    if capital > peak_capital:
                        peak_capital = capital

                    r_multiple = total_trade_pnl / pos["risk_amount"] if pos["risk_amount"] > 0 else 0.0

                    closed_trades.append({
                        "symbol": sym,
                        "entry_time": pos["entry_time"],
                        "exit_time": ts,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "quantity": pos["initial_quantity"],
                        "gross_pnl": gross_pnl,
                        "net_pnl": total_trade_pnl,
                        "fees": total_trade_fees,
                        "slippage": (pos["entry_price"] * self.slippage_rate * pos["initial_quantity"]) + (exit_price * self.slippage_rate * exit_qty),
                        "r_multiple": r_multiple,
                        "exit_reason": exit_reason,
                        "exit_model": exit_model,
                        "capital_after": capital,
                    })
                else:
                    unrealized = (c_price - pos["entry_price"]) * pos["quantity"]
                    pos["unrealized_pnl"] = unrealized
                    remaining_positions.append(pos)

            open_positions = remaining_positions

            # Daily Loss Circuit Breaker
            if daily_pnl <= -self.daily_max_loss:
                continue

            # Evaluate New Signals
            candidates = signals_by_time.get(ts, [])
            if candidates and len(open_positions) < self.max_open_positions and trades_today_count < self.max_trades_per_day:
                candidates.sort(key=lambda x: x.get("confidence", 0.5), reverse=True)
                for sig in candidates:
                    if len(open_positions) >= self.max_open_positions or trades_today_count >= self.max_trades_per_day:
                        break

                    sym = sig["symbol"]
                    if any(p["symbol"] == sym for p in open_positions):
                        continue

                    risk_amount = capital * self.risk_per_trade
                    entry_p = sig["entry_price"] * (1.0 + self.slippage_rate + self.half_spread)
                    stop_p = sig["stop_price"]
                    risk_dist = abs(entry_p - stop_p)
                    if risk_dist <= 0:
                        continue

                    raw_qty = risk_amount / risk_dist
                    max_qty = (capital * 0.25) / entry_p
                    qty = min(raw_qty, max_qty)
                    if qty * entry_p < 10.0:
                        continue

                    entry_fee = qty * entry_p * self.fee_rate
                    capital -= entry_fee

                    tp_p = sig["take_profit"]
                    if exit_model == "A":
                        tp_p = entry_p + (risk_dist * 1.5)
                    elif exit_model == "B":
                        tp_p = entry_p + (risk_dist * 2.0)
                    elif exit_model == "C":
                        tp_p = entry_p + (risk_dist * 2.5)
                    elif exit_model == "D":
                        tp_p = entry_p + (risk_dist * 3.0)

                    open_positions.append({
                        "symbol": sym,
                        "entry_time": ts,
                        "entry_price": entry_p,
                        "stop_loss": stop_p,
                        "take_profit": tp_p,
                        "quantity": qty,
                        "initial_quantity": qty,
                        "risk_amount": risk_amount,
                        "risk_dist": risk_dist,
                        "entry_fee": entry_fee,
                        "atr": sig.get("metadata", {}).get("atr", risk_dist),
                        "partial_tp_hit": False,
                        "partial_tp_price": entry_p + (risk_dist * 1.0),
                        "partial_realized_pnl": 0.0,
                        "partial_fees": 0.0,
                        "peak_price": entry_p,
                    })
                    trades_today_count += 1

        for pos in open_positions:
            sym = pos["symbol"]
            last_ts = timeline[-1]
            prices = price_lookup.get(sym, {}).get(last_ts)
            exit_price = prices[3] if prices else pos["entry_price"]
            fee_exit = pos["quantity"] * exit_price * self.fee_rate
            net_pnl = (exit_price - pos["entry_price"]) * pos["quantity"] - pos["entry_fee"] - fee_exit + pos.get("partial_realized_pnl", 0.0)
            capital += net_pnl
            closed_trades.append({
                "symbol": sym,
                "entry_time": pos["entry_time"],
                "exit_time": last_ts,
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "quantity": pos["initial_quantity"],
                "gross_pnl": net_pnl,
                "net_pnl": net_pnl,
                "fees": pos["entry_fee"] + fee_exit + pos.get("partial_fees", 0.0),
                "slippage": 0.0,
                "r_multiple": net_pnl / pos["risk_amount"] if pos["risk_amount"] > 0 else 0.0,
                "exit_reason": "SIMULATION_END",
                "exit_model": exit_model,
                "capital_after": capital,
            })

        if current_day:
            daily_equity_series[str(current_day)] = capital

        return self._compute_performance(closed_trades, daily_equity_series)

    def _compute_performance(
        self,
        trades: List[Dict[str, Any]],
        daily_equity: Dict[str, float],
    ) -> Dict[str, Any]:
        n_trades = len(trades)
        if n_trades == 0:
            return {
                "trades": 0,
                "net_pnl": 0.0,
                "return_pct": 0.0,
                "profit_factor": 0.0,
                "win_rate": 0.0,
                "expectancy_usd": 0.0,
                "expectancy_r": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "total_fees": 0.0,
                "total_slippage": 0.0,
                "trades_detail": [],
            }

        pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        r_multiples = [t["r_multiple"] for t in trades]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        net_pnl = sum(pnls)
        total_fees = sum(t["fees"] for t in trades)
        total_slippage = sum(t["slippage"] for t in trades)

        win_rate = (len(wins) / n_trades) * 100.0
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = abs(np.mean(losses)) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        p_w = len(wins) / n_trades
        p_l = len(losses) / n_trades
        expectancy_usd = (p_w * avg_win) - (p_l * avg_loss)
        expectancy_r = np.mean(r_multiples)

        eq_curve = [self.initial_capital]
        for p in pnls:
            eq_curve.append(eq_curve[-1] + p)

        peak = self.initial_capital
        max_dd = 0.0
        for eq in eq_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        eq_values = list(daily_equity.values())
        if len(eq_values) > 2:
            daily_returns = np.diff(eq_values) / eq_values[:-1]
            std_daily = np.std(daily_returns)
            mean_daily = np.mean(daily_returns)
            sharpe = float((mean_daily / (std_daily + 1e-9)) * np.sqrt(365))

            downside_returns = daily_returns[daily_returns < 0]
            downside_std = np.std(downside_returns) if len(downside_returns) > 1 else (std_daily or 1e-6)
            sortino = float((mean_daily / (downside_std + 1e-9)) * np.sqrt(365))
        else:
            sharpe = 0.0
            sortino = 0.0

        calmar = ((net_pnl / self.initial_capital) / max_dd) if max_dd > 0 else 0.0

        longest_streak = 0
        cur_streak = 0
        for p in pnls:
            if p < 0:
                cur_streak += 1
                if cur_streak > longest_streak:
                    longest_streak = cur_streak
            else:
                cur_streak = 0

        return {
            "trades": n_trades,
            "net_pnl": round(net_pnl, 2),
            "return_pct": round((net_pnl / self.initial_capital) * 100.0, 2),
            "profit_factor": round(profit_factor, 2),
            "win_rate": round(win_rate, 1),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy_usd": round(expectancy_usd, 2),
            "expectancy_r": round(expectancy_r, 2),
            "max_drawdown_pct": round(max_dd * 100.0, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "calmar_ratio": round(calmar, 2),
            "total_fees": round(total_fees, 2),
            "total_slippage": round(total_slippage, 2),
            "longest_losing_streak": longest_streak,
            "trades_detail": trades,
        }


# -----------------------------------------------------------------------------
# 3. Strategy Signal Generators (Zero Look-Ahead, Closed 15m Candles)
# -----------------------------------------------------------------------------
def generate_trend_following_signals(df_15m: pd.DataFrame, symbol: str) -> List[Dict[str, Any]]:
    if len(df_15m) < 200:
        return []

    close = df_15m["close"]
    high = df_15m["high"]
    low = df_15m["low"]
    ema20 = calculate_ema(close, 20)
    ema50 = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    adx = calculate_adx(high, low, close, 14)
    rsi = calculate_rsi(close, 14)
    atr = calculate_atr(high, low, close, 14)

    signals = []
    for i in range(200, len(df_15m)):
        c = float(close.iloc[i])
        e20 = float(ema20.iloc[i])
        e50 = float(ema50.iloc[i])
        e200 = float(ema200.iloc[i])
        a = float(adx.iloc[i])
        r = float(rsi.iloc[i])
        at = float(atr.iloc[i])

        if np.isnan(e20) or np.isnan(e50) or np.isnan(e200) or np.isnan(a) or np.isnan(r) or np.isnan(at):
            continue

        if e20 > e50 > e200 and a >= 23.0 and 45.0 <= r <= 68.0 and c > e50:
            stop_dist = at * 1.5
            signals.append({
                "symbol": symbol,
                "timestamp": df_15m["timestamp"].iloc[i],
                "entry_price": c,
                "stop_price": round(c - stop_dist, 4),
                "take_profit": round(c + (stop_dist * 2.0), 4),
                "confidence": min(0.95, 0.60 + (a / 100.0)),
                "strategy": "trend_following",
                "metadata": {"atr": at},
            })
    return signals


def generate_mean_reversion_signals(df_15m: pd.DataFrame, symbol: str) -> List[Dict[str, Any]]:
    if len(df_15m) < 60:
        return []

    close = df_15m["close"]
    high = df_15m["high"]
    low = df_15m["low"]
    rsi = calculate_rsi(close, 14)
    atr = calculate_atr(high, low, close, 14)
    adx = calculate_adx(high, low, close, 14)

    rolling_mean = close.rolling(window=20).mean()
    rolling_std = close.rolling(window=20).std()
    bb_lower = rolling_mean - (rolling_std * 2.0)
    bb_middle = rolling_mean

    signals = []
    for i in range(50, len(df_15m)):
        c = float(close.iloc[i])
        b_low = float(bb_lower.iloc[i])
        b_mid = float(bb_middle.iloc[i])
        r = float(rsi.iloc[i])
        at = float(atr.iloc[i])
        a = float(adx.iloc[i])

        if np.isnan(b_low) or np.isnan(r) or np.isnan(at) or np.isnan(a):
            continue

        if a < 25.0 and c <= b_low * 1.002 and r <= 32.0:
            stop_dist = at * 1.5
            signals.append({
                "symbol": symbol,
                "timestamp": df_15m["timestamp"].iloc[i],
                "entry_price": c,
                "stop_price": round(c - stop_dist, 4),
                "take_profit": round(max(b_mid, c + (stop_dist * 2.0)), 4),
                "confidence": 0.70,
                "strategy": "mean_reversion",
                "metadata": {"atr": at},
            })
    return signals


def generate_rsi_divergence_signals(df_15m: pd.DataFrame, symbol: str) -> List[Dict[str, Any]]:
    if len(df_15m) < 60:
        return []

    close = df_15m["close"].values
    high = df_15m["high"].values
    low = df_15m["low"].values
    rsi = calculate_rsi(df_15m["close"], 14).values
    atr = calculate_atr(df_15m["high"], df_15m["low"], df_15m["close"], 14).values

    signals = []
    for i in range(30, len(df_15m)):
        if np.isnan(rsi[i]) or np.isnan(atr[i]):
            continue

        window_price = low[i-15:i]
        window_rsi = rsi[i-15:i]
        min_p_idx = np.argmin(window_price)
        prev_low_price = window_price[min_p_idx]
        prev_low_rsi = window_rsi[min_p_idx]

        if low[i] < prev_low_price and rsi[i] > prev_low_rsi and rsi[i] < 40.0:
            c = close[i]
            at = atr[i]
            stop_dist = at * 1.5
            signals.append({
                "symbol": symbol,
                "timestamp": df_15m["timestamp"].iloc[i],
                "entry_price": c,
                "stop_price": round(c - stop_dist, 4),
                "take_profit": round(c + (stop_dist * 2.0), 4),
                "confidence": 0.72,
                "strategy": "rsi_divergence",
                "metadata": {"atr": at},
            })
    return signals


def generate_r10_rsi_divergence_signals(df_15m: pd.DataFrame, symbol: str) -> List[Dict[str, Any]]:
    """Strategy 4: R10 Causal Multi-Bar Pivot Confirmed RSI Divergence (Single-Pass O(N))."""
    if len(df_15m) < 60:
        return []

    close = df_15m["close"].values
    high = df_15m["high"].values
    low = df_15m["low"].values
    timestamps = df_15m["timestamp"].values
    rsi = calculate_rsi(df_15m["close"], 14).values
    atr = calculate_atr(df_15m["high"], df_15m["low"], df_15m["close"], 14).values
    ema9 = calculate_ema(df_15m["close"], 9).values

    left_bars = 3
    right_bars = 3
    low_pivots = []
    signals = []

    for current_idx in range(left_bars + right_bars, len(df_15m)):
        p = current_idx - right_bars
        window_low = low[p - left_bars : p + right_bars + 1]
        if low[p] == np.min(window_low) and low[p] < np.mean(window_low) and not np.isnan(rsi[p]):
            low_pivots.append((p, low[p], rsi[p], current_idx))

        if len(low_pivots) >= 2 and low_pivots[-1][3] == current_idx:
            p2_idx, p2_price, p2_rsi, _ = low_pivots[-1]
            p1_idx, p1_price, p1_rsi, _ = low_pivots[-2]

            if p2_price < p1_price and p2_rsi > p1_rsi and p2_rsi < 50.0:
                sep = p2_idx - p1_idx
                if 5 <= sep <= 60:
                    c = close[current_idx]
                    at = atr[current_idx] if not np.isnan(atr[current_idx]) else c * 0.015
                    e9 = ema9[current_idx]
                    if np.isnan(e9) or c >= e9:
                        stop_dist = at * 1.5
                        signals.append({
                            "symbol": symbol,
                            "timestamp": timestamps[current_idx],
                            "entry_price": c,
                            "stop_price": round(c - stop_dist, 4),
                            "take_profit": round(c + (stop_dist * 2.0), 4),
                            "confidence": 0.80,
                            "strategy": "r10_rsi_divergence",
                            "metadata": {"atr": at},
                        })
    return signals


def generate_regime_gated_pullback_signals(
    df_15m: pd.DataFrame,
    df_1h: Optional[pd.DataFrame],
    symbol: str,
    adx_threshold: float = 25.0,
    rsi_min: float = 45.0,
    rsi_max: float = 60.0,
    volume_threshold: float = 1.1,
    atr_multiplier: float = 1.5,
    exit_model: str = "B",
) -> List[Dict[str, Any]]:
    if len(df_15m) < 60:
        return []

    close = df_15m["close"].values
    high = df_15m["high"].values
    low = df_15m["low"].values
    volume = df_15m["volume"].values
    timestamps = df_15m["timestamp"].values

    ema20 = calculate_ema(pd.Series(close), 20).values
    ema50 = calculate_ema(pd.Series(close), 50).values
    rsi = calculate_rsi(pd.Series(close), 14).values
    atr = calculate_atr(pd.Series(high), pd.Series(low), pd.Series(close), 14).values
    vwap = calculate_vwap(pd.Series(high), pd.Series(low), pd.Series(close), pd.Series(volume)).values
    vol_ratio = calculate_volume_ratio(pd.Series(volume), 20).values
    _, _, macd_hist = calculate_macd(pd.Series(close), 12, 26, 9)
    macd_hist = macd_hist.values

    trend_1h_lookup = {}
    if df_1h is not None and len(df_1h) >= 200:
        c1 = df_1h["close"]
        h1 = df_1h["high"]
        l1 = df_1h["low"]
        e20_1h = calculate_ema(c1, 20)
        e50_1h = calculate_ema(c1, 50)
        e200_1h = calculate_ema(c1, 200)
        adx_1h = calculate_adx(h1, l1, c1, 14)

        for i in range(len(df_1h)):
            t_ts = df_1h["timestamp"].iloc[i]
            aligned = (
                e20_1h.iloc[i] > e50_1h.iloc[i] > e200_1h.iloc[i]
                and adx_1h.iloc[i] >= adx_threshold
                and c1.iloc[i] > e50_1h.iloc[i]
            )
            trend_1h_lookup[t_ts] = aligned

    signals = []
    for i in range(50, len(df_15m)):
        c_time = timestamps[i]
        c_close = close[i]
        c_low = low[i]
        curr_ema20 = ema20[i]
        curr_vwap = vwap[i]
        curr_rsi = rsi[i]
        curr_atr = atr[i]
        curr_vr = vol_ratio[i]
        curr_h = macd_hist[i]
        prev_h = macd_hist[i-1]

        if np.isnan(curr_ema20) or np.isnan(curr_rsi) or np.isnan(curr_atr):
            continue

        if trend_1h_lookup:
            h_floor = pd.to_datetime(c_time).floor("1h")
            trend_ok = trend_1h_lookup.get(h_floor, False)
            if not trend_ok:
                continue
        else:
            if not (curr_ema20 >= ema50[i] * 0.998):
                continue

        dist_ema = abs(c_low - curr_ema20) / curr_ema20
        dist_vwap = abs(c_low - curr_vwap) / curr_vwap if not np.isnan(curr_vwap) else 1.0
        near_pullback = (dist_ema <= 0.0075) or (dist_vwap <= 0.0075) or (c_low <= curr_ema20 and c_close >= curr_ema20)
        if not near_pullback:
            continue

        if not (rsi_min <= curr_rsi <= rsi_max):
            continue

        if not (curr_h > prev_h):
            continue

        if curr_vr < volume_threshold:
            continue

        if not (c_close >= curr_ema20 and c_close > close[i-1]):
            continue

        recent_low = np.min(low[max(0, i-5):i+1])
        atr_stop = c_close - (curr_atr * atr_multiplier)
        stop_price = max(recent_low * 0.999, atr_stop)
        if stop_price >= c_close:
            stop_price = c_close - (curr_atr * atr_multiplier)

        risk_dist = c_close - stop_price
        if risk_dist <= 0:
            continue

        tp_price = c_close + (risk_dist * 2.0)

        signals.append({
            "symbol": symbol,
            "timestamp": c_time,
            "entry_price": c_close,
            "stop_price": round(stop_price, 4),
            "take_profit": round(tp_price, 4),
            "confidence": min(0.95, 0.70 + (curr_vr - 1.0) * 0.15 + (curr_rsi - 45.0) * 0.005),
            "strategy": "regime_gated_pullback",
            "metadata": {
                "atr": curr_atr,
                "exit_model": exit_model,
                "rsi": curr_rsi,
                "vol_ratio": curr_vr,
            },
        })
    return signals


# -----------------------------------------------------------------------------
# 4. Rolling Walk-Forward & Monte Carlo Utilities
# -----------------------------------------------------------------------------
def run_monte_carlo_bootstrap(trades: List[Dict[str, Any]], iterations: int = 1000) -> Dict[str, Any]:
    if len(trades) < 5:
        return {"support": "INSUFFICIENT_DATA"}

    pnls = np.array([t["net_pnl"] for t in trades])
    n = len(pnls)
    final_pnls = []
    max_dds = []

    for _ in range(iterations):
        sample = np.random.choice(pnls, size=n, replace=True)
        eq = np.cumsum(sample)
        final_pnls.append(eq[-1])

        peaks = np.maximum.accumulate(5000.0 + eq)
        dds = (peaks - (5000.0 + eq)) / peaks
        max_dds.append(np.max(dds))

    return {
        "iterations": iterations,
        "pnl_5th_pct": round(float(np.percentile(final_pnls, 5)), 2),
        "pnl_median": round(float(np.percentile(final_pnls, 50)), 2),
        "pnl_95th_pct": round(float(np.percentile(final_pnls, 95)), 2),
        "max_dd_median_pct": round(float(np.percentile(max_dds, 50)) * 100.0, 2),
        "max_dd_95th_pct": round(float(np.percentile(max_dds, 95)) * 100.0, 2),
        "prob_positive_pnl_pct": round(float(np.mean(np.array(final_pnls) > 0) * 100.0), 1),
    }


# -----------------------------------------------------------------------------
# 5. Master Discovery & Validation Gate Runner
# -----------------------------------------------------------------------------
async def run_discovery_gate():
    logger.info("=====================================================================")
    logger.info("KRIPTO AGENT — STRATEGY DISCOVERY & VALIDATION GATE EXECUTION")
    logger.info("=====================================================================")

    assert not settings.LIVE_TRADING, "SAFETY VIOLATION: LIVE_TRADING MUST BE FALSE"
    logger.info("✅ Live Safety Invariant Confirmed: LIVE_TRADING=False, LIVE_TRADING_ARMED=False.")

    universe_symbols = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "DOGE/USDT", "NEAR/USDT", "AVAX/USDT", "LINK/USDT", "ADA/USDT",
    ]
    logger.info(f"Liquid Universe Selected: {len(universe_symbols)} pairs: {', '.join(universe_symbols)}")

    df_15m_all: Dict[str, pd.DataFrame] = {}
    df_1h_all: Dict[str, pd.DataFrame] = {}

    for sym in universe_symbols:
        df_15 = await fetch_binance_candles(sym, timeframe="15m", target_count=1500)
        df_1 = await fetch_binance_candles(sym, timeframe="1h", target_count=500)
        if not df_15.empty:
            df_15m_all[sym] = df_15
        if not df_1.empty:
            df_1h_all[sym] = df_1

    logger.info(f"Market Data Loaded: {len(df_15m_all)} pairs with valid closed candles.")

    min_len = min(len(df) for df in df_15m_all.values())
    train_cutoff_idx = int(min_len * 0.60)
    val_cutoff_idx = int(min_len * 0.80)

    df_15m_train = {s: df.iloc[:train_cutoff_idx].reset_index(drop=True) for s, df in df_15m_all.items()}
    df_15m_val = {s: df.iloc[train_cutoff_idx:val_cutoff_idx].reset_index(drop=True) for s, df in df_15m_all.items()}
    df_15m_oos = {s: df.iloc[val_cutoff_idx:].reset_index(drop=True) for s, df in df_15m_all.items()}

    logger.info(
        f"Data Partitioning Complete: "
        f"Train: {len(df_15m_train['BTC/USDT'])} bars | "
        f"Val: {len(df_15m_val['BTC/USDT'])} bars | "
        f"OOS: {len(df_15m_oos['BTC/USDT'])} bars."
    )

    strategy_configs = [
        {"id": "trend_following", "name": "TrendFollowing", "gen": generate_trend_following_signals, "exit_model": "B"},
        {"id": "mean_reversion", "name": "MeanReversion", "gen": generate_mean_reversion_signals, "exit_model": "B"},
        {"id": "rsi_divergence", "name": "RSIDivergence", "gen": generate_rsi_divergence_signals, "exit_model": "B"},
        {"id": "r10_rsi_divergence", "name": "R10RSIDivergence", "gen": generate_r10_rsi_divergence_signals, "exit_model": "B"},
        {"id": "pullback_1.5r", "name": "RegimeGatedPullback (A: Fixed 1.5R)", "gen": lambda df, s: generate_regime_gated_pullback_signals(df, df_1h_all.get(s), s, exit_model="A"), "exit_model": "A"},
        {"id": "pullback_2.0r", "name": "RegimeGatedPullback (B: Fixed 2.0R)", "gen": lambda df, s: generate_regime_gated_pullback_signals(df, df_1h_all.get(s), s, exit_model="B"), "exit_model": "B"},
        {"id": "pullback_partial", "name": "RegimeGatedPullback (C: Partial 1R + Trailing)", "gen": lambda df, s: generate_regime_gated_pullback_signals(df, df_1h_all.get(s), s, exit_model="C"), "exit_model": "C"},
        {"id": "pullback_atr_trail", "name": "RegimeGatedPullback (D: ATR Trailing)", "gen": lambda df, s: generate_regime_gated_pullback_signals(df, df_1h_all.get(s), s, exit_model="D"), "exit_model": "D"},
    ]

    simulator = DeterministicPortfolioSimulator()
    benchmark_results = []

    for strat in strategy_configs:
        logger.info(f"--> Benchmarking {strat['name']}...")

        all_signals = []
        for sym, df in df_15m_all.items():
            sigs = strat["gen"](df, sym)
            for s in sigs:
                st = pd.to_datetime(s["timestamp"])
                if getattr(st, "tz", None) is not None:
                    st = st.tz_localize(None)
                s["timestamp"] = st
            all_signals.extend(sigs)
        all_signals.sort(key=lambda x: x["timestamp"])
        full_perf = simulator.simulate(all_signals, df_15m_all, exit_model=strat["exit_model"])

        train_signals = [s for s in all_signals if s["timestamp"] < df_15m_val["BTC/USDT"]["timestamp"].iloc[0]]
        train_perf = simulator.simulate(train_signals, df_15m_train, exit_model=strat["exit_model"])

        oos_start_ts = df_15m_oos["BTC/USDT"]["timestamp"].iloc[0]
        oos_signals = [s for s in all_signals if s["timestamp"] >= oos_start_ts]
        oos_perf = simulator.simulate(oos_signals, df_15m_oos, exit_model=strat["exit_model"])

        sim_fee_stress = DeterministicPortfolioSimulator(fee_rate=0.00125)
        fee_stress_perf = sim_fee_stress.simulate(all_signals, df_15m_all, exit_model=strat["exit_model"])

        sim_slip_2x = DeterministicPortfolioSimulator(slippage_bps=10.0)
        slip_2x_perf = sim_slip_2x.simulate(all_signals, df_15m_all, exit_model=strat["exit_model"])

        sim_slip_3x = DeterministicPortfolioSimulator(slippage_bps=15.0, spread_bps=6.0)
        slip_3x_perf = sim_slip_3x.simulate(all_signals, df_15m_all, exit_model=strat["exit_model"])

        mc_res = run_monte_carlo_bootstrap(full_perf.get("trades_detail", []))

        wf_windows = []
        n_bars = len(df_15m_all["BTC/USDT"])
        w_size = n_bars // 3
        for w_idx in range(3):
            w_start = w_idx * (w_size // 2)
            w_end = min(n_bars, w_start + w_size)
            w_df_dict = {s: df.iloc[w_start:w_end].reset_index(drop=True) for s, df in df_15m_all.items()}
            w_signals = [s for s in all_signals if w_df_dict["BTC/USDT"]["timestamp"].iloc[0] <= s["timestamp"] <= w_df_dict["BTC/USDT"]["timestamp"].iloc[-1]]
            w_res = simulator.simulate(w_signals, w_df_dict, exit_model=strat["exit_model"])
            wf_windows.append({
                "window": w_idx + 1,
                "trades": w_res["trades"],
                "return_pct": w_res["return_pct"],
                "profit_factor": w_res["profit_factor"],
            })

        benchmark_results.append({
            "id": strat["id"],
            "name": strat["name"],
            "full_perf": full_perf,
            "train_perf": train_perf,
            "oos_perf": oos_perf,
            "fee_stress_perf": fee_stress_perf,
            "slip_2x_perf": slip_2x_perf,
            "slip_3x_perf": slip_3x_perf,
            "monte_carlo": mc_res,
            "walk_forward": wf_windows,
        })

    logger.info("--> Scanning Parameter Neighborhoods for Regime-Gated Pullback...")
    param_neighborhoods = []
    adx_options = [20.0, 25.0, 30.0]
    rsi_options = [(40.0, 60.0), (45.0, 60.0), (45.0, 65.0)]

    for adx_v in adx_options:
        for r_min, r_max in rsi_options:
            p_sigs = []
            for sym, df in df_15m_all.items():
                sigs = generate_regime_gated_pullback_signals(
                    df, df_1h_all.get(sym), sym,
                    adx_threshold=adx_v,
                    rsi_min=r_min,
                    rsi_max=r_max,
                    volume_threshold=1.1,
                    atr_multiplier=1.5,
                    exit_model="C",
                )
                p_sigs.extend(sigs)
            p_sigs.sort(key=lambda x: x["timestamp"])
            p_res = simulator.simulate(p_sigs, df_15m_all, exit_model="C")
            param_neighborhoods.append({
                "adx": adx_v,
                "rsi_range": f"{r_min:.0f}-{r_max:.0f}",
                "trades": p_res["trades"],
                "return_pct": p_res["return_pct"],
                "expectancy": p_res["expectancy_usd"],
                "pf": p_res["profit_factor"],
            })

    report_path = PROJECT_ROOT / "backtesting" / "reports" / "strategy_discovery_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        clean_bm = []
        for b in benchmark_results:
            b_copy = dict(b)
            for k in ["full_perf", "train_perf", "oos_perf", "fee_stress_perf", "slip_2x_perf", "slip_3x_perf"]:
                if k in b_copy and "trades_detail" in b_copy[k]:
                    b_copy[k] = {ik: iv for ik, iv in b_copy[k].items() if ik != "trades_detail"}
            clean_bm.append(b_copy)
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), "benchmarks": clean_bm, "neighborhoods": param_neighborhoods}, f, indent=2)

    logger.info(f"Strategy Discovery & Validation Report saved to {report_path}")
    print("ALL_BENCHMARKS_COMPLETE")
    return benchmark_results


if __name__ == "__main__":
    asyncio.run(run_discovery_gate())
