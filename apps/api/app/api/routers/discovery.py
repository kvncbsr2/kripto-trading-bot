from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.app.api.state import market_data_service
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from services.strategy_discovery.discovery_engine import (
    StrategyDiscoveryEngine,
)
from services.strategy_discovery.promotion_gate import (
    PromotionEvaluationResult,
    PromotionGate,
)
from shared.config import get_settings

router = APIRouter(tags=["discovery"])
settings = get_settings()

discovery_engine = StrategyDiscoveryEngine()


class PromoteCandidateRequest(BaseModel):
    candidate_id: str


@router.post("/discovery/run-tournament")
@router.post("/api/v1/strategy-discovery/run")
async def post_run_tournament(
    symbol: str = "BTC/USDT",
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Runs automated tournament across 10 R10 variants using REAL Binance market price history.
    """
    norm_symbol = symbol.replace("-", "/").upper()
    candles = await market_data_service.get_historical_klines(
        norm_symbol, timeframe="1d", limit=365
    )
    if not candles or len(candles) < 60:
        raise HTTPException(
            status_code=422,
            detail=f"DATA_UNAVAILABLE: Insufficient Binance historical candles ({len(candles) if candles else 0} < 60 required) for {norm_symbol}. Zero synthetic fallback permitted.",
        )
    closes = pd.Series([c.close for c in candles])
    discovery_engine.generate_r10_variants()
    ranked_candidates = discovery_engine.run_tournament(
        close_series=closes,
        initial_capital=settings.INITIAL_CAPITAL,
        fees=settings.TAKER_FEE,
        slippage_bps=settings.SLIPPAGE_BPS,
    )

    return {
        "symbol": norm_symbol,
        "candidates_count": len(ranked_candidates),
        "winner": ranked_candidates[0].variant if ranked_candidates else None,
        "winner_robustness_score": (
            ranked_candidates[0].robustness_report.robustness_score
            if ranked_candidates and ranked_candidates[0].robustness_report
            else 0.0
        ),
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "name": c.name,
                "variant": c.variant,
                "tournament_rank": c.tournament_rank,
                "metrics": c.backtest_metrics,
                "robustness_score": (
                    c.robustness_report.robustness_score if c.robustness_report else 0.0
                ),
                "overfit_score": (
                    c.robustness_report.overfit_score if c.robustness_report else 0.0
                ),
            }
            for c in ranked_candidates
        ],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/discovery/candidates")
async def get_candidates():
    if not discovery_engine.candidates:
        discovery_engine.generate_r10_variants()

    return [
        {
            "candidate_id": c.candidate_id,
            "name": c.name,
            "variant": c.variant,
            "tournament_rank": c.tournament_rank,
            "metrics": c.backtest_metrics,
            "dsl_definition": c.dsl_definition,
            "robustness_score": (
                c.robustness_report.robustness_score if c.robustness_report else 0.0
            ),
        }
        for c in discovery_engine.candidates
    ]


@router.post("/discovery/promote")
async def post_promote_candidate(
    payload: PromoteCandidateRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Evaluates candidate strategy through strict 8-point PromotionGate.
    Rejects immediately if any requirement is violated.
    """
    target = next((c for c in discovery_engine.candidates if c.candidate_id == payload.candidate_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Candidate {payload.candidate_id} not found.")

    if not target.robustness_report:
        raise HTTPException(
            status_code=400,
            detail="Candidate has not completed tournament backtesting and robustness audit.",
        )

    res: PromotionEvaluationResult = PromotionGate.evaluate(target.robustness_report)

    return {
        "candidate_id": target.candidate_id,
        "variant": target.variant,
        "approved": res.approved,
        "status": res.status,
        "criteria_checks": res.criteria_checks,
        "reasons": res.reasons,
        "evaluated_at": res.evaluated_at,
    }
