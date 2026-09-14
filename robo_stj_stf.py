#!/usr/bin/env python3
"""
Robô Jurídico STJ/STF
Busca a pauta diária e resumo semanal dos tribunais superiores
e envia por e-mail via Brevo API.
"""

import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ── Configurações ──────────────────────────────────────────────
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
MODO          = os.environ.get("MODO", "diario")   # "diario" ou "semanal"

DESTINATARIOS = [
    {"email": "ingridpita@hotmail.com",        "name": "Ingrid Pita"},
    {"email": "equipe@ramaraladvogados.com",   "name": "Equipe RA"},
]
FUSO_FORTALEZA = timezone(timedelta(hours=-3))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
}

DIAS_PT = ["Segunda-feira", "Terça-feira", "Quarta-feira",
           "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]

# ── Busca de conteúdo ──────────────────────────────────────────

def buscar_duckduckgo(query: str) -> list[dict]:
    """Busca notícias no DuckDuckGo HTML (sem API key)."""
    resultados = []
    try:
        url = "https://html.duckduckgo.com/html/"
        r = requests.post(url, data={"q": query}, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select(".result__body")[:6]:
            titulo  = item.select_one(".result__title")
            trecho  = item.select_one(".result__snippet")
            link    = item.select_one("a.result__url")
            if titulo and trecho:
                resultados.append({
                    "titulo": titulo.get_text(strip=True),
                    "trecho": trecho.get_text(strip=True),
                    "url":    link.get_text(strip=True) if link else "",
                })
    except Exception as e:
        print(f"[DuckDuckGo] erro: {e}")
    return resultados


def buscar_pauta_stf_oficial(data_iso: str) -> list[dict]:
    """Tenta buscar diretamente nas notícias do portal STF."""
    itens = []
    try:
        r = requests.get(
            "https://portal.stf.jus.br/noticias/listarNoticias.asp?tipo=pauta",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        for el in soup.select("li, article, .card-news")[:8]:
            texto = el.get_text(separator=" ", strip=True)
            if len(texto) > 40:
                link_el = el.find("a")
                itens.append({
                    "titulo": texto[:120],
                    "trecho": texto[:250],
                    "url":    "portal.stf.jus.br" + (link_el["href"] if link_el and link_el.get("href","").startswith("/") else ""),
                })
    except Exception as e:
        print(f"[STF oficial] erro: {e}")
    return itens


def buscar_pauta_stj_oficial() -> list[dict]:
    """Tenta buscar diretamente na página de pauta do STJ."""
    itens = []
    try:
        r = requests.get(
            "https://www.stj.jus.br/sites/portalp/Paginas/Comunicacao/Noticias/Pauta.aspx",
            headers=HEADERS, timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        for el in soup.select(".ms-rtestate-field p, .noticias-lista li, article")[:8]:
            texto = el.get_text(separator=" ", strip=True)
            if len(texto) > 40:
                itens.append({
                    "titulo": texto[:120],
                    "trecho": texto[:250],
                    "url":    "www.stj.jus.br",
                })
    except Exception as e:
        print(f"[STJ oficial] erro: {e}")
    return itens


def coletar_conteudo(hoje: datetime) -> tuple[list, list]:
    """Coleta pauta do STF e STJ com múltiplas fontes."""
    data_br = hoje.strftime("%d/%m/%Y")

    # STF
    stf = buscar_pauta_stf_oficial(hoje.strftime("%Y-%m-%d"))
    if not stf:
        stf = buscar_duckduckgo(f"pauta STF julgamentos {data_br}")
    if not stf:
        stf = buscar_duckduckgo("pauta STF julgamentos hoje site:portal.stf.jus.br OR site:stf.jus.br")

    # STJ
    stj = buscar_pauta_stj_oficial()
    if not stj:
        stj = buscar_duckduckgo(f"pauta STJ julgamentos {data_br}")
    if not stj:
        stj = buscar_duckduckgo("pauta STJ julgamentos hoje site:stj.jus.br")

    return stf[:5], stj[:5]


def coletar_resumo_semanal(hoje: datetime) -> tuple[list, list]:
    """Coleta os principais julgados da semana."""
    segunda = hoje - timedelta(days=hoje.weekday())
    data_ini = segunda.strftime("%d/%m")
    data_fim = hoje.strftime("%d/%m/%Y")

    stf = buscar_duckduckgo(
        f"principais julgados decisões STF semana {data_ini} a {data_fim}"
    )
    stj = buscar_duckduckgo(
        f"principais julgados decisões STJ semana {data_ini} a {data_fim}"
    )

    if not stf:
        stf = buscar_duckduckgo(f"STF julgou decidiu semana {data_fim}")
    if not stj:
        stj = buscar_duckduckgo(f"STJ julgou decidiu semana {data_fim}")

    return stf[:5], stj[:5]


# ── Formatação do e-mail ───────────────────────────────────────

CARD_VERDE  = "background:#f0fff4;border-left:3px solid #2B6A4A"
CARD_AZUL   = "background:#f0f4ff;border-left:3px solid #1C2B4A"
COR_VERDE   = "#2B6A4A"
COR_AZUL    = "#1C2B4A"


def _card(item: dict, cor: str) -> str:
    url_val = item.get("url", "")
    link = f'<br><a href="https://{url_val}" style="color:{cor};font-size:12px;">Leia mais ›</a>' if url_val else ""
    return f"""
    <div style="{CARD_AZUL if cor == COR_AZUL else CARD_VERDE};
                 padding:12px 14px;margin:8px 0;border-radius:4px;">
      <strong style="font-size:14px;">{item.get('titulo','')}</strong><br>
      <span style="color:#555;font-size:13px;line-height:1.5;">
        {item.get('trecho','')}
      </span>{link}
    </div>"""


def _secao(titulo: str, icone: str, cor: str, itens: list, vazio: str) -> str:
    corpo = "".join(_card(i, cor) for i in itens) if itens else \
        f"<p style='color:#888;font-size:13px;'>{vazio}</p>"
    return f"""
    <h3 style="color:{cor};border-bottom:2px solid {cor};
               padding-bottom:6px;margin:24px 0 12px;">
      {icone} {titulo}
    </h3>
    {corpo}"""


def html_diario(stf: list, stj: list, hoje: datetime) -> str:
    data_br   = hoje.strftime("%d/%m/%Y")
    dia_semana = DIAS_PT[hoje.weekday()]

    stf_html = _secao(
        "STF — Supremo Tribunal Federal", "🏛️", COR_AZUL, stf,
        "Nenhuma pauta localizada para hoje — pode ser dia sem sessão ou a pauta ainda não foi publicada."
    )
    stj_html = _secao(
        "STJ — Superior Tribunal de Justiça", "⚖️", COR_VERDE, stj,
        "Nenhuma pauta localizada para hoje — pode ser dia sem sessão ou a pauta ainda não foi publicada."
    )

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:660px;
                        margin:0 auto;color:#333;background:#fff;">
    <!-- Cabeçalho -->
    <div style="background:{COR_AZUL};padding:22px 24px;border-radius:8px 8px 0 0;">
      <h2 style="color:#fff;margin:0;font-size:20px;">⚖️ Pauta STJ/STF</h2>
      <p style="color:#aabbcc;margin:4px 0 0;font-size:13px;">
        {dia_semana}, {data_br}
      </p>
    </div>
    <!-- Corpo -->
    <div style="background:#f9f9fb;padding:20px 24px;
                border:1px solid #dde;border-radius:0 0 8px 8px;">
      {stf_html}
      {stj_html}
      <!-- Dica geral -->
      <div style="background:#fff8e1;border:1px solid #f0c040;padding:12px 14px;
                  border-radius:6px;margin-top:24px;font-size:13px;">
        <strong>💡 Fique de olho:</strong> identifique nos resultados acima
        quais julgamentos podem impactar seus processos e clientes —
        atualize suas estratégias conforme necessário.
      </div>
      <p style="color:#aaa;font-size:11px;border-top:1px solid #eee;
                padding-top:12px;margin-top:20px;">
        Robô Jurídico STJ/STF • {data_br}
      </p>
    </div>
    </body></html>"""


def html_semanal(stf: list, stj: list, hoje: datetime) -> str:
    segunda   = hoje - timedelta(days=hoje.weekday())
    periodo   = f"{segunda.strftime('%d/%m')} a {hoje.strftime('%d/%m/%Y')}"

    stf_html = _secao(
        "STF — Destaques da semana", "🏛️", COR_AZUL, stf,
        "Nenhum destaque localizado para esta semana."
    )
    stj_html = _secao(
        "STJ — Destaques da semana", "⚖️", COR_VERDE, stj,
        "Nenhum destaque localizado para esta semana."
    )

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:660px;
                        margin:0 auto;color:#333;background:#fff;">
    <div style="background:{COR_AZUL};padding:22px 24px;border-radius:8px 8px 0 0;">
      <h2 style="color:#fff;margin:0;font-size:20px;">📊 Resumo Semanal STJ/STF</h2>
      <p style="color:#aabbcc;margin:4px 0 0;font-size:13px;">Semana de {periodo}</p>
    </div>
    <div style="background:#f9f9fb;padding:20px 24px;
                border:1px solid #dde;border-radius:0 0 8px 8px;">
      {stf_html}
      {stj_html}
      <div style="background:#e8f5e9;border:1px solid #81c784;padding:12px 14px;
                  border-radius:6px;margin-top:24px;font-size:13px;">
        <strong>🗂️ Dica semanal:</strong> se algum julgado acima impactar
        seus clientes ou processos, o início da próxima semana é o momento
        ideal para informar e atualizar estratégias.
      </div>
      <p style="color:#aaa;font-size:11px;border-top:1px solid #eee;
                padding-top:12px;margin-top:20px;">
        Robô Jurídico STJ/STF • Resumo semanal
      </p>
    </div>
    </body></html>"""


# ── Envio via Brevo ────────────────────────────────────────────

def enviar_email(assunto: str, html: str) -> bool:
    payload = {
        "sender":      {"name": "Robô Jurídico STJ/STF", "email": "ingridpita@hotmail.com"},
        "to":          DESTINATARIOS,
        "subject":     assunto,
        "htmlContent": html,
    }
    resp = requests.post(
        "https://api.brevo.com/v3/smtp/email",
        json=payload,
        headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json"},
        timeout=30,
    )
    ok = resp.status_code in (200, 201)
    print(f"Brevo: {resp.status_code} {'✓' if ok else '✗'} — {resp.text[:200]}")
    return ok


# ── Main ───────────────────────────────────────────────────────

def main():
    hoje = datetime.now(FUSO_FORTALEZA)
    print(f"Robô iniciado — {hoje.strftime('%d/%m/%Y %H:%M')} — modo: {MODO}")

    if MODO == "semanal":
        stf, stj = coletar_resumo_semanal(hoje)
        segunda  = hoje - timedelta(days=hoje.weekday())
        assunto  = (
            f"📊 Resumo Semanal STJ/STF — "
            f"{segunda.strftime('%d/%m')} a {hoje.strftime('%d/%m/%Y')}"
        )
        html = html_semanal(stf, stj, hoje)
    else:
        stf, stj = coletar_conteudo(hoje)
        assunto  = f"📋 Pauta STJ/STF — {hoje.strftime('%d/%m/%Y')}"
        html     = html_diario(stf, stj, hoje)

    enviar_email(assunto, html)


if __name__ == "__main__":
    main()
