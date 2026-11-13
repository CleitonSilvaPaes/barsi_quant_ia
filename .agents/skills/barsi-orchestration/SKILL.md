---
name: barsi-orchestration
description: "Protocolo de orquestração de tarefas, planejamento em árvore e validação de gates para o Barsi Quant IA."
---

# 🎼 Barsi Orchestration — Protocolo de Coordenação de Tarefas

Este skill define o pipeline de execução para orquestrar demandas no repositório.

## 📋 Fases de Execução do Orquestrador

### Fase 1: Diagnóstico e Localização
- Identificar o escopo da solicitação do usuário.
- Se envolver mapeamento de código, rodar `python -m graphify query "<termo>"`.
- Listar exatamente os arquivos que serão alterados.

### Fase 2: Planejamento Atômico
- Registrar as tarefas em [orchestrator_tasks.md](file:///c:/Users/cleiton%20paes/Documents/Projeto/Investimento/.agents/tasks/orchestrator_tasks.md).
- Dividir em subtarefas:
  - Análise & Domínio (`barsi-quant-analyst`)
  - Orçamento & Conciliação (`barsi-budget-conciliator`)
  - Infraestrutura, Segurança & Testes (`barsi-orchestrator`)

### Fase 3: Execução e Edição
- Realizar alterações usando ferramentas cirúrgicas (`edit_file` ou `write_file`).
- Aplicar sempre sanitização de saída (`esc()`), proteção CSRF e padrões PEP 8.

### Fase 4: Garantia de Qualidade (QA Gates)
1. **Linter**: `python -m ruff check barsi_quant_ia/src barsi_quant_ia/tests`
2. **Isolamento de Banco**: Verificar que `memory/barsi_memory.db` não foi alterado.
3. **Suíte de Testes & Cobertura**:
   ```bash
   python -m pytest barsi_quant_ia/tests/ -q --cov=barsi --cov-fail-under=40
   ```

### Fase 5: Persistência Local
- Adicionar os arquivos ao Git.
- Criar commit semântico local (`feat:`, `fix:`, `sec:`, `ci:`).
- **NUNCA executar `git push`** a menos que o usuário dê a instrução direta expressa.
