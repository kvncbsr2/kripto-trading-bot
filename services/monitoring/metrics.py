from prometheus_client import Counter, Gauge, generate_latest

# Prometheus Metrics Definitions for KRIPTO AGENT (Section 76)
SIGNALS_TOTAL = Counter(
    "signals_total", "Total trading signals generated", ["symbol", "strategy", "direction"]
)
ORDERS_TOTAL = Counter("orders_total", "Total paper orders created", ["symbol", "side", "type"])
FILLS_TOTAL = Counter("fills_total", "Total simulated order fills", ["symbol", "side"])
TRADES_TOTAL = Counter("trades_total", "Total closed positions", ["symbol", "strategy"])
WINNING_TRADES_TOTAL = Counter(
    "winning_trades_total", "Total winning closed trades", ["symbol", "strategy"]
)
LOSING_TRADES_TOTAL = Counter(
    "losing_trades_total", "Total losing closed trades", ["symbol", "strategy"]
)

PNL_TOTAL = Gauge("pnl_total", "Cumulative realized net PnL in USD")
EQUITY_CURRENT = Gauge("equity_current", "Current portfolio equity in USD")
DRAWDOWN_CURRENT = Gauge("drawdown_current", "Current portfolio drawdown percentage")

FEES_TOTAL = Gauge("fees_total", "Total simulated fees paid in USD")
SLIPPAGE_TOTAL = Gauge("slippage_total", "Total simulated slippage cost in USD")
RISK_LOCKS_TOTAL = Counter("risk_locks_total", "Total circuit breaker / daily risk locks triggered")
DATA_ERRORS_TOTAL = Counter(
    "data_errors_total", "Total market data quality errors / warnings", ["type"]
)
WEBSOCKET_RECONNECTS_TOTAL = Counter(
    "websocket_reconnects_total", "Total Binance WebSocket reconnect attempts"
)


def get_prometheus_metrics() -> bytes:
    """Exports raw Prometheus metrics formatted text."""
    return generate_latest()
