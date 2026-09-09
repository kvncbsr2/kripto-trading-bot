#!/usr/bin/env python3
"""
KRIPTO AGENT - Risk & Strateji Yönetim Aracı (CLI V2)
====================================================
Kullanım:
    python scripts/set_profile.py <1-10>           -> Risk profilini ayarlar (L1..L10)
    python scripts/set_profile.py strategy <id>    -> Aktif stratejiyi değiştirir
    python scripts/set_profile.py strategies       -> Kullanılabilir tüm stratejileri listeler
    python scripts/set_profile.py reset            -> Tek tıkla Seviye 1 (En Stabil) durumuna döner
    python scripts/set_profile.py list             -> Tüm risk profillerini ve stratejileri listeler
"""

import sys
import os
import json
import urllib.request
import urllib.error

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from shared.config import get_settings
from services.config_manager.risk_profiles import (
    get_all_profiles,
    get_profile,
    apply_profile_to_system,
    apply_strategy_to_system,
)
from services.strategy_engine.registry import get_all_strategies, STRATEGY_REGISTRY

settings = get_settings()
API_BASE = f"http://{settings.API_HOST}:{settings.API_PORT}"


def print_profiles_table(current_level: int = 1, current_strategy_id: str = "r10_rsi_divergence"):
    profiles = get_all_profiles()
    print("\n" + "=" * 94)
    print(" 🚀 KRIPTO AGENT — CANONICAL RISK PROFILES (L1-L10) ")
    print("=" * 94)
    header = f"{'SEVİYE':<6} | {'PROFİL ADI':<24} | {'RİSK %':<8} | {'MİN SKOR':<9} | {'MAKS POZ':<9} | {'HEDEF':<7} | {'DURUM':<8}"
    print(header)
    print("-" * 94)
    for p in profiles:
        active_mark = "👉 AKTİF" if p["level"] == current_level else ""
        row = (
            f"L{p['level']:<4} | "
            f"{p['name'][:24]:<24} | "
            f"%{p['risk_per_trade_pct']:<6.1f} | "
            f"{p['min_signal_score']:<9.1f} | "
            f"{p['max_open_positions']:<9} | "
            f"${p['daily_target']:<6.0f} | "
            f"{active_mark}"
        )
        print(row)
    print("=" * 94)
    print(f"🧩 Aktif Strateji: {current_strategy_id} (STRATEGY != RISK PROFILE)")
    print("=" * 94 + "\n")


def print_strategies_table(current_strategy_id: str = "r10_rsi_divergence"):
    strategies = get_all_strategies()
    print("\n" + "=" * 94)
    print(" 🧩 KRIPTO AGENT — STRATEGY REGISTRY ")
    print("=" * 94)
    header = f"{'STRATEJİ ID':<24} | {'ADI':<26} | {'KATEGORİ':<22} | {'DURUM':<8}"
    print(header)
    print("-" * 94)
    for s in strategies:
        active_mark = "👉 AKTİF" if s["id"] == current_strategy_id else ""
        row = (
            f"{s['id']:<24} | "
            f"{s['name'][:26]:<26} | "
            f"{s['category']:<22} | "
            f"{active_mark}"
        )
        print(row)
    print("=" * 94 + "\n")


def apply_profile_via_api(level: int):
    url = f"{API_BASE}/api/v1/system/profile"
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": settings.API_ADMIN_KEY,
    }
    data = json.dumps({"level": level}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def apply_strategy_via_api(strategy_id: str):
    url = f"{API_BASE}/api/v1/system/strategy"
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": settings.API_ADMIN_KEY,
    }
    data = json.dumps({"strategy_id": strategy_id}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_status_via_api():
    url = f"{API_BASE}/api/v1/system/status"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        status = get_status_via_api()
        cur_lvl = status.get("active_profile_level", 1) if status else 1
        cur_strat = status.get("active_strategy_id", "r10_rsi_divergence") if status else "r10_rsi_divergence"
        print_profiles_table(cur_lvl, cur_strat)
        return

    first_arg = sys.argv[1].strip().lower()

    if first_arg in ("status", "list", "--list", "-l"):
        status = get_status_via_api()
        cur_lvl = status.get("active_profile_level", 1) if status else 1
        cur_strat = status.get("active_strategy_id", "r10_rsi_divergence") if status else "r10_rsi_divergence"
        print_profiles_table(cur_lvl, cur_strat)
        print_strategies_table(cur_strat)
        return

    if first_arg in ("strategies", "strategy-list", "strats"):
        status = get_status_via_api()
        cur_strat = status.get("active_strategy_id", "r10_rsi_divergence") if status else "r10_rsi_divergence"
        print_strategies_table(cur_strat)
        return

    if first_arg == "strategy":
        if len(sys.argv) < 3:
            print("❌ HATA: Strateji ID belirtiniz. Örnek: python scripts/set_profile.py strategy r10_rsi_divergence")
            print(f"Mevcut Stratejiler: {list(STRATEGY_REGISTRY.keys())}")
            sys.exit(1)
        target_strat = sys.argv[2].strip()
        if target_strat not in STRATEGY_REGISTRY:
            print(f"❌ HATA: Geçersiz strateji '{target_strat}'. Mevcut: {list(STRATEGY_REGISTRY.keys())}")
            sys.exit(1)
        print(f"🔄 Strateji '{target_strat}' uygulanıyor...")
        api_res = apply_strategy_via_api(target_strat)
        if api_res and api_res.get("success"):
            print(f"✅ Canlı API üzerinden '{target_strat}' başarıyla aktif edildi!")
        else:
            apply_strategy_to_system(target_strat)
            print(f"✅ Yerel bellek üzerinden '{target_strat}' aktif edildi.")
        return

    if first_arg in ("reset", "rollback", "default", "baseline"):
        target_level = 1
    else:
        try:
            target_level = int(first_arg)
            if target_level < 1 or target_level > 10:
                print(f"❌ HATA: Seviye 1 ile 10 arasında olmalıdır! (Girilen: {target_level})")
                sys.exit(1)
        except ValueError:
            print(f"❌ HATA: Geçersiz parametre '{first_arg}'. 1-10 arası bir sayı veya 'reset' yazınız.")
            sys.exit(1)

    print(f"🔄 Seviye L{target_level} uygulanıyor...")

    api_res = apply_profile_via_api(target_level)
    if api_res and api_res.get("success"):
        prof = api_res.get("profile", {})
        print(f"✅ Canlı API üzerinden L{target_level} ({prof.get('name')}) başarıyla uygulandı!")
    else:
        local_res = apply_profile_to_system(target_level)
        prof = local_res.get("profile", {})
        print(f"✅ Yerel bellek üzerinden L{target_level} ({prof.get('name')}) uygulandı.")

    print(f"\n📊 GÜNCEL KONFİGÜRASYON (L{target_level} - {prof.get('name')}):")
    print(f"   • İşlem Başı Risk:    %{prof.get('risk_per_trade_pct', 0.5):.1f}")
    print(f"   • Min Sinyal Skoru:   {prof.get('min_signal_score')}")
    print(f"   • Maks Açık Pozisyon: {prof.get('max_open_positions')}")
    print(f"   • Günlük Kâr Hedefi:  ${prof.get('daily_target')}")
    print(f"   • Günlük Maks Zarar:  ${prof.get('daily_max_loss')}")
    print(f"   • Hedef Modu:         {prof.get('target_mode')}")
    if target_level == 1:
        print("\n🛡️ SİSTEM EN STABİL, DENETLENMİŞ BAZ SEVİYEYE (L1) DÖNDÜRÜLDÜ.")


if __name__ == "__main__":
    main()
