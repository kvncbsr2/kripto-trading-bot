"""
services/paper_trading/broker.py
Unified alias pointing to the authoritative PaperExecutionEngine.
"""
from services.execution.paper_execution import PaperExecutionEngine

PaperBroker = PaperExecutionEngine

__all__ = ["PaperBroker", "PaperExecutionEngine"]
