# KRIPTO AGENT — 5 Maddelik Düzeltme Paketi + Kârlılığa Giden Yol

## 0. Önce Dürüst Bir Çerçeve

Aşağıdaki 5 madde **koddaki gerçek hataları** düzeltiyor (tutarsızlık, sahte veri, zayıf güvenlik, gereksiz maliyet). Bunları düzeltmek sistemi **daha doğru, daha güvenli ve daha az maliyetli** yapar — ama **hiçbir kod düzeltmesi bir stratejiyi garantili kârlı yapamaz**. Ben finansal danışman değilim ve kimse (bu işi yapan başka bir AI dahi) günlük/haftalık kâr garantisi veremez. Arkadaşınızın gönderdiği bağımsız değerlendirme de aynı noktaya varmış: sistem teknik olarak sağlam, **stratejinin kendisi henüz net kârlı değil**.

Bu paketin amacı: (1) koddaki gerçek kusurları düzeltmek, (2) maliyet direncini artıracak somut bir mekanizma eklemek, (3) "kârlı mı değil mi" sorusuna **doğru şekilde** cevap bulmanız için gereken altyapıyı sağlamlaştırmak. Kâr etmeyi "garanti etmek" değil, **kârlı olup olmadığını dürüstçe ölçebilecek** bir sisteme kavuşturmak.

---

## 1. Nasıl Uygulanır

Pakette 2 dosya var:

- **`01_kod_duzeltmeleri.patch`** — 10 dosyadaki tüm kod değişikliklerini içeren standart git patch. Kendi bilgisayarınızda proje klasöründe:
  ```bash
  cd "KRIPTO AGENT"
  git apply 01_kod_duzeltmeleri.patch
  # veya (daha toleranslı):
  git apply --reject --whitespace=fix 01_kod_duzeltmeleri.patch
  ```
  Patch uygulanmazsa (satır kayması vb.), aşağıdaki madde madde açıklamaları kullanarak değişiklikleri elle de yapabilirsiniz — her madde altında hangi dosyada, ne değişti, neden değişti tam olarak anlatılıyor.

- **`.env` dosyanıza şu 1 satırı elle ekleyin** (patch'e dahil değil, çünkü `.env` git'e commit edilmiyor):
  ```
  EXECUTION_STYLE=TAKER
  ```
  (Madde 5'te ne işe yaradığı anlatılıyor.)

Uyguladıktan sonra mutlaka çalıştırın:
```bash
python -m pytest tests/ -v
ruff check .
python -m py_compile $(git ls-files '*.py')
```

---

## 2. Madde #1 — Commit Edilmemiş Değişiklikleri Kaydedin

`git status` çıktınızda şu an commit edilmemiş şu dosyalar var:
- `services/strategy_engine/strategies/regime_gated_pullback.py` (yeni strateji)
- `tests/security/test_critical_15_invariants.py` (15 kritik güvenlik testi)
- `services/strategy_engine/strategy_manager.py`, `services/strategy_engine/strategies/r10_rsi_divergence.py` (R10 skor formülü güncellemesi)
- `services/market_data/dynamic_screener.py`, `services/risk_engine/btc_regime_shield.py`, birkaç test dosyası (bunlar sadece satır sonu / whitespace formatlaması — gerçek mantık değişikliği yok, doğruladım)

**Neden önemli:** Bu iş zaten yapılmış ve testten geçmiş (15/15 invariant PASSED) ama repo'ya işlenmemiş halde duruyor — bir sonraki `git checkout`/formatlaştırma bunu kolayca kaybettirebilir.

**Yapılacak:**
```bash
git add services/strategy_engine/strategies/regime_gated_pullback.py
git add tests/security/test_critical_15_invariants.py
git add services/strategy_engine/strategy_manager.py
git add services/strategy_engine/strategies/r10_rsi_divergence.py
git commit -m "feat: regime-gated pullback strategy + 15 critical security invariants"

# Bu paketteki patch'i uyguladıktan sonra:
git add -A
git commit -m "fix: R10 tek-kaynak parametre birligi, agents_hub gercek veri, auth guvenligi, maker mode"
```

---

## 3. Madde #2 — R10 Parametre Tutarsızlığı (3 Farklı Davranış → Tek Davranış)

**Sorun neydi:** `autonomous_runner.py` (canlı bot) `.env`'den okuyordu, ama `market.py`, `signals.py`, `strategy_manager.py` hâlâ `R10RSIDivergenceStrategy()`'yi sınıf varsayılanlarıyla çağırıyordu. `.env`'i değiştirseniz bile bu 3 yer sessizce eski değerleri kullanmaya devam ediyordu.

**Ne yaptım:**
- `services/strategy_engine/strategies/r10_rsi_divergence.py` dosyasının sonuna **`create_r10_strategy_from_settings()`** adında tek bir fabrika fonksiyonu ekledim. Bu fonksiyon `.env`/`settings`'ten `R10_PIVOT_LEFT`, `R10_PIVOT_RIGHT`, `MIN_SIGNAL_SCORE`, `R10_TIMEFRAME` değerlerini okuyup stratejiyi kuruyor.
- `market.py`, `signals.py`, `strategy_manager.py`, `autonomous_runner.py` — dördü de artık `R10RSIDivergenceStrategy()` yerine bu fabrikayı çağırıyor.
- **Bonus düzeltme (yeni bulgu):** `autonomous_runner.py` içindeki canlı tarama döngüsü, `R10_TIMEFRAME` ayarına rağmen mum verisini **hep sabit "15m" olarak çekiyordu**. Yani `.env`'de `R10_TIMEFRAME=1h` yazsanız bile bot hâlâ 15 dakikalık veriyle çalışıyordu — strateji nesnesi 1h olarak kurulmuş olsa da, ona hiç 1h veri gitmiyordu. Bunu da düzelttim: artık gerçekten `self.strategy.timeframe`'i kullanıyor. **Bu, Madde #5'teki "1H/4H'e geçmeyi deneyin" önerisinin fiilen çalışabilmesi için zorunluydu** — düzeltilmeden önce bu öneriyi denemek mümkün değildi.
- "Stratejiyi kapat" komutu artık gerçekten işe yarıyor: `bind_command_bus()` artık canlı botun strateji nesnesini, dashboard'un/komutun değiştirdiği **aynı nesneye** bağlıyor (isimden eşleştirerek). Ayrıca `step_cycle()`'a açık bir `.enabled` kontrolü eklendi — strateji kapatılırsa yeni sinyal taraması durur, açık pozisyonlar izlenmeye devam eder.

**Etkilenen dosyalar:** `r10_rsi_divergence.py`, `market.py`, `signals.py`, `strategy_manager.py`, `autonomous_runner.py`

---

## 4. Madde #3 — `agents_hub.py` Sahte Veri Sorunu

**Sorun neydi:** `/api/v1/agents/debate` endpoint'i, sorulan sembol ne olursa olsun `current_price=0.0`, `rsi=50.0`, `ema_20=0.0`, `ema_50=0.0` gibi sabit değerler kullanıyordu. Bu, projenin kendi "Sıfır Sahte Veri" ilkesinin doğrudan ihlaliydi.

**Ne yaptım:**
- Endpoint artık `market_data_service.get_live_ticker()` ile gerçek fiyatı, `get_historical_candles()` ile gerçek 15m mumları, `get_orderbook()` ile gerçek spread'i çekiyor.
- `FeatureEngine.get_latest_feature_vector()` ile gerçek RSI/EMA20/EMA50/realized_vol hesaplanıyor (canlı bot ile aynı motor).
- Gerçek veri çekilemezse (bağlantı sorunu vb.) yanıt artık `"data_quality": "UNAVAILABLE"` etiketiyle şeffaf şekilde işaretleniyor — sahte sayı üretmiyor, ama debate motorunun (rule-based fallback'ının) sayısal formatlamada çökmemesi için o durumda nötr varsayılanlar (rsi=50, ema=0) kullanıyor, **ve bunu `data_quality` alanında açıkça belirtiyor** — önceki haliyle arada hiçbir fark yoktu, şimdi en azından "bu gerçek mi sahte mi" sorusuna cevap var.

**Etkilenen dosya:** `apps/api/app/api/routers/agents_hub.py`

---

## 5. Madde #4 — Zayıf Varsayılan Auth Anahtarları

**Sorun neydi:** `API_AUTH_SECRET="kripto-agent-secret-token"` ve `API_ADMIN_KEY="kripto-agent-admin-key"` açık kaynak repoda düz metin olarak duruyordu. `API_KEY_AUTH_ENABLED=True` yapılıp bu değerler değiştirilmezse, herkese açık bu sabit anahtarlarla auth trivial şekilde bypass edilebilirdi. Karşılaştırma da `==` ile yapılıyordu (sabit-zamanlı değil).

**Ne yaptım:**
- `shared/config/config.py` → `get_settings()` içine bir **başlangıç güvenlik kilidi** ekledim: `API_KEY_AUTH_ENABLED=True` iken `API_AUTH_SECRET`/`API_ADMIN_KEY` hâlâ bilinen zayıf varsayılan değerlerse, uygulama **başlamayı reddediyor** (`RuntimeError`) ve size gerçek bir değer üretme komutunu gösteriyor.
- `apps/api/app/middleware/auth.py` → token/API-key karşılaştırmaları artık `secrets.compare_digest()` ile sabit-zamanlı yapılıyor (timing side-channel riskini kapatır).

**Sizin yapmanız gereken (kod dışı):** Auth'u gerçekten production'da açacaksanız, `.env`'e şunu ekleyin:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
çıktısını `API_AUTH_SECRET` ve ayrı bir çalıştırmadan gelen başka bir çıktıyı `API_ADMIN_KEY` olarak `.env`'e yazın.

**Etkilenen dosyalar:** `shared/config/config.py`, `apps/api/app/middleware/auth.py`

---

## 6. Madde #5 — Komisyon/Slippage Direnci (MAKER Modu)

**Sorun neydi:** Son doğrulama raporunuzda hiçbir strateji, 15 dakikalık spot periyotta %0.30 gidiş-dönüş maliyeti (taker %0.10×2 + slippage 5bps×2) düşüldükten sonra net pozitif kalamıyordu. R10'un brüt sinyal kalitesi pozitifti (OOS brüt PF 2.03) ama maliyet bunu eritiyordu.

**Ne yaptım (somut, test edilebilir bir mekanizma):**
- `services/execution/paper_execution.py` → `execute_market_order()` artık bir `execution_style` parametresi alıyor (`"TAKER"` varsayılan, `"MAKER"` opsiyonel). `MAKER` modunda:
  - İşlem, gerçek fiyattan (slippage'sız) dolmuş kabul edilir — bekleyen bir limit/post-only emrin dolması simüle edilir.
  - Taker fee yerine `settings.MAKER_FEE` kullanılır.
- `shared/config/config.py` → `EXECUTION_STYLE: str = "TAKER"` ayarı eklendi; `.env`'de `MAKER` yaparak devreye alabilirsiniz.
- `services/execution/order_manager.py` → bu ayarı otomatik olarak execution engine'e iletiyor.

**⚠️ Dürüst uyarı (bunu görmezden gelmeyin):**
1. **Binance spot'ta VIP0/BNB indirimsiz seviyede maker ve taker ücreti aynıdır (%0.10)** — yani MAKER modu tek başına ücreti düşürmüyor, sadece **slippage'ı** (round-trip maliyetin ~1/3'ü) ortadan kaldırıyor. Gerçek bir ücret avantajınız varsa (BNB indirimi, yüksek VIP seviyesi), `.env`'deki `MAKER_FEE` değerini **kendi gerçek Binance ücret tarifenize göre** güncelleyin — ben rastgele düşük bir sayı yazmadım, mevcut değeri (`0.001`) koruyarak dürüst kaldım.
2. **Bu bir simülasyon.** Gerçek bir limit emir her zaman dolmaz — fiyat sizi es geçebilir. MAKER modundaki backtest/paper sonuçları **iyimser bir üst sınır** olarak okunmalı, gerçekleşmiş dolum oranı verisiyle doğrulanana kadar.
3. **Bilinçli olarak çıkışlara (stop-loss, trailing lock) MAKER modu uygulamadım.** Bir stop-loss'un "limit emri gibi dolacağını" varsaymak riski gizler — o yüzden `close_position()` hâlâ her zaman taker/piyasa emri olarak modelleniyor. Bu güvenlik açısından doğru bir tercih; değiştirmenizi önermem.
4. Timeframe'i 1h/4h'e çekmek (`.env`'de `R10_TIMEFRAME=1h`), sabit maliyeti daha büyük bir hareketin yüzdesi haline getirdiği için maliyet direncini artırabilir — Madde #2'deki timeframe hatası düzeltildiğinde bu artık gerçekten test edilebilir hale geldi.

**Etkilenen dosyalar:** `paper_execution.py`, `order_manager.py`, `config.py`, `.env` (elle eklenecek satır)

---

## 7. Uygulamadan Sonra: Kârlılığı Doğru Şekilde Test Etme Yol Haritası

Kod düzeltmeleri bittikten sonra, "kâr eder mi" sorusuna dürüst cevap için önerilen sıra:

1. **Örneklem büyüklüğünü artırın.** Son testte stratejiler 12-56 işlem üzerinden değerlendirilmişti; istatistiksel olarak anlamlı bir karar için en az 30-50 işlem (tercihen çok daha fazla, farklı piyasa rejimlerini kapsayan) gerekir. `backtesting/walk_forward.py` ile daha uzun geçmiş veri (aylar, farklı boğa/ayı dönemleri) üzerinde çalıştırın.
2. **MAKER modunu ve 1h/4h timeframe'i ayrı ayrı ve birlikte backtest edin**, `strategy_discovery/discovery_engine.py` üzerinden — artık ikisi de gerçekten çalışıyor.
3. **NO-GO kapısını koruyun.** `LIVE_TRADING=false` ve `LIVE_TRADING_ARMED=false` kalsın; hiçbir strateji, maliyet sonrası pozitif expectancy + yeterli örneklem + kabul edilebilir max drawdown olmadan bu kapıyı geçmesin. Bu kapıyı "acele ediyoruz" diye atlamak, sistemin kendi güvenlik mimarisinin amacını boşa çıkarır.
4. **Gerçek para riski konusunda net kural:** Bu sisteme asla mainnet (gerçek) Binance API anahtarı girmeyin, özellikle para çekme (withdraw) izinli bir anahtarı hiç girmeyin. Denemek isterseniz sadece testnet anahtarı kullanın. `.env`'de `EXCHANGE_TESTNET=false` olduğunu unutmayın — bu, sistemin gerçek Binance'e baktığı (sadece veri için) anlamına geliyor, emir gönderme kilitleri ayrı ama yine de dikkatli olun.

Bu adımların hiçbiri "günlük %1" gibi sabit bir hedefi garanti etmez ve garanti edemez — amaç, pozitif beklenti + dayanıklılık + düşük drawdown + maliyet direnci + örnek-dışı (OOS) istikrarı birlikte sağlayan bir kurulum bulmak, sonra onu **küçük gerçek sermaye ile** (testnet sonrası, isterseniz) kademeli test etmek.
