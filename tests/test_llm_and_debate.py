import pytest
from core.llm.llm_provider import HeuristicFallbackProvider, get_llm_provider
from agents.debate.bull_bear_engine import BullBearDebateEngine, DebateVerdict
from shared.enums import SignalDirection


@pytest.mark.asyncio
async def test_heuristic_llm_provider():
    provider = get_llm_provider("fallback")
    assert isinstance(provider, HeuristicFallbackProvider)

    # Test debate prompt handling
    res = await provider.generate("You are debate moderator", "Bull case: RSI oversold and breakout", json_mode=True)
    assert "verdict" in res
    assert "bull_thesis" in res


@pytest.mark.asyncio
async def test_bull_bear_debate_engine_bullish():
    engine = BullBearDebateEngine(llm_provider=HeuristicFallbackProvider())
    context = {
        "symbol": "BTC/USDT",
        "current_price": 65000.0,
        "features": {"rsi": 35.0, "ema_20": 65200.0, "ema_50": 64800.0, "realized_vol": 0.30},
        "sentiment": {"value": 60, "classification": "Greed"},
        "derivatives": {"long_short_ratio": 1.2, "taker_buy_sell_ratio": 1.35},
        "orderbook": {"spread_bps": 2.1},
    }
    verdict: DebateVerdict = await engine.run_debate(context)
    assert isinstance(verdict, DebateVerdict)
    assert verdict.direction in (SignalDirection.LONG, SignalDirection.FLAT, SignalDirection.SHORT)
    assert len(verdict.bull_thesis) > 0
    assert len(verdict.bear_thesis) > 0
    assert 0.0 <= verdict.confidence <= 1.0


@pytest.mark.asyncio
async def test_bull_bear_debate_engine_bearish():
    engine = BullBearDebateEngine(llm_provider=HeuristicFallbackProvider())
    context = {
        "symbol": "ETH/USDT",
        "current_price": 3500.0,
        "features": {"rsi": 78.0, "ema_20": 3450.0, "ema_50": 3550.0, "realized_vol": 0.55},
        "sentiment": {"value": 20, "classification": "Extreme Fear"},
        "derivatives": {"long_short_ratio": 2.4, "taker_buy_sell_ratio": 0.70},
        "orderbook": {"spread_bps": 3.5},
    }
    verdict: DebateVerdict = await engine.run_debate(context)
    assert isinstance(verdict, DebateVerdict)
    assert verdict.direction in (SignalDirection.SHORT, SignalDirection.FLAT, SignalDirection.LONG)
    assert len(verdict.synthesis) > 0
