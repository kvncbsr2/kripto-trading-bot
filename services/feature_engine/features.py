from typing import List, Optional

import pandas as pd

from services.feature_engine.indicators.divergence import RSIDivergenceDetector
from services.feature_engine.indicators.market_structure import analyze_market_structure
from services.feature_engine.indicators.momentum import calculate_macd, calculate_roc, calculate_rsi
from services.feature_engine.indicators.trend import calculate_adx, calculate_ema
from services.feature_engine.volatility.volatility import (
    calculate_atr,
    calculate_bollinger_bands,
    calculate_realized_volatility,
)
from services.feature_engine.volume.volume import (
    calculate_volume_ratio,
    calculate_volume_sma,
    calculate_vwap,
)
from shared.enums import Timeframe
from shared.schemas import Candle, FeatureVector


class FeatureEngine:
    @staticmethod
    def candles_to_dataframe(candles: List[Candle]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        data = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
        df = pd.DataFrame(data)
        df.sort_values("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    @classmethod
    def compute_features(cls, candles: List[Candle]) -> pd.DataFrame:
        df = cls.candles_to_dataframe(candles)
        if len(df) < 5:
            return df

        # Trend
        df["ema_20"] = calculate_ema(df["close"], 20)
        df["ema_50"] = calculate_ema(df["close"], 50)
        df["ema_200"] = calculate_ema(df["close"], 200)
        df["sma_20"] = df["close"].rolling(window=20).mean()
        df["sma_50"] = df["close"].rolling(window=50).mean()
        df["adx"] = calculate_adx(df["high"], df["low"], df["close"], 14)

        # Momentum
        df["rsi"] = calculate_rsi(df["close"], 14)
        macd_line, macd_sig, macd_hist = calculate_macd(df["close"], 12, 26, 9)
        df["macd_line"] = macd_line
        df["macd_signal"] = macd_sig
        df["macd_histogram"] = macd_hist
        df["roc"] = calculate_roc(df["close"], 12)

        # Volatility
        df["atr"] = calculate_atr(df["high"], df["low"], df["close"], 14)
        bb_upper, bb_mid, bb_lower, bb_width = calculate_bollinger_bands(df["close"], 20, 2.0)
        df["bb_upper"] = bb_upper
        df["bb_middle"] = bb_mid
        df["bb_lower"] = bb_lower
        df["bb_bandwidth"] = bb_width
        df["realized_vol"] = calculate_realized_volatility(df["close"], 20)

        # Volume
        df["volume_sma"] = calculate_volume_sma(df["volume"], 20)
        df["volume_ratio"] = calculate_volume_ratio(df["volume"], 20)
        df["vwap"] = calculate_vwap(df["high"], df["low"], df["close"], df["volume"])

        return df

    @classmethod
    def get_latest_feature_vector(
        cls,
        candles: List[Candle],
        symbol: str,
        timeframe: Timeframe,
    ) -> Optional[FeatureVector]:
        df = cls.compute_features(candles)
        if df.empty or len(df) < 5:
            return None

        last_row = df.iloc[-1]
        indicators = {
            col: float(last_row[col])
            for col in df.columns
            if col not in ["timestamp"] and not pd.isna(last_row[col])
        }

        # Market structure
        struct = analyze_market_structure(df["high"], df["low"], df["close"])
        for k, v in struct.items():
            indicators[k] = 1.0 if v else 0.0

        # RSI Divergence detection
        if len(df) >= 20 and "rsi" in df.columns:
            div = RSIDivergenceDetector.detect_divergence(
                df["low"], df["high"], df["close"], df["rsi"]
            )
            indicators["bullish_divergence"] = 1.0 if div["bullish_divergence"] else 0.0
            indicators["bearish_divergence"] = 1.0 if div["bearish_divergence"] else 0.0
            indicators["divergence_score"] = float(div["divergence_score"])

        return FeatureVector(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=last_row["timestamp"],
            indicators=indicators,
        )
