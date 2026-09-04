from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from shared.enums import (
    ExchangeName,
    MarketRegime,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    SignalDirection,
    Timeframe,
)


# Market Data Schemas
class Candle(BaseModel):
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: Optional[float] = None
    exchange: ExchangeName = ExchangeName.BINANCE


class MarketTick(BaseModel):
    symbol: str
    exchange: ExchangeName
    timestamp: datetime
    bid: float
    ask: float
    last_price: float
    volume_24h: Optional[float] = None


class OrderBookEntry(BaseModel):
    price: float
    amount: float


class OrderBook(BaseModel):
    symbol: str
    exchange: ExchangeName
    timestamp: datetime
    bids: List[OrderBookEntry]
    asks: List[OrderBookEntry]


class Trade(BaseModel):
    symbol: str
    exchange: ExchangeName
    timestamp: datetime
    trade_id: str
    price: float
    amount: float
    side: OrderSide


class FundingRate(BaseModel):
    symbol: str
    exchange: ExchangeName
    timestamp: datetime
    rate: float
    next_funding_time: Optional[datetime] = None


class OpenInterest(BaseModel):
    symbol: str
    exchange: ExchangeName
    timestamp: datetime
    open_interest: float


# Feature & Regime Schemas
class FeatureVector(BaseModel):
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    indicators: Dict[str, float] = Field(default_factory=dict)


class MarketRegimeState(BaseModel):
    symbol: str
    timeframe: Timeframe
    timestamp: datetime
    regime: MarketRegime
    confidence: float = Field(ge=0.0, le=1.0)
    metrics: Dict[str, Any] = Field(default_factory=dict)


# Strategy & Signal Schemas
class Signal(BaseModel):
    symbol: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    strategy: str
    direction: SignalDirection
    entry_price: float
    stop_price: float
    take_profit: float
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    regime: MarketRegime = MarketRegime.UNKNOWN
    reason: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


# Risk Schemas
class RiskLimits(BaseModel):
    max_risk_per_trade: float = 0.005  # 0.5%
    max_daily_loss: float = 0.02  # 2.0%
    max_drawdown: float = 0.10  # 10.0%
    max_open_positions: int = 3
    risk_reward_ratio: float = 2.0


class RiskDecision(BaseModel):
    approved: bool
    symbol: str
    direction: SignalDirection
    calculated_size: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    risk_amount: float = 0.0
    reason: str = ""


# Orders & Fills
class Order(BaseModel):
    order_id: str
    symbol: str
    order_type: OrderType
    side: OrderSide
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    filled_quantity: float = 0.0
    average_fill_price: float = 0.0
    fee_paid: float = 0.0


class Fill(BaseModel):
    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    fee: float
    slippage: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Position & Portfolio
class Position(BaseModel):
    position_id: str
    symbol: str
    side: PositionSide
    entry_price: float
    quantity: float
    current_price: float
    stop_loss: float
    take_profit: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    status: PositionStatus = PositionStatus.OPEN
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    fees_paid: float = 0.0
    strategy: str = ""
    peak_price: Optional[float] = None


class PortfolioState(BaseModel):
    balance: float
    equity: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    open_positions: List[Position] = Field(default_factory=list)
    daily_pnl: float = 0.0
    max_drawdown_current: float = 0.0
    is_halted: bool = False
    halt_reason: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
