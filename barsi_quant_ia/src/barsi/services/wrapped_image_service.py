"""Serviço de Renderização de Imagem para o Barsi Wrapped (PNG Stories / WhatsApp).

Gera um card visual moderno em alta resolução (1080x1350) otimizado para
compartilhamento vertical em redes sociais e Telegram, consumindo menos de 5MB
de memória transitória na TV Box.
"""
from __future__ import annotations

import io
from typing import Any, Dict, Optional
from barsi.infra.logging import logger


class WrappedImageService:
    """Renderizador server-side de cards visuais do Wrapped via Pillow."""

    @classmethod
    def generate_image_bytes(cls, data: Dict[str, Any]) -> Optional[bytes]:
        """Gera imagem PNG formatada com métricas do Wrapped anual."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("wrapped_image_service_pillow_not_installed")
            return None

        try:
            width = 1080
            height = 1440
            img = Image.new("RGB", (width, height), color="#0b0f19")
            draw = ImageDraw.Draw(img)

            # 1. Gradiente / Linhas de background decorativas
            # Fundo com sutis faixas e moldura elegante esmeralda
            draw.rectangle([(20, 20), (width - 20, height - 20)], outline="#1e293b", width=3)
            draw.rectangle([(30, 30), (width - 30, height - 30)], outline="#10b981", width=2)

            # Tenta carregar fontes do sistema ou padrão
            def get_font(size: int, bold: bool = False):
                font_names = [
                    "segoeui.ttf", "segoeuib.ttf" if bold else "segoeui.ttf",
                    "arial.ttf", "arialbd.ttf" if bold else "arial.ttf",
                    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
                ]
                for fn in font_names:
                    try:
                        return ImageFont.truetype(fn, size)
                    except Exception:
                        continue
                return ImageFont.load_default()

            font_title = get_font(52, bold=True)
            font_subtitle = get_font(28)
            font_hero = get_font(72, bold=True)
            font_card_val = get_font(38, bold=True)
            font_card_lbl = get_font(22)
            font_quote = get_font(24)
            font_footer = get_font(20)

            # Cabeçalho
            draw.text((width // 2, 85), "BARSI QUANT IA", fill="#10b981", font=font_subtitle, anchor="mm")
            ano = data.get("year", 2026)
            draw.text((width // 2, 145), f"RETROSPECTIVA {ano}", fill="#f8fafc", font=font_title, anchor="mm")
            draw.text((width // 2, 195), "Relatório Oficial de Liberdade Financeira & Dividendos", fill="#94a3b8", font=font_subtitle, anchor="mm")

            # 2. Hero Card — Proventos Totais
            hero_box = [(70, 240), (width - 70, 430)]
            draw.rectangle(hero_box, fill="#131c2e", outline="#334155", width=2)
            # Destaque lateral esmeralda
            draw.rectangle([(70, 240), (82, 430)], fill="#10b981")

            draw.text((width // 2, 280), "TOTAL DE PROVENTOS RECEBIDOS", fill="#38bdf8", font=font_card_lbl, anchor="mm")
            total_prov = data.get("total_proventos", 0.0)
            val_formatado = f"R$ {total_prov:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            draw.text((width // 2, 345), val_formatado, fill="#4ade80", font=font_hero, anchor="mm")
            
            media_dia = data.get("proventos_por_dia", 0.0)
            media_dia_fmt = f"R$ {media_dia:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            draw.text((width // 2, 400), f"Equivalente a {media_dia_fmt} caindo na conta todos os dias", fill="#94a3b8", font=font_card_lbl, anchor="mm")

            # 3. Grid de Métricas (2 colunas x 3 linhas)
            cards = []
            
            # Média Mensal
            med_mensal = data.get("media_mensal", 0.0)
            med_fmt = f"R$ {med_mensal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            cards.append(("MÉDIA MENSAL", med_fmt, "#38bdf8"))

            # Melhor Mês
            melhor = data.get("melhor_mes")
            melhor_lbl = f"{melhor.get('nome') or '—'}" if melhor else "—"
            melhor_total = float((melhor.get("total") or 0.0)) if melhor else 0.0
            melhor_val = f"R$ {melhor_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            cards.append((f"MELHOR MÊS ({melhor_lbl})", melhor_val, "#facc15"))

            # Campeão de Dividendos
            campeao = data.get("campeao")
            camp_ticker = campeao.get("ticker", "—") if campeao else "—"
            camp_total = float((campeao.get("total") or 0.0)) if campeao else 0.0
            camp_val = f"R$ {camp_total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            cards.append(("REI DOS DIVIDENDOS", f"{camp_ticker} ({camp_val})", "#ec4899"))

            # Bola de Neve
            bola = data.get("bola_de_neve", {})
            cotas = bola.get("cotas_compradas", 0)
            ref_t = bola.get("ticker_referencia", "Ações")
            cards.append(("EFEITO BOLA DE NEVE", f"+{cotas} cotas de {ref_t}", "#a855f7"))

            # Alocação B.E.S.T.
            pct_best = data.get("pct_best", 0.0)
            cards.append(("FORTALEZA B.E.S.T.", f"{pct_best:.1f}% em Perenes", "#10b981"))

            # Meses com Proventos
            meses_renda = data.get("meses_com_renda", 0)
            cards.append(("CONSTÂNCIA PREVIDENCIÁRIA", f"{meses_renda}/12 Meses com Renda", "#06b6d4"))

            start_y = 460
            card_h = 130
            card_w = (width - 160) // 2

            for i, (label, val, accent) in enumerate(cards):
                col = i % 2
                row = i // 2
                x1 = 70 + col * (card_w + 20)
                y1 = start_y + row * (card_h + 18)
                x2 = x1 + card_w
                y2 = y1 + card_h

                draw.rectangle([(x1, y1), (x2, y2)], fill="#131c2e", outline="#1e293b", width=1)
                draw.rectangle([(x1, y1), (x1 + 6, y2)], fill=accent)

                draw.text((x1 + 20, y1 + 30), label, fill="#64748b", font=font_card_lbl)
                draw.text((x1 + 20, y1 + 75), str(val), fill="#f1f5f9", font=font_card_val)

            # 4. Badges / Conquistas
            badges_y = start_y + 3 * (card_h + 18) + 15
            draw.text((width // 2, badges_y + 10), "CONQUISTAS DO ANO DESBLOQUEADAS", fill="#94a3b8", font=font_card_lbl, anchor="mm")

            badges = data.get("badges", [])
            unlocked = [b for b in badges if b.get("desbloqueado")][:4]
            if unlocked:
                b_x_start = 70
                b_w = (width - 140 - ((len(unlocked) - 1) * 15)) // max(1, len(unlocked))
                for j, b in enumerate(unlocked):
                    bx1 = b_x_start + j * (b_w + 15)
                    bx2 = bx1 + b_w
                    by1 = badges_y + 30
                    by2 = by1 + 75
                    draw.rectangle([(bx1, by1), (bx2, by2)], fill="#0f172a", outline="#334155", width=1)
                    titulo_curto = b.get("titulo", "")[:16]
                    draw.text((bx1 + b_w // 2, by1 + 38), f"★ {titulo_curto}", fill="#e2e8f0", font=font_card_lbl, anchor="mm")

            # 5. Frase de Luiz Barsi e Rodapé
            quote_y = height - 220
            draw.rectangle([(70, quote_y), (width - 70, quote_y + 110)], fill="#0d1522", outline="#1e293b", width=1)
            draw.text(
                (width // 2, quote_y + 40),
                "\"Compre ações para ser sócio de empresas perenes e acumule quantidade.\"",
                fill="#cbd5e1",
                font=font_quote,
                anchor="mm",
            )
            draw.text((width // 2, quote_y + 80), "— Luiz Barsi Filho", fill="#10b981", font=font_quote, anchor="mm")

            # Marca d'água inferior
            draw.text(
                (width // 2, height - 55),
                "Gerado com metodologia de investimento em dividendos • Barsi Quant IA",
                fill="#475569",
                font=font_footer,
                anchor="mm",
            )

            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()

        except Exception as exc:
            logger.error("wrapped_image_generation_failed", error=str(exc))
            return None

