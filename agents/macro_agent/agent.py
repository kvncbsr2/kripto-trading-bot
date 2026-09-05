from typing import Any, Dict

from agents.base_agent import AgentAnalysisResult, TradingAgent
from services.market_data.binance_derivatives import derivatives_service
from shared.enums import SignalDirection


class MacroAgent(TradingAgent):
    """
    Macro & Derivatives Position Agent.
    Analyzes DXY trends and institutional Binance Futures positioning
    (Long/Short ratio, Taker volume dominance, Open Interest).
    """

    def __init__(self, enabled: bool = False):
        super().__init__(name="MacroAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        flags = []
        dxy_trend = market_context.get("dxy_trend", "FLAT")
        if dxy_trend == "RISING":
            flags.append("DXY_STRENGTH_HEADWIND")

        symbol = market_context.get("symbol", "BTC/USDT")
        deriv_data = market_context.get("derivatives")
        if not deriv_data:
            deriv_data = await derivatives_service.get_derivatives_metrics(symbol)

        taker_ratio = deriv_data.get("taker_buy_sell_ratio", 1.0)
        ls_ratio = deriv_data.get("long_short_ratio", 1.0)

        direction = SignalDirection.FLAT
        confidence = 0.50
        reasoning = f"Macro environment DXY: {dxy_trend}"

        # If aggressive taker buy dominance and no DXY headwind
        if taker_ratio > 1.20 and dxy_trend != "RISING":
            direction = SignalDirection.LONG
            confidence = 0.70
            reasoning = f"Institutional taker buying pressure ({taker_ratio:.2f}) with neutral macro."
        elif taker_ratio < 0.80 or (ls_ratio > 2.2 and dxy_trend == "RISING"):
            # Overcrowded retail long + rising DXY -> High squeeze risk
            direction = SignalDirection.SHORT
            confidence = 0.68
            flags.append("OVERCROWDED_LONG_LIQUIDATION_RISK")
            reasoning = f"Macro headwind & bearish taker pressure ({taker_ratio:.2f})."

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=confidence,
            recommended_direction=direction,
            reasoning_summary=reasoning,
            risk_flags=flags,
            metadata={
                "dxy_trend": dxy_trend,
                "taker_ratio": taker_ratio,
                "long_short_ratio": ls_ratio,
            },
        )


class OnchainAgent(TradingAgent):
    def __init__(self, enabled: bool = False):
        super().__init__(name="OnchainAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        net_inflows_usd = market_context.get("exchange_net_inflows", 0.0)
        flags = []
        if net_inflows_usd > 50_000_000:
            flags.append("HEAVY_EXCHANGE_INFLOW_SELL_RISK")

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=0.6,
            recommended_direction=SignalDirection.FLAT,
            reasoning_summary=f"Exchange net inflows: ${net_inflows_usd:,.0f}",
            risk_flags=flags,
            metadata={"net_inflows_usd": net_inflows_usd},
        )
