from datetime import datetime, timedelta, timezone
from shared.enums import Timeframe
from shared.schemas import Candle
from services.risk_engine.btc_regime_shield import BTCRegimeShield

BASE_TIME = datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)


def make_candle(close: float, step: int = 0) -> Candle:
    return Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=BASE_TIME + timedelta(minutes=15 * step),
        open=close,
        high=close + 10,
        low=close - 10,
        close=close,
        volume=100.0
    )


def test_btc_regime_shield_normal():
    shield = BTCRegimeShield(dump_threshold_pct=-1.5, enabled=True)
    
    # 5 candles with stable/rising prices
    candles = [
        make_candle(80000.0, 0),
        make_candle(80100.0, 1),
        make_candle(80150.0, 2),
        make_candle(80200.0, 3),
        make_candle(80250.0, 4),
    ]
    
    health = shield.evaluate_btc_health(candles)
    assert health.is_dumping is False
    assert health.return_15m_pct > 0
    
    # Altcoin trade permitted
    can_trade_eth, msg_eth = shield.can_trade_symbol("ETH/USDT", health)
    assert can_trade_eth is True


def test_btc_regime_shield_blocks_altcoins_on_dump():
    shield = BTCRegimeShield(dump_threshold_pct=-1.5, enabled=True)
    
    # BTC dumps from 80,000 to 78,000 (-2.5% drop in 15m)
    candles = [
        make_candle(80000.0, 0),
        make_candle(80000.0, 1),
        make_candle(80000.0, 2),
        make_candle(80000.0, 3),
        make_candle(78000.0, 4),  # -2.5% drop
    ]
    
    health = shield.evaluate_btc_health(candles)
    assert health.is_dumping is True
    assert health.return_15m_pct < -1.5
    
    # Altcoin trade BLOCKED
    can_trade_sol, msg_sol = shield.can_trade_symbol("SOL/USDT", health)
    assert can_trade_sol is False
    assert "alımı engellendi" in msg_sol or "BTC TREND KALKANI" in msg_sol

    # BTC itself is permitted to evaluate its own reversal dip
    can_trade_btc, msg_btc = shield.can_trade_symbol("BTC/USDT", health)
    assert can_trade_btc is True
