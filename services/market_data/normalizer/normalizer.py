from datetime import datetime, timezone
from typing import Any, Dict, List

from shared.enums import ExchangeName, OrderSide, Timeframe
from shared.schemas import Candle, MarketTick, OrderBook, OrderBookEntry, Trade
from shared.utils import normalize_timestamp


class DataNormalizer:
    @staticmethod
    def normalize_ohlcv(
        raw_candle: List[Any],
        symbol: str,
        timeframe: Timeframe,
        exchange: ExchangeName = ExchangeName.BINANCE,
    ) -> Candle:
        """
        CCXT OHLCV format: [timestamp, open, high, low, close, volume]
        """
        ts = normalize_timestamp(raw_candle[0])
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            open=float(raw_candle[1]),
            high=float(raw_candle[2]),
            low=float(raw_candle[3]),
            close=float(raw_candle[4]),
            volume=float(raw_candle[5]),
            exchange=exchange,
        )

    @staticmethod
    def normalize_ticker(
        raw_ticker: Dict[str, Any],
        symbol: str,
        exchange: ExchangeName = ExchangeName.BINANCE,
    ) -> MarketTick:
        ts = normalize_timestamp(raw_ticker.get("timestamp") or datetime.now(timezone.utc))
        return MarketTick(
            symbol=symbol,
            exchange=exchange,
            timestamp=ts,
            bid=float(raw_ticker.get("bid") or raw_ticker.get("last") or 0.0),
            ask=float(raw_ticker.get("ask") or raw_ticker.get("last") or 0.0),
            last_price=float(raw_ticker.get("last") or raw_ticker.get("close") or 0.0),
            volume_24h=float(raw_ticker.get("baseVolume") or 0.0)
            if raw_ticker.get("baseVolume")
            else None,
        )

    @staticmethod
    def normalize_orderbook(
        raw_book: Dict[str, Any],
        symbol: str,
        exchange: ExchangeName = ExchangeName.BINANCE,
    ) -> OrderBook:
        ts = normalize_timestamp(raw_book.get("timestamp") or datetime.now(timezone.utc))
        bids = [
            OrderBookEntry(price=float(b[0]), amount=float(b[1])) for b in raw_book.get("bids", [])
        ]
        asks = [
            OrderBookEntry(price=float(a[0]), amount=float(a[1])) for a in raw_book.get("asks", [])
        ]
        return OrderBook(
            symbol=symbol,
            exchange=exchange,
            timestamp=ts,
            bids=bids,
            asks=asks,
        )

    @staticmethod
    def normalize_trade(
        raw_trade: Dict[str, Any],
        symbol: str,
        exchange: ExchangeName = ExchangeName.BINANCE,
    ) -> Trade:
        ts = normalize_timestamp(raw_trade.get("timestamp") or datetime.now(timezone.utc))
        side = OrderSide.BUY if raw_trade.get("side") == "buy" else OrderSide.SELL
        return Trade(
            symbol=symbol,
            exchange=exchange,
            timestamp=ts,
            trade_id=str(raw_trade.get("id", "")),
            price=float(raw_trade.get("price", 0.0)),
            amount=float(raw_trade.get("amount", 0.0)),
            side=side,
        )
