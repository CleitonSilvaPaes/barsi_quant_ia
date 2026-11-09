"""Script único para migrar o conteúdo e histórico do Excel local para o Google Sheets."""
import os
import sys
import openpyxl
import gspread

def migrar(xlsx_path: str, credentials_path: str, spreadsheet_title: str = "Carteira Barsi Quant IA"):
    if not os.path.exists(xlsx_path):
        print(f"Erro: Arquivo Excel '{xlsx_path}' não encontrado.")
        return
    if not os.path.exists(credentials_path):
        print(f"Erro: Credenciais do Google '{credentials_path}' não encontradas.")
        print("Obtenha sua chave JSON de Service Account no Google Cloud Console.")
        return

    print("==========================================================")
    print("   MIGRANDO EXCEL LOCAL -> GOOGLE SHEETS                 ")
    print("==========================================================")
    
    # 1. Carrega o Excel local
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws_excel = wb["Dashboard Barsi"]

    # 2. Conecta ao Google Sheets
    gc = gspread.service_account(filename=credentials_path)
    try:
        sh = gc.open(spreadsheet_title)
        print(f"[OK] Planilha '{spreadsheet_title}' encontrada no Google Drive.")
    except gspread.SpreadsheetNotFound:
        sh = gc.create(spreadsheet_title)
        print(f"[OK] Planilha '{spreadsheet_title}' criada com sucesso!")

    try:
        ws_sheet = sh.worksheet("Dashboard Barsi")
    except gspread.WorksheetNotFound:
        ws_sheet = sh.add_worksheet(title="Dashboard Barsi", rows="50", cols="20")

    # 3. Lê linhas 1 a 20 do Excel e copia valores
    dados_matriz = []
    for r in range(1, 22):
        linha = []
        for c in range(1, 15):
            val = ws_excel.cell(row=r, column=c).value
            linha.append(val if val is not None else "")
        dados_matriz.append(linha)

    # 4. Atualiza em lote no Google Sheets
    ws_sheet.update(range_name="A1:N22", values=dados_matriz)
    print("[SUCESSO] Dados migrados com sucesso para o Google Sheets!")
    print(f"Acesse em: https://docs.google.com/spreadsheets/d/{sh.id}")

if __name__ == "__main__":
    xlsx = sys.argv[1] if len(sys.argv) > 1 else "carteira_barsi_quant_ia.xlsx"
    creds = sys.argv[2] if len(sys.argv) > 2 else "credentials.json"
    migrar(xlsx, creds)

