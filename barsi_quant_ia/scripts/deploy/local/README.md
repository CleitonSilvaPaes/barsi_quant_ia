# Rodar Barsi Quant IA no PC Local (Windows / Linux / macOS)

Este guia permite rodar o **Barsi Quant IA** diretamente no seu computador pessoal, sem depender de TV Box, ARM64, SSH ou systemd.

---

## 📋 Pré-requisitos
- **Python 3.11+**
- **Git**
- *(Opcional)* **Docker** e **Docker Compose**

---

## 🚀 Método 1: Setup Local Direto (Python venv)

### 1. Criar e Ativar Ambiente Virtual
Na pasta raiz do projeto:
```bash
# Windows (PowerShell):
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Instalar Dependências
```bash
pip install --upgrade pip
pip install -e barsi_quant_ia/
```

### 3. Configurar Variáveis de Ambiente
Copie o template e preencha as variáveis locais essenciais:
```bash
cp barsi_quant_ia/.env.example .env
```
Variáveis mínimas recomendadas para teste no PC:
```ini
BARSI_ENV=development
BARSI_LOG_LEVEL=INFO
BARSI_INITIAL_ADMIN_USER=admin
BARSI_INITIAL_ADMIN_PASS=sua_senha_segura
BARSI_MARKET_PROVIDER=yfinance
BARSI_NEWS_PROVIDER=rss
BARSI_AI_PROVIDER=rule_engine
BARSI_SHEETS_PROVIDER=xlsx_fallback
BARSI_LOCAL_XLSX_PATH=carteira_barsi_quant_ia.xlsx
```

### 4. Inicializar Usuário e Banco SQLite
```bash
# Semeia despesas padrão no SQLite
python -m barsi budget seed

# Cria seu usuário de acesso web
python -m barsi user-add admin sua_senha_segura --role admin
```

### 5. Executar
- **Rodar a API Web (Dashboard):**
  ```bash
  python -m barsi api --port 8000
  # Acesse em: http://localhost:8000
  ```
- **Rodar cálculo e enriquecimento (Dry-Run):**
  ```bash
  python -m barsi run --dry-run
  ```
- **Processar e auditar extratos bancários:**
  ```bash
  python -m barsi extratos --dry-run
  python -m barsi budget audit 2026-10
  ```

---

## 🐳 Método 2: Via Docker & Docker Compose

Se você possui Docker instalado, pode rodar o container completo em apenas 2 comandos:

```bash
cd barsi_quant_ia
docker compose up -d --build
```
Acesse `http://localhost:8000`. Os dados de banco (`memory/`) e cache (`cache/`) são persistidos no disco local através dos volumes declarados.

---

## ⏰ Agendamento Periódico no PC (Substituto do systemd)

Se desejar monitoramento contínuo das cotações em horário de pregão (Segunda a Sexta, 09h às 18h):

### No Windows (Agendador de Tarefas via PowerShell):
```powershell
schtasks /create /tn "BarsiQuantRun" /tr "C:\caminho\Investimento\.venv\Scripts\python.exe -m barsi run" /sc hourly /st 09:00
```

### No Linux / macOS (Crontab):
```cron
0 9-18 * * 1-5 cd /caminho/Investimento && .venv/bin/python -m barsi run >> logs/cron.log 2>&1
```
