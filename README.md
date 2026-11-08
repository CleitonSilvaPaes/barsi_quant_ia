# 🏦 Barsi Quant IA — Ecossistema Previdenciário

Sistema inteligente e resiliente de investimentos previdenciários baseado na metodologia **Luiz Barsi Filho (Setores B.E.S.T.)**, construído com **Arquitetura Hexagonal (Ports & Adapters)**, persistência canônica em **SQLite (Path B)**, Web Suite 2.0 e execução contínua em Edge ARM64 (TV Box Amlogic).

Consulte a documentação completa do projeto em:
- 📖 [Documentação Completa (barsi_quant_ia/README.md)](barsi_quant_ia/README.md)
- 🤖 [Diretrizes de Agentes Antigravity (AGENTS.md)](AGENTS.md)

---

## ⚡ Início Rápido

```bash
# 1. Ativar ambiente virtual
.\.venv\Scripts\Activate.ps1   # Windows
# source .venv/bin/activate    # Linux/Mac

# 2. Executar suíte de testes com cobertura
pytest barsi_quant_ia/tests/ -v --cov=barsi --cov-fail-under=40

# 3. Rodar ciclo diário
python -m barsi run

# 4. Iniciar servidor web local
uvicorn barsi.api.http:app --reload --port 8000
```
