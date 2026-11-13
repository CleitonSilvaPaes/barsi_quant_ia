"""Parser e Conciliador de Extratos Bancários (OFX / Faturas) para o Método Barsi.

Arquitetura pura sem efeitos colaterais de I/O no banco durante o parsing.
Convenção estrita de sinais: despesas somam apenas valores < 0; entradas somam apenas valores > 0.
Zero hardcode de nomes de pessoas e zero uso de print.
"""
from __future__ import annotations

import glob
import os
import re
import zipfile
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from barsi.config import settings
from barsi.domain.models import Categoria
from barsi.domain.rules import is_fii
from barsi.infra.logging import logger

_OFX_TRN_RE = re.compile(
    r"<STMTTRN>.*?"
    r"<TRNTYPE>(?P<type>[^<]*)"
    r".*?<DTPOSTED>(?P<date>\d{8})"
    r".*?<TRNAMT>(?P<amt>[-+]?\d*\.?\d+)"
    r".*?<FITID>(?P<id>[^<]*)"
    r".*?<MEMO>(?P<memo>[^<]*)",
    re.DOTALL,
)

_TICKER_RE = re.compile(r"\b([A-Z]{4}\d{1,2})\b")


class Transacao(BaseModel):
    id_transacao: str
    tipo: str  # CREDIT ou DEBIT
    data: str
    valor: float
    descricao: str
    categoria: str = Categoria.OUTROS.value
    ticker_relacionado: Optional[str] = None
    origem: str = "CONTA"  # CONTA ou CARTAO


class ResumoExtratoMes(BaseModel):
    """Soma dos valores do mês por categoria.

    CONVENÇÃO: `total_entradas` = soma de todo valor > 0; `total_saidas` = soma de todo |valor| < 0.
    Categorias de "entrada" e "despesa" são disjuntas. Nenhuma categoria recebe `abs()` cegamente.
    """

    mes_ano: str  # YYYY-MM
    salario_entradas: float = 0.0
    proventos_recebidos: float = 0.0
    outras_entradas: float = 0.0
    rendimento_conta: float = 0.0

    # Investimentos Barsi
    compras_acoes_barsi: float = 0.0
    compras_fiis: float = 0.0
    total_investimentos: float = 0.0
    detalhes_ativos: Dict[str, float] = Field(default_factory=dict)

    # Fixas e Tributos
    faturas_cartao: float = 0.0
    encargos_cartao: float = 0.0
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


class KeywordTrie:
    """Estrutura Trie para busca e casamento eficiente de palavras-chave em O(len(memo))."""

    def __init__(self, keywords: Iterable[Tuple[str, str, str, int]]):
        self.root: Dict[str, Any] = {}
        for kw, cat, bucket, prio in sorted(keywords, key=lambda x: (x[3], len(x[0])), reverse=True):
            node = self.root
            for ch in kw.upper():
                node = node.setdefault(ch, {})
            if "_cat" not in node:
                node["_cat"] = (cat, bucket, prio)

    def match(self, memo_upper: str) -> Optional[Tuple[str, str, int]]:
        best: Optional[Tuple[str, str, int]] = None
        for i in range(len(memo_upper)):
            node = self.root
            for j in range(i, len(memo_upper)):
                ch = memo_upper[j]
                if ch not in node:
                    break
                node = node[ch]
                if "_cat" in node:
                    candidate: Tuple[str, str, int] = node["_cat"]
                    if best is None or candidate[2] > best[2]:
                        best = candidate
        return best


def _extract_ticker(memo_upper: str) -> Optional[str]:
    """Extrai ticker B3 (4 letras + 1 ou 2 dígitos) do memo."""
    match = _TICKER_RE.search(memo_upper)
    return match.group(1) if match else None


def _classify_transaction(
    memo_upper: str,
    valor: float,
    tipo: str,
    titular_nomes: List[str],
    familiar_keywords: List[str],
    trie: KeywordTrie,
) -> Tuple[Categoria, Optional[str]]:
    """Função pura que classifica uma transação bancária segundo regras Barsi."""
    # 1. Investimento / Aporte em Ações ou FIIs
    ticker = _extract_ticker(memo_upper)
    if ticker:
        return Categoria.APORTE, ticker

    # 2. Rendimento NuConta (CDI / Conta)
    if "CRÉDITO EM CONTA" in memo_upper and not any(w in memo_upper for w in ["DIVIDENDO", "JCP", "PROVENTO"]):
        return Categoria.RENDIMENTO_CONTA, None

    # 3. Proventos B3 (Dividendos / JCP / Rendimentos de Custódia)
    if any(w in memo_upper for w in ["DIVIDENDO", "JCP"]):
        return Categoria.PROVENTOS, None
    if any(w in memo_upper for w in ["RENDIMENTO", "PROVENTO"]):
        if any(w in memo_upper for w in ["B3", "CORRETORA", "CUSTODIA", "FII", "CLEAR", "XP", "RICO", "INTER"]):
            return Categoria.PROVENTOS, None
        if "CRÉDITO EM CONTA" in memo_upper:
            return Categoria.PROVENTOS, None

    # 4. Salário vs Transferência Familiar
    if "SALÁRIO" in memo_upper or "FOLHA DE PAGAMENTO" in memo_upper or "FOLHA PAGAMENTO" in memo_upper:
        return Categoria.SALARIO, None
    if any(w in memo_upper for w in ["TRANSFERÊNCIA RECEBIDA", "TED RECEBIDA", "DOC RECEBIDO", "PIX RECEBIDO"]):
        if titular_nomes and any(t_name in memo_upper for t_name in titular_nomes):
            return Categoria.SALARIO, None
        return Categoria.TRANSF_FAMILIAR, None

    # 5. Fatura Cartão
    if "PAGAMENTO DE FATURA" in memo_upper or "FATURA CARTAO" in memo_upper or "FATURA CARTÃO" in memo_upper:
        return Categoria.FATURA, None
    if memo_upper == "FATURA" or memo_upper.startswith("FATURA "):
        return Categoria.FATURA, None

    # 6. Compromisso Familiar / Moradia
    if any(w in memo_upper for w in ["ALUGUEL", "CONDOMINIO", "CONDOMÍNIO", "MORADIA"]):
        return Categoria.MORADIA, None
    if familiar_keywords and any(fk in memo_upper for fk in familiar_keywords):
        return Categoria.MORADIA, None

    # 7. Ailos / Viacredi
    if "AILOS" in memo_upper:
        return Categoria.AILOS, None
    if "VIACREDI" in memo_upper:
        if titular_nomes and any(t_name in memo_upper for t_name in titular_nomes):
            return Categoria.AILOS, None
        return Categoria.TRANSF_FAMILIAR, None

    # 8. RDB / Caixinha
    if any(w in memo_upper for w in ["RDB", "CAIXINHA", "RESERVA"]):
        return Categoria.RDB, None

    # 9. Consulta Trie (palavras-chave dinâmicas do repositório)
    match_trie = trie.match(memo_upper)
    if match_trie:
        cat_name, bucket, _ = match_trie
        for c in Categoria:
            if c.value == cat_name:
                return c, None
        if bucket == "tributo":
            return Categoria.TRIBUTO, None
        if bucket == "variavel":
            return Categoria.VARIAVEL, None

    # 10. Fallback padrão
    if tipo == "DEBIT" or valor < 0:
        return Categoria.VARIAVEL, None
    return Categoria.OUTROS, None


class OFXParserService:
    _keywords_cache: Optional[List[Tuple[str, str, str, int]]] = None
    _keywords_version: int = -1
    _trie: Optional[KeywordTrie] = None

    @classmethod
    def _get_trie(cls, keywords: Optional[List[Tuple[str, str, str, int]]] = None) -> KeywordTrie:
        if keywords is not None:
            return KeywordTrie(keywords)
        if cls._trie is None:
            from barsi.adapters.history.budget_repository import DEFAULT_KEYWORDS
            cls._trie = KeywordTrie(DEFAULT_KEYWORDS)
            cls._keywords_cache = list(DEFAULT_KEYWORDS)
            cls._keywords_version = 1
        return cls._trie

    @classmethod
    def reload_keywords_from_db(cls) -> None:
        """Recarrega keywords do SQLite. Chamado explicitamente pela CLI/API."""
        try:
            from barsi.adapters.history.budget_repository import BudgetRepository
            repo = BudgetRepository()
            cls._keywords_cache = repo.get_keywords()
            cls._keywords_version = repo.keywords_version()
            cls._trie = KeywordTrie(cls._keywords_cache)
        except Exception as exc:
            logger.warning("reload_keywords_failed", error=str(exc))

    @staticmethod
    def identificar_origem_ofx(content: str) -> str:
        """Determina se o extrato é da Conta Corrente ou Fatura de Cartão de Crédito."""
        if "<CREDITCARDMSGSRSV1>" in content or "<CCACCTINFO>" in content:
            return "CARTAO"
        return "CONTA"

    @classmethod
    def parse_ofx_content(
        cls,
        content: str,
        origem_forçada: Optional[str] = None,
        titular_nomes: Optional[List[str]] = None,
        titular: Optional[str] = None,
        familiar_keywords: Optional[List[str]] = None,
        keywords: Optional[List[Tuple[str, str, str, int]]] = None,
    ) -> List[Transacao]:
        """Extrai e classifica transações OFX em memória com convenção rigorosa de sinais."""
        origem = origem_forçada or cls.identificar_origem_ofx(content)
        trie = cls._get_trie(keywords)

        titulares: List[str] = []
        if titular:
            titulares.append(titular.strip().upper())
        if titular_nomes:
            titulares.extend([t.strip().upper() for t in titular_nomes if t.strip()])
        if not titulares:
            titulares = getattr(settings, "titular_nomes", [])

        fam_kws: List[str] = []
        if familiar_keywords:
            fam_kws.extend([k.strip().upper() for k in familiar_keywords if k.strip()])
        if not fam_kws:
            fam_kws = getattr(settings, "familiar_keywords", [])

        # 1. Primeira passada: extrai dados brutos e mapeia FITIDs de reversão (:reversal)
        raw_items: List[Dict[str, Any]] = []
        reversal_base_ids: Set[str] = set()

        for match in _OFX_TRN_RE.finditer(content):
            t_type = match.group("type").strip().upper()
            t_date = match.group("date").strip()
            t_amt = float(match.group("amt").strip())
            t_id = match.group("id").strip()
            t_memo = match.group("memo").strip()

            if t_id.endswith(":reversal"):
                base_id = t_id[:-9]
                reversal_base_ids.add(base_id)

            raw_items.append({
                "type": t_type,
                "date": t_date,
                "amt": t_amt,
                "id": t_id,
                "memo": t_memo,
            })

        # 2. Segunda passada: filtra pares reversos e classifica cada transação
        transacoes: List[Transacao] = []
        for item in raw_items:
            t_id = str(item["id"])
            t_memo = str(item["memo"])
            t_type = str(item["type"])
            t_amt = float(item["amt"])
            t_date = str(item["date"])
            memo_upper = t_memo.upper()

            # Skip Bug 8: transação com sufixo :reversal ou seu par original revertido
            if t_id.endswith(":reversal") or t_id in reversal_base_ids:
                continue

            categoria, ticker = _classify_transaction(
                memo_upper=memo_upper,
                valor=t_amt,
                tipo=t_type,
                titular_nomes=titulares,
                familiar_keywords=fam_kws,
                trie=trie,
            )

            transacoes.append(Transacao(
                id_transacao=t_id,
                tipo=t_type,
                data=f"{t_date[:4]}-{t_date[4:6]}-{t_date[6:8]}",
                valor=t_amt,
                descricao=t_memo,
                categoria=categoria.value,
                ticker_relacionado=ticker,
                origem=origem,
            ))

        return transacoes

    @classmethod
    def processar_diretorio_extratos(
        cls,
        dir_path: str = "extratos",
        titular_nomes: Optional[List[str]] = None,
        familiar_keywords: Optional[List[str]] = None,
    ) -> Dict[str, ResumoExtratoMes]:
        """Processa todos os extratos de um diretório e consolida os resumos mensais.

        Método PURO: não realiza escritas nem efeitos colaterais no banco SQLite.
        """
        resumos: Dict[str, ResumoExtratoMes] = {}
        if not os.path.exists(dir_path):
            return resumos

        # Descompacta automaticamente arquivos .zip presentes no diretório de extratos
        for zip_file in glob.glob(os.path.join(dir_path, "*.zip")):
            try:
                with zipfile.ZipFile(zip_file, "r") as z:
                    for zip_entry in z.namelist():
                        if zip_entry.lower().endswith(".ofx"):
                            base_name = os.path.basename(zip_entry)
                            target_file = os.path.join(dir_path, base_name)
                            if not os.path.exists(target_file):
                                with open(target_file, "wb") as out_f:
                                    out_f.write(z.read(zip_entry))
            except Exception as exc:
                logger.warning("unzip_failed", file=zip_file, error=str(exc))

        transacoes_vistas: Set[Tuple[str, str, float]] = set()
        faturas_por_mes: Dict[str, List[float]] = {}
        rdb_aplicacoes: Dict[str, float] = {}
        rdb_resgates: Dict[str, float] = {}

        todos_ofx = glob.glob(os.path.join(dir_path, "**", "*.ofx"), recursive=True)
        arquivos_ofx = [
            arq for arq in todos_ofx
            if not any(p.lower() in ("processado", "processados") for p in os.path.normpath(arq).split(os.sep))
        ]
        for arq in sorted(arquivos_ofx):
            try:
                with open(arq, "r", encoding="utf-8", errors="ignore") as file_handle:
                    conteudo = file_handle.read()

                origem = cls.identificar_origem_ofx(conteudo)
                txs = cls.parse_ofx_content(
                    conteudo,
                    origem_forçada=origem,
                    titular_nomes=titular_nomes,
                    familiar_keywords=familiar_keywords,
                )

                for t in txs:
                    tx_key = (t.id_transacao, t.tipo, round(t.valor, 2))
                    if tx_key in transacoes_vistas:
                        continue
                    transacoes_vistas.add(tx_key)

                    mes_chave = t.data[:7]
                    if mes_chave not in resumos:
                        resumos[mes_chave] = ResumoExtratoMes(mes_ano=mes_chave)
                        faturas_por_mes[mes_chave] = []
                        rdb_aplicacoes[mes_chave] = 0.0
                        rdb_resgates[mes_chave] = 0.0

                    res = resumos[mes_chave]
                    res.transacoes.append(t)

                    # Se a transação for de CARTÃO, ela detalha a fatura, não o fluxo de caixa da conta corrente
                    if t.origem == "CARTAO":
                        continue

                    cat = t.categoria

                    # 1. Salário / Renda Principal
                    if cat == Categoria.SALARIO.value:
                        if t.valor > 0:
                            res.salario_entradas = round(res.salario_entradas + t.valor, 2)
                        else:
                            res.despesas_variaveis = round(res.despesas_variaveis + abs(t.valor), 2)

                    # 2. Proventos B3 (Dividendos / JCP)
                    elif cat == Categoria.PROVENTOS.value:
                        if t.valor > 0:
                            res.proventos_recebidos = round(res.proventos_recebidos + t.valor, 2)
                        else:
                            res.despesas_variaveis = round(res.despesas_variaveis + abs(t.valor), 2)

                    # 3. Rendimento de Conta (CDI)
                    elif cat == Categoria.RENDIMENTO_CONTA.value:
                        if t.valor > 0:
                            res.rendimento_conta = round(res.rendimento_conta + t.valor, 2)
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)
                        else:
                            res.despesas_variaveis = round(res.despesas_variaveis + abs(t.valor), 2)

                    # 4. Transferências Familiares (Débitos vão para moradia/família; créditos para outras entradas)
                    elif cat == Categoria.TRANSF_FAMILIAR.value:
                        if t.valor > 0:
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)
                        else:
                            res.moradia_e_familia = round(res.moradia_e_familia + abs(t.valor), 2)

                    # 5. Investimentos em Ações e FIIs (Suporta estornos/cancelamentos)
                    elif cat == Categoria.APORTE.value:
                        valor_efetivo = abs(t.valor) if (t.tipo == "DEBIT" or t.valor < 0) else -abs(t.valor)
                        if t.ticker_relacionado:
                            tk = t.ticker_relacionado
                            res.detalhes_ativos[tk] = round(res.detalhes_ativos.get(tk, 0.0) + valor_efetivo, 2)
                            if is_fii(tk):
                                res.compras_fiis = round(res.compras_fiis + valor_efetivo, 2)
                            else:
                                res.compras_acoes_barsi = round(res.compras_acoes_barsi + valor_efetivo, 2)
                        else:
                            res.compras_acoes_barsi = round(res.compras_acoes_barsi + valor_efetivo, 2)
                        res.total_investimentos = round(res.compras_acoes_barsi + res.compras_fiis, 2)

                    # 6. Pagamento de Faturas
                    elif cat == Categoria.FATURA.value:
                        if t.valor < 0 or t.tipo == "DEBIT":
                            faturas_por_mes[mes_chave].append(abs(t.valor))
                        else:
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)

                    # 7. Tributos & Taxas
                    elif cat == Categoria.TRIBUTO.value:
                        if t.valor < 0:
                            res.tributos_e_taxas = round(res.tributos_e_taxas + abs(t.valor), 2)
                        else:
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)

                    # 8. Compromisso Familiar / Moradia
                    elif cat == Categoria.MORADIA.value:
                        if t.valor < 0:
                            res.moradia_e_familia = round(res.moradia_e_familia + abs(t.valor), 2)
                        else:
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)

                    # 9. Cooperativa Ailos
                    elif cat == Categoria.AILOS.value:
                        if t.valor < 0:
                            res.cooperativa_ailos = round(res.cooperativa_ailos + abs(t.valor), 2)
                        else:
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)

                    # 10. Reserva / RDB
                    elif cat == Categoria.RDB.value:
                        if t.valor < 0 or t.tipo == "DEBIT":
                            rdb_aplicacoes[mes_chave] += abs(t.valor)
                        else:
                            rdb_resgates[mes_chave] += t.valor
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)

                    # 11. Despesas Variáveis
                    elif cat == Categoria.VARIAVEL.value:
                        if t.valor < 0:
                            res.despesas_variaveis = round(res.despesas_variaveis + abs(t.valor), 2)
                        else:
                            res.outras_entradas = round(res.outras_entradas + t.valor, 2)

                    # 12. Fallback geral
                    elif t.valor > 0 or t.tipo == "CREDIT":
                        res.outras_entradas = round(res.outras_entradas + t.valor, 2)
                    else:
                        res.despesas_variaveis = round(res.despesas_variaveis + abs(t.valor), 2)

            except Exception as exc:
                logger.error("parse_extrato_file_failed", file=arq, error=str(exc))

        # 3. Consolidação mensal (Faturas múltiplas, RDB líquido, etc.)
        for mes, r in resumos.items():
            debitos_fatura = faturas_por_mes.get(mes, [])
            if debitos_fatura:
                maior_fatura = max(debitos_fatura)
                r.faturas_cartao = round(maior_fatura, 2)
                r.encargos_cartao = round(sum(debitos_fatura) - maior_fatura, 2)
            elif r.faturas_cartao == 0.0:
                gastos_cartao = sum(
                    abs(t.valor) for t in r.transacoes
                    if t.origem == "CARTAO" and (t.tipo == "DEBIT" or t.valor < 0)
                )
                if gastos_cartao > 0:
                    r.faturas_cartao = round(gastos_cartao, 2)

            # RDB Líquido
            aplic = rdb_aplicacoes.get(mes, 0.0)
            resg = rdb_resgates.get(mes, 0.0)
            r.reserva_rdb = round(max(0.0, aplic - resg), 2)

            r.faturas_e_boletos = round(
                r.faturas_cartao + r.encargos_cartao + r.tributos_e_taxas + r.moradia_e_familia + r.cooperativa_ailos, 2
            )
            r.total_entradas = round(r.salario_entradas + r.proventos_recebidos + r.outras_entradas, 2)
            r.total_saidas = round(
                r.faturas_e_boletos + r.reserva_rdb + r.total_investimentos + r.despesas_variaveis, 2
            )
            r.saldo_livre = round(r.total_entradas - r.total_saidas, 2)

        return resumos

    @classmethod
    def arquivar_arquivos_processados(cls, dir_path: str = "extratos", subpasta: str = "processados") -> int:
        """Move arquivos .ofx e .zip do diretório de extratos para uma subpasta de arquivamento.

        Evita que arquivos já processados permaneçam soltos na pasta raiz de extratos.
        """
        import shutil
        from datetime import datetime

        if not os.path.exists(dir_path):
            return 0

        dest_dir = os.path.join(dir_path, subpasta)
        os.makedirs(dest_dir, exist_ok=True)

        movidos = 0
        for pattern in ("*.ofx", "*.zip"):
            for src_file in glob.glob(os.path.join(dir_path, pattern)):
                if os.path.isfile(src_file):
                    fname = os.path.basename(src_file)
                    dest_file = os.path.join(dest_dir, fname)
                    if os.path.exists(dest_file):
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        name, ext = os.path.splitext(fname)
                        dest_file = os.path.join(dest_dir, f"{name}_{ts}{ext}")
                    try:
                        shutil.move(src_file, dest_file)
                        movidos += 1
                    except Exception as exc:
                        logger.warning("archive_file_failed", file=src_file, error=str(exc))
        return movidos
