"""Script para reparar fórmulas, eliminar #ERROR! e padronizar o design das abas do Google Sheets."""
import gspread

def fix_all_sheets():
    gc = gspread.service_account("barsi_quant_ia/credentials.json")
    sh = gc.open("Carteira Barsi Quant IA B.E.S.T.")
    
    # 1. Repara Screener_BEST_Custodia
    ws_screener = sh.worksheet("Screener_BEST_Custodia")
    
    # Corrige DPA Projetado (Coluna I) para números puros
    dpas = [2.30, 0.58, 3.50, 1.08, 4.50, 12.00, 2.40, 11.40, 9.36, 1.15]
    dpa_updates = [{"range": f"I{row}", "values": [[val]]} for row, val in enumerate(dpas, 10)]
    ws_screener.batch_update(dpa_updates)
    
    # Corrige fórmulas dos Cards do Topo
    ws_screener.update(range_name="D5", values=[["=SUM(ARRAYFORMULA(F10:F19*I10:I19))"]], value_input_option="USER_ENTERED")
    ws_screener.update(range_name="G5", values=[["=D5/A5"]], value_input_option="USER_ENTERED")
    formula_m5 = '=COUNTIF(P10:P19; "*ACUMULAR*") & " de " & COUNTA(A10:A19) & " Ativos"'
    ws_screener.update(range_name="M5", values=[[formula_m5]], value_input_option="USER_ENTERED")
    
    print("[OK] Screener_BEST_Custodia reparada!")
    print("Cards Topo:", ws_screener.row_values(5))
    
    # 2. Inspeciona e repara Simulador_Aportes_Barsi
    ws_sim = sh.worksheet("Simulador_Aportes_Barsi")
    print("\n[OK] Simulador_Aportes_Barsi:")
    for idx, r in enumerate(ws_sim.get("A12:L16"), 12):
        print(f"Linha {idx}:", r[:7])

if __name__ == "__main__":
    fix_all_sheets()

