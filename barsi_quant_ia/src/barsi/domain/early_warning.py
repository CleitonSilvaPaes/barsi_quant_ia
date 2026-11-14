"""Detector de Alertas de Mudança de Tese Barsi (Early Warning NLP).

Identifica sinais precoces de quebra de tese de longo prazo em notícias da B3:
- Corte ou suspensão de dividendos
- Recuperação judicial, fraude contábil e risco de governança
- Perda ou não renovação de concessão (setor de utilities/energia/saneamento)
- Risco de solvência, default ou endividamento crítico
- Intervenção regulatória
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class ThesisAlertResult:
    ticker: str
    risk_type: str
    severity: str
    headline: str
    snippet: str
    recommendation: str


def _normalize_text(text: str) -> str:
    """Normaliza texto removendo acentos e convertendo para minúsculas."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    clean = "".join(c for c in nfkd if not unicodedata.combining(c))
    return clean.lower()


class EarlyWarningDetector:
    """Classificador de alta precisão léxico-semântico para detecção de quebra de tese."""

    PATTERNS: Dict[str, Dict[str, Any]] = {
        "corte_dividendos": {
            "severity": "CRITICA",
            "title": "Corte ou Suspensão de Dividendos",
            "regex": [
                r"\b(cort(e|ou|aram)|reduz(iu|ao|iram)?|cancel(ou|amento)?|suspens(ao|eu|as)?|elimin(ou|acao)?)\s+(de\s+)?(dividendos?|proventos?|jcp)\b",
                r"\b(sem\s+dividendos?|sem\s+pagamento|suspendeu\s+pagamento|zerou\s+dividendos?|reter\s+lucro)\b",
                r"\b(reducao\s+drastica\s+de\s+payout|corte\s+no\s+payout)\b"
            ],
            "recommendation": "ALERTA VERMELHO: O ativo reportou corte ou suspensão de proventos. Suspenda imediatamente novos aportes e revise o histórico de lucro da empresa."
        },
        "fraude_governanca": {
            "severity": "CRITICA",
            "title": "Fraude, Rombo ou Recuperação Judicial",
            "regex": [
                r"\b(recuperacao\s+judicial|pedido\s+de\s+falencia|fraude(\s+contabil)?|rombo(\s+bilionario)?)\b",
                r"\b(escandalo|corrupcao|investigacao\s+da\s+cvm|operacao\s+da\s+pf|falsificacao\s+de\s+balanco)\b",
                r"\b(renuncia\s+em\s+massa|conflito\s+de\s+interesses\s+grave)\b"
            ],
            "recommendation": "ALERTA MÁXIMO: Risco severo de governança ou solvência jurídica. Metodologia Barsi desaconselha investimento em empresas sob risco de falência ou fraude."
        },
        "perda_concessao": {
            "severity": "CRITICA",
            "title": "Perda ou Não Renovação de Concessão",
            "regex": [
                r"\b(perda\s+de\s+concessao|caducidade\s+de\s+concessao|nao\s+renov(ou|acao)|rescisao\s+de\s+contrato\s+publico)\b",
                r"\b(cassacao\s+de\s+outorga|fim\s+da\s+concessao\s+sem\s+indenizacao)\b"
            ],
            "recommendation": "ALERTA ESTRATÉGICO: Empresa corre risco de perda de concessão pública, abalando a previsibilidade de receita do setor perene B.E.S.T."
        },
        "solvencia_divida": {
            "severity": "GRAVE",
            "title": "Risco de Solvência ou Calote de Dívida",
            "regex": [
                r"\b(calote|default|reestruturacao\s+de\s+divida|rebaixamento\s+de\s+rating|crise\s+de\s+liquidez)\b",
                r"\b(calote\s+de\s+debenture|risco\s+de\s+solvencia|alavancagem\s+critica|divida\s+impagavel)\b"
            ],
            "recommendation": "ALERTA DE ENDIVIDAMENTO: Alavancagem financeira abusiva detectada. Barsi evita ativos cuja dívida consuma o fluxo de caixa dos acionistas."
        },
        "regulatorio": {
            "severity": "GRAVE",
            "title": "Intervenção Regulatória ou Bloqueio Judicial",
            "regex": [
                r"\b(intervencao\s+da\s+aneel|intervencao\s+do\s+banco\s+central|multa\s+bilionaria|bloqueio\s+judicial\s+de\s+bens)\b",
                r"\b(intervencao\s+estatal|congelamento\s+de\s+tarifas?)\b"
            ],
            "recommendation": "ALERTA REGULATÓRIO: Risco de intervenção governamental ou regulatória que compromete a margem operacional do ativo."
        }
    }

    def analyze_news(self, ticker: str, text: str) -> Optional[ThesisAlertResult]:
        """Varre o texto da notícia em busca de gatilhos de quebra de tese."""
        if not text:
            return None

        normalized = _normalize_text(text)
        ticker_norm = ticker.strip().upper()

        for risk_key, conf in self.PATTERNS.items():
            for pat in conf["regex"]:
                match = re.search(pat, normalized, re.IGNORECASE)
                if match:
                    # Extrai contexto do snippet ao redor do match
                    start = max(0, match.start() - 40)
                    end = min(len(text), match.end() + 60)
                    snippet = text[start:end].strip()

                    return ThesisAlertResult(
                        ticker=ticker_norm,
                        risk_type=risk_key,
                        severity=conf["severity"],
                        headline=f"⚠️ {conf['title']} detectado para {ticker_norm}",
                        snippet=snippet if snippet else text[:120],
                        recommendation=conf["recommendation"]
                    )

        return None

