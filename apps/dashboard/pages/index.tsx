import React, { useEffect, useState, useCallback } from 'react';
import Head from 'next/head';
import {
  Play,
  Pause,
  AlertOctagon,
  ShieldCheck,
  TrendingUp,
  TrendingDown,
  RefreshCw,
  Zap,
  CheckCircle,
  Clock,
  ArrowUpRight,
  ArrowDownRight,
  DollarSign,
  Wallet,
  Activity,
  Sliders,
  ChevronRight,
  HelpCircle,
  Terminal
} from 'lucide-react';

const API_BASE = 'http://localhost:8000';

const getAuthHeaders = (customHeaders: Record<string, string> = {}) => {
  const key = (typeof window !== 'undefined' && (localStorage.getItem('KRIPTO_API_KEY') || (window as any).__KRIPTO_API_KEY__)) || '';
  const headers: Record<string, string> = { ...customHeaders };
  if (key) {
    headers['X-API-KEY'] = key;
  }
  return headers;
};

export default function UltraSimpleDashboard() {
  // Navigation: 3 simple sections only
  const [activeTab, setActiveTab] = useState<'bot' | 'market' | 'settings'>('bot');

  // Core Data
  const [systemState, setSystemState] = useState<any>(null);
  const [isApiOnline, setIsApiOnline] = useState<boolean>(true);
  const [positions, setPositions] = useState<any[]>([]);
  const [closedOrders, setClosedOrders] = useState<any[]>([]);
  const [tickers, setTickers] = useState<any[]>([]);
  const [r10State, setR10State] = useState<any>(null);
  const [nextAction, setNextAction] = useState<any>(null);

  // Live Activity Logs (Terminal)
  const [liveLogs, setLiveLogs] = useState<any[]>([]);
  const [autoScrollLogs, setAutoScrollLogs] = useState<boolean>(true);
  const logsEndRef = React.useRef<HTMLDivElement>(null);

  // User Actions Feedback
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<{ msg: string; type: 'success' | 'error' } | null>(null);

  // Quick Test Trade state
  const [testSymbol, setTestSymbol] = useState('BTC/USDT');
  const [testAmount, setTestAmount] = useState('100');

  // Backtest simple state
  const [btLoading, setBtLoading] = useState(false);
  const [btResult, setBtResult] = useState<any>(null);

  // -------------------------------------------------------------------------
  // Fetcher
  // -------------------------------------------------------------------------
  const loadData = useCallback(async () => {
    try {
      // 1. Sistem ve Bakiye Durumu
      const sysRes = await fetch(`${API_BASE}/api/v1/system/status`).catch(() => null);
      if (sysRes && sysRes.ok) {
        setSystemState(await sysRes.json());
        setIsApiOnline(true);
      } else {
        setIsApiOnline(false);
      }

      // 2. Açık Pozisyonlar
      const posRes = await fetch(`${API_BASE}/api/v1/positions/active`).catch(() => null);
      if (posRes && posRes.ok) setPositions(await posRes.json());

      // 3. Tamamlanan Kârlı / Zararlı İşlemler
      const histRes = await fetch(`${API_BASE}/api/v1/positions/history`).catch(() => null);
      if (histRes && histRes.ok) {
        const hData = await histRes.json();
        setClosedOrders(hData.positions || []);
      }

      // 4. Canlı Fiyatlar
      const tickRes = await fetch(`${API_BASE}/api/v1/market/live-tickers`).catch(() => null);
      if (tickRes && tickRes.ok) {
        const tData = await tickRes.json();
        const list = Array.isArray(tData) ? tData : (tData.tickers || Object.values(tData));
        setTickers(list);
      }

      // 5. Botun Canlı Sinyal Durumu
      const r10Res = await fetch(`${API_BASE}/api/v1/r10/live-state?symbol=BTC/USDT`).catch(() => null);
      if (r10Res && r10Res.ok) setR10State(await r10Res.json());

      // 6. Sıradaki Öneri
      const nextRes = await fetch(`${API_BASE}/api/v1/system/next-action`).catch(() => null);
      if (nextRes && nextRes.ok) setNextAction(await nextRes.json());
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 2500);
    return () => clearInterval(interval);
  }, [loadData]);

  // -------------------------------------------------------------------------
  // Canlı Logları Çek (1.5 saniyede bir hızlı akış)
  // -------------------------------------------------------------------------
  const fetchLogs = useCallback(async () => {
    try {
      const logsRes = await fetch(`${API_BASE}/api/v1/system/logs?limit=80`).catch(() => null);
      if (logsRes && logsRes.ok) {
        const data = await logsRes.json();
        if (data && Array.isArray(data.logs)) {
          setLiveLogs(data.logs);
        }
      }
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    fetchLogs();
    const logInterval = setInterval(fetchLogs, 1500);
    return () => clearInterval(logInterval);
  }, [fetchLogs]);

  useEffect(() => {
    if (autoScrollLogs && logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [liveLogs, autoScrollLogs]);

  // -------------------------------------------------------------------------
  // Botu Başlat / Durdur
  // -------------------------------------------------------------------------
  const toggleBot = async () => {
    setActionLoading(true);
    setFeedback(null);
    try {
      const isRunning = systemState?.is_autonomous_active;
      const endpoint = isRunning ? `${API_BASE}/api/agent/stop` : `${API_BASE}/api/agent/start?unhalt=true&force=true`;
      const res = await fetch(endpoint, { method: 'POST', headers: getAuthHeaders() });
      const data = await res.json();
      if (res.ok && data.success !== false) {
        setFeedback({
          msg: isRunning ? 'Bot durduruldu.' : 'Bot başlatıldı! Canlı piyasa taranıyor.',
          type: 'success'
        });
      } else {
        setFeedback({ msg: data.detail || data.message || 'İşlem gerçekleştirilemedi.', type: 'error' });
      }
      loadData();
    } catch (e: any) {
      setFeedback({ msg: e.message, type: 'error' });
    } finally {
      setActionLoading(false);
      setTimeout(() => setFeedback(null), 4000);
    }
  };

  // Acil Durdurma
  const handleEmergencyStop = async () => {
    if (!confirm('Tüm işlemleri derhal dondurmak istiyor musunuz?')) return;
    try {
      await fetch(`${API_BASE}/api/v1/risk/emergency-stop`, { method: 'POST', headers: getAuthHeaders() });
      setFeedback({ msg: 'Acil durdurma devrede. Bot kapatıldı.', type: 'success' });
      loadData();
    } catch (e: any) {
      setFeedback({ msg: e.message, type: 'error' });
    }
  };

  // Hızlı Test Alımı
  const handleQuickTrade = async () => {
    setActionLoading(true);
    setFeedback(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/orders/paper/execute`, {
        method: 'POST',
        headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          symbol: testSymbol,
          side: 'BUY',
          amount_usd: parseFloat(testAmount) || 100
        })
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setFeedback({ msg: `Başarılı! ${testAmount}$ değerinde test ${testSymbol} alındı.`, type: 'success' });
        loadData();
      } else {
        setFeedback({ msg: data.detail || data.rejection_reason || 'Alım reddedildi.', type: 'error' });
      }
    } catch (e: any) {
      setFeedback({ msg: e.message, type: 'error' });
    } finally {
      setActionLoading(false);
      setTimeout(() => setFeedback(null), 4000);
    }
  };

  // Pozisyon Kapat
  const closePosition = async (id: string) => {
    try {
      await fetch(`${API_BASE}/api/v1/positions/${id}/close`, { method: 'POST', headers: getAuthHeaders() });
      setFeedback({ msg: 'Pozisyon satıldı ve nakde geçildi.', type: 'success' });
      loadData();
    } catch (e: any) {
      setFeedback({ msg: e.message, type: 'error' });
    }
  };

  // Hızlı Geçmiş Testi (Backtest)
  const runQuickBacktest = async () => {
    setBtLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/backtest/run`, {
        method: 'POST',
        headers: getAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          symbol: 'BTC/USDT',
          timeframe: '15m',
          strategy: 'r10_rsi_divergence',
          initial_capital: Number(systemState?.initial_capital ?? 5000),
          fees: 0.001,
          slippage_bps: 5.0
        })
      });
      const data = await res.json();
      setBtResult(data);
    } catch (e) {
      console.error(e);
    } finally {
      setBtLoading(false);
    }
  };

  // Değişkenler
  const isRunning = Boolean(systemState?.is_autonomous_active);
  const initialCapital = Number(systemState?.initial_capital ?? 5000.0);
  const balance = systemState?.balance ?? initialCapital;
  const equity = systemState?.equity ?? initialCapital;
  const dailyPnl = systemState?.daily_pnl ?? 0.0;
  const totalProfit = equity - initialCapital;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans flex flex-col selection:bg-blue-600 selection:text-white">
      <Head>
        <title>KRIPTO AGENT — Kripto Alım Satım Botu</title>
        <meta name="description" content="Sade ve Güvenilir Kripto Al-Sat Botu" />
      </Head>

      {/* =================================================================== */}
      {/* 1. ÜST BİLGİ & GÜVENLİK BARI                                         */}
      {/* =================================================================== */}
      <header className="bg-slate-900 border-b border-slate-800 px-4 py-3 sticky top-0 z-50">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          {/* Logo & Durum */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-blue-600 flex items-center justify-center font-black text-white text-lg shadow-md shadow-blue-500/20">
              KA
            </div>
            <div>
              <div className="text-base font-bold text-white leading-none">
                KRIPTO AGENT
              </div>
              <div className="text-xs text-slate-400 mt-1 flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'}`}></span>
                <span>{isRunning ? 'Bot Aktif (Al-Sat Yapıyor)' : 'Bot Beklemede (Kapalı)'}</span>
              </div>
            </div>
          </div>

          {/* Test / Deneme Güvencesi */}
          <div className="hidden sm:flex items-center gap-2 bg-blue-950/80 border border-blue-800/80 text-blue-300 px-3.5 py-1.5 rounded-full text-xs font-semibold">
            <ShieldCheck className="w-4 h-4 text-blue-400" />
            <span>DENEME PARASI (PAPER MODU)</span>
            <span className="text-blue-400/70 border-l border-blue-800 pl-2 font-normal">Cebinizden para çıkmaz</span>
          </div>

          {/* Acil Durdurma Butonu */}
          <button
            onClick={handleEmergencyStop}
            className="text-xs font-semibold text-red-400 hover:text-red-300 bg-red-950/60 hover:bg-red-900/60 border border-red-800/60 px-3 py-1.5 rounded-lg transition"
          >
            Acil Durdur
          </button>
        </div>
      </header>

      {/* Bildirim Alanı */}
      {feedback && (
        <div className={`py-2 px-4 text-center text-xs font-bold border-b ${
          feedback.type === 'success' ? 'bg-emerald-950 text-emerald-300 border-emerald-800' : 'bg-red-950 text-red-300 border-red-800'
        }`}>
          {feedback.msg}
        </div>
      )}

      {/* =================================================================== */}
      {/* 2. SADE 3 SEKMELİ MENÜ                                              */}
      {/* =================================================================== */}
      <nav className="bg-slate-900/60 border-b border-slate-800 px-4">
        <div className="max-w-5xl mx-auto flex gap-4 text-sm font-semibold">
          <button
            onClick={() => setActiveTab('bot')}
            className={`py-3 border-b-2 transition flex items-center gap-2 ${
              activeTab === 'bot' ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Activity className="w-4 h-4" />
            Ana Ekran & İşlemlerim
          </button>

          <button
            onClick={() => setActiveTab('market')}
            className={`py-3 border-b-2 transition flex items-center gap-2 ${
              activeTab === 'market' ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <TrendingUp className="w-4 h-4" />
            Canlı Fiyatlar ({tickers.length})
          </button>

          <button
            onClick={() => setActiveTab('settings')}
            className={`py-3 border-b-2 transition flex items-center gap-2 ${
              activeTab === 'settings' ? 'border-blue-500 text-blue-400' : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Sliders className="w-4 h-4" />
            Ayarlar & Geçmiş Testi
          </button>
        </div>
      </nav>

      {/* =================================================================== */}
      {/* 3. ANA İÇERİK                                                       */}
      {/* =================================================================== */}
      <main className="max-w-5xl mx-auto w-full p-4 space-y-5 flex-1">
        {/* TAB 1: BOT ANA EKRANI & İŞLEMLER */}
        {activeTab === 'bot' && (
          <div className="space-y-5">
            {/* A) BÜYÜK BAŞLAT / DURDUR KARTI */}
            <div className={`p-5 rounded-2xl border transition shadow-lg ${
              isRunning
                ? 'bg-emerald-950/20 border-emerald-800/80 shadow-emerald-950/30'
                : 'bg-slate-900 border-slate-800 shadow-slate-950'
            }`}>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2.5">
                    <span className={`w-3.5 h-3.5 rounded-full ${isRunning ? 'bg-emerald-500 animate-ping' : 'bg-slate-600'}`}></span>
                    <h2 className="text-xl font-bold text-white">
                      {isRunning ? 'Yapay Zeka Botu Açık ve Çalışıyor' : 'Yapay Zeka Botu Şu Anda Kapalı'}
                    </h2>
                  </div>
                  <p className="text-xs text-slate-400 mt-1.5">
                    {isRunning
                      ? 'Bot gece-gündüz piyasayı tarar, düşen coinleri analiz eder, kâr gördüğünde otomatik satar.'
                      : 'Alım-satımları başlatmak için sağdaki butona basarak botu açık konuma getirin.'}
                  </p>
                </div>

                <button
                  onClick={toggleBot}
                  disabled={actionLoading}
                  className={`px-6 py-3.5 rounded-xl font-bold text-sm flex items-center justify-center gap-2 shadow-lg transition active:scale-95 ${
                    isRunning
                      ? 'bg-amber-600 hover:bg-amber-500 text-white shadow-amber-600/30'
                      : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-emerald-600/30'
                  }`}
                >
                  {isRunning ? (
                    <>
                      <Pause className="w-5 h-5" />
                      <span>BOTU DURDUR</span>
                    </>
                  ) : (
                    <>
                      <Play className="w-5 h-5 fill-current" />
                      <span>BOTU BAŞLAT</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* B) EN ÖNEMLİ 3 RAKAM: Bakiye, Kâr/Zarar, Açık İşlem */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {/* Toplam Bakiye */}
              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                  <Wallet className="w-4 h-4 text-blue-400" />
                  Toplam Bakiye (Deneme)
                </div>
                <div className="text-2xl font-black text-white font-mono mt-2">
                  ${equity.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </div>
                <div className="text-[11px] text-slate-500 mt-1">Başlangıç: ${initialCapital.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
              </div>

              {/* Bugünkü Kâr / Zarar */}
              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                  <DollarSign className="w-4 h-4 text-emerald-400" />
                  Toplam Kâr / Zarar Durumu
                </div>
                <div className={`text-2xl font-black font-mono mt-2 flex items-center gap-1.5 ${
                  totalProfit > 0 ? 'text-emerald-400' : totalProfit < 0 ? 'text-red-400' : 'text-slate-300'
                }`}>
                  {totalProfit > 0 ? <ArrowUpRight className="w-6 h-6" /> : totalProfit < 0 ? <ArrowDownRight className="w-6 h-6" /> : null}
                  {totalProfit >= 0 ? '+' : ''}${totalProfit.toFixed(2)}
                </div>
                <div className="text-[11px] text-slate-500 mt-1">
                  {totalProfit > 0 ? 'Tebrikler, kardasınız!' : totalProfit < 0 ? 'Piyasa dalgalanması' : 'Henüz tamamlanan işlem yok'}
                </div>
              </div>

              {/* Açık İşlemler */}
              <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                  <Activity className="w-4 h-4 text-purple-400" />
                  Açık Kripto Pozisyonları
                </div>
                <div className="text-2xl font-black text-white font-mono mt-2">
                  {positions.length} <span className="text-sm font-normal text-slate-400">Adet</span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1">
                  {positions.length > 0 ? 'Bot yükseliş bekliyor' : 'Şu an açık coin yok'}
                </div>
              </div>
            </div>

            {/* C) BOT ŞU AN NE YAPIYOR? (CANLI SİNYAL & ANLIK DURUM) */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
              <div className="flex items-center justify-between pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-amber-400" />
                  <h3 className="text-sm font-bold text-white">Bot Şimdi Ne Yapıyor?</h3>
                </div>
                <span className="text-xs text-slate-500 font-mono">Binance Spot Verisi</span>
              </div>

              <div className="mt-3 flex items-start gap-3">
                <div className="w-8 h-8 rounded-full bg-blue-950 border border-blue-800 flex items-center justify-center shrink-0 text-blue-400 mt-0.5">
                  🤖
                </div>
                <div>
                  <div className="text-sm font-semibold text-slate-200">
                    {r10State?.has_signal ? (
                      <span className="text-emerald-400">
                        🟢 Alım Fırsatı Bulundu: {r10State.symbol} için RSI dip formasyonu tespit edildi!
                      </span>
                    ) : isRunning ? (
                      <span>Piyasa taranıyor. Bitcoin ve altcoinlerde düşüşün bittiği ve yükselişin başlayacağı güvenli nokta bekleniyor.</span>
                    ) : (
                      <span>Bot durdurulmuş vaziyette. Başlatıldığında anlık alım fırsatlarını izleyecektir.</span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-1">
                    Risk Kuralı: Bot kafasına göre rastgele işlem açmaz. Dip onaylanmadan ve risk motoru izin vermeden paranızı riske atmaz.
                  </p>
                </div>
              </div>
            </div>

            {/* C2) CANLI BOT AKTİVİTE & TERMİNAL LOGLARI */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
                <div className="flex items-center gap-2.5">
                  <div className="p-1.5 rounded-lg bg-emerald-950/80 border border-emerald-800 text-emerald-400">
                    <Terminal className="w-4 h-4" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-bold text-white">Canlı Bot Terminali & İşlem Logları</h3>
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-950/80 border border-emerald-800 text-[11px] font-mono text-emerald-400">
                        <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                        {isRunning ? 'Canlı Akıyor (1.5s)' : 'Beklemede'}
                      </span>
                    </div>
                    <p className="text-xs text-slate-400 mt-0.5">
                      Botun arka planda ne yaptığını (taranan coinler, canlı fiyatlar, RSI değerleri, risk kararları) anlık izleyin.
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none bg-slate-950/60 px-2.5 py-1 rounded-lg border border-slate-800">
                    <input
                      type="checkbox"
                      checked={autoScrollLogs}
                      onChange={(e) => setAutoScrollLogs(e.target.checked)}
                      className="rounded bg-slate-800 border-slate-700 text-blue-500 focus:ring-0 w-3.5 h-3.5"
                    />
                    <span>Oto-Kaydır</span>
                  </label>
                  <button
                    onClick={() => setLiveLogs([])}
                    className="text-xs text-slate-400 hover:text-slate-200 bg-slate-950/60 hover:bg-slate-800 px-2.5 py-1 rounded-lg border border-slate-800 transition"
                  >
                    Temizle
                  </button>
                  <span className="text-xs text-slate-500 font-mono bg-slate-950/80 px-2 py-1 rounded-lg border border-slate-800">
                    {liveLogs.length} Kayıt
                  </span>
                </div>
              </div>

              {/* Terminal Ekranı */}
              <div className="mt-4 bg-slate-950 border border-slate-800/90 rounded-xl p-3.5 font-mono text-xs max-h-64 overflow-y-auto space-y-1.5 shadow-inner scrollbar-thin scrollbar-thumb-slate-800">
                {liveLogs.length === 0 ? (
                  <div className="text-slate-500 py-8 text-center flex flex-col items-center justify-center gap-2">
                    <Terminal className="w-6 h-6 text-slate-600" />
                    <span>Henüz log kaydı yok. 'BOTU BAŞLAT' butonuna bastığınızda canlı piyasa taraması ve analizler burada anlık akacaktır.</span>
                  </div>
                ) : (
                  liveLogs.map((log, idx) => {
                    const levelColors: Record<string, string> = {
                      SUCCESS: 'text-emerald-400 bg-emerald-950/60 border-emerald-800/80',
                      INFO: 'text-sky-400 bg-sky-950/60 border-sky-800/80',
                      WARNING: 'text-amber-400 bg-amber-950/60 border-amber-800/80',
                      ERROR: 'text-rose-400 bg-rose-950/60 border-rose-800/80'
                    };
                    const badgeClass = levelColors[log.level] || 'text-slate-400 bg-slate-800 border-slate-700';

                    return (
                      <div
                        key={idx}
                        className="flex items-start gap-2 leading-relaxed hover:bg-slate-900/70 px-2 py-1 rounded transition border border-transparent hover:border-slate-800/60"
                      >
                        <span className="text-slate-500 shrink-0 select-none text-[11px]">
                          [{log.time}]
                        </span>
                        <span className={`px-1.5 py-0.2 rounded text-[10px] uppercase font-bold border shrink-0 ${badgeClass}`}>
                          {log.level}
                        </span>
                        <span className="text-slate-400 shrink-0 select-none font-semibold text-[11px]">
                          [{log.service}]
                        </span>
                        <span className="text-slate-200 break-words flex-1">
                          {log.message}
                        </span>
                      </div>
                    );
                  })
                )}
                <div ref={logsEndRef} />
              </div>

              {/* Alt Bilgilendirme */}
              <div className="mt-2.5 text-[11px] text-slate-500 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-1">
                <span>💡 Bot her 15 saniyede bir Binance Spot piyasasından BTC, ETH, SOL vb. coinlerin 15m mumlarını çeker ve RSI teyidi arar.</span>
                <span className="font-mono text-slate-400">R10 Causal Strategy • 0 Leakage</span>
              </div>
            </div>

            {/* D) AÇIK POZİSYONLARIM (Şu Anda Elimde Olan Coinler) */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  <span>Elimdeki Kripto Paralar (Açık Pozisyonlar)</span>
                  <span className="bg-blue-950 text-blue-400 text-xs px-2 py-0.5 rounded-full font-mono">
                    {positions.length}
                  </span>
                </h3>
                <span className="text-xs text-slate-500">Hedefe ulaşınca bot otomatik satar</span>
              </div>

              {positions.length > 0 ? (
                <div className="space-y-2.5">
                  {positions.map((p) => (
                    <div
                      key={p.position_id}
                      className="bg-slate-950 border border-slate-800 rounded-xl p-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-slate-900 flex items-center justify-center font-bold text-white text-sm">
                          {p.symbol.split('/')[0]}
                        </div>
                        <div>
                          <div className="font-bold text-sm text-white">{p.symbol}</div>
                          <div className="text-xs text-slate-400 font-mono">
                            Alış Fiyatı: ${p.entry_price} • Şimdiki Fiyat: ${p.current_price}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center justify-between sm:justify-end gap-4">
                        <div className="text-right font-mono">
                          <div className="text-xs text-slate-400">Anlık Kâr / Zarar</div>
                          <div className={`text-sm font-bold ${p.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                            {p.unrealized_pnl >= 0 ? '+' : ''}${p.unrealized_pnl?.toFixed(2)}
                          </div>
                        </div>

                        <button
                          onClick={() => closePosition(p.position_id)}
                          className="px-3 py-1.5 rounded-lg bg-red-950 hover:bg-red-900 text-red-300 text-xs font-bold border border-red-800 transition"
                        >
                          Hemen Sat
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-8 text-center text-slate-500 text-xs rounded-xl bg-slate-950/50 border border-dashed border-slate-800">
                  <div className="font-semibold text-slate-400 text-sm mb-1">Şu anda açık bir alımınız yok</div>
                  <div>Bot piyasada uygun bir düşüş-yükseliş fırsatı yakaladığında otomatik olarak alım yapacaktır.</div>
                </div>
              )}
            </div>

            {/* E) YAPILAN ALIM-SATIMLAR (KÂR / ZARAR GEÇMİŞİ) */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  <span>Tamamlanan Alım - Satım Geçmişi</span>
                  <span className="bg-slate-800 text-slate-300 text-xs px-2 py-0.5 rounded-full font-mono">
                    {closedOrders.length}
                  </span>
                </h3>
                <span className="text-xs text-slate-500">Kâr eden ve kapatılan işlemler</span>
              </div>

              {closedOrders.length > 0 ? (
                <div className="space-y-2">
                  {closedOrders.map((o, idx) => (
                    <div
                      key={idx}
                      className="bg-slate-950 border border-slate-800 rounded-xl p-3 flex items-center justify-between font-mono text-xs"
                    >
                      <div className="flex items-center gap-2.5">
                        <span className={`w-2 h-2 rounded-full ${o.is_win ? 'bg-emerald-400' : 'bg-red-400'}`}></span>
                        <span className="font-bold text-white">{o.symbol}</span>
                        <span className="text-slate-500 hidden sm:inline">
                          (${o.entry_price} &rarr; ${o.exit_price})
                        </span>
                      </div>

                      <div className="flex items-center gap-3">
                        <span className={`font-bold ${o.realized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {o.realized_pnl >= 0 ? '+' : ''}${o.realized_pnl} ({o.roi_pct}%)
                        </span>
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                          o.is_win ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-red-950 text-red-300 border border-red-800'
                        }`}>
                          {o.is_win ? 'KÂR' : 'ZARAR'}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="py-6 text-center text-slate-500 text-xs rounded-xl bg-slate-950/50 border border-dashed border-slate-800">
                  Henüz kapanmış bir ticaret yok. Bot alıp sattıkça kazandığınız veya kaybettiğiniz net tutar burada listelenir.
                </div>
              )}
            </div>

            {/* F) KENDİN DENE (HIZLI TEST ALIŞI) */}
            <div className="bg-slate-900/70 border border-slate-800 rounded-2xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <div className="text-xs font-bold text-slate-300">Botu Beklemeden Kendin Dene (Test Emri)</div>
                <div className="text-[11px] text-slate-500 mt-0.5">Sanal para ile tek tıkla test alımı yapıp sistemin çalıştığını görebilirsiniz.</div>
              </div>

              <div className="flex items-center gap-2 text-xs">
                <select
                  value={testSymbol}
                  onChange={(e) => setTestSymbol(e.target.value)}
                  className="bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-white font-mono"
                >
                  <option value="BTC/USDT">BTC/USDT</option>
                  <option value="ETH/USDT">ETH/USDT</option>
                  <option value="SOL/USDT">SOL/USDT</option>
                </select>

                <input
                  type="number"
                  value={testAmount}
                  onChange={(e) => setTestAmount(e.target.value)}
                  className="w-20 bg-slate-800 border border-slate-700 rounded-lg px-2.5 py-1.5 text-white font-mono text-center"
                />
                <span className="text-slate-400">$</span>

                <button
                  onClick={handleQuickTrade}
                  disabled={actionLoading}
                  className="px-3.5 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold transition"
                >
                  Alış Yap
                </button>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: CANLI FİYATLAR */}
        {activeTab === 'market' && (
          <div className="space-y-4">
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h2 className="text-base font-bold text-white">Canlı Binance Kripto Fiyatları</h2>
                  <p className="text-xs text-slate-400">Gerçek zamanlı borsa fiyatları</p>
                </div>
                <span className="text-xs bg-emerald-950 text-emerald-400 px-2.5 py-1 rounded-full font-mono border border-emerald-800">
                  GERÇEK PİYASA VERİSİ
                </span>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {tickers.map((t: any) => (
                  <div key={t.symbol} className="bg-slate-950 border border-slate-800 rounded-xl p-4">
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-bold text-white text-base">{t.symbol}</span>
                      <span className="text-[11px] text-slate-500 font-mono">Spot</span>
                    </div>
                    <div className="text-xl font-black text-emerald-400 font-mono mt-1">
                      ${Number(t.price).toLocaleString()}
                    </div>
                    <div className="text-xs text-slate-500 font-mono mt-2 flex justify-between">
                      <span>Alış: ${Number(t.bid || 0).toLocaleString()}</span>
                      <span>Satış: ${Number(t.ask || 0).toLocaleString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: AYARLAR & GEÇMİŞ TESTİ */}
        {activeTab === 'settings' && (
          <div className="space-y-5">
            {/* Geçmiş Veri Testi (Backtest) */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
              <h2 className="text-base font-bold text-white mb-1">Geçmişte Bu Bot Ne Kadar Kazandırırdı? (Backtest)</h2>
              <p className="text-xs text-slate-400 mb-4">
                Botun stratejisini son aylardaki gerçek Binance fiyatları üzerinde simüle edin.
              </p>

              <button
                onClick={runQuickBacktest}
                disabled={btLoading}
                className="px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold transition flex items-center gap-2 shadow-lg shadow-blue-600/20"
              >
                {btLoading ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4 fill-current" />}
                <span>{btLoading ? 'Hesaplanıyor...' : 'Geçmiş Testi Çalıştır (BTC 15m)'}</span>
              </button>

              {btResult && (
                <div className="mt-4 p-4 bg-slate-950 rounded-xl border border-slate-800 grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono text-xs">
                  <div>
                    <span className="text-slate-500 block">Net Kâr / Zarar:</span>
                    <span className={`text-base font-bold ${btResult.total_net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      ${btResult.total_net_pnl}
                    </span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Kazanma Oranı (Win Rate):</span>
                    <span className="text-base font-bold text-white">{btResult.win_rate}%</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">İşlem Sayısı:</span>
                    <span className="text-base font-bold text-white">{btResult.total_trades}</span>
                  </div>
                  <div>
                    <span className="text-slate-500 block">Maksimum Düşüş (Risk):</span>
                    <span className="text-base font-bold text-amber-400">{btResult.max_drawdown}%</span>
                  </div>
                </div>
              )}
            </div>

            {/* Risk Ayarları Bilgisi */}
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5">
              <h2 className="text-base font-bold text-white mb-2">Otomatik Güvenlik & Risk Kuralları</h2>
              <div className="space-y-2 text-xs text-slate-300">
                <div className="p-3 bg-slate-950 rounded-lg flex justify-between">
                  <span>Tek İşlemde En Fazla Risk:</span>
                  <span className="font-bold text-white font-mono">1.0% ($50)</span>
                </div>
                <div className="p-3 bg-slate-950 rounded-lg flex justify-between">
                  <span>Günlük Maksimum Zarar Limiti:</span>
                  <span className="font-bold text-red-400 font-mono">$50.00 (Limit aşılırsa bot o gün durur)</span>
                </div>
                <div className="p-3 bg-slate-950 rounded-lg flex justify-between">
                  <span>Aynı Anda En Fazla Açık Pozisyon:</span>
                  <span className="font-bold text-white font-mono">3 Adet</span>
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* =================================================================== */}
      {/* 4. ALT BİLGİ                                                        */}
      {/* =================================================================== */}
      <footer className="bg-slate-900 border-t border-slate-800 px-4 py-3 text-xs text-slate-500 text-center">
        KRIPTO AGENT • Basit, Güvenilir ve Gerçek Zamanlı Kripto Al-Sat Simülatörü
      </footer>
    </div>
  );
}
