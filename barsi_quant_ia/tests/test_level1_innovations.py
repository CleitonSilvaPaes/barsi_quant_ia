"""Testes automatizados para as inovações Nível 1:
- Radar Screening B3 Automatizado (RadarScreeningService)
- Rebalanceamento Setorial Inteligente (PortfolioBalanceService)
- Barsi Wrapped em Imagem PNG (WrappedImageService)
- Endpoints REST e Comandos Telegram (/radar_b3, /setores, /wrapped_img)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from barsi.domain.models import Position, AcaoBarsi
from barsi.services.radar_screening_service import RadarScreeningService
from barsi.services.portfolio_balance_service import PortfolioBalanceService
from barsi.services.wrapped_image_service import WrappedImageService
from barsi.adapters.history.budget_repository import BudgetRepository


class MockHistoryItem:
    def __init__(self, close: float):
        self.close = close


@pytest.fixture
def mock_market_adapter():
    adapter = MagicMock()
    # Simula cotações para diferentes tickers
    async def get_history(ticker: str, period: str = "1mo"):
        prices = {
            "BBAS3": 25.0,  # Com div_proj 2.50 -> teto 41.67 -> margem 40.0%
            "EGIE3": 35.0,
            "SAPR11": 22.0,
            "VALE3": 60.0,
        }
        close_price = prices.get(ticker, 30.0)
        return [MockHistoryItem(close=close_price)]

    adapter.get_history = AsyncMock(side_effect=get_history)
    return adapter


@pytest.fixture
def sample_positions():
    return [
        Position(
            ticker="BBAS3",
            row_index=1,
            empresa="Banco do Brasil",
            setor="BANCOS",
            qtd_antes=500,
            preco_atual=25.0,
            div_proj=2.50,
            status_barsi=AcaoBarsi.ACUMULAR,
            margem_seguranca=40.0,
        ),
        Position(
            ticker="ITSA4",
            row_index=2,
            empresa="Itaúsa",
            setor="BANCOS",
            qtd_antes=1000,
            preco_atual=10.0,
            div_proj=0.90,
            status_barsi=AcaoBarsi.ACUMULAR,
            margem_seguranca=33.3,
        ),
        Position(
            ticker="TAEE11",
            row_index=3,
            empresa="Taesa",
            setor="ENERGIA",
            qtd_antes=200,
            preco_atual=35.0,
            div_proj=3.00,
            status_barsi=AcaoBarsi.MANTER,
            margem_seguranca=10.0,
        ),
    ]


# ----------------- TESTES DO RADAR SCREENING B3 -----------------

@pytest.mark.asyncio
async def test_radar_screening_service_filters_margin(mock_market_adapter, sample_positions):
    """Garante que apenas ativos com margem >= margem_min são retornados e ordenados."""
    tickers = ["BBAS3", "EGIE3", "VALE3"]
    oportunidades = await RadarScreeningService.screen_opportunities(
        market_adapter=mock_market_adapter,
        margem_min=25.0,
        tickers=tickers,
        canonical_positions=sample_positions,
    )

    assert len(oportunidades) >= 1
    # BBAS3 tem margem 40% na carteira
    bbas = next((o for o in oportunidades if o["ticker"] == "BBAS3"), None)
    assert bbas is not None
    assert bbas["na_carteira"] is True
    assert bbas["margem_seguranca"] >= 25.0

    # Ordenação decrescente por margem de segurança
    for i in range(len(oportunidades) - 1):
        assert oportunidades[i]["margem_seguranca"] >= oportunidades[i + 1]["margem_seguranca"]


def test_radar_screening_format_telegram():
    """Valida a formatação de resumo Telegram do radar."""
    sample_ops = [
        {
            "ticker": "BBAS3",
            "preco_atual": 25.0,
            "preco_teto": 41.67,
            "margem_seguranca": 40.0,
            "setor": "BANCOS",
            "na_carteira": True,
        },
        {
            "ticker": "EGIE3",
            "preco_atual": 35.0,
            "preco_teto": 46.67,
            "margem_seguranca": 25.0,
            "setor": "ENERGIA",
            "na_carteira": False,
        },
    ]
    summary = RadarScreeningService.format_telegram_summary(sample_ops, margem_min=20.0)
    assert "Radar B3 Ampliado" in summary
    assert "BBAS3" in summary
    assert "EGIE3" in summary
    assert "💼" in summary  # Indicador de ativo na carteira


@pytest.mark.asyncio
async def test_radar_screening_execute_and_notify_preference(tmp_path, mock_market_adapter):
    """Verifica que notificação respeita preferência de usuário no SQLite."""
    db_file = str(tmp_path / "test_pref.db")
    repo = BudgetRepository(db_file)
    notifier = MagicMock()
    notifier.notify = AsyncMock(return_value=True)

    # Desabilita preferência
    repo.set_notification_enabled("radar_b3", False)

    res = await RadarScreeningService.execute_and_notify(
        market_adapter=mock_market_adapter,
        notifier=notifier,
        repo=repo,
        margem_min=20.0,
        force=False,
    )
    assert res["enviado"] is False
    assert res["motivo"] == "preferencia_desabilitada"
    assert notifier.notify.call_count == 0


# ----------------- TESTES DO REBALANCEAMENTO SETORIAL -----------------

def test_portfolio_balance_detects_imbalance(sample_positions):
    """Detecta hiperconcentração em Bancos (500*25 + 1000*10 = 22500 de 29500 = 76%)."""
    # Total: BBAS3 (12.500) + ITSA4 (10.000) = Bancos (22.500)
    # TAEE11 (7.000) = Energia (7.000)
    # Total carteira: 29.500. Bancos = 76.3%
    analise = PortfolioBalanceService.analyze_sector_allocation(sample_positions, max_concentration=35.0)

    assert analise["patrimonio_total"] == 29500.0
    assert analise["tem_alerta"] is True
    assert len(analise["setores_concentrados"]) == 1
    assert analise["setores_concentrados"][0]["setor"] == "BANCOS"
    assert analise["setores_concentrados"][0]["percentual"] > 70.0

    # Setores faltantes B.E.S.T. (Saneamento, Telecom, Seguros)
    assert "SANEAMENTO" in analise["setores_faltantes_best"]


def test_portfolio_balance_format_telegram(sample_positions):
    """Valida formatação do alerta de rebalanceamento no Telegram."""
    analise = PortfolioBalanceService.analyze_sector_allocation(sample_positions, max_concentration=35.0)
    msg = PortfolioBalanceService.format_telegram_report(analise)

    assert "Diagnóstico Setorial" in msg
    assert "BANCOS" in msg
    assert "Atenção: Concentração acima do teto de segurança!" in msg
    assert "Diretriz Barsi:" in msg


# ----------------- TESTES DO WRAPPED EM IMAGEM (PNG) -----------------

def test_wrapped_image_generation():
    """Valida geração dos bytes de imagem PNG do Wrapped."""
    sample_data = {
        "year": 2026,
        "total_proventos": 12450.80,
        "media_mensal": 1037.56,
        "proventos_por_dia": 34.11,
        "melhor_mes": {"nome": "Novembro", "total": 3150.0},
        "campeao": {"ticker": "BBAS3", "total": 5200.0, "percentual": 41.8},
        "pct_best": 85.5,
        "meses_com_renda": 11,
        "bola_de_neve": {"cotas_compradas": 45, "ticker_referencia": "BBAS3"},
        "badges": [
            {"id": "b1", "icon": "🏛️", "titulo": "Fortaleza B.E.S.T.", "desbloqueado": True},
            {"id": "b2", "icon": "❄️", "titulo": "Bola de Neve", "desbloqueado": True},
        ],
    }

    img_bytes = WrappedImageService.generate_image_bytes(sample_data)
    assert img_bytes is not None
    assert len(img_bytes) > 1000  # Imagem PNG real não vazia

    # Valida assinatura do cabeçalho PNG (Magic Bytes)
    png_signature = b"\x89PNG\r\n\x1a\n"
    assert img_bytes.startswith(png_signature)


# ----------------- TESTES DO NOTIFICADOR SEND_PHOTO -----------------

@pytest.mark.asyncio
async def test_telegram_notifier_send_photo_success(respx_mock):
    """Valida envio de foto via TelegramNotifier."""
    from barsi.adapters.notifications.telegram_adapter import TelegramNotifier

    bot_token = "123456:tok_photo"
    chat_id = "987654"
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    respx_mock.post(url).respond(status_code=200, json={"ok": True, "result": {"message_id": 202}})
    notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    dummy_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    ok = await notifier.send_photo(dummy_png, filename="test.png", caption="Legenda teste")
    assert ok is True


# ----------------- TESTES DOS ENDPOINTS REST LEVEL 1 -----------------

@pytest.fixture
def auth_client():
    from starlette.testclient import TestClient
    from barsi.api.http import app
    from barsi.infra.security import create_session_token
    from barsi.config import settings

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)
    return client


def test_api_carteira_setores(auth_client):
    """Testa endpoint GET /api/carteira/setores."""
    resp = auth_client.get("/api/carteira/setores")
    assert resp.status_code == 200
    data = resp.json()
    assert "patrimonio_total" in data
    assert "setores" in data
    assert "concentracao_maxima" in data


def test_api_wrapped_image_download(auth_client):
    """Testa endpoint GET /api/wrapped/{year}/image retornando image/png."""
    resp = auth_client.get("/api/wrapped/2026/image")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_api_radar_b3(auth_client):
    """Testa endpoint GET /api/radar/b3."""
    resp = auth_client.get("/api/radar/b3?margem_min=10.0")
    assert resp.status_code == 200
    data = resp.json()
    assert "margem_min" in data
    assert "oportunidades" in data
