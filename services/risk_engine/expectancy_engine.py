"""
Mathematical Expectancy Engine for KRIPTO AGENT.

Implements quant expectancy and breakeven formulas:
  1. Win Rate (w): wins / total
  2. Loss Rate (l): losses / total = 1 - w
  3. Win/Loss Ratio (b): avg_win / avg_loss
  4. Mathematical Breakeven Rate: avg_loss / (avg_win + avg_loss) = 1 / (1 + b)
  5. Mathematical Expectancy: (w * avg_win) - (l * avg_loss)
  6. Expectancy in R: (w * b) - l
  7. Automated "Hemen Dur" Circuit Breaker: If N >= 5 and Expectancy <= 0, halt immediately.
"""

from typing import Any, Dict, List, Optional
from decimal import Decimal, ROUND_HALF_UP
from shared.logging import get_logger

logger = get_logger("expectancy-engine", service="risk_engine")


def _fin_round(val: float, decimals: int = 2) -> float:
    """Standard financial round-half-up for accurate currency & ratio reporting."""
    try:
        dec_exp = Decimal(f"1e-{decimals}")
        return float(Decimal(str(round(val, 6))).quantize(dec_exp, rounding=ROUND_HALF_UP))
    except Exception:
        return round(val, decimals)


class ExpectancyEngine:
    @staticmethod
    def calculate_from_positions(closed_positions: List[Any]) -> Dict[str, Any]:
        """
        Calculates authoritative mathematical expectancy and edge metrics
        from a list of closed positions (dictionaries, SQLite rows, or Position objects).
        """
        records = []
        for p in closed_positions:
            if isinstance(p, dict):
                raw_pnl = p.get("realized_pnl") if p.get("realized_pnl") is not None else p.get("net_pnl", 0.0)
                pnl = float(raw_pnl or 0.0)
                pos_id = str(p.get("position_id") or "")
                symbol = str(p.get("symbol") or "")
            else:
                raw_pnl = getattr(p, "realized_pnl", None)
                if raw_pnl is None:
                    raw_pnl = getattr(p, "net_pnl", 0.0)
                pnl = float(raw_pnl or 0.0)
                pos_id = str(getattr(p, "position_id", ""))
                symbol = str(getattr(p, "symbol", ""))
            records.append({"position_id": pos_id, "symbol": symbol, "realized_pnl": pnl})

        total_trades = len(records)
        if total_trades == 0:
            return {
                "sample_size": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": 0.0,
                "loss_rate_pct": 0.0,
                "average_win_usd": 0.0,
                "average_loss_usd": 0.0,
                "win_loss_ratio_b": 0.0,
                "breakeven_win_rate_pct": 50.0,
                "edge_buffer_pct": 0.0,
                "expectancy_usd_per_trade": 0.0,
                "expectancy_r": 0.0,
                "rolling_5_expectancy_usd": 0.0,
                "is_positive_expectancy": False,
                "should_halt": False,
                "status": "INSUFFICIENT_DATA",
                "status_badge": "BEKLENİYOR",
                "status_message": "Henüz tamamlanmış işlem bulunmuyor. İlk 5 işlem sonrasında matematiksel devre kesici aktif olacaktır.",
                "formula_definition": "Expectancy = (WinRate * AvgWin) - (LossRate * AvgLoss) | Başabaş = AvgLoss / (AvgWin + AvgLoss)",
            }

        wins = [r["realized_pnl"] for r in records if r["realized_pnl"] > 0]
        losses = [abs(r["realized_pnl"]) for r in records if r["realized_pnl"] <= 0]

        winning_trades = len(wins)
        losing_trades = len(losses)

        win_rate = winning_trades / total_trades
        loss_rate = losing_trades / total_trades
        win_rate_pct = round(win_rate * 100.0, 1)
        loss_rate_pct = round(loss_rate * 100.0, 1)

        avg_win = (sum(wins) / winning_trades) if winning_trades > 0 else 0.0
        avg_loss = (sum(losses) / losing_trades) if losing_trades > 0 else 0.0

        # Win/Loss Ratio b = avg_win / avg_loss
        win_loss_ratio_b = (avg_win / avg_loss) if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)

        # Mathematical Breakeven Rate = avg_loss / (avg_win + avg_loss)
        if (avg_win + avg_loss) > 0:
            breakeven_win_rate = avg_loss / (avg_win + avg_loss)
        else:
            breakeven_win_rate = 0.50
        breakeven_win_rate_pct = round(breakeven_win_rate * 100.0, 1)

        # Mathematical Expectancy = (w * avg_win) - (l * avg_loss)
        expectancy_usd = (win_rate * avg_win) - (loss_rate * avg_loss)

        # Expectancy in R-multiples = (w * b) - l
        if avg_loss > 0:
            expectancy_r = (win_rate * win_loss_ratio_b) - loss_rate
        else:
            expectancy_r = win_rate if avg_win > 0 else 0.0

        # Safety Buffer: how far above breakeven we are
        edge_buffer_pct = round(win_rate_pct - breakeven_win_rate_pct, 1)

        is_positive = expectancy_usd > 0.0

        # Rolling 5 trades check (for 'her 5-10 işlemde bir yeniden hesaplayın' rule)
        recent_5 = records[-5:] if total_trades >= 5 else records
        r5_wins = [r["realized_pnl"] for r in recent_5 if r["realized_pnl"] > 0]
        r5_losses = [abs(r["realized_pnl"]) for r in recent_5 if r["realized_pnl"] <= 0]
        r5_w = len(r5_wins) / len(recent_5)
        r5_l = len(r5_losses) / len(recent_5)
        r5_avg_w = (sum(r5_wins) / len(r5_wins)) if r5_wins else 0.0
        r5_avg_l = (sum(r5_losses) / len(r5_losses)) if r5_losses else 0.0
        rolling_5_expectancy = (r5_w * r5_avg_w) - (r5_l * r5_avg_l)

        # Rule: 'Expectancy pozitif kaldığı sürece yöndesiniz; negatife dönerse hemen dur.'
        # Trigger halt only if we have at least 5 closed trades and expectancy is non-positive
        should_halt = (total_trades >= 5 and expectancy_usd <= 0.0)

        if should_halt:
            status = "NEGATIVE_EXPECTANCY_HALTED"
            status_badge = "DURDURMA AKTİF"
            exp_str = f"-${abs(expectancy_usd):.2f}" if expectancy_usd < 0 else f"${expectancy_usd:.2f}"
            status_message = (
                f"🛑 MATEMATİKSEL DEVRE KESİCİ DEVREDE! Beklenen getiri ({exp_str}/işlem) negatife döndü. "
                f"Kazanma oranı (%{win_rate_pct:.1f}), başabaş eşiğinin (%{breakeven_win_rate_pct:.1f}) altına indi. Sermayeyi korumak için alımlar durduruldu!"
            )
            logger.debug(status_message)
        elif is_positive:
            status = "POSITIVE_EDGE"
            status_badge = "YÖNDESİNİZ"
            status_message = (
                f"✅ Yöndesiniz! İşlem başına beklenen getiri: +${expectancy_usd:.2f} (+{expectancy_r:.2f}R). "
                f"b={win_loss_ratio_b:.2f}x sayesinde başabaş eşiği sadece %{breakeven_win_rate_pct:.1f}, mevcut kazanma oranı %{win_rate_pct:.1f} (+%{edge_buffer_pct:.1f} güvenlik marjı)."
            )
        else:
            status = "EARLY_MONITORING"
            status_badge = "İZLENİYOR"
            status_message = f"İşlem sayısı ({total_trades}/5) erken aşamada. Matematiksel model takip ediliyor."

        return {
            "sample_size": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate_pct": win_rate_pct,
            "loss_rate_pct": loss_rate_pct,
            "average_win_usd": _fin_round(avg_win, 2),
            "average_loss_usd": _fin_round(avg_loss, 2),
            "win_loss_ratio_b": _fin_round(win_loss_ratio_b, 2),
            "breakeven_win_rate_pct": breakeven_win_rate_pct,
            "edge_buffer_pct": edge_buffer_pct,
            "expectancy_usd_per_trade": _fin_round(expectancy_usd, 2),
            "expectancy_r": _fin_round(expectancy_r, 2),
            "rolling_5_expectancy_usd": _fin_round(rolling_5_expectancy, 2),
            "is_positive_expectancy": is_positive,
            "should_halt": should_halt,
            "status": status,
            "status_badge": status_badge,
            "status_message": status_message,
            "formula_definition": "Expectancy = (WinRate * AvgWin) - (LossRate * AvgLoss) | Başabaş = AvgLoss / (AvgWin + AvgLoss)",
        }
