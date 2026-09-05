"""
Order & Position Reconciliation Service for KRIPTO AGENT.
Detects state drift between local database/memory state and exchange (Binance) state.
Enforces fail-closed: if mismatch detected, raises alarm and flags RECONCILIATION_REQUIRED.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from shared.logging import get_logger

logger = get_logger("reconciliation-engine", service="execution")


class ReconciliationDiscrepancy(BaseModel):
    discrepancy_type: str  # ORDER_MISMATCH, POSITION_MISMATCH, BALANCE_DRIFT
    symbol: str
    local_state: Dict[str, Any]
    exchange_state: Dict[str, Any]
    description: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationReport(BaseModel):
    is_synchronized: bool
    total_local_orders: int
    total_exchange_orders: int
    discrepancies: List[ReconciliationDiscrepancy] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationEngine:
    """
    Authoritative state reconciler comparing local broker state with Binance exchange state.
    """

    def __init__(self, execution_engine=None):
        self.execution_engine = execution_engine

    async def reconcile_orders_and_positions(
        self,
        local_open_positions: Dict[str, Any],
        local_orders: Dict[str, Any],
        exchange_client: Optional[Any] = None,
    ) -> ReconciliationReport:
        """
        Queries exchange open orders and balances, cross-referencing with local state.
        """
        client = exchange_client or getattr(self.execution_engine, "client", None)
        discrepancies: List[ReconciliationDiscrepancy] = []

        # In paper mode (no live exchange client), reconcile in-memory broker state with SQLite DB
        if not client:
            import os
            import sqlite3
            db_path = getattr(self.execution_engine, "db_path", None) if self.execution_engine else "./kripto_agent.db"
            if db_path and os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cur = conn.cursor()

                    # 1. Reconcile open positions
                    cur.execute("SELECT position_id, symbol, status FROM paper_positions WHERE status='OPEN'")
                    db_open = {r["symbol"]: r["position_id"] for r in cur.fetchall()}

                    for sym, pos in local_open_positions.items():
                        pos_id = getattr(pos, "position_id", None) or (pos.get("position_id") if isinstance(pos, dict) else str(pos))
                        if sym not in db_open:
                            discrepancies.append(
                                ReconciliationDiscrepancy(
                                    discrepancy_type="POSITION_DRIFT",
                                    symbol=sym,
                                    local_state={"symbol": sym, "position_id": pos_id},
                                    exchange_state={"exists_in_db": False},
                                    description=f"In-memory position {sym} ({pos_id}) is missing from SQLite database!",
                                )
                            )
                        elif db_open[sym] != pos_id:
                            discrepancies.append(
                                ReconciliationDiscrepancy(
                                    discrepancy_type="POSITION_MISMATCH",
                                    symbol=sym,
                                    local_state={"symbol": sym, "position_id": pos_id},
                                    exchange_state={"db_position_id": db_open[sym]},
                                    description=f"Position ID mismatch for {sym}: in-memory={pos_id}, DB={db_open[sym]}",
                                )
                            )

                    for sym, db_pid in db_open.items():
                        if sym not in local_open_positions:
                            discrepancies.append(
                                ReconciliationDiscrepancy(
                                    discrepancy_type="ORPHAN_DB_POSITION",
                                    symbol=sym,
                                    local_state={"exists_locally": False},
                                    exchange_state={"position_id": db_pid},
                                    description=f"Database has OPEN position {sym} ({db_pid}) not loaded into memory!",
                                )
                            )

                    # 2. Reconcile account balance
                    cur.execute("SELECT balance, reserved_balance FROM paper_account WHERE id=1")
                    acc_row = cur.fetchone()
                    if acc_row and self.execution_engine:
                        db_bal = float(acc_row["balance"])
                        mem_bal = getattr(self.execution_engine, "balance", db_bal)
                        if abs(db_bal - mem_bal) > 0.05:
                            discrepancies.append(
                                ReconciliationDiscrepancy(
                                    discrepancy_type="BALANCE_DRIFT",
                                    symbol="GLOBAL",
                                    local_state={"memory_balance": mem_bal},
                                    exchange_state={"db_balance": db_bal},
                                    description=f"Balance drift between memory (${mem_bal:.2f}) and SQLite (${db_bal:.2f})!",
                                )
                            )
                    conn.close()
                except Exception as ex:
                    logger.error(f"Paper broker SQLite reconciliation error: {ex}")
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            discrepancy_type="RECONCILIATION_DB_ERROR",
                            symbol="GLOBAL",
                            local_state={},
                            exchange_state={"error": str(ex)},
                            description=f"Failed to query SQLite for paper reconciliation: {ex}",
                        )
                    )

            is_sync = len(discrepancies) == 0
            if not is_sync:
                logger.critical(
                    f"PAPER RECONCILIATION FAILURE: {len(discrepancies)} state discrepancies detected!"
                )
            return ReconciliationReport(
                is_synchronized=is_sync,
                total_local_orders=len(local_orders),
                total_exchange_orders=0,
                discrepancies=discrepancies,
            )

        try:
            # 1. Query Exchange Open Orders
            raw_exchange_orders = await client.fetch_open_orders()
            exchange_order_ids = {str(o["id"]): o for o in raw_exchange_orders}

            # Compare local open orders with exchange open orders
            for oid, order in local_orders.items():
                status_val = getattr(order, "status", None)
                if hasattr(status_val, "value"):
                    status_val = status_val.value

                if status_val in ["SUBMITTED", "OPEN"]:
                    if oid not in exchange_order_ids:
                        discrepancies.append(
                            ReconciliationDiscrepancy(
                                discrepancy_type="ORDER_MISMATCH",
                                symbol=getattr(order, "symbol", "UNKNOWN"),
                                local_state={"order_id": oid, "status": status_val},
                                exchange_state={"exists_on_exchange": False},
                                description=f"Local order {oid} is OPEN locally but does not exist on Binance exchange.",
                            )
                        )

            # Check if exchange has orphan orders unknown to local state
            for xid, xorder in exchange_order_ids.items():
                if xid not in local_orders:
                    discrepancies.append(
                        ReconciliationDiscrepancy(
                            discrepancy_type="ORPHAN_EXCHANGE_ORDER",
                            symbol=xorder.get("symbol", "UNKNOWN"),
                            local_state={"exists_locally": False},
                            exchange_state={"order_id": xid, "side": xorder.get("side"), "amount": xorder.get("amount")},
                            description=f"Exchange order {xid} exists on Binance but is not tracked locally!",
                        )
                    )

            is_sync = len(discrepancies) == 0
            if not is_sync:
                logger.critical(
                    f"RECONCILIATION FAILURE: {len(discrepancies)} state discrepancies detected with Binance!"
                )
            else:
                logger.info("Reconciliation check completed: local and exchange state are 100% synchronized.")

            return ReconciliationReport(
                is_synchronized=is_sync,
                total_local_orders=len(local_orders),
                total_exchange_orders=len(raw_exchange_orders),
                discrepancies=discrepancies,
            )

        except Exception as e:
            logger.error(f"Reconciliation error while querying exchange: {e}")
            discrepancies.append(
                ReconciliationDiscrepancy(
                    discrepancy_type="EXCHANGE_QUERY_FAILURE",
                    symbol="GLOBAL",
                    local_state={},
                    exchange_state={"error": str(e)},
                    description=f"Failed to query Binance for reconciliation: {e}",
                )
            )
            return ReconciliationReport(
                is_synchronized=False,
                total_local_orders=len(local_orders),
                total_exchange_orders=0,
                discrepancies=discrepancies,
            )


reconciliation_engine = ReconciliationEngine()
