from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from services.market_scanner.scanner import BinanceMarketScanner
from services.paper_broker.broker import PaperBroker
from services.risk_engine.readiness_gate import ReadinessGate
from services.strategy_engine.strategy_manager import StrategyManager
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("command-bus", service="command_bus")
settings = get_settings()


class AuditEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    user: str = "local_operator"
    action: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    success: bool
    result: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class CommandBus:
    """
    Central Command Bus executing operations initiated from the Local Trading Control Center.
    Every action is validated, dispatched to real engines, and appended to the audit trail.
    """

    def __init__(
        self,
        runtime_state: Dict[str, Any],
        broker: Optional[PaperBroker] = None,
        strategy_manager: Optional[StrategyManager] = None,
    ):
        self.runtime_state = runtime_state
        self.broker = broker or PaperBroker(
            initial_balance=settings.INITIAL_CAPITAL,
            maker_fee=settings.MAKER_FEE,
            taker_fee=settings.TAKER_FEE,
            slippage_bps=settings.SLIPPAGE_BPS,
        )
        self.strategy_manager = strategy_manager or StrategyManager()
        self.audit_log: List[AuditEntry] = []

    def _log_audit(
        self,
        action: str,
        parameters: Dict[str, Any],
        success: bool,
        result: Dict[str, Any],
        error: Optional[str] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            action=action,
            parameters=parameters,
            success=success,
            result=result,
            error=error,
        )
        self.audit_log.append(entry)
        logger.info(
            f"AUDIT: [{action}] success={success}",
            extra={"action": action, "success": success, "error": error},
        )
        return entry

    async def execute_start_agent(self, user: str = "operator") -> Dict[str, Any]:
        """Runs Readiness Gate before transitioning system state to TRADING."""
        gate_result = await ReadinessGate.evaluate()
        if not gate_result["ready"]:
            res = {
                "success": False,
                "message": "Readiness gate check failed. Cannot start trading agent.",
                "reasons": gate_result["blocking_reasons"],
            }
            self._log_audit(
                "START_AGENT", {"user": user}, False, res, error="Readiness gate failed"
            )
            return res

        self.runtime_state["system_state"] = "TRADING"
        self.runtime_state["is_halted"] = False
        res = {
            "success": True,
            "system_state": "TRADING",
            "message": "KRIPTO AGENT started successfully in Paper Trading mode.",
            "capital": self.broker.portfolio.equity,
            "risk_per_trade_pct": settings.RISK_PER_TRADE,
            "active_symbols": len(settings.DEFAULT_SYMBOLS),
        }
        self._log_audit("START_AGENT", {"user": user}, True, res)
        return res

    def execute_pause_agent(self, user: str = "operator") -> Dict[str, Any]:
        """Pauses agent: stops opening new positions while keeping stop/TP active."""
        self.runtime_state["system_state"] = "PAUSED"
        res = {
            "success": True,
            "system_state": "PAUSED",
            "message": "Agent paused. Existing positions remain protected by stop-loss/TP.",
            "open_positions": len(self.broker.open_positions),
        }
        self._log_audit("PAUSE_AGENT", {"user": user}, True, res)
        return res

    def execute_resume_agent(self, user: str = "operator") -> Dict[str, Any]:
        """Resumes agent from paused state back to TRADING."""
        if self.runtime_state.get("system_state") == "RISK_LOCK":
            res = {
                "success": False,
                "message": "Cannot resume while system is in EMERGENCY RISK LOCK. Clear lock first.",
            }
            self._log_audit("RESUME_AGENT", {"user": user}, False, res, error="System in RISK_LOCK")
            return res

        self.runtime_state["system_state"] = "TRADING"
        res = {
            "success": True,
            "system_state": "TRADING",
            "message": "Agent resumed trading operations.",
        }
        self._log_audit("RESUME_AGENT", {"user": user}, True, res)
        return res

    def execute_stop_agent(self, user: str = "operator") -> Dict[str, Any]:
        """Stops agent trading loop gracefully."""
        self.runtime_state["system_state"] = "STOPPED"
        res = {
            "success": True,
            "system_state": "STOPPED",
            "message": "Trading agent stopped.",
        }
        self._log_audit("STOP_AGENT", {"user": user}, True, res)
        return res

    def execute_emergency_stop(self, user: str = "operator") -> Dict[str, Any]:
        """Emergency circuit: cuts off all order generation and transitions to RISK_LOCK."""
        self.runtime_state["system_state"] = "RISK_LOCK"
        self.runtime_state["is_halted"] = True
        self.runtime_state["circuit_state"] = "TRIPPED"

        # Cancel all open paper orders
        cancelled_orders = len(self.broker.open_orders)
        for order in list(self.broker.open_orders.values()):
            self.broker.cancel_order(order.order_id)

        res = {
            "success": True,
            "system_state": "RISK_LOCK",
            "is_halted": True,
            "cancelled_orders": cancelled_orders,
            "open_positions_preserved": len(self.broker.open_positions),
            "message": "EMERGENCY STOP TRIGGERED: Order generation frozen and all open orders cancelled.",
        }
        self._log_audit("EMERGENCY_STOP", {"user": user}, True, res)
        return res

    def execute_close_position(
        self, symbol: str, reason: str = "USER_MANUAL_CLOSE"
    ) -> Dict[str, Any]:
        """Simulates immediate market fill at current price to close open position."""
        pos = self.broker.open_positions.get(symbol)
        if not pos:
            res = {"success": False, "message": f"No open position found for symbol {symbol}."}
            self._log_audit(
                "CLOSE_POSITION", {"symbol": symbol}, False, res, error="Position not found"
            )
            return res

        closed_pos = self.broker.close_position(
            symbol=symbol, exit_price=pos.current_price, reason=reason
        )
        if not closed_pos:
            res = {"success": False, "message": f"Failed to close position for symbol {symbol}."}
            self._log_audit("CLOSE_POSITION", {"symbol": symbol}, False, res, error="Failed to close")
            return res

        res = {
            "success": True,
            "symbol": symbol,
            "realized_pnl": closed_pos.realized_pnl,
            "exit_price": pos.current_price,
            "reason": reason,
            "new_equity": self.broker.portfolio.equity,
            "message": f"Position on {symbol} successfully closed. Realized PnL: ${closed_pos.realized_pnl:+.2f}.",
        }
        self._log_audit("CLOSE_POSITION", {"symbol": symbol, "reason": reason}, True, res)
        return res

    def execute_cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancels an active paper order."""
        cancelled = self.broker.cancel_order(order_id)
        if not cancelled:
            res = {
                "success": False,
                "message": f"Order {order_id} could not be cancelled or does not exist.",
            }
            self._log_audit(
                "CANCEL_ORDER", {"order_id": order_id}, False, res, error="Order cancel failed"
            )
            return res

        res = {
            "success": True,
            "order_id": order_id,
            "status": "CANCELLED",
            "message": f"Order {order_id} cancelled successfully.",
        }
        self._log_audit("CANCEL_ORDER", {"order_id": order_id}, True, res)
        return res

    def execute_update_risk_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Dynamically updates Risk Engine thresholds in memory and configuration."""
        updated = {}
        if "risk_per_trade_pct" in params:
            val = float(params["risk_per_trade_pct"])
            if 0.001 <= val <= 0.05:
                settings.RISK_PER_TRADE = val
                updated["risk_per_trade_pct"] = val
        if "daily_max_loss_pct" in params:
            val = float(params["daily_max_loss_pct"])
            if 0.005 <= val <= 0.10:
                settings.DAILY_MAX_LOSS = val
                updated["daily_max_loss_pct"] = val
        if "max_open_positions" in params:
            val_int = int(params["max_open_positions"])
            if 1 <= val_int <= 10:
                settings.MAX_OPEN_POSITIONS = val_int
                updated["max_open_positions"] = val_int
        if "atr_multiplier" in params:
            val = float(params["atr_multiplier"])
            if 1.0 <= val <= 5.0:
                settings.ATR_SL_MULTIPLIER = val
                updated["atr_multiplier"] = val

        res = {
            "success": True,
            "updated_parameters": updated,
            "current_risk_config": {
                "risk_per_trade_pct": settings.RISK_PER_TRADE,
                "daily_max_loss_pct": settings.DAILY_MAX_LOSS,
                "max_open_positions": settings.MAX_OPEN_POSITIONS,
                "atr_multiplier": settings.ATR_SL_MULTIPLIER,
                "minimum_rr": settings.MIN_RISK_REWARD,
            },
            "message": "Risk engine configuration updated successfully.",
        }
        self._log_audit("UPDATE_RISK_CONFIG", params, True, res)
        return res

    def execute_toggle_strategy(self, strategy_name: str, enabled: bool) -> Dict[str, Any]:
        """Enables or disables an active trading strategy."""
        found = False
        for strat in self.strategy_manager.strategies:
            if strat.name.lower() == strategy_name.lower():
                strat.enabled = enabled
                found = True
                break

        if not found:
            res = {"success": False, "message": f"Strategy '{strategy_name}' not found."}
            self._log_audit(
                "TOGGLE_STRATEGY",
                {"name": strategy_name, "enabled": enabled},
                False,
                res,
                error="Strategy not found",
            )
            return res

        res = {
            "success": True,
            "strategy": strategy_name,
            "enabled": enabled,
            "message": f"Strategy '{strategy_name}' is now {'ENABLED' if enabled else 'DISABLED'}.",
        }
        self._log_audit("TOGGLE_STRATEGY", {"name": strategy_name, "enabled": enabled}, True, res)
        return res

    async def execute_run_scanner(self) -> Dict[str, Any]:
        """Runs the Binance Market Scanner across monitored pairs."""
        scanner = BinanceMarketScanner()
        results = await scanner.scan_market()
        res = {
            "success": True,
            "scanned_count": len(results),
            "opportunities": [asdict(s) for s in results],
            "message": f"Binance scanner completed. {len(results)} pairs evaluated.",
        }
        self._log_audit("RUN_SCANNER", {}, True, {"scanned_count": len(results)})
        return res

    def execute_reset_experiment(self, confirmation: bool = False) -> Dict[str, Any]:
        """Resets virtual paper account and trading history with safety confirmation."""
        if not confirmation:
            res = {
                "success": False,
                "message": "Reset experiment requires explicit confirmation=True.",
            }
            self._log_audit(
                "RESET_EXPERIMENT",
                {"confirmation": confirmation},
                False,
                res,
                error="Confirmation required",
            )
            return res

        self.broker = PaperBroker(
            initial_balance=settings.INITIAL_CAPITAL,
            maker_fee=settings.MAKER_FEE,
            taker_fee=settings.TAKER_FEE,
            slippage_bps=settings.SLIPPAGE_BPS,
        )
        self.runtime_state["balance"] = settings.INITIAL_CAPITAL
        self.runtime_state["equity"] = settings.INITIAL_CAPITAL
        self.runtime_state["daily_pnl"] = 0.0
        self.runtime_state["max_drawdown"] = 0.0
        self.runtime_state["open_positions"] = []
        self.runtime_state["closed_positions"] = []

        res = {
            "success": True,
            "initial_capital": settings.INITIAL_CAPITAL,
            "equity": settings.INITIAL_CAPITAL,
            "message": "Paper trading experiment reset to initial state ($5,000 capital).",
        }
        self._log_audit("RESET_EXPERIMENT", {"confirmation": True}, True, res)
        return res
