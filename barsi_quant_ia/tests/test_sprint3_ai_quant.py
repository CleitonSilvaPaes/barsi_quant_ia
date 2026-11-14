"""
Testes automatizados para a Sprint 3 do Roadmap Barsi Quant IA v3.0:
- Inteligência Artificial: Alertas de Mudança de Tese (Early Warning via NLP)
- Análise Quantitativa: Backtesting de Bola de Neve (Simulador Previdenciário Barsi)
- Endpoints REST associados na API FastAPI
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.early_warning import EarlyWarningDetector, ThesisAlertResult
from barsi.services.early_warning_service import EarlyWarningService
from barsi.services.snowball_backtest_service import SnowballBacktestService
from barsi.domain.models import Position, NewsSummary
from barsi.api.http import app


@pytest.fixture
def repo(tmp_path):
    """Instancia um BudgetRepository isolado em banco temporário."""
    db_file = tmp_path / "test_sprint3.db"
    return BudgetRepository(db_path=str(db_file))


# =====================================================================
# 1. Testes de Detecção de Padrões NLP (EarlyWarningDetector)
# =====================================================================

def test_early_warning_detector_patterns():
    detector = EarlyWarningDetector()

    # 1. Corte de proventos
    corte_text = "Conselho de Administração aprova suspensão e corte de dividendos para preservação de caixa."
    res = detector.analyze_news("TAEE11", corte_text)
    assert res is not None
    assert res.ticker == "TAEE11"
    assert res.risk_type == "corte_dividendos"
    assert res.severity == "CRITICA"
    assert "Corte ou Suspensão" in res.headline

    # 2. Fraude ou Recuperação Judicial
    recup_text = "Companhia protocola pedido de recuperação judicial após descoberta de inconsistências contábeis de 20 bilhões."
    res = detector.analyze_news("AMER3", recup_text)
    assert res is not None
    assert res.risk_type == "fraude_governanca"
    assert res.severity == "CRITICA"

    # 3. Perda de concessão
    concessao_text = "Aneel cassa concessão e determina rescisão de contrato público de distribuição."
    res = detector.analyze_news("EQTL3", concessao_text)
    assert res is not None
    assert res.risk_type == "perda_concessao"
    assert res.severity == "CRITICA"

    # 4. Solvência e Dívida
    divida_text = "Companhia entra em default e anuncia reestruturação de dívida com bancos credores."
    res = detector.analyze_news("CSNA3", divida_text)
    assert res is not None
    assert res.risk_type == "solvencia_divida"
    assert res.severity == "GRAVE"

    # 5. Notícia inócua / rotineira
    neutra_text = "Empresa divulga calendário de reuniões ordinárias para o próximo exercício social sem alterações."
    res = detector.analyze_news("ITSA4", neutra_text)
    assert res is None


# =====================================================================
# 2. Testes de Serviço EarlyWarningService & Persistência no SQLite
# =====================================================================

def test_early_warning_service_scan_and_dismiss(repo):
    # Injeta um alerta simulado diretamente no repositório
    alert_id = repo.record_thesis_alert(
        ticker="TAEE11",
        risk_type="corte_dividendos",
        severity="CRITICA",
        headline="Corte ou Suspensão de Dividendos",
        snippet="Diretoria recomenda corte de proventos extraordinários",
        source="google_rss"
    )
    assert alert_id > 0

    # Listagem de alertas ativos
    alerts = repo.list_thesis_alerts(active_only=True)
    assert len(alerts) == 1
    assert alerts[0]["ticker"] == "TAEE11"
    assert alerts[0]["risk_type"] == "corte_dividendos"
    assert alerts[0]["active"] == 1

    # Dispensar o alerta
    ok = repo.dismiss_thesis_alert(alert_id)
    assert ok is True

    # Verifica que não há mais alertas ativos
    alerts_active = repo.list_thesis_alerts(active_only=True)
    assert len(alerts_active) == 0

    # Mas ainda existe no histórico inativo
    alerts_all = repo.list_thesis_alerts(active_only=False)
    assert len(alerts_all) == 1
    assert alerts_all[0]["active"] == 0


@pytest.mark.asyncio
async def test_early_warning_scan_with_mocked_news(repo):
    """Testa o método scan_portfolio do EarlyWarningService com notícias assíncronas mockadas."""
    # Adiciona uma posição na carteira
    pos = Position(
        row_index=1,
        ticker="TAEE11",
        empresa="Taesa Transmissora",
        setor="Energia Elétrica",
        preco_atual=35.0,
        preco_teto=40.0,
        margem_seguranca=0.14,
        qtd_antes=100,
        qtd_aporte=0,
        div_proj=3.0,
        decisao="ACUMULAR",
        valor_total=3500.0
    )
    repo.save_positions([pos])

    mock_news = AsyncMock()
    mock_news.get_latest_news.return_value = NewsSummary(
        ticker="TAEE11",
        headlines="Taesa aprova suspensão e corte de dividendos para reter caixa",
        sentiment="negativo",
        source="google_rss"
    )

    mock_notifier = AsyncMock()
    service = EarlyWarningService(
        budget_repo=repo,
        news_adapter=mock_news,
        notification_adapter=mock_notifier
    )

    alerts = await service.scan_portfolio()
    assert len(alerts) == 1
    assert alerts[0]["ticker"] == "TAEE11"
    assert alerts[0]["risk_type"] == "corte_dividendos"

    # Confirma que foi persistido no SQLite
    db_alerts = repo.list_thesis_alerts(active_only=True)
    assert len(db_alerts) == 1
    assert db_alerts[0]["ticker"] == "TAEE11"

    # Confirma notificação disparada (via notify ou send_message)
    assert mock_notifier.notify.await_count == 1 or mock_notifier.send_message.await_count == 1


# =====================================================================
# 3. Testes do Simulador de Bola de Neve (SnowballBacktestService)
# =====================================================================

def test_snowball_simulation_math(repo):
    service = SnowballBacktestService(budget_repo=repo)

    result = service.simulate_snowball(
        aporte_mensal=1500.0,
        taxa_reinvestimento=1.0,
        anos=20,
        dy_base=8.0,
        crescimento_anual_proventos=3.0,
        valor_inicial=10000.0
    )

    resumo = result["resumo"]
    curva = result["curva"]

    # Validações matemáticas do resumo
    assert resumo["total_aportado_bolso"] == 10000.0 + (1500.0 * 12 * 20)  # R$ 370.000
    assert resumo["patrimonio_final"] > resumo["patrimonio_sem_reinvestimento"]
    assert resumo["vantagem_drip_reinvestimento"] > 0
    assert resumo["renda_mensal_final"] > 0
    assert resumo["equivalente_salarios_minimos"] > 0
    assert resumo["multiplicador_patrimonial"] > 1.0

    # Verifica se houve detecção de crossover (ponto de inflexão)
    assert resumo["ano_crossover"] is not None
    assert 1 <= resumo["ano_crossover"] <= 20

    # Validação dos pontos da curva (ano 0 a 20 = 21 pontos)
    assert len(curva) == 21
    for ponto in curva[1:]:
        assert ponto["patrimonio"] >= ponto["sem_reinvestimento"]
        assert ponto["provento_anual"] > 0


def test_snowball_simulation_sem_reinvestimento(repo):
    service = SnowballBacktestService(budget_repo=repo)

    result = service.simulate_snowball(
        aporte_mensal=1000.0,
        taxa_reinvestimento=0.0,
        anos=10,
        dy_base=7.0,
        crescimento_anual_proventos=0.0,
        valor_inicial=5000.0
    )

    resumo = result["resumo"]
    # Sem reinvestimento e sem crescimento, a vantagem do DRIP deve ser zero
    assert resumo["vantagem_drip_reinvestimento"] == 0.0
    assert resumo["patrimonio_final"] == resumo["patrimonio_sem_reinvestimento"]


# =====================================================================
# 4. Testes de Integração com a API FastAPI
# =====================================================================

def test_api_early_warning_endpoints():
    client = TestClient(app)

    # 1. Listar alertas (usuário logado via mock)
    with patch("barsi.api.http.get_current_user", return_value={"username": "admin", "role": "admin"}):
        res = client.get("/api/early-warning/alerts")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    # 2. Disparar varredura
    with patch("barsi.api.http.require_role_action", return_value={"username": "admin", "role": "admin"}), \
         patch("barsi.api.http.EarlyWarningService.scan_portfolio", new_callable=AsyncMock) as mock_scan:
        mock_scan.return_value = []
        res_scan = client.post("/api/early-warning/scan")
        assert res_scan.status_code == 200
        data = res_scan.json()
        assert data["status"] == "ok"
        assert data["alerts_count"] == 0

    # 3. Dispensar alerta
    with patch("barsi.api.http.require_role_action", return_value={"username": "admin", "role": "admin"}), \
         patch("barsi.api.http.EarlyWarningService.dismiss_alert", return_value=True):
        res_dis = client.post("/api/early-warning/1/dismiss")
        assert res_dis.status_code == 200
        assert res_dis.json()["status"] == "ok"


def test_api_snowball_simulate_endpoint():
    client = TestClient(app)

    payload = {
        "valor_inicial": 25000.0,
        "aporte_mensal": 2000.0,
        "anos": 15,
        "dy_base": 7.5,
        "crescimento_anual_proventos": 2.5,
        "taxa_reinvestimento": 1.0
    }

    with patch("barsi.api.http.get_current_user", return_value={"username": "admin", "role": "admin"}):
        res = client.post("/api/snowball/simulate", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "resumo" in data
        assert "curva" in data
        assert data["resumo"]["patrimonio_final"] > data["resumo"]["total_aportado_bolso"]
        assert len(data["curva"]) == 16  # ano 0 a 15
