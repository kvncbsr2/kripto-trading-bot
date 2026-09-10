import pytest
from services.market_data.market_data_service import MarketDataService


def test_orderbook_imbalance_calculation():
    # 1. Balanced book: equal bid and ask volume
    balanced_ob = {
        "symbol": "BTC/USDT",
        "spread_bps": 1.5,
        "bids": [[60000.0, 1.0], [59990.0, 1.0]],
        "asks": [[60010.0, 1.0], [60020.0, 1.0]],
    }
    res = MarketDataService.calculate_orderbook_imbalance(balanced_ob)
    assert res["obi"] == 0.0
    assert res["bid_volume"] == 2.0
    assert res["ask_volume"] == 2.0
    assert res["spread_bps"] == 1.5

    # 2. Heavy buyer book (positive OBI): 3x bids vs asks
    buyer_ob = {
        "symbol": "BTC/USDT",
        "spread_bps": 2.0,
        "bids": [[60000.0, 3.0], [59990.0, 3.0]],
        "asks": [[60010.0, 1.0], [60020.0, 1.0]],
    }
    res_buyer = MarketDataService.calculate_orderbook_imbalance(buyer_ob)
    # (6.0 - 2.0) / (6.0 + 2.0) = 4.0 / 8.0 = 0.50
    assert res_buyer["obi"] == 0.50
    assert res_buyer["bid_volume"] == 6.0
    assert res_buyer["ask_volume"] == 2.0

    # 3. Heavy seller wall (negative OBI): 1 bid vs 4 asks
    seller_ob = {
        "symbol": "BTC/USDT",
        "spread_bps": 3.0,
        "bids": [[60000.0, 1.0]],
        "asks": [[60010.0, 4.0]],
    }
    res_seller = MarketDataService.calculate_orderbook_imbalance(seller_ob)
    # (1.0 - 4.0) / (1.0 + 4.0) = -3.0 / 5.0 = -0.60
    assert res_seller["obi"] == -0.60

    # 4. None or empty book safe fallback
    assert MarketDataService.calculate_orderbook_imbalance(None)["obi"] == 0.0
    assert MarketDataService.calculate_orderbook_imbalance({})["obi"] == 0.0
