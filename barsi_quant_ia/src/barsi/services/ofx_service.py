"""Parser e Conciliador de Extratos Bancários (OFX / Faturas) para o Método Barsi."""
import os
import re
import glob
from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field

class Transacao(BaseModel):
    id_transacao: str
    tipo: str  # CREDIT ou DEBIT
    data: str
    valor: float
    descricao: str
    categoria: str = "Outros"
    ticker_relacionado: Optional[str] = None

class ResumoExtratoMes(BaseModel):
    mes_ano: str  # YYYY-MM
    salario_entradas: float = 0.0
    proventos_recebidos: float = 0.0
    compras_acoes_barsi: float = 0.0
    faturas_e_boletos: float = 0.0
    reserva_rdb: float = 0.0
    total_saidas: float = 0.0
    detalhes_ativos: Dict[str, float] = Field(default_factory=dict)
    transacoes: List[Transacao] = Field(default_factory=list)

class OFXParserService:
    @staticmethod
    def parse_ofx_content(content: str) -> List[Transacao]:
        transacoes = []
        # Padrão SGML para transações bancárias OFX
        pattern = re.compile(
            r"<STMTTRN>.*?"
            r"<TRNTYPE>(?P<type>.*?)<.*?"
            r"<DTPOSTED>(?P<date>\d{8}).*?"
            r"<TRNAMT>(?P<amt>[-+]?\d*\.?\d+).*?"
            r"<FITID>(?P<id>.*?)<.*?"
            r"<MEMO>(?P<memo>.*?)<.*?"
            r"</STMTTRN>",
            re.DOTALL
        )

        for match in pattern.finditer(content):
            t_type = match.group("type").strip()
            t_date = match.group("date").strip()
            t_amt = float(match.group("amt").strip())
            t_id = match.group("id").strip()
            t_memo = match.group("memo").strip()

            # Classificação inteligente de categorias Barsi
            memo_upper = t_memo.upper()
            categoria = "Outros"
            ticker = None

            # Detecta compras de ações e FIIs da carteira
            ticker_match = re.search(r"\b([A-Z]{4}\d{1,2})\b", memo_upper)
            if ticker_match:
                ticker = ticker_match.group(1)
                categoria = "Aporte Ações/FIIs"
            elif any(w in memo_upper for w in ["SALÁRIO", "TRANSFERÊNCIA RECEBIDA", "TED RECEBIDA"]) and t_amt > 1000:
                categoria = "Salário / Renda Principal"
            elif any(w in memo_upper for w in ["DIVIDENDO", "RENDIMENTO", "PROVENTO", "JCP"]) or ("CRÉDITO EM CONTA" in memo_upper and t_amt < 100):
                categoria = "Proventos B3"
            elif "RDB" in memo_upper or "RESERVA" in memo_upper or "INVESTIMENTO" in memo_upper:
                categoria = "Reserva / RDB"
            elif any(w in memo_upper for w in ["FATURA", "BOLETO", "DAS", "SEFAZ", "SIMPLES"]):
                categoria = "Despesas Fixas / Tributos"
            elif t_type == "DEBIT":
                categoria = "Despesas Gerais"

            transacoes.append(Transacao(
                id_transacao=t_id,
                tipo=t_type,
                data=f"{t_date[:4]}-{t_date[4:6]}-{t_date[6:8]}",
                valor=t_amt,
                descricao=t_memo,
                categoria=categoria,
                ticker_relacionado=ticker
            ))
        return transacoes

    @classmethod
    def processar_diretorio_extratos(cls, dir_path: str = "extratos") -> Dict[str, ResumoExtratoMes]:
        resumos: Dict[str, ResumoExtratoMes] = {}
        if not os.path.exists(dir_path):
            return resumos

        arquivos_ofx = glob.glob(os.path.join(dir_path, "*.ofx"))
        for arq in arquivos_ofx:
            try:
                with open(arq, "r", encoding="utf-8", errors="ignore") as f:
                    conteudo = f.read()
                txs = cls.parse_ofx_content(conteudo)
                
                for t in txs:
                    mes_chave = t.data[:7]  # YYYY-MM
                    if mes_chave not in resumos:
                        resumos[mes_chave] = ResumoExtratoMes(mes_ano=mes_chave)
                    
                    res = resumos[mes_chave]
                    res.transacoes.append(t)

                    if t.categoria == "Salário / Renda Principal":
                        res.salario_entradas += t.valor
                    elif t.categoria == "Proventos B3":
                        res.proventos_recebidos += t.valor
                    elif t.categoria == "Aporte Ações/FIIs":
                        res.compras_acoes_barsi += abs(t.valor)
                        if t.ticker_relacionado:
                            res.detalhes_ativos[t.ticker_relacionado] = res.detalhes_ativos.get(t.ticker_relacionado, 0.0) + abs(t.valor)
                    elif t.categoria == "Despesas Fixas / Tributos":
                        res.faturas_e_boletos += abs(t.valor)
                    elif t.categoria == "Reserva / RDB":
                        res.reserva_rdb += abs(t.valor)

                    if t.tipo == "DEBIT":
                        res.total_saidas += abs(t.valor)
            except Exception as exc:
                print(f"[!] Erro ao processar extrato '{arq}': {exc}")

        return resumos
