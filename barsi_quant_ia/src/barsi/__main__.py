"""Interface de Linha de Comando (CLI) para o Sistema Previdenciário Barsi."""
import typer
import asyncio
import uvicorn
from typing import Optional
from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.services.sync_service import SyncService
from barsi.infra.logging import setup_logging, logger

app = typer.Typer(name="barsi", help="Sistema Previdenciário Barsi - CLI Quant & Edge")

@app.command()
def run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Executa o cálculo sem persistir alterações"),
    ticker: Optional[str] = typer.Option(None, "--ticker", help="Executa o ciclo para um único ativo (ex: BBAS3)")
):
    """Executa o pipeline completo de sincronização e enriquecimento Barsi."""
    setup_logging(settings.log_level)
    
    async def _main():
        market = AdapterFactory.create_market_adapter(settings)
        news = AdapterFactory.create_news_adapter(settings)
        ai = AdapterFactory.create_ai_adapter(settings)
        sheets = AdapterFactory.create_sheets_adapter(settings)
        history = AdapterFactory.create_history_adapter(settings)
        notifier = AdapterFactory.create_notification_adapter(settings)

        service = SyncService(
            market=market,
            news=news,
            ai=ai,
            sheets=sheets,
            history=history,
            notifier=notifier,
            max_concurrency=settings.max_concurrency
        )
        summary = await service.execute_run(dry_run=dry_run, target_ticker=ticker)
        typer.echo(f"\n[OK] Run concluída! ID: {summary.run_id}")
        typer.echo(f"     Ativos analisados: {summary.tickers_analyzed}")
        typer.echo(f"     Duração: {summary.duration_ms} ms | RAM RSS: {summary.memory_peak_mb} MB")
        if summary.errors:
            typer.echo(f"     Erros: {summary.errors}")

    asyncio.run(_main())

@app.command()
def api(
    host: str = typer.Option("0.0.0.0", "--host", help="Host de binding"),
    port: int = typer.Option(8000, "--port", help="Porta HTTP")
):
    """Inicia o servidor HTTP minimalista para integração com Cloudflare Tunnel."""
    setup_logging(settings.log_level)
    uvicorn.run("barsi.api.http:app", host=host, port=port, reload=False, log_level="warning")

@app.command()
def extratos(
    dir_path: str = typer.Option("extratos", "--dir", help="Pasta com arquivos de extrato .ofx"),
    sync_sheet: bool = typer.Option(False, "--sync-sheet", help="Sincroniza valores consolidados na aba Budget_Manager_Fluxo"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra resultado da conciliação sem persistir no SQLite nem no Sheets"),
):
    """Lê e processa os extratos bancários da pasta extratos/ para conciliação Barsi."""
    from barsi.services.ofx_service import OFXParserService
    OFXParserService.reload_keywords_from_db()
    resumos = OFXParserService.processar_diretorio_extratos(dir_path)
    if not resumos:
        typer.echo(f"[!] Nenhum extrato .ofx encontrado em '{dir_path}'")
        return

    typer.echo("\n" + "="*65)
    typer.echo("    CONCILIAÇÃO BANCÁRIA & APORTES (MÉTODO LUIZ BARSI)        ")
    typer.echo("="*65)
    
    for mes, r in sorted(resumos.items()):
        if r.total_entradas == 0 and r.total_saidas == 0:
            continue
        typer.echo(f"\n📅 MÊS DE REFERÊNCIA: {mes}")
        typer.echo(f"   (+) Salário CLT / Renda       : R$ {r.salario_entradas:>9.2f}")
        typer.echo(f"   (+) Proventos B3 / Dividendos : R$ {r.proventos_recebidos:>9.2f}")
        if r.outras_entradas > 0:
            typer.echo(f"   (+) Outras Entradas           : R$ {r.outras_entradas:>9.2f}")
        typer.echo(f"   ──> TOTAL ENTRADAS REALIZADAS : R$ {r.total_entradas:>9.2f}")
        
        typer.echo(f"\n   💼 INVESTIMENTOS REALIZADOS (BARSI B.E.S.T.):")
        typer.echo(f"       • Ações B.E.S.T. Total    : R$ {r.compras_acoes_barsi:>9.2f}")
        typer.echo(f"       • FIIs (Papel/Tijolo)     : R$ {r.compras_fiis:>9.2f}")
        typer.echo(f"       ──> TOTAL APORTADO B3     : R$ {r.total_investimentos:>9.2f}")
        for t, val in r.detalhes_ativos.items():
            typer.echo(f"           - {t:<6}                : R$ {val:>8.2f}")
        
        typer.echo(f"\n   🛡️ SAÍDAS OBRIGATÓRIAS & COLCHÃO:")
        typer.echo(f"       • Fatura Nubank Fechada   : R$ {r.faturas_cartao:>9.2f}")
        typer.echo(f"       • Moradia & Família (Pix) : R$ {r.moradia_e_familia:>9.2f}")
        typer.echo(f"       • Tributos (DAS/Sefaz)    : R$ {r.tributos_e_taxas:>9.2f}")
        if r.cooperativa_ailos > 0:
            typer.echo(f"       • Cooperativa Ailos       : R$ {r.cooperativa_ailos:>9.2f}")
        typer.echo(f"       • Colchão de Giro (RDB)   : R$ {r.reserva_rdb:>9.2f}")
        if r.despesas_variaveis > 0:
            typer.echo(f"       • Variáveis (Débito/Dia)  : R$ {r.despesas_variaveis:>9.2f}")
        typer.echo(f"   ──> TOTAL DE SAÍDAS EFETIVAS  : R$ {r.total_saidas:>9.2f}")
        typer.echo(f"   💵 SALDO LIVRE / RESIDUAL     : R$ {r.saldo_livre:>9.2f}")

    if dry_run:
        typer.echo("\n[DRY-RUN] Simulação concluída. Nada foi persistido no SQLite nem no Google Sheets.")
        return

    # Persistência no SQLite via caller (mantém SRP: parser não tem side-effects de I/O)
    try:
        from barsi.adapters.history.budget_repository import BudgetRepository
        repo = BudgetRepository(settings.sqlite_db_path)
        todas = [t for r in resumos.values() for t in r.transacoes]
        if todas:
            repo.persist_transacoes(todas)
        BUCKETS_PERSISTIR = {
            "Salário / Renda Principal":     lambda r: r.salario_entradas,
            "Proventos B3":                 lambda r: r.proventos_recebidos,
            "Aporte Ações/FIIs":            lambda r: r.compras_acoes_barsi,
            "Fatura Cartão Nubank":         lambda r: r.faturas_cartao,
            "Reserva / RDB":                lambda r: r.reserva_rdb,
            "Tributos & Taxas":             lambda r: r.tributos_e_taxas,
            "Compromisso Familiar / Moradia": lambda r: r.moradia_e_familia,
            "Cooperativa Ailos":            lambda r: r.cooperativa_ailos,
            "Despesas Variáveis":           lambda r: r.despesas_variaveis,
        }
        for mes, res in resumos.items():
            for categoria, getter in BUCKETS_PERSISTIR.items():
                val = getter(res)
                if val > 0:
                    repo.record_budget_entry(mes, categoria, val, source="ofx")
    except Exception as exc:
        logger.warning("persist_transacoes_failed", error=str(exc))

    if sync_sheet:
        typer.echo("\n[+] Sincronizando com Google Sheets (Budget_Manager_Fluxo)...")
        from barsi.config import Settings
        from barsi.adapters.factory import AdapterFactory
        cfg = Settings()
        sheets_adapter = AdapterFactory.create_sheets_adapter(cfg)
        if hasattr(sheets_adapter, "sync_budget_manager"):
            sheets_adapter.sync_budget_manager(resumos)
            typer.echo("[✔] Sincronização orçamentária concluída com sucesso no Google Sheets!")
        else:
            typer.echo("[!] Adapter atual não suporta sync de orçamento.")

@app.command()
def sync_tvbox(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP da TV Box na rede (lê de BARSI_TVBOX_IP se nulo)"),
    user: Optional[str] = typer.Option(None, "--user", help="Usuário SSH (lê de BARSI_TVBOX_USER se nulo)"),
    password: Optional[str] = typer.Option(None, "--pass", help="Senha SSH (lê de BARSI_TVBOX_PASS se nulo)")
):
    """Sincroniza todos os fontes, modelos, templates e extratos da máquina local para a TV Box."""
    import os, glob, getpass, paramiko
    from scp import SCPClient

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    if not target_ip or not target_user:
        typer.echo("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env (ou passe --ip/--user)")
        raise typer.Exit(code=1)
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    typer.echo(f"\n[+] Conectando na TV Box ({target_user}@{target_ip}) para sincronização completa...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=8)
    except Exception as exc:
        typer.echo(f"[!] Falha na conexão SSH com a TV Box: {exc}")
        return

    target_homes = [settings.tvbox_home]
    alt_home = f"/home/{target_user}/barsi_quant_ia"
    if alt_home not in target_homes:
        target_homes.append(alt_home)

    root_src = "barsi_quant_ia/src/barsi" if os.path.exists("barsi_quant_ia/src/barsi") else "src/barsi"
    extratos_list = glob.glob("extratos/*.ofx") or glob.glob("barsi_quant_ia/extratos/*.ofx")
    base_dir = "barsi_quant_ia" if os.path.exists("barsi_quant_ia") else "."
    cred_p = "barsi_quant_ia/credentials.json" if os.path.exists("barsi_quant_ia/credentials.json") else "credentials.json"
    env_p = ".env" if os.path.exists(".env") else "barsi_quant_ia/.env"

    for remote_home in target_homes:
        # Garante diretórios essenciais na TV Box com permissão para o usuário
        init_cmd = (
            f"sudo mkdir -p '{remote_home}/extratos' '{remote_home}/src/barsi' '{remote_home}/memory' '{remote_home}/cache' "
            f"&& sudo chown -R {target_user}:{target_user} '{remote_home}' "
            f"&& sudo chmod -R 777 '{remote_home}/memory' '{remote_home}/cache' '{remote_home}/extratos'"
        )
        stdin, stdout, stderr = ssh.exec_command(init_cmd, get_pty=True)
        stdin.write(f"{target_pass}\n")
        stdin.flush()
        stdout.channel.recv_exit_status()

        # Prepara lista de arquivos do código fonte
        files_to_sync = []
        remote_dirs = set()
        for root, _, files in os.walk(root_src):
            for file in files:
                if file.endswith((".py", ".html", ".json", ".sql")) and "__pycache__" not in root:
                    local_p = os.path.join(root, file)
                    rel_p = os.path.relpath(local_p, root_src).replace("\\", "/")
                    remote_p = f"{remote_home}/src/barsi/{rel_p}"
                    files_to_sync.append((local_p, remote_p))
                    remote_dirs.add(os.path.dirname(remote_p))

        # Garante que todas as subpastas remotas existam antes do SCP
        for rdir in sorted(remote_dirs):
            _, out, _ = ssh.exec_command(f"mkdir -p '{rdir}'")
            out.channel.recv_exit_status()

        with SCPClient(ssh.get_transport()) as scp:
            # 1. Extratos
            typer.echo(f"    - Sincronizando extratos bancários (.ofx) para {remote_home}...")
            for f in extratos_list:
                try:
                    scp.put(f, f"{remote_home}/extratos/{os.path.basename(f)}")
                except Exception as e:
                    typer.echo(f"    [!] Aviso ao enviar extrato {f}: {e}")

            # 2. Código fonte principal (inclui templates HTML)
            typer.echo(f"    - Sincronizando código fonte e templates HTML para {remote_home}...")
            for local_p, remote_p in files_to_sync:
                try:
                    scp.put(local_p, remote_p)
                except Exception as e:
                    typer.echo(f"    [!] Aviso ao enviar {local_p}: {e}")

            # 3. Metadados e Docker
            for meta_file in ["pyproject.toml", "README.md", "Dockerfile", "docker-compose.yml"]:
                meta_path = os.path.join(base_dir, meta_file)
                if os.path.exists(meta_path):
                    try:
                        scp.put(meta_path, f"{remote_home}/{meta_file}")
                    except Exception:
                        pass

            # 4. Credentials se existir
            if os.path.exists(cred_p):
                try:
                    scp.put(cred_p, f"{remote_home}/credentials.json")
                except Exception:
                    pass

            # 5. .env local para TV Box
            if os.path.exists(env_p):
                try:
                    scp.put(env_p, f"{remote_home}/.env")
                except Exception:
                    pass

            # 6. Banco SQLite local consolidado com histórico e paridade
            db_candidates = ["memory/barsi_memory.db", "barsi_quant_ia/memory/barsi_memory.db"]
            for db_cand in db_candidates:
                if os.path.exists(db_cand):
                    try:
                        typer.echo(f"    - Sincronizando banco SQLite ({db_cand}) com histórico e paridade...")
                        scp.put(db_cand, f"{remote_home}/memory/barsi_memory.db")
                        break
                    except Exception as e:
                        typer.echo(f"    [!] Aviso ao sincronizar SQLite memory DB: {e}")

            # 7. Excel local espelhado
            xlsx_candidates = ["carteira_barsi_quant_ia.xlsx", "barsi_quant_ia/carteira_barsi_quant_ia.xlsx"]
            for xlsx_cand in xlsx_candidates:
                if os.path.exists(xlsx_cand):
                    try:
                        typer.echo(f"    - Sincronizando carteira Excel (.xlsx)...")
                        scp.put(xlsx_cand, f"{remote_home}/carteira_barsi_quant_ia.xlsx")
                        break
                    except Exception as e:
                        typer.echo(f"    [!] Aviso ao sincronizar XLSX: {e}")

    typer.echo("    - Reiniciando serviço barsi-api na TV Box...")
    stdin, stdout, stderr = ssh.exec_command("sudo systemctl restart barsi-api.service", get_pty=True)
    stdin.write(f"{target_pass}\n")
    stdin.flush()
    stdout.channel.recv_exit_status()
    ssh.close()
    typer.echo("[✔] TV Box sincronizada com sucesso e serviço reiniciado!\n")

@app.command()
def tvbox(
    ip: Optional[str] = typer.Option(None, "--ip", help="IP da TV Box na rede (lê de BARSI_TVBOX_IP se nulo)"),
    user: Optional[str] = typer.Option(None, "--user", help="Usuário SSH (lê de BARSI_TVBOX_USER se nulo)"),
    password: Optional[str] = typer.Option(None, "--pass", help="Senha SSH (lê de BARSI_TVBOX_PASS se nulo)")
):
    """Consulta a telemetria ao vivo da TV Box (Temperatura, RAM, Uptime e Timer)."""
    import getpass, paramiko

    target_ip = ip or settings.tvbox_ip
    target_user = user or settings.tvbox_user
    if not target_ip or not target_user:
        typer.echo("[!] Configure BARSI_TVBOX_IP e BARSI_TVBOX_USER no .env")
        raise typer.Exit(code=1)
    target_pass = password or settings.tvbox_pass or getpass.getpass(f"Senha SSH para {target_user}@{target_ip}: ")

    typer.echo(f"\n[+] Conectando na TV Box ({target_user}@{target_ip})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(target_ip, username=target_user, password=target_pass, timeout=5)
    except Exception as exc:
        typer.echo(f"[!] Falha na conexão SSH com a TV Box: {exc}")
        return

    # Consulta temperatura
    _, stdout, _ = ssh.exec_command("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || vcgencmd measure_temp")
    temp_raw = stdout.read().decode().strip()
    try:
        temp_c = f"{float(temp_raw)/1000.0:.1f} °C" if temp_raw.isdigit() else temp_raw
    except Exception:
        temp_c = "N/D"

    # Consulta memória RAM
    _, stdout, _ = ssh.exec_command("free -h | awk '/Mem:/ {print $3 \" usados de \" $2 \" (\" $7 \" disponíveis)\"}'")
    ram_info = stdout.read().decode().strip()

    # Consulta Uptime
    _, stdout, _ = ssh.exec_command("uptime -p")
    uptime_info = stdout.read().decode().strip()

    # Consulta status do timer
    _, stdout, _ = ssh.exec_command("systemctl is-active barsi-sync.timer 2>/dev/null")
    timer_status = stdout.read().decode().strip()

    # Consulta última execução do service
    _, stdout, _ = ssh.exec_command("systemctl show barsi.service --property=ActiveEnterTimestamp,ExecMainExitTimestamp,SubState --no-pager")
    service_props = stdout.read().decode().strip()

    ssh.close()

    typer.echo("="*55)
    typer.echo("      TELEMETRIA DA TV BOX (DEBIAN ARM64 - S905W)      ")
    typer.echo("="*55)
    typer.echo(f"🌡️  Temperatura CPU : {temp_c}")
    typer.echo(f"🧠 Memória RAM     : {ram_info}")
    typer.echo(f"⏱️  Tempo de Atividade: {uptime_info}")
    typer.echo(f"⏰ Systemd Timer   : {'● ATIVO (Seg-Sex 09h-18h)' if timer_status == 'active' else '○ INATIVO'}")
    typer.echo("\n📋 Última Execução:")
    for line in service_props.splitlines():
        if line:
            typer.echo(f"   • {line}")
    typer.echo("="*55 + "\n")

@app.command()
def user_add(
    username: str = typer.Argument(..., help="Nome de usuário"),
    password: str = typer.Argument(..., help="Senha de acesso"),
    role: str = typer.Option("leitor", "--role", help="Perfil: admin | investidor | leitor")
):
    """Cria um usuário criptografado no banco SQLite com perfil de acesso (RBAC)."""
    from barsi.adapters.history.user_repository import UserRepository
    repo = UserRepository(settings.sqlite_db_path)
    if role not in ["admin", "investidor", "leitor"]:
        typer.echo("[!] Perfil inválido! Escolha: admin | investidor | leitor")
        return
    ok = repo.create_user(username, password, role=role)
    if ok:
        typer.echo(f"[✔] Usuário '{username}' criado com sucesso no banco SQLite! Perfil: {role.upper()}")
    else:
        typer.echo(f"[!] Erro: Usuário '{username}' já existe no banco.")

@app.command()
def user_list():
    """Lista todos os usuários e perfis cadastrados no banco de dados SQLite."""
    from barsi.adapters.history.user_repository import UserRepository
    repo = UserRepository(settings.sqlite_db_path)
    rows = repo.list_users()

    typer.echo("\n=======================================================")
    typer.echo("     USUÁRIOS E PERFIS DE ACESSO (SQLITE RBAC)        ")
    typer.echo("=======================================================")
    if not rows:
        typer.echo("Nenhum usuário cadastrado.")
    for r in rows:
        typer.echo(f" • ID: {r['id']} | Usuário: {r['username']:<12} | Perfil: {r['role'].upper():<10} | Permissões: {r['permissoes']}")
    typer.echo("=======================================================\n")

budget_app = typer.Typer(name="budget", help="Gestão e reconciliação de orçamento e despesas recorrentes (Zero Magic Numbers)")
app.add_typer(budget_app)

@budget_app.command("seed")
def budget_seed():
    """Popula as despesas recorrentes iniciais no banco SQLite."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    count = repo.seed_defaults()
    typer.echo(f"[✔] {count} despesas recorrentes semeadas com sucesso no banco SQLite!")

@budget_app.command("list")
def budget_list():
    """Lista todas as despesas recorrentes e vigências cadastradas no banco SQLite."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    rows = repo.list_recurring()
    typer.echo("\n" + "="*80)
    typer.echo("       DESPESAS RECORRENTES & ORÇAMENTO (SQLITE SOURCE OF TRUTH)        ")
    typer.echo("="*80)
    if not rows:
        typer.echo("Nenhuma despesa recorrente cadastrada.")
    for r in rows:
        v_to = r['valid_to'] or 'VIGENTE'
        typer.echo(f" • [{r['kind']:<9}] {r['category']:<32} : R$ {r['amount']:>8.2f} (de {r['valid_from']} até {v_to})")
    typer.echo("="*80 + "\n")

@budget_app.command("set")
def budget_set(
    category: str = typer.Argument(..., help="Nome da categoria (ex: 'Aluguel')"),
    amount: float = typer.Argument(..., help="Valor mensal da despesa (ex: 550.00)"),
    valid_from: str = typer.Argument(..., help="Data de início da vigência no formato YYYY-MM-DD (ex: '2027-01-01')"),
    valid_to: Optional[str] = typer.Option(None, "--valid-to", help="Data de fim da vigência (opcional)"),
    kind: str = typer.Option("fixa", "--kind", help="Tipo: fixa | variavel | tributo | reserva | investimento | entrada"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Observações")
):
    """Cria ou atualiza uma despesa recorrente, fechando automaticamente a vigência anterior."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    repo.upsert_recurring(category, amount, valid_from, valid_to=valid_to, kind=kind, source="manual", notes=notes)
    typer.echo(f"[✔] Despesa '{category}' configurada para R$ {amount:.2f} a partir de {valid_from}!")

@budget_app.command("sync")
def budget_sync(
    dir_path: str = typer.Option("extratos", "--dir", help="Pasta com extratos .ofx")
):
    """Executa a conciliação completa de extratos e sincroniza a aba Budget_Manager_Fluxo."""
    from barsi.services.ofx_service import OFXParserService
    from barsi.adapters.factory import AdapterFactory
    resumos = OFXParserService.processar_diretorio_extratos(dir_path)
    if not resumos:
        typer.echo(f"[!] Nenhum extrato encontrado em '{dir_path}'")
        return

    # Persistência no SQLite via caller
    try:
        from barsi.adapters.history.budget_repository import BudgetRepository
        repo = BudgetRepository(settings.sqlite_db_path)
        todas = [t for r in resumos.values() for t in r.transacoes]
        if todas:
            repo.persist_transacoes(todas)
        BUCKETS_PERSISTIR = {
            "Salário / Renda Principal":     lambda r: r.salario_entradas,
            "Proventos B3":                 lambda r: r.proventos_recebidos,
            "Aporte Ações/FIIs":            lambda r: r.compras_acoes_barsi,
            "Fatura Cartão Nubank":         lambda r: r.faturas_cartao,
            "Reserva / RDB":                lambda r: r.reserva_rdb,
            "Tributos & Taxas":             lambda r: r.tributos_e_taxas,
            "Compromisso Familiar / Moradia": lambda r: r.moradia_e_familia,
            "Cooperativa Ailos":            lambda r: r.cooperativa_ailos,
            "Despesas Variáveis":           lambda r: r.despesas_variaveis,
        }
        for mes, res in resumos.items():
            for categoria, getter in BUCKETS_PERSISTIR.items():
                val = getter(res)
                if val > 0:
                    repo.record_budget_entry(mes, categoria, val, source="ofx")
    except Exception as exc:
        logger.warning("persist_transacoes_failed", error=str(exc))

    sheets_adapter = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets_adapter, "sync_budget_manager"):
        sheets_adapter.sync_budget_manager(resumos)
        typer.echo("[✔] Orçamento sincronizado com o Google Sheets com base nas despesas do SQLite!")

@budget_app.command("audit")
def budget_audit(
    mes_ano: str = typer.Argument(..., help="Competência para auditoria no formato YYYY-MM (ex: '2026-10')")
):
    """Verifica integridade cruzada: transações dos extratos × budget_entries (conciliado)."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)

    INCOME_CATEGORIES = {
        "Salário / Renda Principal",
        "Proventos B3",
        "Rendimento NuConta",
        "Outras Entradas",
    }
    CATEGORY_MAPPING = {
        "Despesas Gerais": "Despesas Variáveis",
        "Transferência Familiar": "Compromisso Familiar / Moradia",
    }

    with repo._conn() as conn:
        # 1. Totalizações em budget_entries para o mês
        rows_be = conn.execute(
            """
            SELECT c.name as categoria, b.amount
              FROM budget_entries b
              JOIN expense_categories c ON c.id = b.category_id
             WHERE b.mes_ano = ?
            """,
            (mes_ano,)
        ).fetchall()
        soma_be = {r["categoria"]: round(float(r["amount"]), 2) for r in rows_be}

        # Fallback para recurring se não houver registros em budget_entries
        if not soma_be:
            soma_be = repo.get_all_expenses(mes_ano)

        # 2. Transações dos extratos
        rows_tx = conn.execute(
            """
            SELECT tipo, categoria, valor
              FROM transacoes
             WHERE mes_ano = ?
            """,
            (mes_ano,)
        ).fetchall()

    soma_tx: dict[str, float] = {}
    for r in rows_tx:
        raw_cat = r["categoria"]
        cat = CATEGORY_MAPPING.get(raw_cat, raw_cat)
        val = float(r["valor"])
        if cat in INCOME_CATEGORIES:
            if val > 0:
                soma_tx[cat] = round(soma_tx.get(cat, 0.0) + val, 2)
        else:
            if val < 0:
                soma_tx[cat] = round(soma_tx.get(cat, 0.0) + abs(val), 2)

    typer.echo("\n" + "=" * 80)
    typer.echo(f"   AUDITORIA DE INTEGRIDADE ORÇAMENTÁRIA — COMPETÊNCIA {mes_ano}   ")
    typer.echo("=" * 80)
    all_cats = sorted(set(soma_tx) | set(soma_be))
    if not all_cats:
        typer.echo(f"[!] Nenhum registro de extrato ou orçamento encontrado para o mês {mes_ano}.")
        typer.echo("=" * 80 + "\n")
        return

    typer.echo(f" {'STATUS':<6} | {'CATEGORIA':<34} | {'EXTRATOS':>12} | {'ORÇAMENTO':>12} | {'DELTA':>10}")
    typer.echo("-" * 80)
    total_delta = 0.0
    for cat in all_cats:
        tx = soma_tx.get(cat, 0.0)
        be = soma_be.get(cat, 0.0)
        delta = round(be - tx, 2)
        total_delta += abs(delta)
        status = "  ✓   " if abs(delta) < 0.05 else "  ⚠️  "
        typer.echo(f" {status} | {cat:<34} | R$ {tx:>9.2f} | R$ {be:>9.2f} | R$ {delta:>+7.2f}")
    typer.echo("=" * 80)
    if total_delta < 0.05:
        typer.echo(" [✔] 100% CONCILIADO — Nenhuma divergência significativa entre extratos e orçamento.\n")
    else:
        typer.echo(f" [!] Detectada divergência acumulada de R$ {total_delta:.2f} entre extratos e orçamento.\n")


keywords_app = typer.Typer(help="Gerenciamento de palavras-chave para classificação automática de extratos")
app.add_typer(keywords_app, name="keywords")

@keywords_app.command("list")
def keywords_list():
    """Lista todas as palavras-chave cadastradas para classificação de transações."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    kws = repo.get_keywords()
    typer.echo("\n" + "="*75)
    typer.echo("   PALAVRAS-CHAVE DE CLASSIFICAÇÃO AUTOMÁTICA (KEYWORD TRIE)   ")
    typer.echo("="*75)
    typer.echo(f" {'PRIO':<6} | {'PALAVRA-CHAVE':<24} | {'CATEGORIA':<26} | {'BUCKET':<10}")
    typer.echo("-" * 75)
    for kw, cat, bucket, prio in kws:
        typer.echo(f" {prio:<6} | {kw:<24} | {cat:<26} | {bucket:<10}")
    typer.echo("="*75 + "\n")

@keywords_app.command("add")
def keywords_add(
    keyword: str = typer.Argument(..., help="Palavra ou termo a identificar no extrato"),
    category: str = typer.Argument(..., help="Categoria atribuída (ex: 'Despesas Variáveis')"),
    bucket: str = typer.Option("variavel", "--bucket", help="Bucket: variavel | tributo | fixa | entrada | reserva | investimento"),
    priority: int = typer.Option(100, "--priority", help="Prioridade no casamento (maior = mais prioritário)")
):
    """Adiciona ou atualiza uma palavra-chave no banco de dados para classificação automática."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.services.ofx_service import OFXParserService
    repo = BudgetRepository(settings.sqlite_db_path)
    repo.upsert_keyword(keyword, category, bucket=bucket, priority=priority)
    OFXParserService.reload_keywords_from_db()
    typer.echo(f"[✔] Palavra-chave '{keyword.upper()}' associada à categoria '{category}' (prioridade {priority})!")

@keywords_app.command("remove")
def keywords_remove(
    keyword: str = typer.Argument(..., help="Palavra-chave a remover")
):
    """Remove uma palavra-chave do banco de dados."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    from barsi.services.ofx_service import OFXParserService
    repo = BudgetRepository(settings.sqlite_db_path)
    removido = repo.delete_keyword(keyword)
    if removido:
        OFXParserService.reload_keywords_from_db()
        typer.echo(f"[✔] Palavra-chave '{keyword.upper()}' removida com sucesso!")
    else:
        typer.echo(f"[!] Palavra-chave '{keyword.upper()}' não encontrada no banco.")

@keywords_app.command("test")
def keywords_test(
    memo: str = typer.Argument(..., help="Texto ou descrição do extrato para testar classificação"),
    valor: float = typer.Option(-100.0, "--valor", help="Valor da transação (positivo=crédito, negativo=débito)"),
):
    """Testa qual categoria o classificador atribui a uma descrição de extrato."""
    from barsi.services.ofx_service import OFXParserService, _classify_transaction
    OFXParserService.reload_keywords_from_db()
    trie = OFXParserService._get_trie()
    tipo = "CREDIT" if valor > 0 else "DEBIT"
    cat, ticker = _classify_transaction(
        memo_upper=memo.upper(),
        valor=valor,
        tipo=tipo,
        titular_nomes=settings.titular_nomes,
        familiar_keywords=settings.familiar_keywords,
        trie=trie,
    )
    typer.echo(f"\n🔍 TESTE DE CLASSIFICAÇÃO:")
    typer.echo(f"   Memo analisado : '{memo}'")
    typer.echo(f"   Valor / Tipo   : R$ {valor:.2f} ({tipo})")
    typer.echo(f"   [✔] Categoria  : {cat.value}")
    if ticker:
        typer.echo(f"   [✔] Ticker     : {ticker}")
    typer.echo("")

notify_app = typer.Typer(help="Envio e teste de notificações proativas (Telegram e WhatsApp)")
app.add_typer(notify_app, name="notify")

@notify_app.command("test")
def notify_test(
    title: str = typer.Option("Teste de Notificação Barsi", "--title", help="Título do alerta"),
    message: str = typer.Option("Alerta de teste enviado pelo Sistema Barsi Quant IA.", "--msg", help="Corpo da mensagem"),
    level: str = typer.Option("info", "--level", help="Nível: info | alert | warn | success"),
):
    """Envia uma mensagem de teste para os canais de notificação configurados no .env."""
    import asyncio
    from barsi.adapters.factory import AdapterFactory
    from barsi.adapters.notifications import NoOpNotifier

    notifier = AdapterFactory.create_notification_adapter(settings)
    if isinstance(notifier, NoOpNotifier):
        typer.echo("\n[!] Nenhum canal de notificação está habilitado ou configurado no .env.")
        typer.echo("    Habilite BARSI_TELEGRAM_ALERTS_ENABLED=true ou BARSI_WHATSAPP_ALERTS_ENABLED=true.\n")
        return

    typer.echo(f"\n[+] Disparando notificação de teste ({level.upper()})...")
    ok = asyncio.run(notifier.notify(title=title, body=message, level=level))
    if ok:
        typer.echo("[✔] Notificação entregue com sucesso aos canais configurados!\n")
    else:
        typer.echo("[❌] Falha no envio. Verifique as credenciais no .env e a conectividade de rede.\n")

@notify_app.command("get-chat-id")
def notify_get_chat_id():
    """Consulta os updates do bot do Telegram para capturar o chat_id e salvar no .env."""
    import os
    import httpx
    import re
    token = settings.telegram_bot_token
    if not token:
        typer.echo("[!] BARSI_TELEGRAM_BOT_TOKEN não configurado no .env.")
        return

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    typer.echo("\n[+] Consultando mensagens enviadas ao seu bot no Telegram...")
    try:
        resp = httpx.get(url, timeout=10.0)
        data = resp.json()
        results = data.get("result", [])
        if not results:
            bot_username = "seu_bot"
            try:
                me_resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5.0).json()
                bot_username = me_resp.get("result", {}).get("username", "seu_bot")
            except Exception:
                pass
            typer.echo("\n[!] Nenhuma mensagem encontrada ainda!")
            typer.echo(f"    1. Abra o link do seu bot: https://t.me/{bot_username}")
            typer.echo("    2. Clique em 'Começar' ou envie '/start'")
            typer.echo("    3. Execute este comando novamente: python -m barsi notify get-chat-id\n")
            return

        last_update = results[-1]
        msg = last_update.get("message") or last_update.get("channel_post") or {}
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id"))

        if not chat_id or chat_id == "None":
            typer.echo("[!] Não foi possível identificar o chat_id no retorno da API.\n")
            return

        typer.echo(f"[✔] Chat identificado com sucesso! ID: {chat_id}")

        # Atualiza ou insere BARSI_TELEGRAM_CHAT_ID no .env
        env_path = ".env" if os.path.exists(".env") else "barsi_quant_ia/.env"
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "BARSI_TELEGRAM_CHAT_ID=" in content:
                content = re.sub(r"BARSI_TELEGRAM_CHAT_ID=.*", f"BARSI_TELEGRAM_CHAT_ID={chat_id}", content)
            else:
                content += f"\nBARSI_TELEGRAM_CHAT_ID={chat_id}\n"
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(content)
            typer.echo(f"[✔] BARSI_TELEGRAM_CHAT_ID salvo com segurança no seu arquivo .env local!")

        # Envia mensagem de boas-vindas teste
        from barsi.adapters.notifications import TelegramNotifier
        import asyncio
        notifier = TelegramNotifier(bot_token=token, chat_id=chat_id)
        typer.echo("[+] Enviando alerta de confirmação para o seu Telegram...")
        asyncio.run(notifier.notify("Barsi Quant IA Conectado!", "Seu bot de alertas proativos está 100% configurado e ativo.", level="success"))
        typer.echo("[✔] Alerta de teste entregue no seu Telegram!\n")
    except Exception as exc:
        typer.echo(f"[!] Erro ao consultar API do Telegram: {exc}\n")

@app.command("audit-sources")
def audit_sources_cli(
    reconcile: bool = typer.Option(False, "--reconcile", help="Força reconciliação e paridade 100% entre as 3 fontes"),
    master: str = typer.Option("sheets", "--master", help="Fonte master para reconciliação: sheets | sqlite | xlsx")
):
    """Audita a integridade e paridade entre Google Sheets, Excel local (.xlsx) e SQLite."""
    from barsi.services.sync_health_service import SyncHealthService
    svc = SyncHealthService(settings)
    report = asyncio.run(svc.audit_sources())
    
    typer.echo("\n" + "="*60)
    typer.echo("📊 AUDITORIA DE FONTES DE DADOS (Sheets ↔ XLSX ↔ SQLite)")
    typer.echo("="*60)
    for src, info in report["sources"].items():
        status_ico = "✔" if info["available"] else "✖"
        typer.echo(f"  [{status_ico}] {src.upper():7}: {info['count']} ativos encontrados" + (f" (Erro: {info['error']})" if info['error'] else ""))

    if report["in_sync"]:
        typer.echo("\n[✔] PARIDADE CONFIRMADA: As 3 fontes possuem dados 100% idênticos!\n")
    else:
        typer.echo(f"\n[!] DIVERGÊNCIA DETECTADA em {len(report['divergences'])} ativo(s):")
        for d in report["divergences"]:
            typer.echo(f"  - {d['ticker']}:")
            for diff in d["divergences"]:
                typer.echo(f"      {diff['field']}: Sheets={diff['values'].get('sheets')}, XLSX={diff['values'].get('xlsx')}, SQLite={diff['values'].get('sqlite')}")

    if reconcile:
        rec_res = asyncio.run(svc.reconcile_sources(master=master))
        if rec_res.get("ok"):
            typer.echo(f"\n[✔] RECONCILIAÇÃO CONCLUÍDA! {rec_res['positions_count']} posições sincronizadas usando '{master.upper()}'.\n")
        else:
            typer.echo(f"\n[!] Falha na reconciliação: {rec_res.get('detail')}\n")

@app.command("history-sync")
def history_sync_cli():
    """Baixa o histórico diário real via YFinance para todos os ativos e recalcula a curva no SQLite."""
    from barsi.adapters.history.budget_repository import BudgetRepository
    repo = BudgetRepository(settings.sqlite_db_path)
    typer.echo("[+] Sincronizando cotações históricas diárias via YFinance...")
    count = repo.sync_yfinance_history()
    typer.echo(f"[✔] {count} pregões salvos na tabela price_history!")
    snapshots = repo.build_real_portfolio_snapshots()
    typer.echo(f"[✔] Curva patrimonial real recalculada com {len(snapshots)} meses históricos!\n")

if __name__ == "__main__":
    app()

