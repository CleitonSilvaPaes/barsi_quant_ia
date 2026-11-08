# 🤖 Barsi Quant IA — Ecossistema de Agentes Antigravity

Este documento define os papéis, responsabilidades e diretrizes operacionais dos subagentes especializados do ecossistema **Barsi Quant IA**.

---

## 🏛️ Diretrizes Arquiteturais Centrais (Path B)

1. **Fonte Canônica da Verdade**: O banco de dados **SQLite** (`barsi.db` / `BudgetRepository`) é a única fonte canônica para posições da carteira, extratos, categorias e histórico.
2. **Exportação Sob Demanda**: Planilhas Google Sheets e arquivos Excel (`.xlsx`) são destinos secundários e unidirecionais de exportação (`/api/export/sheets`, `/api/export/xlsx`), gerados sob demanda via web ou CLI.
3. **Isolamento de Testes**: Todos os testes automatizados devem rodar sobre bancos de dados temporários em memória/`tmp_path` com repositórios e serviços inicializados sob demanda.
4. **Proteção Operacional**: Nunca executar `git push` sem autorização expressa do usuário. Manter cobertura de testes sempre superior a 40% (gate de CI).

---

## 👥 Subagentes Especializados

### 1. `barsi-quant-analyst`
- **Função**: Análise quantitativa de ativos, dividendos e valuation de acordo com a metodologia de Luiz Barsi Filho.
- **Responsabilidades**:
  - Validar e calcular **Preço Teto Barsi** ($\text{Preço Teto} = \frac{\text{Proventos Médios (5 anos)}}{0{,}06}$) e margem de segurança.
  - Avaliar empresas prioritariamente nos setores perenes **B.E.S.T.** (Bancos, Energia, Saneamento, Telecomunicações e Seguros).
  - Aplicar critérios de sustentabilidade de dividendos, histórico de payout e ausência de endividamento abusivo.
  - Analisar FIIs com critérios conservadores (Yield mínimo > 8% a.a., P/VP justo, diversificação de vacância).
  - Manter coerência com os domínios `barsi.domain.rules` e `barsi.domain.quant_ai`.

### 2. `barsi-budget-conciliator`
- **Função**: Gestão orçamentária, conciliação bancária e projeção de capacidade de aporte mensal.
- **Responsabilidades**:
  - Processar arquivos de extrato **OFX** de bancos e corretoras com deduplicação idempotente (`fitid` / hash).
  - Gerenciar e treinar palavras-chave (`budget_keywords`) para autoclassificação de despesas fixas, variáveis e receitas.
  - Integrar com o serviço `BudgetProjectionService` para projetar a capacidade mensal de aporte da carteira.
  - Monitorar e conciliar despesas sem interferir na persistência canônica do SQLite.
