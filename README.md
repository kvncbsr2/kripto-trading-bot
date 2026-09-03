# ⚡ KRIPTO AGENT — Master V3

> **Binance Real-Time Autonomous Paper Trading & Validation System**  
> *Production-Oriented 7-Day $5,000 Paper Trading Validation System*

---

## 🎯 Ana Hedef & Felsefe

**KRIPTO AGENT V3**, gerçek zamanlı Binance piyasa verilerini (Kline, Ticker, BookTicker, OrderBook Depth) kullanarak, tek bir dolar gerçek para riske etmeden (`LIVE_TRADING=false`), gerçek piyasa dinamiklerini (komisyon, kayma, spread, fonlama) simüle ederek algoritmik stratejilerin istatistiksel güvenilirliğini test eden profesyonel bir kuantitatif ticaret altyapısıdır.

* **Sanal Başlangıç Sermayesi**: \$5,000.00
* **Piyasa Verisi**: Binance Spot (Gerçek Zamanlı WebSocket + REST Fallback)
* **Emir Modeli**: Paper Trading (5 bps slippage, %0.1 fee, bid/ask spread)
* **İşlem Başına Risk**: %0.5 (yaklaşık \$25.00)
* **Günlük Maksimum Zarar Kilidi**: \$50.00 (`DAILY_RISK_LOCK`)
* **Günlük Hedef Aralığı**: +\$20 ile +\$100 net kâr (Performans karşılaştırma ölçütü)
* **Canlı İşlem Koruması**: Kod düzeyinde sert kilit (`LIVE_TRADING` aktif edilirse veya `BinanceLiveExecutionEngine` çağrılırsa `RuntimeError` fırlatılır).

---

## 🏗️ Mimari Pipeline

```text
                  BINANCE SPOT
                       │
                       ▼
         Real-Time Multiplexed WebSocket
   (kline_15m, bookTicker, miniTicker, depth)
                       │
                       ▼
            Data Quality & Validation
(OHLC check, Out-of-Order, Duplicate, Stale, Spread Anomaly)
                       │
                       ▼
             Binance Market Scanner
(Liquidity Filter >$10M, Spread Filter <15 bps, Opportunity Score)
                       │
                       ▼
                 Feature Engine
 (Trend, Momentum, Volatilite, Hacim, Market Structure, RSI Divergence)
                       │
                       ▼
              Market Regime Engine
(BULL_TREND, BEAR_TREND, SIDEWAYS, HIGH_VOLATILITY, LOW_VOLATILITY)
                       │
                       ▼
         Strategy Engine & Signal Scorer
 (Trend Following, Mean Reversion, RSI Divergence Swing)
                       │
                       ▼
                 Risk Engine
(0.5% ATR Sizing, $50 Daily Loss Lock, 4-Aşamalı Devre Kesici)
                       │
                       ▼
                 Paper Broker
 (Ask/Bid Spread, 5 bps Slippage, %0.1 Fees, Break-Even & Trailing Stop)
                       │
                       ▼
        Portfolio & Performance Engine
 (Metrics, Monte Carlo, Duyarlılık Analizi, Günlük Raporlar)
                       │
                       ▼
    FastAPI Backend & Canlı Web Dashboard & Telegram Bot
```

---

## 💻 Teknoloji Stack

* **Backend**: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic
* **Veri Tabanı**: PostgreSQL 16 / TimescaleDB (Hypertables), SQLite (yerel test), Redis
* **Kuantitatif & Analiz**: NumPy, Pandas, SciPy, custom vectorized technical indicators
* **Borsa Bağlantısı**: Binance WebSocket, CCXT Async
* **Backtest & Doğrulama**: BacktestRunner, WalkForwardValidator, MonteCarloSimulator
* **Arayüz**: FastAPI OpenAPI (`/docs`), Dahili İnteraktif HTML5/Tailwind Dashboard (`/dashboard`), Next.js / TypeScript (`apps/dashboard/`)
* **Test & QA**: Pytest, Pytest-Asyncio, Ruff, MyPy (%100 geçiş, 28/28 test)
* **Konteyner**: Docker, Docker Compose

---

## 🚀 Hızlı Başlangıç

### 1. Kurulum
```bash
# Bağımlılıkları yükleyin
make install

# Ortam değişkenlerini hazırlayın
cp .env.example .env

# Veritabanı tablolarını oluşturun
make migrate
```

### 2. Testleri Çalıştırın
```bash
# Tüm testleri çalıştır (Unit, Strategy, Risk, Failure, API, E2E)
make test

# Linter kontrolü
make lint

# Tip denetimi
make typecheck
```

### 3. 7 Günlük \$5,000 Paper Trading Deneyini Başlatın
```bash
make paper
```
Bu komut 7 günlük simülasyonu çalıştırır; her gün için günlük rapor (Day 1-7), hedef tutturma oranları (\$20, \$50, \$100/gün), strateji ve coin katkıları, komisyon ve kayma duyarlılık analizleri ve Monte Carlo simülasyonu ile nihai kararını (`GREEN` / `YELLOW` / `RED`) üretir.

### 4. API & Web Dashboard'u Başlatın
```bash
make dev
```
* **Swagger API Dokümantasyonu**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **İnteraktif Web Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)

### 5. Docker ile Tek Komutla Çalıştırma
```bash
make docker-up
```

---

## 📊 Stratejiler

1. **Trend Following**: EMA20 > EMA50 > EMA200 uyumu, ADX $\ge 23$, RSI 45–68 arası sağlıklı momentum.
2. **Mean Reversion**: Sadece `SIDEWAYS` rejiminde; Bollinger Bandı sapması ve RSI aşırı satım/alım dönüşü.
3. **RSI Divergence Swing**: Fiyat Lower Low yaparken RSI Higher Low yaptığında (veya tersi) çalışan, hacim ve piyasa yapısı onayı gerektiren yüksek olasılıklı swing stratejisi.

---

## 🔒 Güvenlik İlkeleri

* Asla API key veya secret kod içine yazılmaz.
* Para çekme (withdrawal) izni olan API anahtarı kesinlikle kabul edilmez.
* Canlı işlem katmanı V3 süresince **TAMAMEN KİLİTLİDİR** (`BinanceLiveExecutionEngine` çağrılırsa `RuntimeError` fırlatılır).
* Stratejiler ve AI ajanları asla Risk Engine limitlerini bypass edemez.

---

## 📚 Dokümantasyonlar

* [Sistem Mimarisi](docs/ARCHITECTURE.md)
* [Binance Entegrasyon Mimarisi](docs/BINANCE.md)
* [Piyasa Verisi & Tarayıcı (Scanner)](docs/MARKET_DATA.md)
* [Strateji Motoru](docs/STRATEGIES.md)
* [RSI Uyumsuzluk (Divergence) Motoru](docs/RSI_DIVERGENCE.md)
* [Risk Modeli & Devre Kesici](docs/RISK_MODEL.md)
* [Paper Broker & Maliyet Simülasyonu](docs/PAPER_TRADING.md)
* [Backtesting Kılavuzu](docs/BACKTESTING.md)
* [Walk-Forward Doğrulama](docs/WALK_FORWARD.md)
* [Monte Carlo Risk Simülasyonu](docs/MONTE_CARLO.md)
* [7 Günlük Doğrulama Deneyi](docs/7_DAY_EXPERIMENT.md)
* [API Dokümantasyonu](docs/API.md)
* [Güvenlik Mimarisi](docs/SECURITY.md)
* [Operasyon El Kitabı](docs/OPERATIONS.md)
