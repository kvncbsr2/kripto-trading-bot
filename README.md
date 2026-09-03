# ⚡ KRIPTO AGENT — Master V2

> **Autonomous Algorithmic Crypto Trading & Paper-Trading Platform**  
> *Production-Oriented 7-Day $5,000 Paper Trading Validation System*

---

## 🎯 Ana Hedef & Felsefe

**KRIPTO AGENT V2**, tek bir satır gerçek para riske etmeden (`LIVE_TRADING=false`), gerçek piyasa dinamiklerini (komisyon, kayma, spread, fonlama) simüle ederek algoritmik stratejilerin istatistiksel güvenilirliğini test eden profesyonel bir kuantitatif ticaret altyapısıdır.

* **Sanal Başlangıç Sermayesi**: \$5,000.00
* **İşlem Başına Risk**: %0.5 (yaklaşık \$25.00)
* **Günlük Maksimum Zarar Kilidi**: \$50.00 (`DAILY_RISK_LOCK`)
* **Günlük Hedef Aralığı**: +\$20 ile +\$100 net kâr (Performans karşılaştırma ölçütü)
* **Canlı İşlem Koruması**: Kod düzeyinde sert kilit (`LIVE_TRADING` aktif edilirse `RuntimeError` fırlatılır).

---

## 🏗️ Mimari Pipeline

```text
Market Data (CCXT / Binance REST & WebSocket)
        ↓
Data Validation & Normalization
        ↓
Feature Engine (Trend, Momentum, Volatilite, Hacim, Market Structure, RSI Divergence)
        ↓
Market Regime Engine (BULL_TREND, BEAR_TREND, SIDEWAYS, HIGH_VOLATILITY, LOW_VOLATILITY)
        ↓
Strategy Engine & Signal Scorer (Trend Following, Mean Reversion, RSI Divergence)
        ↓
Opportunity Scoring (0 - 100 Piyasa Fırsat Skoru)
        ↓
Risk Engine (Veto Yetkisi, ATR Pozisyon Boyutlandırma, Devre Kesici)
        ↓
Paper Broker (5 bps Slippage, %0.1 Komisyon, Break-Even & Trailing Stop)
        ↓
Portfolio & Performance Engine (Drawdown, Sharpe, Sortino, Monte Carlo, Journal)
        ↓
FastAPI Backend & Canlı Web Dashboard & Telegram Raporlama
```

---

## 💻 Teknoloji Stack

* **Backend**: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic
* **Veri Tabanı**: PostgreSQL 16 / TimescaleDB (Hypertables), SQLite (yerel test), Redis
* **Kuantitatif & Analiz**: NumPy, Pandas, SciPy, custom vectorized technical indicators
* **Backtest & Doğrulama**: BacktestRunner, WalkForwardValidator, MonteCarloSimulator
* **Arayüz**: FastAPI OpenAPI (`/docs`), Dahili İnteraktif HTML5/Tailwind Dashboard (`/dashboard`), Next.js / TypeScript (`apps/dashboard/`)
* **Test & QA**: Pytest, Pytest-Asyncio, Ruff, MyPy (%100 geçiş)
* **Konteyner**: Docker, Docker Compose

---

## 🚀 Hızlı Başlangıç

### 1. Kurulum
```bash
# Bağımlılıkları yükleyin
pip install -e ".[dev]"

# Ortam değişkenlerini hazırlayın
cp .env.example .env

# Veritabanı tablolarını oluşturun
python -m database.init_db
```

### 2. Testleri Çalıştırın
```bash
# Tüm testleri çalıştır (Unit, Strategy, Risk, API, E2E)
pytest

# Linter kontrolü
ruff check .

# Tip denetimi
mypy .
```

### 3. 7 Günlük \$5,000 Paper Trading Deneyini Başlatın
```bash
python -m scripts.run_7day_experiment
```
Bu komut 7 günlük simülasyonu çalıştırır; her gün için günlük rapor (Day 1-7), hedef tutturma oranları (\$20, \$50, \$100/gün), komisyon ve kayma duyarlılık analizleri ve Monte Carlo simülasyonu ile nihai kararını (`GREEN` / `YELLOW` / `RED`) üretir.

### 4. API & Web Dashboard'u Başlatın
```bash
uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 --reload
```
* **Swagger API Dokümantasyonu**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **İnteraktif Web Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)

### 5. Docker ile Tek Komutla Çalıştırma
```bash
docker compose up -d
```

---

## 📊 Stratejiler

1. **Trend Following**: EMA20 > EMA50 > EMA200 uyumu, ADX $\ge 23$, RSI 45–68 arası sağlıklı momentum.
2. **Mean Reversion**: Sadece `SIDEWAYS` rejiminde; Bollinger Bandı sapması ve RSI aşırı satım/alım dönüşü.
3. **RSI Divergence Swing**: Fiyat Lower Low yaparken RSI Higher Low yaptığında (veya tersi) çalışan yüksek olasılıklı swing stratejisi.

---

## 🔒 Güvenlik İlkeleri

* Asla API key veya secret kod içine yazılmaz.
* Para çekme (withdrawal) izni olan API anahtarı kesinlikle kabul edilmez.
* Canlı işlem katmanı V2 süresince devre dışıdır.
* Stratejiler ve AI ajanları asla Risk Engine limitlerini bypass edemez.

---

## 📚 Dokümantasyonlar

* [Sistem Mimarisi](docs/ARCHITECTURE.md)
* [Risk Modeli](docs/RISK_MODEL.md)
* [Strateji Motoru](docs/STRATEGIES.md)
* [Paper Broker & Maliyet Simülasyonu](docs/PAPER_TRADING.md)
* [Backtesting & Walk-Forward](docs/BACKTESTING.md)
* [API Dokümantasyonu](docs/API.md)
* [7 Günlük Doğrulama Deneyi](docs/7_DAY_EXPERIMENT.md)
* [Güvenlik Mimarisi](docs/SECURITY.md)
* [Operasyon El Kitabı](docs/OPERATIONS.md)
