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
def tvbox(
    ip: str = typer.Option("192.168.100.72", "--ip", help="IP da TV Box na rede"),
    user: str = typer.Option("cleiton", "--user", help="Usuário SSH"),
    password: str = typer.Option("compstream", "--pass", help="Senha SSH")
):
    """Consulta a telemetria ao vivo da TV Box (Temperatura, RAM, Uptime e Timer)."""
    import paramiko
    typer.echo(f"\n[+] Conectando na TV Box ({user}@{ip})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(ip, username=user, password=password, timeout=5)
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

if __name__ == "__main__":
    app()

