#!/bin/bash
# Instala ou atualiza o Mídia SW numa VM Debian/Ubuntu (Google Cloud).
#
#   sudo bash instalar.sh [DOMINIO] [GOOGLE_CLIENT_ID]
#
# DOMINIO omitido ou "auto": usa <ip>.sslip.io (HTTPS sem precisar mexer em DNS).
# Pode rodar quantas vezes quiser: atualiza o código e NUNCA sobrescreve o banco
# que já está no servidor (o banco do pacote só é usado na primeira instalação).
set -euo pipefail

DOMINIO="${1:-auto}"
CLIENT_ID="${2:-}"
ORIGEM="$(cd "$(dirname "$0")/.." && pwd)"
DESTINO=/opt/midia-sw

if [ "$(id -u)" != 0 ]; then echo "rode com sudo"; exit 1; fi

if [ "$DOMINIO" = "auto" ]; then
  IP=$(curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || true)
  [ -z "$IP" ] && IP=$(curl -s https://api.ipify.org)
  DOMINIO="${IP//./-}.sslip.io"
fi
echo "==> domínio: https://$DOMINIO"

echo "==> fuso horário de São Paulo"
timedatectl set-timezone America/Sao_Paulo || true

echo "==> pacotes"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 curl gpg debian-keyring debian-archive-keyring apt-transport-https >/dev/null 2>&1 || \
  apt-get install -y -qq python3 curl gpg apt-transport-https
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy
fi

echo "==> código em $DESTINO"
id midia &>/dev/null || useradd --system --home-dir "$DESTINO" --shell /usr/sbin/nologin midia
mkdir -p "$DESTINO/dados/backups"
for item in midia web deploy docs tests run.py README.md; do
  [ -e "$ORIGEM/$item" ] || continue
  rm -rf "${DESTINO:?}/$item"
  cp -r "$ORIGEM/$item" "$DESTINO/$item"
done
if [ ! -f "$DESTINO/dados/midia.db" ]; then
  if [ -f "$ORIGEM/dados/midia.db" ]; then
    cp "$ORIGEM/dados/midia.db" "$DESTINO/dados/midia.db"
    echo "    banco inicial copiado do pacote"
  else
    echo "    sem banco no pacote: começa vazio"
  fi
else
  echo "    banco existente preservado"
  sudo -u midia python3 "$DESTINO/run.py" backup >/dev/null 2>&1 || true
fi

# config.json: mantém o client id anterior se não for informado agora
CFG="$DESTINO/config.json"
if [ -z "$CLIENT_ID" ] && [ -f "$CFG" ]; then
  CLIENT_ID=$(python3 -c "import json;print(json.load(open('$CFG')).get('google_client_id',''))")
fi
cat > "$CFG" <<EOF
{
  "google_client_id": "$CLIENT_ID",
  "dominio": "streetwise.com.br",
  "login_dev": "0",
  "host": "127.0.0.1",
  "https": "1",
  "porta": 8000
}
EOF
chown -R midia:midia "$DESTINO"
chmod 750 "$DESTINO/dados"

echo "==> serviço"
cat > /etc/systemd/system/midia-sw.service <<EOF
[Unit]
Description=Midia SW
After=network.target

[Service]
User=midia
WorkingDirectory=$DESTINO
Environment=PYTHONIOENCODING=utf-8
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $DESTINO/run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/midia-sw-backup.service <<EOF
[Unit]
Description=Backup diario do Midia SW

[Service]
Type=oneshot
User=midia
WorkingDirectory=$DESTINO
ExecStart=/usr/bin/python3 $DESTINO/run.py backup
EOF

cat > /etc/systemd/system/midia-sw-backup.timer <<EOF
[Unit]
Description=Backup diario do Midia SW

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo "==> HTTPS (Caddy)"
cat > /etc/caddy/Caddyfile <<EOF
$DOMINIO {
  encode gzip
  request_body {
    max_size 30MB
  }
  reverse_proxy 127.0.0.1:8000
}
EOF

systemctl daemon-reload
systemctl enable --now midia-sw-backup.timer >/dev/null
systemctl enable midia-sw >/dev/null
systemctl restart midia-sw
systemctl enable caddy >/dev/null
systemctl restart caddy

sleep 2
if systemctl is-active --quiet midia-sw; then
  echo
  echo "================================================================"
  echo " Mídia SW no ar:  https://$DOMINIO"
  if [ -z "$CLIENT_ID" ]; then
    echo " ATENÇÃO: falta o Google Client ID. Rode de novo com ele:"
    echo "   sudo bash $DESTINO/deploy/instalar.sh $DOMINIO SEU_CLIENT_ID"
  fi
  echo " (o certificado HTTPS pode levar 1 minuto na primeira vez)"
  echo "================================================================"
else
  echo "o serviço não subiu; veja: sudo journalctl -u midia-sw -n 50"
  exit 1
fi
