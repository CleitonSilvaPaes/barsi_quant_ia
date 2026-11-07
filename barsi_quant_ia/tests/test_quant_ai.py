"""Testes unitários para Inteligência Quantitativa, Deep Learning NLP e Q-Learning RL."""
from barsi.domain.models import Position, AcaoBarsi
from barsi.domain.quant_ai import (
    SentimentNLPNeuralNet,
    StatisticalBarsiEngine,
    QLearningBarsiAgent,
    gerar_insight_quant_avancado
)
from barsi.domain.rules import gerar_insight_deterministico

def test_nlp_neural_net_sentiment():
    nlp = SentimentNLPNeuralNet()
    
    # 1. Notícia positiva de dividendos
    score_pos, label_pos = nlp.predict_sentiment("Banco do Brasil anuncia pagamento recorde de dividendos e JCP")
    assert score_pos > 0.2
    assert label_pos == "POSITIVO"
    
    # 2. Notícia de risco/corte
    score_neg, label_neg = nlp.predict_sentiment("Empresa sofre prejuízo com queda no lucro e risco de recuperação judicial")
    assert score_neg < -0.2
    assert label_neg == "CAUTELA"
    
    # 3. Notícia sem termos-chave
    score_neu, label_neu = nlp.predict_sentiment("Reunião trimestral do conselho marcada para a próxima terça-feira")
    assert label_neu == "NEUTRO"

def test_statistical_barsi_engine():
    # Posição BBAS3 com margem favorável
    p = Position(ticker="BBAS3", preco_atual=27.50, div_proj=2.85, quantidade=100, row_index=10)
    p.preco_teto = round(p.div_proj / 0.06, 2)
    p.margem_seguranca = round(((p.preco_teto - p.preco_atual) / p.preco_atual) * 100.0, 2)
    
    stats = StatisticalBarsiEngine.calcular_metricas_estatisticas(p)
    assert stats["setor"] == "BANCOS"
    assert "z_score" in stats
    assert stats["yield_projetado_pct"] > 6.0
    assert 0 <= stats["score_barsi_quant"] <= 100

def test_qlearning_agent_decision():
    agent = QLearningBarsiAgent()
    
    # Score alto + sentimento positivo deve recomendar ACUMULAR_FORTE
    action, conf = agent.select_action(score_quant=85.0, sentiment_label="POSITIVO")
    assert action == "ACUMULAR_FORTE"
    
    # Score baixo + sentimento cautela deve recomendar AGUARDAR
    action_aguardar, _ = agent.select_action(score_quant=30.0, sentiment_label="CAUTELA")
    assert action_aguardar == "AGUARDAR"

def test_gerar_insight_quant_avancado_integrado():
    p = Position(ticker="SAPR4", preco_atual=5.20, div_proj=0.48, quantidade=200, row_index=11)
    p.preco_teto = round(p.div_proj / 0.06, 2)
    p.margem_seguranca = round(((p.preco_teto - p.preco_atual) / p.preco_atual) * 100.0, 2)
    
    res = gerar_insight_deterministico(p, noticias="Sanepar aprova distribuição de dividendos e bonificação")
    assert res.ticker == "SAPR4"
    assert "Score Quant B.E.S.T." in res.insight
    assert "Sentimento NLP B3" in res.insight
    assert "Diretriz RL" in res.insight
    assert res.acao_sugerida == AcaoBarsi.ACUMULAR

