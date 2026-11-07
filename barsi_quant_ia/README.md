# 🏦 Sistema Previdenciário Barsi - Agente Quantitativo Resiliente

Sistema de investimento previdenciário baseado no **Método Barsi (B.E.S.T.)**, construído sob **Arquitetura Hexagonal (Ports & Adapters)** e otimizado para execução contínua em **Edge / SBC (TV Box Amlogic S905W · ARM64)** integrado a **Google Sheets** e **Cloudflare Workers / Tunnels**.

---

## 🏛️ Diagrama da Arquitetura Hexagonal

```text
               +-------------------------------------------------------------+
               |                    Nuvem / Interfaces                      |
               |                                                             |
               |  [Google Sheets]     [Cloudflare Tunnel]   [Cloudflare R2]  |
               +-------+---------------------+---------------------+---------+
                       |                     |                     |
                       v                     v                     v
+----------------------+---------------------+---------------------+----------------------+
| ADAPTERS             |                     |                     |                      |
|                      |  [GSpreadAdapter]   |  [FastAPI /run]     |  [R2MirrorAdapter]   |
|                      |                     |                     |                      |
+----------------------+---------------------+---------------------+----------------------+
| PORTS                |                     |                     |                      |
|                      |  SheetsPort         |  HTTP Controller    |  HistoryPort         |
+----------------------+---------------------+---------------------+----------------------+
|                                                                                         |
| DOMÍNIO PURO & REGRAS B.E.S.T. (Sem I/O, 100% Testável)                                 |
|                                                                                         |
|       * Preço Teto Barsi: Teto = DivProj / 0.06                                         |
|       * Margem de Segurança: ((Teto - Preço) / Preço) * 100                             |
|       * Status Barsi: ACUMULAR (Margem >= 0) / AGUARDAR (Margem < 0)                    |
|       * Engine Determinístico de Regras (RuleEngineAdapter)                             |
|                                                                                         |
+----------------------+---------------------+---------------------+----------------------+
| PORTS                |                     |                     |                      |
|                      |  MarketDataPort     |  NewsPort           |  AIPort              |
+----------------------+---------------------+---------------------+----------------------+
| ADAPTERS             |                     |                     |                      |
|  [CachedMarket]      |  [GoogleNewsRSS]    |  [RuleEngine]       |  [SQLiteAdapter]     |
|   ├── YFinance       |   └── XML Parser    |   └── OllamaRemote  |   └── barsi_memory   |
|   └── Brapi (token)  |                     |                     |                      |
+----------------------+---------------------+---------------------+----------------------+
                       |                     |                     |
                       v                     v                     v
               +-------------------------------------------------------------+
               |                  Hardware Alvo (TV Box)                     |
               |   Amlogic S905W (ARM64) · RAM < 250MB RSS · Sem GPU/VPU      |
               +-------------------------------------------------------------+
```

---

## 📑 ADR - Tabela de Decisões de Arquitetura

| Decisão | Opção Escolhida | Justificativa Técnica |
| :--- | :--- | :--- |
| **Padrão Arquitetural** | Ports & Adapters (Hexagonal) | Desacopla 100% o cálculo financeiro das APIs externas. Trocar de Excel para Google Sheets ou de yfinance para Brapi não altera 1 linha de regra Barsi. |
| **Dados em Runtime** | Sem Pandas (Dicionários + Pydantic) | Pandas aloca entre 80 MB e 150 MB de RAM base. Em um hardware com apenas 500 MB livres, Pydantic consome < 15 MB e atinge NF02. |
| **Geração de Insights** | Strategy (RuleEngine + Ollama Remoto) | O modelo neural `gemma2:2b` exige > 2 GB de RAM e travava a TV Box. O padrão padrão é o motor de regras determinístico B.E.S.T.; LLM externa é apenas um plugin opcional. |
| **Orquestração** | Cloudflare Cron + Tunnel | A TV Box conectada em Wi-Fi doméstico não tem IP público fixo e está atrás de CGNAT. O Cloudflare Tunnel expõe `/run` com segurança Zero Trust e o Cron Trigger da Cloudflare dispara as runs. |
| **Resiliência de Rede** | Circuit Breaker + Retry Exponencial | Evita que oscilações da B3 ou do Google News travem o ciclo. Falhas consecutivas abrem o circuito e usam cache em disco (`diskcache`). |

---

## 🚀 Instalação Rápida na TV Box (ARM64)

### 1. Conecte na TV Box via SSH
```bash
ssh cleiton@192.168.100.72
```

### 2. Clone o repositório e execute o script idempotente
```bash
git clone <seu-repo> /opt/barsi_quant_ia
cd /opt/barsi_quant_ia
chmod +x scripts/install_tvbox.sh
./scripts/install_tvbox.sh
```
O script cuidará de:
* Instalar pacotes de sistema (`python3-venv`, `cron`, `jq`);
* Criar memória Swap de 2 GB (evitando OOM Killer);
* Desativar a interface gráfica (liberando +250 MB de RAM livre);
* Instalar o `cloudflared` oficial para ARM64;
* Configurar o serviço do sistema `barsi.service` no Systemd.

---

## 🔄 Migração: Excel Local -> Google Sheets (Passo Único)

1. Obtenha suas credenciais de Service Account no Google Cloud Console e salve em `credentials.json`.
2. Compartilhe sua planilha do Google Sheets com o email da Service Account gerada.
3. Execute o script de migração:
```bash
python scripts/migrate_xlsx_to_sheets.py carteira_barsi_quant_ia.xlsx credentials.json
```

---

## ☁️ Configuração Cloudflare (Tunnel + Worker Cron)

### 1. Criar o Túnel na TV Box
```bash
cloudflared tunnel login
cloudflared tunnel create tvbox-invest
cloudflared tunnel route dns tvbox-invest barsi.seudominio.com
```

### 2. Configurar o arquivo `~/.cloudflared/config.yml`
```yaml
tunnel: <UUID_DO_TUNEL>
credentials-file: /home/cleiton/.cloudflared/<UUID>.json

ingress:
  - hostname: barsi.seudominio.com
    service: http://localhost:8000
  - service: http_status:404
```

### 3. Deploy do Worker Cron Trigger
Crie um Worker na Cloudflare com o código em `scripts/cloudflare_worker.js` e configure o agendador:
* Cron: `0 9-18 * * 1-5` (de hora em hora durante o horário de pregão).
* Variáveis de ambiente: `BARSI_TUNNEL_URL` e `BARSI_API_BEARER_TOKEN`.

---

## 🧪 Execução de Testes
```bash
# Rodar todos os testes com cobertura do domínio
pytest tests/ -v --cov=barsi.domain --cov-report=term-missing
```

---

## 🔮 Stretch Goals (Próximos Passos Futuros)
* [ ] Notificações automáticas no Telegram / ntfy em caso de cotação com margem > 30%.
* [ ] Dashboard web estático via Cloudflare Pages consumindo os snapshots JSONL do R2.
* [ ] Backtesting histórico de proventos acumulados por ativo.

