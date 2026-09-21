#!/usr/bin/env bash
# ==============================================================================
# KRIPTO AGENT - VPS 1-CLICK DEPLOYMENT & DAEMON SETUP (Ubuntu/Debian)
# ==============================================================================
set -euo pipefail

echo "======================================================================"
echo "          KRIPTO AGENT - VPS KURULUM VE CANLIYA ALMA ARACI             "
echo "======================================================================"

# 1. Root veya Sudo Kontrolü
if [ "$EUID" -ne 0 ]; then
  echo "[!] Lütfen bu betiği root veya 'sudo bash deploy_vps.sh' ile çalıştırın."
  exit 1
fi

# 2. Sistem Paket Güncellemesi
echo "[*] Sistem paketleri güncelleniyor..."
apt-get update -y && apt-get upgrade -y
apt-get install -y curl git ufw unzip htop

# 3. Docker & Docker Compose Kurulumu (Eğer yüklü değilse)
if ! command -v docker &> /dev/null; then
    echo "[*] Docker bulunamadı, resmi Docker motoru kuruluyor..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm -f get-docker.sh
    systemctl enable docker
    systemctl start docker
    echo "[+] Docker başarıyla kuruldu."
else
    echo "[+] Docker zaten kurulu."
fi

# 4. Güvenlik Duvarı (UFW) Ayarı (Port 8000 ve SSH)
echo "[*] Güvenlik duvarı (UFW) yapılandırılıyor..."
ufw allow 22/tcp || true
ufw allow 8000/tcp || true
ufw --force enable || true
echo "[+] 22 (SSH) ve 8000 (KRIPTO AGENT) portları açıldı."

# 5. Log Klasörü ve İzinler
mkdir -p logs
touch kripto_agent.db

# 6. Docker İmajını Derle ve Başlat
echo "[*] KRIPTO AGENT VPS konteyneri derleniyor ve arka planda başlatılıyor..."
docker compose -f docker-compose.vps.yml down || true
docker compose -f docker-compose.vps.yml build --no-cache
docker compose -f docker-compose.vps.yml up -d

echo ""
echo "======================================================================"
echo "       [+] TEBRİKLER! KRIPTO AGENT BAŞARIYLA CANLIYA ALINDI!          "
echo "======================================================================"
IP_ADDR=$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')
echo ""
echo "  Web Paneli (Dashboard) : http://${IP_ADDR}:8000/dashboard.html"
echo "  Sistem Durumu (API)    : http://${IP_ADDR}:8000/api/v1/system/status"
echo "  Portföy Durumu (API)   : http://${IP_ADDR}:8000/portfolio"
echo ""
echo "Faydalı Komutlar:"
echo "  - Canlı Logları İzlemek İçin : docker compose -f docker-compose.vps.yml logs -f"
echo "  - Botu Durdurmak İçin        : docker compose -f docker-compose.vps.yml stop"
echo "  - Botu Yeniden Başlatmak İçin: docker compose -f docker-compose.vps.yml restart"
echo "======================================================================"
