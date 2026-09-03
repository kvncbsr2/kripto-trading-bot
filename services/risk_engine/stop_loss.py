from shared.enums import SignalDirection


def validate_stop_and_target(
    direction: SignalDirection,
    entry_price: float,
    stop_price: float,
    take_profit: float,
    min_risk_reward: float = 1.5,
) -> bool:
    """
    Validates logical correctness of Stop Loss and Take Profit levels:
    - Long: stop_loss < entry_price < take_profit
    - Short: take_profit < entry_price < stop_loss
    - R:R >= min_risk_reward
    """
    if entry_price <= 0 or stop_price <= 0 or take_profit <= 0:
        return False

    if direction == SignalDirection.LONG:
        if not (stop_price < entry_price < take_profit):
            return False
        risk = entry_price - stop_price
        reward = take_profit - entry_price
        return (reward / risk) >= (min_risk_reward - 1e-4)

    elif direction == SignalDirection.SHORT:
        if not (take_profit < entry_price < stop_price):
            return False
        risk = stop_price - entry_price
        reward = entry_price - take_profit
        return (reward / risk) >= (min_risk_reward - 1e-4)

    return False
