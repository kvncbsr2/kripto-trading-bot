# 5.000 dolar sermaye / günlük 50 dolar hedef denetimi

Tarih: 10 Eylül 2026. Kullanıcı isteği, her gün yeni 5.000 dolar yatırılması değil,
5.000 dolarlık portföy üzerinden günlük net 50 dolar hedefi olarak yorumlandı.
50 / 5.000 = günlük %1. Bu getiri kanıtlanmış veya garanti edilmiş değildir.

## Uygulanan değişiklikler

- Varsayılanlar, yerel `.env` ve Seviye 1 profili 50 dolar hedefe hizalandı.
  Kaydedilmiş başlangıç profili Seviye 10'dan Seviye 1'e alındı.
- İşlem başına planlanan risk sermayenin %0,5'i; başlangıçta 25 dolar.
  Günlük zarar eşiği 50 dolar; en çok iki açık pozisyon ve günde beş giriş.
  Tek pozisyon nominal büyüklüğü için yerel üst sınır sermayenin %20'si.
- HARD hedef artık gerçekleşmiş net günlük kârı kullanıyor. 50 dolar eşiğinde
  yeni girişler reddediliyor; açık işlemler otomatik kapatılmıyor.
- Pozisyon boyutlandırmasına gidiş-dönüş komisyonu, olumsuz fiyat kayması ve
  kaymış fiyat üzerinden komisyon dahil edildi. Miktar aşağı yuvarlanıyor.
- Brüt getiri/risk filtresine ek olarak maliyet sonrası en az 1,5 getiri/risk
  aranıyor. Bu filtre istatistiksel pozitif beklenti kanıtı değildir.
- Gün içi kayıp ve açık pozisyonların mevcut fiyattan stopa kadar kalan riski,
  yeni işleme ayrılabilecek günlük bütçeden düşülüyor.
- Günlük işlem sayısı parametresinin CircuitBreaker'a aktarılmaması düzeltildi.
- Geçersiz, negatif veya sonlu olmayan maliyetlerle pozisyon hesaplanması engellendi.

Mevcut ücret varsayımı her yönde %0,10 komisyon ve 5 baz puan kaymadır.
1.000 dolarlık giriş-çıkış için yaklaşık 3 dolar maliyet oluşur; kesin tutar
çıkış fiyatına bağlıdır. Beş böyle işlem yaklaşık 15 dolar maliyet taşır.
Günlük net 50 dolar için bu örnekte yaklaşık 65 dolar brüt kazanç gerekir.
Bu hesap kazanç olasılığı veya beklenen getiri tahmini değildir.

## Mevcut strateji kanıtı

Depodaki `backtesting/reports/strategy_discovery_report.json` raporunun zaman
damgası 6 Eylül 2026'dır. Aşağıdaki rakamlar eski rapordan okunmuştur;
bu değişikliklerden sonra elde edilmiş yeni backtest sonuçları değildir.

| Strateji | İşlem | Tüm dönem net PnL | Örneklem dışı net PnL |
| --- | ---: | ---: | ---: |
| Trend following | 57 | -92,63 dolar | -91,96 dolar |
| Mean reversion | 35 | +30,66 dolar | -19,19 dolar |
| RSI divergence | 52 | -242,70 dolar | -49,35 dolar |
| R10 RSI divergence | 34 | +22,73 dolar | +20,21 dolar |
| Pullback 2R | 26 | -20,15 dolar | +41,96 dolar |

Bu kayıt günlük 50 dolar sonucunu kanıtlamıyor. Raporda günlük işlem dökümü ve
veri parmak izi bulunmadığından günlük hedefe erişme oranı güvenilir biçimde
hesaplanamıyor. Güncel cache dosyalarını eski raporun kesin veri seti saymak doğru olmaz.

Araştırma betiğindeki R10, 3/3 pivot ve ATR mesafesi kullanıyor. Uygulamadaki
Seviye 1 ise 5/2 pivot, farklı kalite puanı ve pivot tabanlı stop kullanıyor.
Araştırma simülatörünün %25 pozisyon tavanı da yerel %20 ayarından farklı.
Dolayısıyla eski rapor uygulamanın güncel işlemlerinin doğrulaması değildir.
Kanıt olmadan daha agresif strateji veya kaldıraç seçilmedi.

## Sınırlar ve kalan bulgular

- Gerçek para işlemi açılmadı. Yerel yapılandırma paper modunda kaldı.
- Yerel API güncellenen kodla yeniden başlatıldı. Seviye 1, R10, 50 dolar HARD
  hedef, 50 dolar zarar limiti ve %0,5 işlem riski çalışan API'de doğrulandı.
  Otonom paper motoru tekrar başlatıldı; üç mevcut paper pozisyon korundu.
- HARD hedef bir giriş filtresidir; açık pozisyonların sonraki zararı nedeniyle
  gün sonu kârı 50 doların altına düşebilir. Günlük zarar eşiği de fiyat boşluğu,
  likidite ve gerçekleşme fiyatı nedeniyle kesin kayıp garantisi değildir.
- Gün sınırı mevcut uygulamada UTC'dir; İstanbul gece yarısı değildir.
- Kısmi kapanışın zamanı pozisyon kaydına eklendi. Önceki günün kısmi kapanış
  kârı artık bugünkü gerçekleşmiş kâr ve 50 dolar hedef hesabına katılmıyor.
- Varsayılan API kimlik doğrulama değerleri kaynak kodda sabit. Canlı ortama
  geçmeden gerçek yerel gizli değerler ve kimlik doğrulama ayrı denetlenmeli.
- CLI bulunamadığından TestSprite uçtan uca testi çalıştırılamadı. Bu sınır,
  projenin `.agents/skills/testsprite-verify/SKILL.md` yönergesindeki preflight
  koşulundan kaynaklanıyor; otomatik kurulum veya yeni proje kurulumu yapılmadı.

Bu çalışma hedefe yönelik risk ve maliyet düzeltmesidir. Günlük 50 dolar
kazandırdığı doğrulanmış bir yatırım sistemi teslim edildiği anlamına gelmez.
