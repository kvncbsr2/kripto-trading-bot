"""
services/paper_broker/broker.py
Unified alias pointing to the authoritative PaperExecutionEngine.
"""
from services.execution.paper_execution import PaperExecutionEngine

# Canonical alias
PaperBroker = PaperExecutionEngine

__all__ = ["PaperBroker", "PaperExecutionEngine"]
