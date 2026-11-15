# 🔐 Política de Segurança — Barsi Quant IA

Este documento descreve como reportar vulnerabilidades e como agir em caso de vazamento acidental de dados sensíveis no repositório.

---

## 1. Dados classificados como sensíveis

| Classe | Exemplos | Onde deve viver |
|---|---|---|
| **PII de terceiros** | Nome completo, CPF, número de conta, endereço | `.env` local, `extratos/`, `memory/*.db` — todos `.gitignore` |
| **Credenciais** | Senha SSH, token HMAC, API keys, `credentials.json` | `.env` local, `chmod 600` |
| **Infra interna** | IP da TV Box, hostname doméstico, MAC address | `.env` local, nunca em código |
| **Financeiro** | Saldos, carteira, histórico de compras | SQLite local + Google Sheets privado |

---

## 2. Vazamento de credenciais (procedimento)

Se você commitou acidentalmente um dos itens da seção 1:

### Passo 1 — Rotacione imediatamente
```bash
# Senha SSH da TV Box
ssh SEU_USUARIO@SEU_IP 'passwd'   # nova senha

# Variáveis do .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # novo token HMAC
```

### Passo 2 — Verifique o histórico do Git
```bash
# Encontra todos os commits que tiveram o arquivo/dado
git log --all -S "NOME_TITULAR" --oneline
git log --all -S "192.168.0." --oneline
# Se retornar algum commit, o dado está no histórico
```

### Passo 3 — Purge o histórico (caso tenha ido para o remoto)
```bash
# Instale git-filter-repo
pip install git-filter-repo

# Remove um arquivo específico do histórico inteiro
git filter-repo --path .env --path credentials.json --invert-paths

# Remove por padrão de texto em qualquer arquivo
git filter-repo --replace-text <(echo "NOME_COMPLETO_TITULAR==>TITULAR")
git filter-repo --replace-text <(echo "IP_PRIVADO_REAL==>192.0.2.1")
git filter-repo --replace-text <(echo "usuario_pessoal@==>user@")

# Force-push e avise todos os colaboradores
git push --force --all
```

> ⚠️ **Aviso:** `git filter-repo` reescreve o histórico. Todos os colaboradores precisam re-clonar. Se o repositório é público (GitHub), considere deletar e recriar para garantir que forks antigos não tenham cache.

### Passo 4 — Revogue credenciais antigas
1. Google Cloud Console → revogar Service Account `credentials.json`
2. Cloudflare → revogar tokens R2
3. SSH da TV Box → `~/.ssh/authorized_keys` (remover chave antiga, se houver)

### Passo 5 — Documente no CHANGELOG
```markdown
## [Unreleased] - Security
- **BREAKING:** Purgado histórico do Git para remover PII (ver SECURITY.md §2)
```

---

## 3. Reportar uma vulnerabilidade

Abra um GitHub Security Advisory privado (não uma issue pública) com:
1. Versão afetada
2. Passos para reproduzir
3. Impacto estimado (confidencialidade, integridade, disponibilidade)
4. Sugestão de fix (se tiver)

Resposta esperada em até 72h.

---

## 4. Como o projeto protege os dados por padrão

- `.env` está no `.gitignore` e tem `chmod 600` recomendado
- `.env.example` contém apenas placeholders vazios
- Logs estruturados não incluem `<MEMO>` completo nem nome de titular
- `memory/*.db` fica fora do Git
- `extratos/*.ofx` fica fora do Git
- `credentials.json` fica fora do Git
- Backup no R2 exige `BARSI_R2_ENABLED=true` + credenciais explícitas

---

## 5. Modelo de Autenticação, 2FA e Revogação de Sessões

### A. Política de 2FA para Administrador (Nudge vs. Enforcement)
- **Uso Pessoal em Edge Hardware (Tailscale / TV Box)**: O 2FA opera em regime de **soft nudge** (redirecionamento orientativo para `/settings#2fa` no login e banner visual persistente). Não há hard lockout de navegação para evitar interrupções catastróficas em caso de perda temporária de autenticador.
- **Ambiente Multi-Usuário / Exposição Pública**: Caso novos administradores sejam adicionados ou a aplicação seja exposta sem VPN, recomenda-se ativar middleware de bloqueio estrito em rotas de mutação financeira.

### B. Defesa em Profundidade na Desativação do 2FA
- A desativação de 2FA via Web (`/api/auth/2fa/disable`) exige **prova de posse** via código TOTP ou código de recuperação de emergência (backup code).
- O método `UserRepo.disable_totp` possui `bypass_verification=False` como padrão seguro; apenas rotinas administrativas de linha de comando com acesso root/SSH (`barsi user-disable-2fa`) podem informar explicitamente `bypass_verification=True`.

### C. Revogação Instantânea de Sessões Antigas (`session_version` + JTI)
- Trocas de senha (`/api/user/change-password`, `user-pass-reset`) ou desativação de segundo fator incrementam o campo `session_version` no banco de dados.
- Tokens emitidos em sessões concorrentes ou dispositivos secundários com versões antigas de sessão são rejeitados de imediato por `get_current_user`, garantindo encerramento forçado de acessos antigos (session invalidation).
- O logout explícito (`/logout`) revoga o identificador exclusivo `jti` na tabela `session_revocation`.

### D. Invalidação Imediata de Tokens em Exclusão de Usuário
- Tokens de usuários excluídos do banco de dados são rejeitados de imediato em `get_current_user` (verificação explícita `if not user_db: return None`), impedindo qualquer persistência de acesso após desativação ou exclusão de conta.
- Troca de senha (`/api/user/change-password`) e desativação de 2FA (`/api/auth/2fa/disable`) reemitem o token atualizado para o dispositivo que realizou a operação, mantendo a sessão atual ativa enquanto encerram imediatamente todas as demais sessões em outros dispositivos.

---

## 6. Arquitetura de ML Seguro na Borda (Isolation Forest OFX)

### A. Separação Estrita entre Treino e Inferência
- **Treino Offline no PC**: O treinamento do modelo `IsolationForest` utiliza scikit-learn (`barsi.training.anomaly_trainer` / `python -m barsi ml-retrain-anomaly`) em ambiente de desenvolvimento, combinando dados reais anonimizados com baseline sintético.
- **Inferência Ultraleve na TV Box (Edge)**: O modelo é compilado em matrizes NumPy puras compactadas (`memory/anomaly_model.npz`, < 100 KB). O runtime da TV Box executa apenas operações vetoriais de travessia em árvore (`AnomalyDetector`), sem dependência de scikit-learn ou scipy, garantindo latência < 2ms e consumo de memória irrelevante (< 1 MB).

### B. Proteção contra Envenenamento de Dados (Poisoning Protection)
- Transações com feedback do usuário marcado como `OK_ANOMALIA` são automaticamente excluídas do pipeline de treino subsequente (`load_transactions_from_db`), prevenindo viés ou envenenamento deliberado da distribuição base.
- O isolamento multi-tenant restringe consultas, contagens e mutações de transações (`user_id`), impedindo vazamento de dados de fluxo financeiro entre usuários.

### C. Resiliência e Fallback sem Alertas Falsos
- Na ausência do arquivo compilado `.npz`, o sistema ativa modo fallback com log de aviso (`anomaly_model_missing_fallback`), aplicando apenas heurística de segurança para valores extremamente anômalos (> R$ 10.000) e suspendendo disparos automáticos de notificações para evitar ruído ou falsos alertas no Telegram.

### D. Semântica de Feedback do Usuário em Transações
- `OK_ANOMALIA`: Falso positivo confirmado pelo usuário. Arquiva os alertas ativos vinculados ao `fitid` exato e exclui a transação do dataset de retreinamento do Isolation Forest para evitar contaminação do perfil de gastos.
- `NORMAL`: Transação confirmada como operação legítima e regular. Mantida no histórico e incluída no dataset de retreinamento como comportamento padrão do investidor.
- `REVISAR`: Transação sob análise manual ou com pendência de conciliação. Mantém o alerta ativo no radar para revisão posterior e não altera o pipeline de aprendizado.

### E. Ciclo de Vida dos Modelos de ML no Deploy (PC $\rightarrow$ TV Box)
Os modelos de Machine Learning compilados (`memory/anomaly_model.npz` e `memory/payout_weights.json`) **NÃO** são versionados no Git por serem artefatos binários gerados pelo retreinamento:
1. **Treino Offline no PC**:
   - `python -m barsi ml-retrain-anomaly`: Gera o modelo vetorial de anomalias compilado (~82 KB).
   - `python -m barsi ml-retrain-payout`: Gera os coeficientes lineares interpretáveis com RLHF (< 2 KB).
2. **Deploy na Edge (TV Box)**:
   - `python -m barsi sync-tvbox --upload-ml`: Sincroniza exclusivamente os artefatos compilados de ML sem sobrescrever o banco de dados canônico SQLite da TV Box (em estrita conformidade com o Path B).
   - Alternativamente, `python -m barsi sync-tvbox --upload-db` envia tanto o banco SQLite quanto os artefatos de ML.
3. **Validação de Prontidão Operacional**:
   - `curl http://localhost:8000/health`: Valida que `anomaly_detector_loaded: true` e o threshold correspondente está ativo.
   - O serviço `barsi-api.service` reiniciado pelo sync garante a recarga atômica dos singletons em memória.
4. **Fallback Gracioso**:
   - Se os modelos estiverem ausentes na TV Box, o sistema opera automaticamente nos modos seguros: heurística financeira para anomalias (> R$ 10.000 sem ruído de notificação) e pesos baseline conservadores Luiz Barsi para sustentabilidade de proventos.

---

## 7. Arquitetura de Sustentabilidade de Proventos v2 (Regressão Logística com RLHF)

### A. Modelo Probabilístico Interpretável (White-Box ML)
- **Substituição de TinyMLP Oposta por Regressão Logística L2**: A versão 2 (`PayoutScorer` / `DividendSustainabilityScorer`) substitui a heurística de rede neural simples por uma Regressão Logística com regularização L2 calibrada via scikit-learn (`barsi.training.payout_trainer` / `python -m barsi ml-retrain-payout`).
- **Auditabilidade Total das Decisões**: Cada característica financeira possui um coeficiente linear $w_i$ explícito, permitindo expor exatamente a contribuição de cada indicador no logit final ($z = b + \sum w_i x_i$), eliminando decisões opacas e explicando ao investidor quais fundamentos apoiam ou degradam a tese de dividendos.

### B. Aprendizado por Reforço com Feedback Humano (RLHF)
- O pipeline de retreinamento consome os feedbacks explícitos da tabela canônica `historico_analises.feedback_usuario` (`CONCORDO` $\rightarrow 1.0$, `DISCORDO` $\rightarrow 0.0$), combinando o dataset curado dos setores perenes B.E.S.T. com a intuição e critérios específicos do investidor.

### C. Inferência em Borda (Edge TV Box) e Fallback Seguro
- Os pesos aprendidos ($w \in \mathbb{R}^8$ e intercept $b$) são salvos em arquivo JSON ultra-leve (`memory/payout_weights.json`, < 2 KB).
- O runtime de inferência executa em puro Python e NumPy sem carregar bibliotecas externas pesadas, operando com latência < 0.1ms.
- Na ausência do arquivo de pesos, o sistema opera em modo fallback gracioso com pesos baseline conservadores derivados diretamente do método Luiz Barsi Filho.
