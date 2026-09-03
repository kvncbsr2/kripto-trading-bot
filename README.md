# ⚡ KRIPTO AGENT — Master V5: Local Trading Control Center

> **Production-Grade Autonomous Crypto Trading Agent & Local Mission Control**  
> *Binance Real-Time Data + Command Bus + VectorBT Backtesting + Prometheus Monitoring*

---

## 🎯 Ana Hedef & Felsefe

**KRIPTO AGENT V5**, yalnızca pasif bir izleme paneli değil; yerel bilgisayarınızda çalışan, borsa bağlantısından risk yönetimine, tarayıcıdan strateji yürütümüne kadar tüm alt motorları doğrudan yöneten bir **Local Trading Control Center**'dır.

* **Sıfır Sahte / Sıfır Mock Prensibi**: Arayüzdeki her buton ve kontrol, arka plandaki gerçek bir servisi, Command Bus eylemini ve borsa/motor fonksiyonunu çalıştırır.
* **Sanal Başlangıç Sermayesi**: \$5,000.00 USD
* **Piyasa Verisi**: Binance Spot (Gerçek Zamanlı Multiplexed WebSocket + REST Fallback)
* **İşlem Modeli**: Paper Trading (%0.1 komisyon, 5 bps slippage, gerçek Ask/Bid spread)
* **İşlem Başına Risk**: %0.5 (yaklaşık \$25.00)
* **Günlük Maksimum Zarar**: \$50.00 (`DAILY_RISK_LOCK`)
* **Canlı Borsa Koruması**: Kod düzeyinde sert güvenlik kilidi (`LIVE_TRADING=false`). Canlı emir motoru `BinanceLiveExecutionEngine` doğrudan kilitlidir.

---

## 🏗️ Mimari Şema & Command Bus Akışı

```text
               LOCAL OPERATOR ACTION
                         │
                         ▼
        LOCAL TRADING CONTROL DASHBOARD
         (http://localhost:8000/dashboard)
                         │
                         ▼
               FASTAPI COMMAND BUS
  (/api/agent/*, /api/risk/*, /api/strategies/*)
                         │
                         ▼
             READINESS GATE VALIDATION
  (Safety Lock, Risk Limits, Capital, DB Health)
                         │
      ┌──────────────────┴──────────────────┐
      ▼                                     ▼
TRADING ENGINES                      PERSISTENCE & AUDIT
• Binance Connector                  • Immutable Audit Trail
• Market Scanner                     • PostgreSQL / TimescaleDB
• Feature & Regime Engine            • Redis Real-Time State
• Strategy Manager                   • Prometheus /metrics
• Risk Engine & Circuit Breaker      • Grafana Dashboards
• Paper Broker & Fills
• VectorBT & Monte Carlo
```

---

## 🚀 Tek Komutla Yerel Başlatma

Windows ortamında tek tıklamayla sistemi başlatabilir ve doğrudan Control Center ekranına ulaşabilirsiniz:

```bash
# Windows Batch Dosyası:
start.bat

# veya PowerShell:
.\start.ps1

# veya Makefile ile:
make dev
```

Dashboard otomatik olarak tarayıcınızda açılır:
* **Local Control Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
* **OpenAPI Swagger Belgeleri**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Prometheus Metrikleri**: [http://localhost:8000/metrics](http://localhost:8000/metrics)

---

## 🎮 Local Control Center Yetenekleri

1. **Global Control Bar**:
   * Sistem durumu (`TRADING`, `PAUSED`, `RISK_LOCK`, `STOPPED`), Binance bağlantısı ve Pre-flight Readiness Gate durumunu canlı görüntüler.
   * `START AGENT`, `PAUSE`, `RESUME`, `STOP` ve `EMERGENCY STOP` eylemlerini gerçek backend fonksiyonlarıyla çalıştırır.
2. **Emergency Stop (Acil Durum Devre Kesici)**:
   * Tek tıkla tüm açık limit emirlerini iptal eder, yeni emir üretimini dondurur ve sistemi `RISK_LOCK` durumuna kilitler.
3. **Canlı Binance Tarayıcısı (Live Scanner)**:
   * `SCAN NOW` butonu ile Binance üzerindeki likit USDT paritelerini (hacim >\$10M, spread <15 bps) tarar ve Fırsat Skorunu (0–100) anında günceller.
4. **Strateji Yönetimi (Strategies)**:
   * `Trend Following`, `Mean Reversion` ve `RSI Divergence` stratejilerini çalışma anında devreye alıp devreden çıkarma (`ENABLE / DISABLE`).
5. **Pozisyon Kapatma (Close Position)**:
   * Açık paper pozisyonlarını simüle edilmiş piyasa fiyatından anında kapatır ve gerçekleşen kâr/zararı hesaplar.
6. **Dinamik Risk Merkezi (Risk Center)**:
   * İşlem başı risk, günlük zarar limiti, maksimum pozisyon adedi ve ATR çarpanını doğrudan arayüzden günceller.
7. **VectorBT Backtest & Monte Carlo**:
   * İstenen parite ve strateji için vektörize portföy simülasyonu ve 1.000 tekrarlı Monte Carlo çekilişi gerçekleştirir.
8. **Denetim İzi (Audit Trail)**:
   * Kullanıcı tarafından gerçekleştirilen her işlemi zaman damgası, parametreler ve başarı durumuyla kayıt altına alır.

---

## 🧪 Kalite ve Test Durumu

Tüm test paketleri (Unit, Strategy, Risk, Failure, Backtest, E2E, Control Center) başarıyla geçmiştir:

```text
============================== 40 passed in 27.76s ==============================
```

* **Pytest**: **40 / 40 PASS (%100 Başarı)**
* **Ruff Linter & Formatter**: **0 Hata**
* **MyPy Tip Denetimi**: **0 Hata (151 kaynak dosya)**

---

## 📚 Dokümantasyonlar

* [Local Control Center Mimarisi](docs/CONTROL_CENTER.md)
* [GitHub Kaynak ve Bağımlılık Matrisi](docs/SOURCE_AUDIT.md)
* [Sistem Mimarisi](docs/ARCHITECTURE.md)
* [Binance Entegrasyon Mimarisi](docs/BINANCE.md)
* [Piyasa Verisi & Tarayıcı (Scanner)](docs/MARKET_DATA.md)
* [Strateji Motoru](docs/STRATEGIES.md)
* [RSI Uyumsuzluk Motoru](docs/RSI_DIVERGENCE.md)
* [Risk Modeli & Devre Kesici](docs/RISK_MODEL.md)
* [Paper Broker & Maliyet Simülasyonu](docs/PAPER_TRADING.md)
* [Walk-Forward Doğrulama](docs/WALK_FORWARD.md)
* [Monte Carlo Simülasyonu](docs/MONTE_CARLO.md)
* [7 Günlük Doğrulama Deneyi](docs/7_DAY_EXPERIMENT.md)
* [API Dokümantasyonu](docs/API.md)
* [Güvenlik Mimarisi](docs/SECURITY.md)
* [Operasyon El Kitabı](docs/OPERATIONS.md)
