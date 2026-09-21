"""
Unit and Invariant Tests for Contextual Multi-Armed Bandit Engine
=================================================================
Verifies:
1. Predefined 25 arms & 6 market contexts.
2. Normal-Gamma Bayesian conjugate updating.
3. Thompson Sampling distribution convergence & arm selection.
4. Automatic 24-hour quarantine on underperforming arms (R < -0.5 across 10 trades).
5. State dictionary serialization for API and dashboard observability.
"""

import os
import tempfile
import pytest
import numpy as np

from services.learning.contextual_bandit import (
    ARM_IDS,
    ARMS,
    CONTEXTS,
    STRATEGIES,
    RISK_LEVELS,
    ContextualBandit,
)


@pytest.fixture
def temp_bandit():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    bandit = ContextualBandit(db_path=db_path)
    yield bandit
    try:
        os.remove(db_path)
    except Exception:
        pass


def test_arms_and_contexts_structure(temp_bandit):
    assert len(ARMS) == 25, "Must have exactly 25 predefined arms (5 strategies x 5 risk levels)"
    assert len(ARM_IDS) == 25
    assert len(CONTEXTS) == 6, "Must have exactly 6 macro contexts"
    assert len(STRATEGIES) == 5
    assert len(RISK_LEVELS) == 5


def test_posterior_default_and_persistence(temp_bandit):
    context = "BTC_BULL_LOW_VOL"
    arm = ("momentum_dip_rebound", 1)

    mu, n, alpha, beta = temp_bandit.get_posterior(context, arm)
    assert mu > 0.0, "L1 arms should boot with a conservative positive prior"
    assert n == 2.0
    assert alpha == 2.0
    assert beta == 2.0

    # Save custom posterior
    temp_bandit.save_posterior(context, arm, mu=0.75, n=10.0, alpha=5.0, beta=4.0)
    mu2, n2, alpha2, beta2 = temp_bandit.get_posterior(context, arm)
    assert mu2 == 0.75
    assert n2 == 10.0
    assert alpha2 == 5.0
    assert beta2 == 4.0


def test_conjugate_normal_gamma_updating(temp_bandit):
    context = "BTC_BEAR_HIGH_VOL"
    arm = ("ema_macd_pullback", 5)

    mu0, n0, alpha0, beta0 = temp_bandit.get_posterior(context, arm)

    # 1. Update with large positive win (R = +2.5)
    temp_bandit.update_posterior(context, arm, r_multiple=2.5)
    mu1, n1, alpha1, beta1 = temp_bandit.get_posterior(context, arm)

    assert n1 == n0 + 1.0
    assert mu1 > mu0, "Posterior mean must increase after a profitable R trade"
    assert alpha1 == alpha0 + 0.5
    assert beta1 >= beta0

    # 2. Update with loss (R = -1.0)
    temp_bandit.update_posterior(context, arm, r_multiple=-1.0)
    mu2, n2, alpha2, beta2 = temp_bandit.get_posterior(context, arm)

    assert n2 == n1 + 1.0
    assert mu2 < mu1, "Posterior mean must decrease after a loss"


def test_thompson_sampling_convergence(temp_bandit):
    """
    Simulates 1000 Thompson Samples between an intentionally strong arm (mu=+1.5)
    and an intentionally weak arm (mu=-1.0) with equal confidence.
    Proves Bayesian exploration overwhelmingly exploits the winning arm.
    """
    context = "BTC_RANGING_LOW_VOL"
    arm_winner = ("momentum_dip_rebound", 3)
    arm_loser = ("bollinger_volume_breakout", 10)

    temp_bandit.save_posterior(context, arm_winner, mu=1.5, n=20.0, alpha=10.0, beta=5.0)
    temp_bandit.save_posterior(context, arm_loser, mu=-1.0, n=20.0, alpha=10.0, beta=5.0)

    # For other arms, assign very negative mu to isolate the two
    for a in ARMS:
        if a not in [arm_winner, arm_loser]:
            temp_bandit.save_posterior(context, a, mu=-5.0, n=20.0, alpha=10.0, beta=5.0)

    winner_count = 0
    total_samples = 1000

    for _ in range(total_samples):
        selected = temp_bandit.select_arm(context)
        if selected == arm_winner:
            winner_count += 1

    win_rate = winner_count / total_samples
    assert win_rate > 0.95, f"Expected winner arm to be chosen >95% of the time, got {win_rate:.2%}"


def test_24h_quarantine_safety_invariant(temp_bandit):
    """
    Verifies that 10 consecutive poor trades with avg R < -0.5 automatically
    quarantines the arm, preventing select_arm from picking it.
    """
    context = "BTC_BEAR_LOW_VOL"
    arm_bad = ("trend_following", 10)

    assert not temp_bandit.is_arm_quarantined(context, arm_bad)

    # Feed 10 consecutive losses (R = -0.8)
    for _ in range(10):
        temp_bandit.update_posterior(context, arm_bad, r_multiple=-0.8)

    assert temp_bandit.is_arm_quarantined(context, arm_bad), "Arm must be quarantined after 10 trades with avg R < -0.5"

    # Even if we artificially set its mu very high, select_arm must ignore it
    temp_bandit.save_posterior(context, arm_bad, mu=10.0, n=20.0, alpha=10.0, beta=5.0)
    for _ in range(50):
        selected = temp_bandit.select_arm(context)
        assert selected != arm_bad, "Quarantined arm must NEVER be selected by Thompson Sampling"


def test_state_dict_observability(temp_bandit):
    state = temp_bandit.get_state_dict()
    assert state["success"] is True
    assert state["total_arms_defined"] == 25
    assert state["total_contexts_defined"] == 6
    assert len(state["contexts"]) == 6
    assert len(state["preferred_by_context"]) == 6
    for ctx in CONTEXTS:
        assert ctx in state["posteriors"]
