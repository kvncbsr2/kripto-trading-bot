from typing import Any, Dict

import pandas as pd


class RSIDivergenceDetector:
    """
    RSI Divergence Engine.
    Detects regular Bullish and Bearish divergences between price action and RSI.
    """

    @staticmethod
    def detect_divergence(
        low: pd.Series,
        high: pd.Series,
        close: pd.Series,
        rsi: pd.Series,
        window: int = 4,
        lookback: int = 35,
    ) -> Dict[str, Any]:
        n = len(close)
        result = {
            "bullish_divergence": False,
            "bearish_divergence": False,
            "divergence_score": 0.0,
            "description": "No divergence detected",
            "price_points": (),
            "rsi_points": (),
        }

        if n < lookback:
            return result

        # Analyze the lookback window
        window_low = low.iloc[-lookback:]
        window_high = high.iloc[-lookback:]
        window_rsi = rsi.iloc[-lookback:]

        # Find swing lows for Bullish Divergence
        swing_low_indices = []
        for i in range(window, len(window_low) - window):
            curr_val = window_low.iloc[i]
            if curr_val == min(window_low.iloc[i - window : i + window + 1]):
                swing_low_indices.append(i)

        if len(swing_low_indices) >= 2:
            prev_idx = swing_low_indices[-2]
            curr_idx = swing_low_indices[-1]

            p_prev = window_low.iloc[prev_idx]
            p_curr = window_low.iloc[curr_idx]

            rsi_prev = window_rsi.iloc[prev_idx]
            rsi_curr = window_rsi.iloc[curr_idx]

            # Regular Bullish Divergence: Price Lower Low (p_curr < p_prev), RSI Higher Low (rsi_curr > rsi_prev)
            if p_curr < p_prev and rsi_curr > rsi_prev and rsi_curr < 50.0:
                result["bullish_divergence"] = True
                rsi_diff = rsi_curr - rsi_prev
                price_drop_pct = (p_prev - p_curr) / p_prev
                # Score 10 - 20 pts
                score = min(20.0, 10.0 + (rsi_diff * 0.5) + (price_drop_pct * 100))
                result["divergence_score"] = round(score, 1)
                result["description"] = (
                    f"Bullish RSI Divergence: Price LL ({p_prev:.2f}->{p_curr:.2f}) with RSI HL ({rsi_prev:.1f}->{rsi_curr:.1f})"
                )
                result["price_points"] = (p_prev, p_curr)
                result["rsi_points"] = (rsi_prev, rsi_curr)
                return result

        # Find swing highs for Bearish Divergence
        swing_high_indices = []
        for i in range(window, len(window_high) - window):
            curr_val = window_high.iloc[i]
            if curr_val == max(window_high.iloc[i - window : i + window + 1]):
                swing_high_indices.append(i)

        if len(swing_high_indices) >= 2:
            prev_idx = swing_high_indices[-2]
            curr_idx = swing_high_indices[-1]

            p_prev = window_high.iloc[prev_idx]
            p_curr = window_high.iloc[curr_idx]

            rsi_prev = window_rsi.iloc[prev_idx]
            rsi_curr = window_rsi.iloc[curr_idx]

            # Regular Bearish Divergence: Price Higher High (p_curr > p_prev), RSI Lower High (rsi_curr < rsi_prev)
            if p_curr > p_prev and rsi_curr < rsi_prev and rsi_curr > 50.0:
                result["bearish_divergence"] = True
                rsi_diff = rsi_prev - rsi_curr
                price_rise_pct = (p_curr - p_prev) / p_prev
                score = min(20.0, 10.0 + (rsi_diff * 0.5) + (price_rise_pct * 100))
                result["divergence_score"] = round(score, 1)
                result["description"] = (
                    f"Bearish RSI Divergence: Price HH ({p_prev:.2f}->{p_curr:.2f}) with RSI LH ({rsi_prev:.1f}->{rsi_curr:.1f})"
                )
                result["price_points"] = (p_prev, p_curr)
                result["rsi_points"] = (rsi_prev, rsi_curr)
                return result

        return result
