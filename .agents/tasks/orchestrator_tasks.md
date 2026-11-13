# 📋 Backlog e Fila de Tarefas do Orquestrador

Este documento serve como a fonte de verdade para o acompanhamento das tarefas ativas e do backlog do ecossistema de agentes.

## 🟢 Tarefas Concluídas Recentemente
- [x] **Arquitetura FITID-first na Conciliação OFX**: Deduplicação e guarda de meses anteriores.
- [x] **Isolamento Estrito de Testes**: Redirecionamento 100% automático para `tmp_path` no `conftest.py`.
- [x] **Arquivamento de Extratos**: Mover extratos processados para `extratos/processados/`.
- [x] **Segurança Nível 1 & 2**: Extração de inline JS para arquivos estáticos em `/static/js/*.js` e mitigação de XSS via `esc()`.
- [x] **Proteção CSRF**: Middleware com HMAC-SHA256, cookies SameSite e interceptor global no `common.js`.
- [x] **CI & Suporte a Act**: Pipeline modernizado em `.github/workflows/ci.yml`, mapeamento em `.secrets` e `.actrc`.
- [x] **Estrutura de Agentes (.agents)**: Orquestrador, subagentes especializados, skills de economia de tokens com Graphify e Repomix.

## 🟡 Em Planejamento / Próximos Passos
- [ ] **RBAC Modular no Painel Admin**: Gestão granular de páginas e permissões de tarefas por usuário/perfil no banco de dados.
- [ ] **Automatização de Alertas de Oportunidades B.E.S.T.**: Notificações programadas quando ativos atingirem margem de segurança > 15%.
- [ ] **Auditoria Semanal de Despesas**: Relatório automatizado de divergências orçamentárias vs extrato bancário.
