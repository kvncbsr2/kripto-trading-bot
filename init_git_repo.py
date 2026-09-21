"""
Zero-Fork Pure-Python Repository Updater for KRIPTO AGENT (GoDaddy cPanel Compatible)
Eliminates git-remote-https fork errors by directly streaming archive from GitHub.
"""

import io
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone

project_root = os.path.dirname(os.path.abspath(__file__))
zip_url = "https://github.com/kvncbsr2/kripto-trading-bot/archive/refs/heads/main.zip"

PROTECTED_PATHS = {
    "kripto_agent.db",
    "kripto_agent_denetim_salt_okunur.db",
    ".env",
    "passenger_wsgi.py",
}

print(f"[{datetime.now(timezone.utc).isoformat()}] GitHub arşivi indiriliyor: {zip_url}...")

try:
    req = urllib.request.Request(zip_url, headers={"User-Agent": "KRIPTO-AGENT-SYNC/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP Hata Kodu: {resp.status}")
        zip_bytes = resp.read()

    print(f"[{datetime.now(timezone.utc).isoformat()}] İndirme tamamlandı ({len(zip_bytes):,} bayt). Dosyalar ayıklanıyor...")

    updated_count = 0
    skipped_count = 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for member in z.infolist():
            parts = member.filename.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            rel_path = parts[1]

            if rel_path in PROTECTED_PATHS or rel_path.startswith((".git/", "tmp/")):
                skipped_count += 1
                continue
            if rel_path.endswith((".db", ".sqlite", ".sqlite3")):
                skipped_count += 1
                continue

            dest_path = os.path.join(project_root, rel_path)
            if member.is_dir():
                os.makedirs(dest_path, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with z.open(member) as src, open(dest_path, "wb") as dst:
                    dst.write(src.read())
                updated_count += 1

    print(f"[{datetime.now(timezone.utc).isoformat()}] Ayıklama tamamlandı: {updated_count} dosya güncellendi, {skipped_count} korumalı dosya korundu.")

    # Trigger Passenger reload
    tmp_dir = os.path.join(project_root, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    restart_file = os.path.join(tmp_dir, "restart.txt")
    with open(restart_file, "a") as f:
        f.write(f"\n# Reload at {datetime.now(timezone.utc).isoformat()}")

    print(f"[{datetime.now(timezone.utc).isoformat()}] Passenger yeniden başlatma sinyali tetiklendi ({restart_file}).")
    print("SUCCESS: Güncelleme başarıyla tamamlandı!")
except Exception as e:
    print(f"ERROR: Güncelleme sırasında hata oluştu: {e}")
    sys.exit(1)

