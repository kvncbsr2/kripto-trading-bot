from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.models.base import Base, TimestampMixin


class ExchangeModel(Base, TimestampMixin):
    __tablename__ = "exchanges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)


class SymbolModel(Base, TimestampMixin):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(10), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_order_size: Mapped[float] = mapped_column(Float, default=0.0001, nullable=False)
    price_precision: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    quantity_precision: Mapped[int] = mapped_column(Integer, default=6, nullable=False)

    __table_args__ = (Index("ix_symbols_exchange_symbol", "exchange_name", "symbol", unique=True),)


class CandleModel(Base, TimestampMixin):
    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    quote_volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_candles_symbol_tf_ts", "symbol", "timeframe", "timestamp", unique=True),
    )


class MarketTickModel(Base, TimestampMixin):
    __tablename__ = "market_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    bid: Mapped[float] = mapped_column(Float, nullable=False)
    ask: Mapped[float] = mapped_column(Float, nullable=False)
    last_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume_24h: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_market_ticks_symbol_ts", "symbol", "timestamp"),)


class OrderBookModel(Base, TimestampMixin):
    __tablename__ = "order_books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    bids: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    asks: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)


class FeatureModel(Base, TimestampMixin):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    data: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_features_symbol_tf_ts", "symbol", "timeframe", "timestamp"),)


class SignalModel(Base, TimestampMixin):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    regime: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class OrderModel(Base, TimestampMixin):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    average_fill_price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fee_paid: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class FillModel(Base, TimestampMixin):
    __tablename__ = "fills"

    fill_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("orders.order_id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    fee: Mapped[float] = mapped_column(Float, nullable=False)
    slippage: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PositionModel(Base, TimestampMixin):
    __tablename__ = "positions"

    position_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    current_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fees_paid: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN", nullable=False)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PortfolioSnapshotModel(Base, TimestampMixin):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    daily_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    open_positions_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class RiskEventModel(Base, TimestampMixin):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)


class StrategyRunModel(Base, TimestampMixin):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    parameters: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestRunModel(Base, TimestampMixin):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    final_equity: Mapped[float] = mapped_column(Float, nullable=False)
    total_return: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    sortino_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, nullable=False)
    profit_factor: Mapped[float] = mapped_column(Float, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)


class SystemEventModel(Base, TimestampMixin):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
