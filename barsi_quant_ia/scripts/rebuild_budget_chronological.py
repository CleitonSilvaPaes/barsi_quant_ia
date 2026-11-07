"""Reconstrói e padroniza a aba Budget_Manager_Fluxo em ordem cronológica estrita (Jan..Nov 2026)."""
import gspread
from barsi.services.ofx_service import OFXParserService

def col_idx_to_letter(idx: int) -> str:
    string = ""
    while idx > 0:
        idx, remainder = divmod(idx - 1, 26)
        string = chr(65 + remainder) + string
    return string

def rebuild_budget():
    gc = gspread.service_account('barsi_quant_ia/credentials.json')
    sh = gc.open('Carteira Barsi Quant IA B.E.S.T.')
    ws = sh.worksheet('Budget_Manager_Fluxo')

    resumos = OFXParserService.processar_diretorio_extratos('extratos')
    meses_ordenados = sorted(resumos.keys())  # ['2026-01', ..., '2026-11']

    meses_pt = {
        "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
        "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
        "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
    }

    headers = ["Item / Descrição Orçamentária"]
    for m in meses_ordenados:
        ano, mes = m.split("-")
        headers.append(f"{meses_pt[mes]}/{ano} (R$)")
    
    headers.extend(["Variação Nominal (R$)", "% Orçamento Nov", "Status de Execução", "Meio de Liquidação"])

    total_cols = len(headers)
    if ws.col_count < total_cols:
        ws.add_cols(total_cols - ws.col_count)
    elif ws.col_count > total_cols:
        ws.delete_columns(total_cols + 1, ws.col_count)

    # 1. Configura merges dos 4 Cards Superiores (Linhas 4, 5, 6)
    # A..D (cols 0..4), E..H (cols 4..8), I..L (cols 8..12), M..P (cols 12..16)
    body_merges = {
        'requests': [
            {'unmergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 6, 'startColumnIndex': 0, 'endColumnIndex': 16}}},
            # Card 1: A:D
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 0, 'endColumnIndex': 4}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 0, 'endColumnIndex': 4}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 0, 'endColumnIndex': 4}, 'mergeType': 'MERGE_ALL'}},
            # Card 2: E:H
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 4, 'endColumnIndex': 8}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 4, 'endColumnIndex': 8}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 4, 'endColumnIndex': 8}, 'mergeType': 'MERGE_ALL'}},
            # Card 3: I:L
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 8, 'endColumnIndex': 12}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 8, 'endColumnIndex': 12}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 8, 'endColumnIndex': 12}, 'mergeType': 'MERGE_ALL'}},
            # Card 4: M:P
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 12, 'endColumnIndex': 16}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 4, 'endRowIndex': 5, 'startColumnIndex': 12, 'endColumnIndex': 16}, 'mergeType': 'MERGE_ALL'}},
            {'mergeCells': {'range': {'sheetId': ws.id, 'startRowIndex': 5, 'endRowIndex': 6, 'startColumnIndex': 12, 'endColumnIndex': 16}, 'mergeType': 'MERGE_ALL'}},
        ]
    }
    try:
        sh.batch_update(body_merges)
    except Exception as e:
        print("Aviso merges:", e)

    # 2. Atualiza cabeçalho linha 9
    range_h = f"A9:{col_idx_to_letter(total_cols)}9"
    ws.update(range_name=range_h, values=[headers], value_input_option="USER_ENTERED")

    updates = []
    
    # Preenche cada coluna de mês (Linhas 11 a 45)
    for col_idx, m in enumerate(meses_ordenados, start=2):
        c_let = col_idx_to_letter(col_idx)
        r = resumos[m]

        salario = round(r.salario_entradas, 2)
        proventos = round(r.proventos_recebidos, 2)
        saldo_ant = 50.28 if m == "2026-11" else 0.0

        updates.append({"range": f"{c_let}11", "values": [[salario]]})
        updates.append({"range": f"{c_let}12", "values": [[proventos]]})
        updates.append({"range": f"{c_let}13", "values": [[saldo_ant]]})
        updates.append({"range": f"{c_let}14", "values": [[f"=SUM({c_let}11:{c_let}13)"]]})

        fatura = round(r.faturas_cartao, 2)
        aluguel = 521.30 if m in ["2026-10", "2026-11"] else 0.0
        nizielma = 400.00 if m in ["2026-10", "2026-11"] else 0.0
        das = 86.05 if m in ["2026-10", "2026-11"] else 0.0
        licenciamento = 149.37 if m == "2026-11" else 0.0
        ailos = 60.00 if m in ["2026-10", "2026-11"] else 0.0

        updates.append({"range": f"{c_let}17", "values": [[fatura]]})
        updates.append({"range": f"{c_let}18", "values": [[aluguel]]})
        updates.append({"range": f"{c_let}19", "values": [[nizielma]]})
        updates.append({"range": f"{c_let}20", "values": [[das]]})
        updates.append({"range": f"{c_let}21", "values": [[licenciamento]]})
        updates.append({"range": f"{c_let}22", "values": [[ailos]]})
        updates.append({"range": f"{c_let}23", "values": [[f"=SUM({c_let}17:{c_let}22)"]]})

        saldo_ant_caixa = 180.44 if m == "2026-11" else 0.0
        novo_dep = round(r.reserva_rdb, 2)
        updates.append({"range": f"{c_let}26", "values": [[saldo_ant_caixa]]})
        updates.append({"range": f"{c_let}27", "values": [[novo_dep]]})
        updates.append({"range": f"{c_let}28", "values": [[f"={c_let}26+{c_let}27"]]})

        acoes = round(r.compras_acoes_barsi, 2)
        fiis = round(r.compras_fiis, 2)
        updates.append({"range": f"{c_let}31", "values": [[acoes]]})
        updates.append({"range": f"{c_let}32", "values": [[fiis]]})
        updates.append({"range": f"{c_let}33", "values": [[f"=SUM({c_let}31:{c_let}32)"]]})

        if m == "2026-11":
            updates.append({"range": f"{c_let}36", "values": [[450.00]]})
            updates.append({"range": f"{c_let}37", "values": [[120.00]]})
            updates.append({"range": f"{c_let}38", "values": [[120.00]]})
            updates.append({"range": f"{c_let}39", "values": [[65.00]]})
            updates.append({"range": f"{c_let}40", "values": [[50.00]]})
        elif m == "2026-10":
            updates.append({"range": f"{c_let}36", "values": [[420.00]]})
            updates.append({"range": f"{c_let}37", "values": [[50.00]]})
            updates.append({"range": f"{c_let}38", "values": [[110.00]]})
            updates.append({"range": f"{c_let}39", "values": [[60.00]]})
            updates.append({"range": f"{c_let}40", "values": [[50.00]]})
        else:
            updates.append({"range": f"{c_let}36", "values": [[0.00]]})
            updates.append({"range": f"{c_let}37", "values": [[0.00]]})
            updates.append({"range": f"{c_let}38", "values": [[0.00]]})
            updates.append({"range": f"{c_let}39", "values": [[0.00]]})
            updates.append({"range": f"{c_let}40", "values": [[0.00]]})

        updates.append({"range": f"{c_let}41", "values": [[f"=SUM({c_let}36:{c_let}40)"]]})
        updates.append({"range": f"{c_let}44", "values": [[f"={c_let}23+{c_let}27+{c_let}33+{c_let}41"]]})
        updates.append({"range": f"{c_let}45", "values": [[f"={c_let}14-{c_let}44"]]})

    # Colunas de Variação Nominal e % Orçamento
    penultimo_col = col_idx_to_letter(1 + len(meses_ordenados) - 1)  # K (Outubro)
    ultimo_col = col_idx_to_letter(1 + len(meses_ordenados))          # L (Novembro)
    var_col = col_idx_to_letter(1 + len(meses_ordenados) + 1)         # M (Variação)
    pct_col = col_idx_to_letter(1 + len(meses_ordenados) + 2)         # N (% Orçamento)

    for r in range(11, 46):
        if r in [15, 24, 29, 34, 42]:
            continue
        updates.append({"range": f"{var_col}{r}", "values": [[f"={ultimo_col}{r}-{penultimo_col}{r}"]]})
        updates.append({"range": f"{pct_col}{r}", "values": [[f"={ultimo_col}{r}/{ultimo_col}$14"]]})

    # 3. Atualiza os 4 Cards de Topo
    updates.append({"range": "A4", "values": [["ENTRADAS TOTAIS (NOV/26)"]]})
    updates.append({"range": "A5", "values": [[f"={ultimo_col}14"]]})
    updates.append({"range": "A6", "values": [["Salário + Proventos + Saldo anterior"]]})

    updates.append({"range": "E4", "values": [["SAÍDAS FIXAS OBRIGATÓRIAS"]]})
    updates.append({"range": "E5", "values": [[f"={ultimo_col}23"]]})
    updates.append({"range": "E6", "values": [[f"=TEXT({ultimo_col}23/{ultimo_col}14; \"0,0%\") & \" da renda comprometida\""]]})

    updates.append({"range": "I4", "values": [["APORTES + RESERVA"]]})
    updates.append({"range": "I5", "values": [[f"={ultimo_col}27+{ultimo_col}33"]]})
    updates.append({"range": "I6", "values": [[f"=TEXT(({ultimo_col}27+{ultimo_col}33)/{ultimo_col}14; \"0,0%\") & \" de taxa de poupança\""]]})

    updates.append({"range": "M4", "values": [["SALDO LIVRE RESIDUAL"]]})
    updates.append({"range": "M5", "values": [[f"={ultimo_col}45"]]})
    updates.append({"range": "M6", "values": [["Folga livre pós-contas e aportes"]]})

    # Título Geral Linha 8
    updates.append({"range": "A8", "values": [["CONCILIAÇÃO ORÇAMENTÁRIA DETALHADA (JANEIRO A NOVEMBRO 2026)"]]})

    ws.batch_update(updates, value_input_option="USER_ENTERED")
    print(f"[OK] Reconstrução concluída com sucesso! Cards e colunas cronológicas de B a L ajustados.")

if __name__ == "__main__":
    rebuild_budget()
