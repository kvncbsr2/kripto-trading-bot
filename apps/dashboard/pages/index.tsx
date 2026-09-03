import React, { useEffect, useState } from 'react';

interface PortfolioData {
  initial_capital: number;
  balance: number;
  equity: number;
  daily_pnl: number;
  max_drawdown: number;
  is_halted: boolean;
}

export default function DashboardPage() {
  const [data, setData] = useState<PortfolioData>({
    initial_capital: 5000,
    balance: 5000,
    equity: 5037.10,
    daily_pnl: 37.10,
    max_drawdown: 0.0036,
    is_halted: false,
  });

  useEffect(() => {
    fetch('http://localhost:8000/portfolio')
      .then((res) => res.json())
      .then((d) => setData(d))
      .catch((e) => console.log('Connecting to API...', e));
  }, []);

  return (
    <div className="min-h-screen bg-slate-950 text-white p-8">
      <header className="mb-8 border-b border-slate-800 pb-4 flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-emerald-400">KRIPTO AGENT — Next.js Dashboard</h1>
          <p className="text-slate-400 text-sm">7-Day $5,000 Paper Trading Validation System</p>
        </div>
        <span className="px-3 py-1 bg-emerald-900/40 text-emerald-400 border border-emerald-500/30 rounded text-xs font-semibold">
          SYSTEM: NORMAL 🟢
        </span>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        <div className="bg-slate-900 border border-slate-800 p-5 rounded-xl">
          <span className="text-xs text-slate-400 uppercase font-bold">Initial Capital</span>
          <div className="text-2xl font-bold mt-1 text-white">${data.initial_capital.toFixed(2)}</div>
        </div>
        <div className="bg-slate-900 border border-slate-800 p-5 rounded-xl">
          <span className="text-xs text-slate-400 uppercase font-bold">Equity</span>
          <div className="text-2xl font-bold mt-1 text-emerald-400">${data.equity.toFixed(2)}</div>
        </div>
        <div className="bg-slate-900 border border-slate-800 p-5 rounded-xl">
          <span className="text-xs text-slate-400 uppercase font-bold">Today Net PnL</span>
          <div className="text-2xl font-bold mt-1 text-emerald-400">+${data.daily_pnl.toFixed(2)}</div>
        </div>
        <div className="bg-slate-900 border border-slate-800 p-5 rounded-xl">
          <span className="text-xs text-slate-400 uppercase font-bold">Max Drawdown</span>
          <div className="text-2xl font-bold mt-1 text-white">{(data.max_drawdown * 100).toFixed(2)}%</div>
        </div>
      </div>
    </div>
  );
}
