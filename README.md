# ⚡ KRIPTO AGENT V6.1 — Reality-Hardened Local Paper Trading Platform

> **Production-Hardened Quantitative Local Paper Trading Platform**  
> *Real Binance Market Data + Strictly Causal R10 Engine + Mandatory Risk Guardrails + Zero Fake Data*

---

## 🎯 Temel İlkeler ve Mimari Güvenceler (V6.1)

1. **Sıfır Sahte Veri Politikası (Zero Fake Data Policy)**:
   - Tüm uç noktalar (`/market/ticker`, `/market/orderbook`, `/scanner`, `/positions`, `/trades`, `/performance/daily`, vb.) yalnızca gerçek Binance REST/WebSocket verisi ve yetkili Paper Broker durumunu yansıtır.
   - Veri bulunamadığında veya bağlantı kurulamadığında asla rastgele veya uydurma veri dönülmez; doğrudan `NO_DATA`, 404 veya boş liste döndürülür.

2. **Tek Yetkili Kağıt Yürütüm Motoru (`PaperExecutionEngine`)**:
   - Çift broker karmaşası giderilmiş, tek yetkili sınıf `services/execution/paper_execution.py` üzerinden tam portföy muhasebesi sağlanmıştır.
   - Gerçekçi %0.10 taker komisyonu, 5 bps slippage ve dinamik %50 zirve kâr kilitleme (Trailing Profit Lock) uygulanır.

3. **Spot Modu ve Açığa Satış Koruması**:
   - Binance Spot modunda (`is_spot_mode=True`), `LONG` emirlerine izin verilir.
   - `SHORT` yönlü tüm sinyaller hem Risk Motoru hem de Yürütüm Motoru seviyesinde `SIGNAL_ONLY` gerekçesiyle otomatik olarak reddedilir, spot hesabı açığa satamaz.

4. **Risk Motoru Baypas Yasağı**:
   - Hiçbir strateji, otonom motor, dashboard veya API ucu doğrudan emir üretemez.
   - Tüm akış zorunlu olarak: `Signal -> RiskEngine.evaluate() -> Approved RiskDecision -> PaperExecutionEngine.execute_order()` kuralına bağlıdır. Risk motoru mutlak veto yetkisine sahiptir.

5. **Nedensel R10 Uyumsuzluk & Katı Anti-Lookahead Güvencesi**:
   - T barındaki pivot noktaları strictly $T + 5$ sağ bar oluştuktan sonra teyit edilir.
   - Sinyaller geçmişe boyanmaz (`pivot_time != signal_time`), teyit anında üretilir.

---

## 🏗️ Mimari Pipeline

```text
                  BINANCE SPOT REAL-TIME (REST + WS)
                                 │
                                 ▼
                     DATA QUALITY ENGINE GATE
         (Monotonicity, Anomaly Check, Dynamic Spread Filter)
                                 │
                                 ▼
               CAUSAL STRATEGY ENGINE (R10 DIVERGENCE)
               (Strict T+5 Delay, Zero Future Knowledge)
                                 │
                                 ▼
                      CENTRAL RISK ENGINE GATE
      (Spot Long-Only Veto, $25 Max Risk/Trade, $50 Max Daily Loss)
                                 │
                                 ▼
              AUTHORITATIVE PAPER EXECUTION ENGINE
           (0.10% Fee, 5 bps Slippage, Trailing 50% Peak Lock)
                                 │
                                 ▼
             LOCAL CANONICAL NEXT.JS DASHBOARD & API
```

---

## 🚀 Yerel Başlatma ve Kullanım

### 1. Python Sanal Ortam & Bağımlılıklar
```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

### 2. API ve Otonom Motor Başlatma
```bash
python -m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000
```

### 3. Dashboard Seçenekleri
Sistem iki farklı kontrol paneli arabirimini tam uyumlu olarak destekler:

1. **Dahili Tek-Sayfa Kontrol Paneli (Hafif / Sıfır Bağımlılık)**:
   * Node.js/npm gerektirmez, doğrudan FastAPI tarafından servis edilir.
   * Tek tıkla `start.bat` çalıştırıldığında tarayıcıda otomatik açılır: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
2. **Kanonik Next.js Dashboard (Gelişmiş React Web UI)**:
   * Modern TypeScript/Tailwind mimarisi:
   ```bash
   cd apps/dashboard
   npm install
   npm run dev
   ```
   * Adres: [http://localhost:3000](http://localhost:3000)

### 4. Servis & API Linkleri
* **Dahili Kontrol Merkezi**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
* **Kanonik Next.js Paneli**: [http://localhost:3000](http://localhost:3000)
* **API Swagger Dokümantasyonu**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Sağlık & Hazırlık Kontrolü**: [http://localhost:8000/health](http://localhost:8000/health)
* **Prometheus Metrikleri**: [http://localhost:8000/metrics](http://localhost:8000/metrics)

---

## 🧪 Test Suite

Tüm birim, güvenlik, risk ve nedensellik testleri:
```bash
pytest tests/ -v
```
