# Telegram Bildirim Servisi & Bitcoin Trend Kalkanı (BTC Regime Filter) Uygulama Planı

Bu plan, kullanıcının talebi doğrultusunda sisteme iki kritik ve güvenli özellik kazandırmak amacıyla hazırlanmıştır:
1. **Telegram Anlık Bildirim Servisi:** Botun açtığı/kapattığı işlemleri, kâr/zarar durumunu ve günlük özetleri kullanıcının cep telefonuna anlık Telegram mesajı olarak iletir.
2. **Bitcoin Trend Kalkanı (BTC Regime Filter):** Kripto piyasasında Bitcoin sert düşerken altcoinlerin de peşinden çakılmasını engellemek için, BTC'de ani panik satışı varken altcoin alımlarını geçici olarak veto eden defansif koruma filtresi.

---

## Kullanıcı Onayı Gereken Konular (User Review Required)

> [!IMPORTANT]
> **Telegram Bot Token & Chat ID:** Telegram bildirimlerinin gerçek telefonunuza gelmesi için bir Telegram botu oluşturup token ve Chat ID bilgisi girmeniz gerekecektir. Bilgiler girilmediğinde sistem hata **vermez**, arka planda sessizce "Mock/Simülasyon" modunda log kaydetmeye devam eder.

> [!TIP]
> **BTC Trend Kalkanı Eşiği:** Varsayılan olarak Bitcoin son 15 dakikalık veya 1 saatlik mumda **-%1.5'ten fazla** ani düşüş yaşarsa altcoin (`ETH`, `SOL`, `BNB`, `XRP`, `DOGE`) alımları geçici olarak durdurulur. BTC sakinleştiğinde otomatik olarak alımlara devam edilir.

---

## Önerilen Değişiklikler (Proposed Changes)

### 1. Konfigürasyon (`shared/config/config.py` & `.env`)
- `TELEGRAM_ENABLED: bool = False`
- `TELEGRAM_BOT_TOKEN: Optional[str] = None`
- `TELEGRAM_CHAT_ID: Optional[str] = None`
- `BTC_REGIME_FILTER_ENABLED: bool = True`
- `BTC_DUMP_THRESHOLD_PCT: float = -1.5`

### 2. Telegram Bildirim Servisi (`services/notification/telegram_service.py`) [YENİ]
- Asenkron non-blocking mesaj iletimi (`aiohttp`).
- Token veya Chat ID eksikse ana sistemi hiçbir şekilde bloklamayan, hata fırlatmayan dayanıklı yapı (`graceful fallback`).
- Formatlanmış mesaj şablonları:
  - `notify_bot_started()`
  - `notify_bot_stopped()`
  - `notify_order_opened(symbol, side, qty, price, sl, tp)`
  - `notify_order_closed(symbol, reason, exit_price, pnl, total_equity)`
  - `notify_circuit_breaker(reason)`
  - `notify_daily_summary(equity, daily_pnl, win_count, loss_count)`

### 3. Bitcoin Trend Kalkanı (`services/risk_engine/btc_regime_shield.py`) [YENİ]
- Her tarama döngüsünde Binance'ten çekilen gerçek `BTC/USDT` son mumlarını inceler.
- BTC'nin anlık getirisini ($Ret_{15m}$ ve $Ret_{1h}$) ve volatilite durumunu hesaplar.
- Eğer BTC panik satışındaysa `BTC_DUMP_ACTIVE = True` durumuna geçer ve RiskEngine'e veto iletir.
- Sıfır sahte veri (Zero Fake Data) invariantı korunur.

### 4. Entegrasyon (`services/autonomous_runner.py` & `apps/api/app/api/routers/system.py`)
- `autonomous_runner.py` içinde:
  - Alım veya satım tetiklendiğinde `telegram_service` bildirimleri çağrılır.
  - Altcoin taranırken önce `btc_regime_shield` kontrol edilir; eğer kalkan aktifse terminale:
    `"🛡️ BTC TREND KALKANI: Bitcoin sert düşüşte (-%X.X). Altcoin alımları geçici olarak duraklatıldı."` logu yazılır ve işlem pas geçilir.
- `apps/api/app/api/routers/system.py` içine Telegram test uç noktası (`POST /api/v1/system/telegram/test`) eklenir.

---

## Doğrulama ve Test Planı (Verification Plan)

### Otomatik Testler
- `tests/unit/test_telegram_service.py`: Token olmadan sessizce pas geçme, token varken doğru endpoint'e formatlı payload gönderme testleri.
- `tests/unit/test_btc_regime_shield.py`: BTC normal seyrederken altcoin alımlarına onay verme; BTC sert düşerken (`<-1.5%`) altcoin alımını veto etme testleri.
- Mevcut `103/103` test paketinin kırılmadığının teyidi (`pytest tests/`).

### Manuel Doğrulama
- Canlı terminalde BTC Trend Kalkanının durum loglarının izlenmesi.
- API üzerinden Telegram test mesajı gönderilip yanıtın teyit edilmesi.
