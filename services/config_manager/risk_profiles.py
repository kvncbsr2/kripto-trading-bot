"""
Authoritative Risk Profile Architecture V2 for KRIPTO AGENT.
Strictly separates Risk Profiles (L1-L10) from Strategy selection (STRATEGY != RISK PROFILE).
Provides single source of truth, atomic switching, restart persistence, and safe rollback to Level 1.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from services.config_manager.state_persistence import (
    load_persisted_profile_state,
    save_persisted_profile_state,
)
from services.strategy_engine.registry import (
    DEFAULT_STRATEGY_ID,
    create_strategy,
    get_strategy_metadata,
)
from shared.logging import add_system_log, get_logger

logger = get_logger("risk-profiles", service="config_manager")


@dataclass(frozen=True)
class RiskProfile:
    level: int
    name: str
    badge: str
    description: str
    risk_per_trade: float
    min_signal_score: float
    min_opportunity_score: float
    pivot_left: int
    pivot_right: int
    max_open_positions: int
    daily_target: float
    daily_max_loss: float
    target_mode: str
    preferred_risk_reward: float
    timeframe: str
    color: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk_per_trade_pct"] = round(self.risk_per_trade * 100, 2)
        return d


# 10 Canonical Risk Profiles (Decoupled from Strategy Names)
RISK_PROFILES: Dict[int, RiskProfile] = {
    1: RiskProfile(
        level=1,
        name="Ultra Conservative (Capital Preservation)",
        badge="🛡️ Seviye 1",
        description="En yüksek seçicilik, minimum risk, maksimum sermaye koruma. Sadece A-Grade sinyaller.",
        risk_per_trade=0.005,  # %0.5
        min_signal_score=65.0,
        min_opportunity_score=50.0,
        pivot_left=5,
        pivot_right=2,
        max_open_positions=2,
        daily_target=50.0,
        daily_max_loss=50.0,
        target_mode="HARD",
        preferred_risk_reward=2.0,
        timeframe="15m",
        color="emerald",
    ),
    2: RiskProfile(
        level=2,
        name="Conservative",
        badge="🛡️ Seviye 2",
        description="Düşük riskli, yüksek filtreli dengeli koruma profili.",
        risk_per_trade=0.008,  # %0.8
        min_signal_score=60.0,
        min_opportunity_score=48.0,
        pivot_left=5,
        pivot_right=2,
        max_open_positions=3,
        daily_target=100.0,
        daily_max_loss=50.0,
        target_mode="SOFT",
        preferred_risk_reward=2.0,
        timeframe="15m",
        color="teal",
    ),
    3: RiskProfile(
        level=3,
        name="Balanced",
        badge="⚖️ Seviye 3",
        description="Orta vadeli dalgalanmaları değerlendiren dengeli risk/kazanç profili.",
        risk_per_trade=0.010,  # %1.0
        min_signal_score=58.0,
        min_opportunity_score=48.0,
        pivot_left=4,
        pivot_right=2,
        max_open_positions=3,
        daily_target=120.0,
        daily_max_loss=60.0,
        target_mode="SOFT",
        preferred_risk_reward=2.0,
        timeframe="15m",
        color="cyan",
    ),
    4: RiskProfile(
        level=4,
        name="Growth",
        badge="⚖️ Seviye 4",
        description="Kontrollü sermaye büyümesi hedefleyen standart optimal profil.",
        risk_per_trade=0.012,  # %1.2
        min_signal_score=55.0,
        min_opportunity_score=45.0,
        pivot_left=4,
        pivot_right=2,
        max_open_positions=4,
        daily_target=150.0,
        daily_max_loss=75.0,
        target_mode="SOFT",
        preferred_risk_reward=2.0,
        timeframe="15m",
        color="blue",
    ),
    5: RiskProfile(
        level=5,
        name="Active",
        badge="⚖️ Seviye 5",
        description="Genişletilmiş piyasa taraması ile aktif fırsat yakalama profili.",
        risk_per_trade=0.015,  # %1.5
        min_signal_score=52.0,
        min_opportunity_score=42.0,
        pivot_left=4,
        pivot_right=2,
        max_open_positions=5,
        daily_target=180.0,
        daily_max_loss=90.0,
        target_mode="SOFT",
        preferred_risk_reward=1.8,
        timeframe="15m",
        color="indigo",
    ),
    6: RiskProfile(
        level=6,
        name="Aggressive",
        badge="📈 Seviye 6",
        description="Daha erken teyit ve yüksek pozisyon esnekliği sağlayan agresif büyüme profili.",
        risk_per_trade=0.016,  # %1.6
        min_signal_score=50.0,
        min_opportunity_score=40.0,
        pivot_left=3,
        pivot_right=2,
        max_open_positions=5,
        daily_target=200.0,
        daily_max_loss=100.0,
        target_mode="SOFT",
        preferred_risk_reward=1.8,
        timeframe="15m",
        color="violet",
    ),
    7: RiskProfile(
        level=7,
        name="High Risk",
        badge="📈 Seviye 7",
        description="Hızlı tepki aralıkları ile yüksek risk toleranslı işlem profili.",
        risk_per_trade=0.018,  # %1.8
        min_signal_score=48.0,
        min_opportunity_score=38.0,
        pivot_left=3,
        pivot_right=1,
        max_open_positions=6,
        daily_target=250.0,
        daily_max_loss=125.0,
        target_mode="SOFT",
        preferred_risk_reward=1.8,
        timeframe="15m",
        color="purple",
    ),
    8: RiskProfile(
        level=8,
        name="High Activity",
        badge="⚡ Seviye 8",
        description="Yüksek işlem frekansı ve dinamik kâr arayışı sunan aktif profil.",
        risk_per_trade=0.020,  # %2.0
        min_signal_score=45.0,
        min_opportunity_score=38.0,
        pivot_left=3,
        pivot_right=1,
        max_open_positions=6,
        daily_target=300.0,
        daily_max_loss=120.0,
        target_mode="SOFT",
        preferred_risk_reward=1.7,
        timeframe="15m",
        color="amber",
    ),
    9: RiskProfile(
        level=9,
        name="Very Aggressive",
        badge="⚡ Seviye 9",
        description="Yüksek volatilite dönemlerinde esnetilmiş sinyal toleransları uygulayan çok agresif profil.",
        risk_per_trade=0.022,  # %2.2
        min_signal_score=42.0,
        min_opportunity_score=35.0,
        pivot_left=2,
        pivot_right=1,
        max_open_positions=7,
        daily_target=400.0,
        daily_max_loss=150.0,
        target_mode="SOFT",
        preferred_risk_reward=1.6,
        timeframe="15m",
        color="orange",
    ),
    10: RiskProfile(
        level=10,
        name="Maximum Configured Risk",
        badge="🔥 Seviye 10",
        description="Sistemin yapılandırılmış en üst risk sınırlarını çalıştıran maksimum profil. Temel emniyet kilitleri aktiftir.",
        risk_per_trade=0.025,  # %2.5
        min_signal_score=40.0,
        min_opportunity_score=30.0,
        pivot_left=2,
        pivot_right=1,
        max_open_positions=8,
        daily_target=500.0,
        daily_max_loss=200.0,
        target_mode="SOFT",
        preferred_risk_reward=1.5,
        timeframe="15m",
        color="rose",
    ),
}


def get_all_profiles() -> List[Dict[str, Any]]:
    """Returns all 10 canonical risk profiles."""
    return [p.to_dict() for p in sorted(RISK_PROFILES.values(), key=lambda x: x.level)]


def get_profile(level: int) -> RiskProfile:
    """Safe lookup: defaults strictly to Level 1 if level is unknown or out of bounds."""
    if level not in RISK_PROFILES:
        logger.warning(f"Profile level {level} not found. Safe fallback to Level 1 baseline.")
        return RISK_PROFILES[1]
    return RISK_PROFILES[level]


def _get_system_instances():
    try:
        from apps.api.app.api.state import RUNTIME_STATE
        from services.autonomous_runner import autonomous_trader
    except ImportError:
        RUNTIME_STATE = {}
        autonomous_trader = None
    return RUNTIME_STATE, autonomous_trader


def apply_profile_to_system(level: int, source: str = "api") -> Dict[str, Any]:
    """
    Atomically applies the chosen Risk Profile (1-10) to the running system.
    Persists selection across restarts.
    Guarantees that existing open positions are never forcibly closed on limit downgrade.
    """
    profile = get_profile(level)
    RUNTIME_STATE, autonomous_trader = _get_system_instances()

    current_strategy_id = RUNTIME_STATE.get("active_strategy_id", DEFAULT_STRATEGY_ID)

    # 1. Update Autonomous Trader's Risk Engine
    if autonomous_trader and hasattr(autonomous_trader, "risk_engine") and autonomous_trader.risk_engine:
        re = autonomous_trader.risk_engine
        re.risk_per_trade = profile.risk_per_trade
        re.daily_target_max = profile.daily_target
        re.daily_target_min = round(profile.daily_target * 0.7, 2)
        re.daily_max_loss_usd = profile.daily_max_loss
        re.max_open_positions = profile.max_open_positions
        re.target_mode = profile.target_mode
        re.min_risk_reward = profile.preferred_risk_reward
        if hasattr(re, "circuit_breaker") and re.circuit_breaker:
            re.circuit_breaker.daily_max_loss_usd = profile.daily_max_loss

    # 2. Update Strategy Risk Parameters (without changing the strategy class)
    if autonomous_trader and hasattr(autonomous_trader, "strategy") and autonomous_trader.strategy:
        st = autonomous_trader.strategy
        if hasattr(st, "min_signal_score"):
            st.min_signal_score = profile.min_signal_score
        if hasattr(st, "right_bars"):
            st.right_bars = profile.pivot_right
        if hasattr(st, "left_bars"):
            st.left_bars = profile.pivot_left
        if hasattr(st, "risk_reward_ratio"):
            st.risk_reward_ratio = profile.preferred_risk_reward
        if hasattr(st, "timeframe"):
            st.timeframe = profile.timeframe

    # 3. Update Authoritative Runtime State
    RUNTIME_STATE["active_risk_profile"] = profile.to_dict()
    RUNTIME_STATE["active_profile_level"] = profile.level
    RUNTIME_STATE["daily_target"] = profile.daily_target
    RUNTIME_STATE["daily_target_max"] = profile.daily_target
    RUNTIME_STATE["daily_target_min"] = round(profile.daily_target * 0.7, 2)
    RUNTIME_STATE["daily_max_loss"] = profile.daily_max_loss
    RUNTIME_STATE["max_open_positions"] = profile.max_open_positions
    RUNTIME_STATE["min_opportunity_score"] = profile.min_opportunity_score
    RUNTIME_STATE["min_signal_score"] = profile.min_signal_score
    RUNTIME_STATE["target_mode"] = profile.target_mode

    # 4. Persist state to disk for restart recovery
    save_persisted_profile_state(
        risk_level=profile.level,
        strategy_id=current_strategy_id,
        source=source,
    )

    msg = (
        f"🎯 PROFILE_CHANGED: {profile.badge} - {profile.name} (Risk: %{profile.risk_per_trade * 100:.1f}, "
        f"Min Skor: {profile.min_signal_score}, Hedef: ${profile.daily_target:.0f}, Max Kayıp: ${profile.daily_max_loss:.0f})"
    )
    logger.info(msg)
    try:
        add_system_log(msg, level="SUCCESS", service="risk")
    except Exception:
        pass

    return {
        "success": True,
        "level": profile.level,
        "active_level": profile.level,
        "profile": profile.to_dict(),
        "message": msg,
    }


def apply_strategy_to_system(strategy_id: str, source: str = "api") -> Dict[str, Any]:
    """
    Atomically switches the active strategy to one from the Strategy Registry.
    Re-uses current Risk Profile parameters (pivots, R:R, signal score).
    Never forcibly closes existing open positions.
    """
    meta = get_strategy_metadata(strategy_id)
    RUNTIME_STATE, autonomous_trader = _get_system_instances()

    active_level = RUNTIME_STATE.get("active_profile_level", 1)
    profile = get_profile(active_level)

    # Build new strategy with active risk profile parameters
    new_strategy = create_strategy(
        meta.id,
        left_bars=profile.pivot_left,
        right_bars=profile.pivot_right,
        min_signal_score=profile.min_signal_score,
        risk_reward_ratio=profile.preferred_risk_reward,
        timeframe=profile.timeframe,
    )

    # Swap onto autonomous trader
    if autonomous_trader:
        autonomous_trader.strategy = new_strategy

    # Update runtime state
    RUNTIME_STATE["active_strategy_id"] = meta.id
    RUNTIME_STATE["active_strategy"] = meta.to_dict()

    # Persist state to disk
    save_persisted_profile_state(
        risk_level=active_level,
        strategy_id=meta.id,
        source=source,
    )

    msg = f"🧩 STRATEGY_CHANGED: {meta.name} ({meta.category}) aktif edildi. (Timeframe: {meta.default_timeframe})"
    logger.info(msg)
    try:
        add_system_log(msg, level="SUCCESS", service="strategy")
    except Exception:
        pass

    return {
        "success": True,
        "strategy_id": meta.id,
        "strategy": meta.to_dict(),
        "message": msg,
    }


def restore_runtime_system_state() -> Dict[str, Any]:
    """
    Called on system BOOT to synchronize persisted state across all components.
    Ensures UI, API, Runtime, RiskEngine, and Trader start with 100% identical state.
    """
    persisted = load_persisted_profile_state()
    level = persisted.get("active_risk_profile_level", 1)
    strategy_id = persisted.get("active_strategy_id", DEFAULT_STRATEGY_ID)

    logger.info(f"RUNTIME_RESTORED: Restoring persisted Profile L{level} and Strategy '{strategy_id}'.")

    # 1. Apply risk profile
    apply_profile_to_system(level, source="startup_recovery")

    # 2. Apply strategy
    apply_strategy_to_system(strategy_id, source="startup_recovery")

    msg = f"🔄 RUNTIME_RESTORED: Seviye L{level} ve Strateji '{strategy_id}' diskten başarıyla yüklendi."
    try:
        add_system_log(msg, level="INFO", service="system")
    except Exception:
        pass

    return {
        "active_profile_level": level,
        "active_strategy_id": strategy_id,
        "restored": True,
    }
