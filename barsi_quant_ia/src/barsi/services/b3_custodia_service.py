"""Serviço de importação e conciliação de Custódia Oficial da B3 (Área do Investidor)."""
from pathlib import Path
from typing import Dict, Any, List, Optional
import openpyxl
import structlog
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position, AcaoBarsi
from barsi.domain.rules import (
    calcular_preco_teto,
    calcular_margem_seguranca,
    determinar_acao_barsi,
    is_fii,
)

logger = structlog.get_logger(__name__)

SETOR_MAP = {
    "BBAS3": "Bancos",
    "ITUB4": "Bancos",
    "ITUB3": "Bancos",
    "BBDC4": "Bancos",
    "BBDC3": "Bancos",
    "SANB11": "Bancos",
    "SANB4": "Bancos",
    "BBSE3": "Seguros",
    "CXSE3": "Seguros",
    "PSSA3": "Seguros",
    "CMIG4": "Energia",
    "CMIG3": "Energia",
    "TAEE11": "Energia",
    "TAEE4": "Energia",
    "TRPL4": "Energia",
    "CPLE6": "Energia",
    "EGIE3": "Energia",
    "EQTL3": "Energia",
    "SAPR4": "Saneamento",
    "SAPR11": "Saneamento",
    "SBSP3": "Saneamento",
    "CSMG3": "Saneamento",
    "VIVT3": "Telecomunicações",
    "TIMS3": "Telecomunicações",
    "PETR4": "Óleo e Gás",
    "PETR3": "Óleo e Gás",
    "VALE3": "Mineração",
    "KLBN11": "Papel e Celulose",
    "MXRF11": "FII Papel",
    "KNCR11": "FII Papel",
    "HGCR11": "FII Papel",
    "XPLG11": "FII Tijolo",
    "HGLG11": "FII Tijolo",
    "KNRI11": "FII Tijolo",
    "XPML11": "FII Shoppings",
    "VISC11": "FII Shoppings",
}

class B3CustodiaService:
    """Importador e reconciliador da planilha oficial de custódia da Área do Investidor da B3."""

    def __init__(self, repo: BudgetRepository):
        self.repo = repo

    def parse_b3_excel(self, file_path_or_bytes: Any) -> List[Dict[str, Any]]:
        """Lê arquivo Excel da B3 e extrai todos os ativos das abas 'Acoes' e 'Fundo de Investimento'."""
        if isinstance(file_path_or_bytes, (str, Path)):
            wb = openpyxl.load_workbook(file_path_or_bytes, data_only=True)
        else:
            import io
            wb = openpyxl.load_workbook(io.BytesIO(file_path_or_bytes), data_only=True)

        ativos: List[Dict[str, Any]] = []

        for sheet_name in wb.sheetnames:
            lower_name = sheet_name.lower()
            tipo_aba = "Acao"
            if "fundo" in lower_name or "fii" in lower_name:
                tipo_aba = "FII"

            ws = wb[sheet_name]
            header_map = {}
            for col_idx in range(1, ws.max_column + 1):
                header_val = ws.cell(1, col_idx).value
                if header_val:
                    header_map[str(header_val).strip().lower()] = col_idx

            ticker_col = header_map.get("código de negociação") or header_map.get("codigo de negociacao")
            produto_col = header_map.get("produto")
            qtd_col = header_map.get("quantidade")
            preco_col = header_map.get("preço de fechamento") or header_map.get("preco de fechamento")
            valor_col = header_map.get("valor atualizado")

            if not ticker_col or not qtd_col:
                continue

            for row_idx in range(2, ws.max_row + 1):
                ticker = ws.cell(row_idx, ticker_col).value
                if not ticker or not str(ticker).strip():
                    continue

                ticker_str = str(ticker).strip().upper()
                if ticker_str == "TOTAL" or "TOTAL" in ticker_str:
                    continue

                produto = ws.cell(row_idx, produto_col).value if produto_col else ""
                empresa_nome = str(produto).split(" - ")[-1].strip() if produto and " - " in str(produto) else (str(produto).strip() if produto else ticker_str)

                try:
                    qtd_val = int(float(ws.cell(row_idx, qtd_col).value or 0))
                except (ValueError, TypeError):
                    qtd_val = 0

                try:
                    preco_val = round(float(ws.cell(row_idx, preco_col).value or 0.0), 2) if preco_col else 0.0
                except (ValueError, TypeError):
                    preco_val = 0.0

                try:
                    valor_val = round(float(ws.cell(row_idx, valor_col).value or 0.0), 2) if valor_col else round(qtd_val * preco_val, 2)
                except (ValueError, TypeError):
                    valor_val = round(qtd_val * preco_val, 2)

                if qtd_val > 0:
                    setor_detectado = SETOR_MAP.get(ticker_str)
                    if not setor_detectado:
                        setor_detectado = "FII" if tipo_aba == "FII" or is_fii(ticker_str) else "Outros"

                    ativos.append({
                        "ticker": ticker_str,
                        "empresa": empresa_nome,
                        "setor": setor_detectado,
                        "tipo": tipo_aba,
                        "quantidade": qtd_val,
                        "preco_fechamento": preco_val,
                        "valor_atualizado": valor_val,
                    })

        return ativos

    def sync_custodia_to_sqlite(self, file_path_or_bytes: Any) -> Dict[str, Any]:
        """Importa e sincroniza a custódia da B3 com o banco SQLite canônico."""
        ativos_b3 = self.parse_b3_excel(file_path_or_bytes)
        if not ativos_b3:
            return {
                "status": "warning",
                "message": "Nenhum ativo com quantidade positiva encontrado na planilha B3.",
                "total_importados": 0,
                "ativos": []
            }

        posicoes_existentes = {p.ticker: p for p in self.repo.load_positions()}
        posicoes_atualizadas: List[Position] = []

        total_patrimonio = 0.0
        novos_count = 0
        atualizados_count = 0

        next_row_index = len(posicoes_existentes) + 1

        for item in ativos_b3:
            ticker = item["ticker"]
            qtd = item["quantidade"]
            preco = item["preco_fechamento"]
            setor = item["setor"]
            empresa = item["empresa"]
            total_patrimonio += (qtd * preco)

            if ticker in posicoes_existentes:
                p = posicoes_existentes[ticker]
                p.qtd_antes = qtd
                p.preco_atual = preco if preco > 0 else p.preco_atual
                p.valor_total = round(p.qtd_antes * p.preco_atual, 2)
                if not p.setor or p.setor == "Geral":
                    p.setor = setor
                if not p.empresa or p.empresa == ticker:
                    p.empresa = empresa

                # Recalcula margem
                if not p.teto_manual and p.div_proj > 0:
                    p.preco_teto = calcular_preco_teto(p.div_proj, p.preco_atual, ticker=ticker, setor=p.setor)

                p.margem_seguranca = calcular_margem_seguranca(p.preco_teto, p.preco_atual)
                p.status_barsi = determinar_acao_barsi(p.margem_seguranca, dy_real=round((p.div_proj / p.preco_atual) * 100, 2) if p.preco_atual > 0 else 0.0, div_proj=p.div_proj)

                posicoes_atualizadas.append(p)
                atualizados_count += 1
            else:
                # Novo ativo na carteira
                div_estimado = round(preco * 0.06, 2) if not is_fii(ticker, setor) else round(preco * 0.09, 2)
                teto = calcular_preco_teto(div_estimado, preco, ticker=ticker, setor=setor)
                margem = calcular_margem_seguranca(teto, preco)
                status = determinar_acao_barsi(margem, dy_real=6.0, div_proj=div_estimado)

                nova_pos = Position(
                    ticker=ticker,
                    row_index=next_row_index,
                    empresa=empresa,
                    setor=setor,
                    qtd_antes=qtd,
                    qtd_aporte=0,
                    preco_atual=preco,
                    div_proj=div_estimado,
                    preco_teto=teto,
                    margem_seguranca=margem,
                    status_barsi=status,
                    valor_total=round(qtd * preco, 2),
                    teto_manual=False
                )
                posicoes_atualizadas.append(nova_pos)
                next_row_index += 1
                novos_count += 1

        # Salva atomicamente no SQLite
        self.repo.save_positions(posicoes_atualizadas)

        logger.info(
            "b3_custodia_synced",
            total_ativos=len(posicoes_atualizadas),
            novos=novos_count,
            atualizados=atualizados_count,
            patrimonio_total=total_patrimonio
        )

        return {
            "status": "ok",
            "message": f"Sincronização concluída: {len(posicoes_atualizadas)} ativos na carteira ({novos_count} novos, {atualizados_count} atualizados).",
            "total_importados": len(posicoes_atualizadas),
            "novos": novos_count,
            "atualizados": atualizados_count,
            "patrimonio_total": round(total_patrimonio, 2),
            "ativos": [
                {
                    "ticker": p.ticker,
                    "empresa": p.empresa,
                    "setor": p.setor,
                    "qtd": p.qtd_antes,
                    "preco": p.preco_atual,
                    "teto": p.preco_teto,
                    "margem": p.margem_seguranca,
                    "status": str(p.status_barsi)
                }
                for p in posicoes_atualizadas
            ]
        }
