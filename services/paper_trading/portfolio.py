from datetime import datetime, timezone
from typing import Dict, Optional

from shared.config import get_settings
from shared.enums import PositionSide, PositionStatus
from shared.schemas import PortfolioState, Position


class PortfolioTracker:
    def __init__(self, initial_balance: Optional[float] = None):
        if initial_balance is None:
            initial_balance = getattr(get_settings(), "INITIAL_CAPITAL", 5000.0)
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: Dict[str, Position] = {}  # symbol -> active Position
        self.realized_pnl = 0.0
        self.peak_equity = initial_balance
        self.total_fees_paid = 0.0
        self.daily_starting_equity = initial_balance

    @property
    def unrealized_pnl(self) -> float:
        return sum(
            p.unrealized_pnl for p in self.positions.values() if p.status == PositionStatus.OPEN
        )

    @property
    def equity(self) -> float:
        return self.balance + self.unrealized_pnl

    @property
    def max_drawdown_current(self) -> float:
        eq = self.equity
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity <= 0:
            return 0.0
        dd = (self.peak_equity - eq) / self.peak_equity
        return max(0.0, dd)

    @property
    def daily_pnl(self) -> float:
        return self.equity - self.daily_starting_equity

    def update_market_price(self, symbol: str, current_price: float):
        if symbol in self.positions:
            pos = self.positions[symbol]
            if pos.status == PositionStatus.OPEN:
                pos.current_price = current_price
                if pos.side == PositionSide.LONG:
                    pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity

    def add_position(self, position: Position):
        self.positions[position.symbol] = position

    def close_position(self, symbol: str, exit_price: float, fee: float) -> Optional[Position]:
        if symbol not in self.positions:
            return None
        pos = self.positions[symbol]
        if pos.status != PositionStatus.OPEN:
            return None

        pos.current_price = exit_price
        pos.closed_at = datetime.now(timezone.utc)
        pos.status = PositionStatus.CLOSED
        pos.fees_paid += fee

        if pos.side == PositionSide.LONG:
            gross_pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.quantity

        net_pnl = gross_pnl - pos.fees_paid
        pos.realized_pnl = net_pnl
        pos.unrealized_pnl = 0.0

        self.balance += net_pnl
        self.realized_pnl += net_pnl
        self.total_fees_paid += fee

        del self.positions[symbol]
        return pos

    def get_state(self) -> PortfolioState:
        eq = self.equity
        return PortfolioState(
            balance=round(self.balance, 2),
            equity=round(eq, 2),
            unrealized_pnl=round(self.unrealized_pnl, 2),
            realized_pnl=round(self.realized_pnl, 2),
            open_positions=list(self.positions.values()),
            daily_pnl=round(self.daily_pnl, 2),
            max_drawdown_current=round(self.max_drawdown_current, 4),
            is_halted=False,
            timestamp=datetime.now(timezone.utc),
        )
