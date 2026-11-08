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

        service = SyncService(
            market=market,
            news=news,
            ai=ai,
            sheets=sheets,
            history=history,
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
    sync_sheet: bool = typer.Option(False, "--sync-sheet", help="Sincroniza valores consolidados na aba Budget_Manager_Fluxo")
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

    remote_home = settings.tvbox_home
    # Garante diretórios essenciais na TV Box
    ssh.exec_command(f"mkdir -p {remote_home}/extratos {remote_home}/src/barsi/api/templates {remote_home}/memory")

    with SCPClient(ssh.get_transport()) as scp:
        # 1. Extratos
        typer.echo("    - Sincronizando extratos bancários (.ofx)...")
        for f in glob.glob("extratos/*.ofx"):
            scp.put(f, f"{remote_home}/extratos/{os.path.basename(f)}")

        # 2. Código fonte principal
        typer.echo("    - Sincronizando código fonte e adaptadores...")
        root_src = "barsi_quant_ia/src/barsi" if os.path.exists("barsi_quant_ia/src/barsi") else "src/barsi"
        for root, _, files in os.walk(root_src):
            for file in files:
                if file.endswith((".py", ".html", ".json", ".sql")) and "__pycache__" not in root:
                    local_p = os.path.join(root, file)
                    rel_p = os.path.relpath(local_p, root_src).replace("\\", "/")
                    remote_p = f"{remote_home}/src/barsi/{rel_p}"
                    remote_dir = os.path.dirname(remote_p)
                    ssh.exec_command(f"mkdir -p {remote_dir}")
                    scp.put(local_p, remote_p)

        # 3. Credentials se existir
        cred_p = "barsi_quant_ia/credentials.json" if os.path.exists("barsi_quant_ia/credentials.json") else "credentials.json"
        if os.path.exists(cred_p):
            scp.put(cred_p, f"{remote_home}/credentials.json")

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

if __name__ == "__main__":
    app()

