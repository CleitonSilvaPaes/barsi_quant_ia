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
    dir_path: str = typer.Option("extratos", "--dir", help="Pasta com arquivos de extrato .ofx")
):
    """Lê e processa os extratos bancários da pasta extratos/ para conciliação Barsi."""
    from barsi.services.ofx_service import OFXParserService
    resumos = OFXParserService.processar_diretorio_extratos(dir_path)
    if not resumos:
        typer.echo(f"[!] Nenhum extrato .ofx encontrado em '{dir_path}'")
        return

    typer.echo("\n========================================================")
    typer.echo("   CONCILIAÇÃO BANCÁRIA & APORTES (MÉTODO BARSI)       ")
    typer.echo("========================================================")
    for mes, r in sorted(resumos.items()):
        typer.echo(f"\n📅 Mês de Referência: {mes}")
        typer.echo(f"   (+) Salário / Renda Principal : R$ {r.salario_entradas:>9.2f}")
        typer.echo(f"   (+) Proventos B3 / Dividendos : R$ {r.proventos_recebidos:>9.2f}")
        typer.echo(f"   (-) Compras de Ações e FIIs   : R$ {r.compras_acoes_barsi:>9.2f}")
        if r.detalhes_ativos:
            for t, val in r.detalhes_ativos.items():
                typer.echo(f"       • {t:<6} : R$ {val:>8.2f}")
        typer.echo(f"   (-) Faturas e Tributos        : R$ {r.faturas_e_boletos:>9.2f}")
        typer.echo(f"   (-) Total de Saídas           : R$ {r.total_saidas:>9.2f}")

if __name__ == "__main__":
    app()

