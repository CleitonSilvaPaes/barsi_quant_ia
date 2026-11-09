# 🏦 Barsi Quant IA — Ecossistema Previdenciário

Sistema inteligente e resiliente de investimentos previdenciários baseado na metodologia **Luiz Barsi Filho (Setores B.E.S.T.)**, construído com **Arquitetura Hexagonal (Ports & Adapters)**, persistência canônica em **SQLite (Path B)**, Web Suite 2.0 e execução contínua em Edge ARM64 (TV Box Amlogic).

Consulte a documentação completa do projeto em:
- 📖 [Documentação Completa & Roadmap Estratégico (barsi_quant_ia/README.md)](barsi_quant_ia/README.md)
- 🤖 [Diretrizes de Agentes Antigravity (AGENTS.md)](AGENTS.md)

---

## 🌟 Funcionalidades Principais do Sistema

- 💼 **Carteira B.E.S.T. & Custódia**: Acompanhamento de cotas, Preço Teto Barsi, Margem de Segurança, **Custo Médio** e **Yield on Cost (YoC)** com edição inline.
- 💵 **Rastreamento de Dividendos Reais (P0)**: Histórico completo de proventos recebidos (`dividends_received`) para cálculo da taxa de crescimento da bola de neve.
- 🔐 **Autenticação Segura & 2FA (TOTP)**: Criptografia PBKDF2 e autenticação em dois fatores RFC 6238 nativa compatível com Google Authenticator e Authy.
- 📈 **Analytics & Benchmarks em Tempo Real**: Comparativo direto da carteira contra IBOVESPA (`^BVSP`) e CDI Acumulado 12M (via API oficial do Banco Central).
- 📑 **Orçamento & Conciliação OFX**: Leitura e desduplicação de extratos bancários, comparativo Previsto vs Realizado e autoclassificação via regras de palavras-chave.
- 🧠 **IA Quantitativa Interpretável**: Ensemble de Regressão Logística (DSS) para sustentabilidade de proventos, Q-Learning para alocação de aportes e Rule Engine determinístico.
- 🚀 **Deploy Contínuo via Tailscale SSH**: Pipeline de CI/CD automatizado via GitHub Actions com rollback automático e zero portas abertas na rede doméstica.

---

## ⚡ Início Rápido

```bash
# 1. Ativar ambiente virtual
.\.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate    # Linux/Mac

# 2. Executar suíte de testes com cobertura (108 testes)
pytest barsi_quant_ia/tests/ -v --cov=barsi --cov-fail-under=40

# 3. Rodar ciclo diário quantitativo
python -m barsi run

# 4. Iniciar servidor web local
uvicorn barsi.api.http:app --reload --port 8000
```


