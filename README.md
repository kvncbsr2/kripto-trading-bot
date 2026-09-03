# ⚡ KRIPTO AGENT — Master V6

> **Strategy Discovery + R10 RSI Divergence + Automated Backtest + Overfitting Protection + Real-Time Validation**  
> *Production-Grade Quantitative Crypto Trading Platform & Local Mission Control*

---

## 🎯 Ana Hedef & Felsefe (Master V6)

**KRIPTO AGENT V6**, yalnızca geçmiş veride "eğri uydurarak (curve-fitting) kârlı görünen" stratejiler aramaz. Asıl amaç:

* **Sıfır Lookahead / Geleceğe Bakış Engeli**: Pivotlar ancak sağ taraftaki 5 bar (`right_bars=5`) oluştuktan sonra doğrulanır. Sinyal geçmişe boyanmaz (`pivot_time != signal_time`), sadece gerçek zamanlı kesinleşme anında üretilir.
* **Katı Aşırı Öğrenme (Overfitting) Koruması**: Arındırılmış (Purged) zaman serisi bölmesi (60% Train, 20% Val, 20% OOS) ve 5 barlık ambargo (Embargo) penceresi.
* **Otomatik Strateji Keşfi & Turnuvası**: R10 ailesinden 10 farklı varyant (R10-V1 .. R10-V10) otomatik olarak üretilir, In-Sample ve Out-of-Sample verilerde yarıştırılır ve **Robustness Score (0–100)** ile sıralanır.
* **Stres Testleri**: 2x ve 3x komisyon testi (`FEE_FRAGILE`), 20 bps slippage testi (`SLIPPAGE_FRAGILE`), 5,000 simülasyonlu Monte Carlo çekilişi ve parametre platosu kararlılık analizi.
* **Promotion Gate (Canlıya Terfi Kapısı)**: Robustness Skoru $\ge 70$, OOS Kâr Faktörü $\ge 1.10$, pozitif expectancy ve 0 lookahead ihlali gerektirir.
* **Paper Trading & Yürütüm Sapması**: Backtest beklentileri ile canlı simülasyon arasındaki kayma ve kâr sapmasını (`BACKTEST_LIVE_DEVIATION`) anlık takip eder.

---

## 🏗️ Mimari Pipeline

```text
                  BINANCE REAL-TIME MARKET DATA
                                │
                                ▼
               CAUSAL FEATURE & PIVOT ENGINE
         (Strict T+5 Delay, Zero Future Knowledge)
                                │
                                ▼
              R10 RSI DIVERGENCE SIGNAL GENERATOR
     (Regular Bullish / Bearish, Quality & Signal Score)
                                │
                                ▼
             STRATEGY DISCOVERY & TOURNAMENT ENGINE
         (10 R10 Variants, VectorBT Vectorized Engine)
                                │
                                ▼
                  OVERFITTING PROTECTION ENGINE
      ┌─────────────────────────┴─────────────────────────┐
      ▼                                                   ▼
PURGED TIME-SERIES OOS                            STRESS TESTING
• 60% In-Sample / 20% OOS                         • Fee Stress (1x, 2x, 3x)
• 5-bar Boundary Embargo                          • Slippage Stress (5..50 bps)
• Walk-Forward Stability                          • Monte Carlo (5,000 runs)
• Parameter Plateau Analysis                      • Cross-Coin & Regime Tests
      │                                                   │
      └─────────────────────────┬─────────────────────────┘
                                ▼
                     STRATEGY PROMOTION GATE
                    (Robustness Score >= 70)
                                │
                                ▼
                 REAL-TIME PAPER VALIDATION ENGINE
              (Execution Efficiency & Deviation Tracking)
```

---

## 🚀 Tek Komutla Yerel Başlatma

Windows ortamında tek tıkla sistemi başlatabilir ve doğrudan Control Center ekranına ulaşabilirsiniz:

```bash
# Windows Batch:
start.bat

# veya PowerShell:
.\start.ps1

# veya Make:
make dev
```

Dashboard Linkleri:
* **Local Control Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
* **OpenAPI Swagger Belgeleri**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Prometheus Metrikleri**: [http://localhost:8000/metrics](http://localhost:8000/metrics)

---

## 🎮 V6 Dashboard & API Modülleri

1. **R10 Divergences Tab**:
   * Gerçek zamanlı doğrulanmış swing dip ve tepeler, RSI uyumsuzluk oranları, kalite skoru ve kesinleşmiş sinyaller.
2. **Strategy Discovery & Tournament Tab**:
   * `RUN STRATEGY TOURNAMENT` butonu ile 10 R10 varyantını yarıştırma, Robustness ve Overfit Skorları, OOS sonuçları ve `Promote to Paper` eylemi.
3. **Backtest vs Paper Validation**:
   * Gerçek kağıt işlemler ile teorik model arasındaki yürütüm verimliliği (`ALIGNED` / `DEVIATION_WARNING`).

---

## 🧪 Test Durumu

Tüm test paketleri (Unit, Anti-Lookahead, Strategy, Risk, Backtest, Discovery, API) %100 başarıyla geçmiştir:

```text
============================== 49 passed in 38.76s ==============================
```

* **Pytest**: **49 / 49 PASS (%100 Başarı)**
* **Ruff Linter & Formatter**: **0 Hata**
* **MyPy Tip Denetimi**: **0 Hata (159 kaynak dosya)**

---

## 📚 Dokümantasyonlar

* [R10 RSI Divergence Stratejisi](docs/r10-divergence.md)
* [Strategy Discovery & Turnuva Motoru](docs/strategy-discovery.md)
* [Geleceğe Bakış Engelleme (Anti-Lookahead)](docs/anti-lookahead.md)
* [Strateji Terfi Kapısı & Doğrulama](docs/validation.md)
* [Local Control Center Mimarisi](docs/CONTROL_CENTER.md)
* [GitHub Kaynak Matrisi](docs/SOURCE_AUDIT.md)
