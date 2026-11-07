"""Parser e Conciliador de Extratos Bancários (OFX / Faturas) para o Método Barsi."""
import os
import re
import glob
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class Transacao(BaseModel):
    id_transacao: str
    tipo: str  # CREDIT ou DEBIT
    data: str
    valor: float
    descricao: str
    categoria: str = "Outros"
    ticker_relacionado: Optional[str] = None
    origem: str = "CONTA"  # CONTA ou CARTAO

class ResumoExtratoMes(BaseModel):
    mes_ano: str  # YYYY-MM
    salario_entradas: float = 0.0
    proventos_recebidos: float = 0.0
    outras_entradas: float = 0.0
    
    # Investimentos Barsi
    compras_acoes_barsi: float = 0.0
    compras_fiis: float = 0.0
    total_investimentos: float = 0.0
    detalhes_ativos: Dict[str, float] = Field(default_factory=dict)
    
    # Fixas e Tributos
    faturas_cartao: float = 0.0
    tributos_e_taxas: float = 0.0
    moradia_e_familia: float = 0.0
    cooperativa_ailos: float = 0.0
    faturas_e_boletos: float = 0.0  # Total consolidador fixo
    
    # Reserva de Emergência / Colchão
    reserva_rdb: float = 0.0
    
    # Variáveis (Débito do dia a dia)
    despesas_variaveis: float = 0.0
    
    total_entradas: float = 0.0
    total_saidas: float = 0.0
    saldo_livre: float = 0.0
    transacoes: List[Transacao] = Field(default_factory=list)

class OFXParserService:
    @staticmethod
    def identificar_origem_ofx(content: str) -> str:
        """Determina se o extrato é da Conta Corrente ou Fatura de Cartão de Crédito."""
        if "<CREDITCARDMSGSRSV1>" in content or "<CCACCTINFO>" in content:
            return "CARTAO"
        return "CONTA"

    @staticmethod
    def parse_ofx_content(content: str, origem_forçada: Optional[str] = None) -> List[Transacao]:
        transacoes = []
        origem = origem_forçada or OFXParserService.identificar_origem_ofx(content)

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
            t_type = match.group("type").strip().upper()
            t_date = match.group("date").strip()
            t_amt = float(match.group("amt").strip())
            t_id = match.group("id").strip()
            t_memo = match.group("memo").strip()

            memo_upper = t_memo.upper()
            categoria = "Outros"
            ticker = None

            # Detecta compras de ações ou FIIs
            ticker_match = re.search(r"\b([A-Z]{4}\d{1,2})\b", memo_upper)
            if ticker_match:
                ticker = ticker_match.group(1)
                categoria = "Aporte Ações/FIIs"
            elif any(w in memo_upper for w in ["SALÁRIO", "TRANSFERÊNCIA RECEBIDA", "TED RECEBIDA"]) and t_amt >= 1000:
                categoria = "Salário / Renda Principal"
            elif any(w in memo_upper for w in ["DIVIDENDO", "RENDIMENTO", "PROVENTO", "JCP"]) or ("CRÉDITO EM CONTA" in memo_upper and 0 < t_amt < 100):
                categoria = "Proventos B3"
            elif "RDB" in memo_upper or "RESERVA" in memo_upper or "CAIXINHA" in memo_upper:
                categoria = "Reserva / RDB"
            elif "PAGAMENTO DE FATURA" in memo_upper or "FATURA" in memo_upper:
                categoria = "Fatura Cartão Nubank"
            elif any(w in memo_upper for w in ["DAS-SIMPLES", "SEFAZ", "DETRAN", "LICENCIAMENTO", "TRIBUTO"]):
                categoria = "Tributos & Taxas"
            elif "VIACREDI" in memo_upper or "AILOS" in memo_upper:
                categoria = "Cooperativa Ailos"
            elif "NIZIELMA" in memo_upper:
                categoria = "Compromisso Familiar / Moradia"
            elif any(w in memo_upper for w in ["SUPERMERCADO", "ARCHER", "KOCH", "POSTO", "BARBEARIA", "RESTAURANTE", "DELIVERY"]):
                categoria = "Despesas Variáveis"
            elif t_type == "DEBIT" or t_amt < 0:
                categoria = "Despesas Gerais"

            transacoes.append(Transacao(
                id_transacao=t_id,
                tipo=t_type,
                data=f"{t_date[:4]}-{t_date[4:6]}-{t_date[6:8]}",
                valor=t_amt,
                descricao=t_memo,
                categoria=categoria,
                ticker_relacionado=ticker,
                origem=origem
            ))
        return transacoes

    @classmethod
    def processar_diretorio_extratos(cls, dir_path: str = "extratos") -> Dict[str, ResumoExtratoMes]:
        resumos: Dict[str, ResumoExtratoMes] = {}
        if not os.path.exists(dir_path):
            return resumos

        fitids_vistos = set()  # Garante idempotência: nenhuma transação é processada mais de uma vez
        arquivos_ofx = glob.glob(os.path.join(dir_path, "*.ofx"))
        for arq in sorted(arquivos_ofx):
            try:
                with open(arq, "r", encoding="utf-8", errors="ignore") as f:
                    conteudo = f.read()
                
                origem = cls.identificar_origem_ofx(conteudo)
                txs = cls.parse_ofx_content(conteudo, origem_forçada=origem)
                
                for t in txs:
                    # Deduplicação rigorosa por ID bancário único (FITID)
                    if t.id_transacao in fitids_vistos:
                        continue
                    fitids_vistos.add(t.id_transacao)

                    mes_chave = t.data[:7]  # YYYY-MM
                    if mes_chave not in resumos:
                        resumos[mes_chave] = ResumoExtratoMes(mes_ano=mes_chave)
                    
                    res = resumos[mes_chave]
                    res.transacoes.append(t)

                    # Se a transação for de extrato de CARTÃO, ela detalha a fatura, não o fluxo de caixa da conta
                    if t.origem == "CARTAO":
                        continue

                    # Entradas de Conta Corrente
                    if t.categoria == "Salário / Renda Principal" and t.valor > 0:
                        res.salario_entradas += t.valor
                    elif t.categoria == "Proventos B3" and t.valor > 0:
                        res.proventos_recebidos += t.valor
                    elif t.tipo == "CREDIT" and t.valor > 0 and t.categoria != "Aporte Ações/FIIs":
                        res.outras_entradas += t.valor

                    # Investimentos em Ações e FIIs (Tratamento preciso de cancelamentos/estornos)
                    elif t.categoria == "Aporte Ações/FIIs":
                        # Se for DEBIT (compra): soma positivo.
                        # Se for CREDIT (estorno/cancelamento/sobra): subtrai!
                        valor_efetivo = abs(t.valor) if (t.tipo == "DEBIT" or t.valor < 0) else -abs(t.valor)
                        
                        if t.ticker_relacionado:
                            tk = t.ticker_relacionado
                            res.detalhes_ativos[tk] = round(res.detalhes_ativos.get(tk, 0.0) + valor_efetivo, 2)
                            if tk.startswith("MXRF") or "11" in tk:
                                res.compras_fiis = round(res.compras_fiis + valor_efetivo, 2)
                            else:
                                res.compras_acoes_barsi = round(res.compras_acoes_barsi + valor_efetivo, 2)
                        else:
                            res.compras_acoes_barsi = round(res.compras_acoes_barsi + valor_efetivo, 2)
                        
                        res.total_investimentos = round(res.compras_acoes_barsi + res.compras_fiis, 2)

                    # Saídas Fixas / Tributos
                    elif t.categoria == "Fatura Cartão Nubank":
                        res.faturas_cartao += abs(t.valor)
                    elif t.categoria == "Tributos & Taxas":
                        res.tributos_e_taxas += abs(t.valor)
                    elif t.categoria == "Compromisso Familiar / Moradia":
                        res.moradia_e_familia += abs(t.valor)
                    elif t.categoria == "Cooperativa Ailos":
                        res.cooperativa_ailos += abs(t.valor)
                    elif t.categoria == "Reserva / RDB":
                        res.reserva_rdb += abs(t.valor)
                    elif t.categoria == "Despesas Variáveis":
                        res.despesas_variaveis += abs(t.valor)
                    elif t.tipo == "DEBIT":
                        res.despesas_variaveis += abs(t.valor)

                # Consolida totais
                for mes, r in resumos.items():
                    r.faturas_e_boletos = round(r.faturas_cartao + r.tributos_e_taxas + r.moradia_e_familia + r.cooperativa_ailos, 2)
                    r.total_entradas = round(r.salario_entradas + r.proventos_recebidos + r.outras_entradas, 2)
                    r.total_saidas = round(r.faturas_e_boletos + r.reserva_rdb + r.total_investimentos + r.despesas_variaveis, 2)
                    r.saldo_livre = round(r.total_entradas - r.total_saidas, 2)

            except Exception as exc:
                print(f"[!] Erro ao processar extrato '{arq}': {exc}")

        return resumos
