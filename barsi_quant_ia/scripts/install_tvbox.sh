#!/usr/bin/env bash
# ==============================================================================
# SCRIPT DE INSTALAÇÃO E CONFIGURAÇÃO IDEMPOTENTE PARA TV BOX (ARM64)
# Hardware: Amlogic S905W / Debian ComPSTEAM
# ==============================================================================
set -euo pipefail

echo "=========================================================="
echo "   INICIANDO INSTALAÇÃO DO SISTEMA BARSI NA TV BOX        "
echo "=========================================================="

# 1. Dependências mínimas de SO
echo "[1/6] Atualizando pacotes e instalando dependências essenciais..."
sudo apt update
sudo apt install -y python3 python3-venv python3-pip curl git cron jq

# 2. Configuração de Memória Swap (2 GB) se não existir
echo "[2/6] Verificando memória Swap..."
if [ ! -f /swapfile ]; then
    echo "Criando swap de 2GB para estabilidade..."
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap configurada com sucesso!"
else
    echo "Swapfile já existe. Mantendo."
fi

# 3. Desativar Interface Gráfica para liberar 250MB de RAM (se estiver ativa)
CURRENT_TARGET=$(systemctl get-default)
if [ "$CURRENT_TARGET" = "graphical.target" ]; then
    echo "[3/6] Desativando interface gráfica (liberando ~250MB de RAM)..."
    sudo systemctl set-default multi-user.target
fi

# 4. Instalar Cloudflare Tunnel (cloudflared arm64) se não existir
if ! command -v cloudflared &> /dev/null; then
    echo "[4/6] Instalando Cloudflare Tunnel (cloudflared arm64)..."
    curl -L --output /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
    sudo dpkg -i /tmp/cloudflared.deb
    rm -f /tmp/cloudflared.deb
    echo "Cloudflared instalado!"
fi

# 5. Criar ambiente virtual Python e instalar pacote
APP_DIR="/opt/barsi_quant_ia"
echo "[5/6] Configurando aplicação em $APP_DIR..."
sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER:$USER" "$APP_DIR"

if [ ! -d "$APP_DIR/.venv" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi

"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

# 6. Criar e habilitar serviço systemd
echo "[6/6] Configurando Systemd Service e Timer..."
cat << 'EOF' | sudo tee /etc/systemd/system/barsi.service
[Unit]
Description=Sistema Previdenciario Barsi API & Worker
After=network.target

[Service]
Type=simple
User=cleiton
WorkingDirectory=/opt/barsi_quant_ia
ExecStart=/opt/barsi_quant_ia/.venv/bin/python -m barsi api --port 8000
Restart=always
RestartSec=10
EnvironmentFile=/opt/barsi_quant_ia/.env
MemoryMax=250M

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now barsi.service

echo "=========================================================="
echo "   INSTALAÇÃO CONCLUÍDA COM SUCESSO!                     "
echo "   Verifique a saúde com: curl http://localhost:8000/health"
echo "=========================================================="

