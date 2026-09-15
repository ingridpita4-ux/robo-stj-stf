#!/usr/bin/env python3
"""
Robô Jurídico STJ/STF
Busca a pauta diária e resumo semanal dos tribunais superiores,
processa via Claude API e envia por e-mail via Brevo.
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ── Configurações ──────────────────────────────────────────────
BREVO_API_KEY     = os.environ["BREVO_API_KEY"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODO              = os.environ.get("MODO", "diario")   # "diario" ou "semanal"

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


# ── Busca e extração de texto bruto ────────────────────────────

def buscar_duckduckgo(query: str) -> list[dict]:
    """Busca notícias no DuckDuckGo HTML."""
    resultados = []
    try:
        url = "https://html.duckduckgo.com/html/"
        r = requests.post(url, data={"q": query}, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        for item in soup.select(".result__body")[:8]:
            titulo = item.select_one(".result__title")
            trecho = item.select_one(".result__snippet")
            link   = item.select_one("a.result__url")
            if titulo and trecho:
                resultados.append({
                    "titulo": titulo.get_text(strip=True),
                    "trecho": trecho.get_text(strip=True),
                    "url":    link.get_text(strip=True) if link else "",
                })
    except Exception as e:
        print(f"[DuckDuckGo] erro: {e}")
    return resultados


def extrair_texto_pagina(url: str, limite: int = 4000) -> str:
    """Busca uma URL e retorna o texto limpo (sem scripts/nav/footer)."""
    try:
        url_completo = url if url.startswith("http") else f"https://{url}"
        r = requests.get(url_completo, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:limite]
    except Exception as e:
        print(f"[extrair_texto] erro em {url}: {e}")
        return ""


def coletar_fontes_stf() -> str:
    """Coleta texto bruto de múltiplas fontes do STF."""
    blocos = []

    # 1. Informativos STF
    t = extrair_texto_pagina("https://portal.stf.jus.br/informativos/")
    if t:
        blocos.append(f"[STF — Informativos]\n{t}")

    # 2. Pautas STF
    t = extrair_texto_pagina("https://portal.stf.jus.br/pautas/")
    if t:
        blocos.append(f"[STF — Pautas]\n{t}")

    # 3. Notícias STF
    t = extrair_texto_pagina(
        "https://portal.stf.jus.br/noticias/listarNoticias.asp?tipo=pauta"
    )
    if t:
        blocos.append(f"[STF — Notícias/Pauta]\n{t}")

    # 4. DuckDuckGo → páginas oficiais e conjur
    resultados = buscar_duckduckgo(
        "STF julgamentos pauta decisão hoje site:portal.stf.jus.br OR site:conjur.com.br"
    )
    for r in resultados[:3]:
        url = r.get("url", "")
        if url:
            t = extrair_texto_pagina(url, 3000)
            if t:
                blocos.append(f"[{url}]\n{t}")

    return "\n\n---\n\n".join(blocos)


def coletar_fontes_stj() -> str:
    """Coleta texto bruto de múltiplas fontes do STJ."""
    blocos = []

    # 1. Informativo STJ
    t = extrair_texto_pagina(
        "https://www.stj.jus.br/sites/portalp/Paginas/Comunicacao/"
        "Informativos-de-Jurisprudencia.aspx"
    )
    if t:
        blocos.append(f"[STJ — Informativo de Jurisprudência]\n{t}")

    # 2. Notícias STJ
    t = extrair_texto_pagina(
        "https://www.stj.jus.br/sites/portalp/Paginas/Comunicacao/Noticias.aspx"
    )
    if t:
        blocos.append(f"[STJ — Notícias]\n{t}")

    # 3. Temas repetitivos
    t = extrair_texto_pagina(
        "https://processo.stj.jus.br/repetitivos/temas_repetitivos/pesquisa.jsp"
    )
    if t:
        blocos.append(f"[STJ — Temas Repetitivos]\n{t}")

    # 4. DuckDuckGo → fontes especializadas
    resultados = buscar_duckduckgo(
        "STJ julgamentos pauta decisão hoje site:stj.jus.br OR site:conjur.com.br"
    )
    for r in resultados[:3]:
        url = r.get("url", "")
        if url:
            t = extrair_texto_pagina(url, 3000)
            if t:
                blocos.append(f"[{url}]\n{t}")

    return "\n\n---\n\n".join(blocos)


# ── Processamento com Claude API ───────────────────────────────

def _chamar_claude(prompt: str) -> list[dict]:
    """Chama a API do Claude e retorna lista de dicts extraídos do JSON."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-3-5-haiku-20241022",
                "max_tokens": 2500,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=50,
        )
        if resp.status_code != 200:
            print(f"[Claude API] status {resp.status_code}: {resp.text[:200]}")
            return []
        texto = resp.json()["content"][0]["text"].strip()
        match = re.search(r'\[.*\]', texto, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        print(f"[Claude API] erro: {e}")
    return []


def processar_diario(texto_stf: str, texto_stj: str, hoje: datetime) -> tuple[list, list]:
    """Extrai pauta do dia via Claude."""
    if not ANTHROPIC_API_KEY:
        return [], []

    data_br = hoje.strftime("%d/%m/%Y")
    dia     = DIAS_PT[hoje.weekday()]

    instrucao = f"""Você é um assessor jurídico sênior especializado em STJ e STF.

Analise o texto abaixo e extraia as informações sobre a PAUTA e JULGAMENTOS de {dia}, {data_br}.

Para cada processo, afetação ou decisão encontrada, crie um objeto JSON com:
- "titulo": descrição concisa (ex: "Tema 1464 — REsp 2.255.394/MA" ou "ADI 7.000 — Relatora Min. Rosa Weber")
- "trecho": resumo estruturado contendo: seção/turma, data da sessão, ministro relator, questão jurídica central e resultado/decisão
- "url": URL da fonte (deixe "" se não houver)

Se não houver julgamentos identificáveis para a data, retorne [].
Retorne APENAS o array JSON, sem markdown nem explicações."""

    stf = _chamar_claude(f"{instrucao}\n\nCONTEÚDO STF:\n{texto_stf[:6500]}")
    stj = _chamar_claude(f"{instrucao}\n\nCONTEÚDO STJ:\n{texto_stj[:6500]}")
    return stf, stj


def processar_semanal(texto_stf: str, texto_stj: str, hoje: datetime) -> tuple[list, list]:
    """Extrai destaques da semana + síntese analítica via Claude."""
    if not ANTHROPIC_API_KEY:
        return [], []

    segunda  = hoje - timedelta(days=hoje.weekday())
    periodo  = f"{segunda.strftime('%d/%m')} a {hoje.strftime('%d/%m/%Y')}"

    instrucao = f"""Vocà é um assessor jurídico sênior especializado em STJ e STF.

Analise o texto abaixo e extraia os PRINCIPAIS JULGAMENTOS E DECISÕES da semana de {periodo}.

Para cada decisão relevante, crie um objeto JSON:
- "titulo": identificação do caso (ex: "Tema 1466 — REsp 2.258.565/SP" ou "RE 1.396.490 — Repercussão Geral")
- "trecho": resumo com seção/turma, data, relator (Ministro), questão jurídica, resultado/decisão e impacto prático
- "url": URL da fonte (deixe "" se não houver)

Ao final, adicione OBRIGATORIAMENTE um objeto de síntese:
- "titulo": "📊 Síntese Analítica da Semana"
- "trecho": análise de como os temas julgados se interligam, o que fecham na jurisprudência e qual o impacto estratégico para a advocacia
- "url": ""

Retorne APENAS o array JSON, sem markdown nem explicações."""

    stf = _chamar_claude(f"{instrucao}\n\nCONTEÚDO STF:\n{texto_stf[:6500]}")
    stj = _chamar_claude(f"{instrucao}\n\nCONTEÚDO STJ:\n{texto_stj[:6500]}")
    return stf, stj


# ── Coleta principal ────────────────────────────────────────────

def coletar_conteudo(hoje: datetime) -> tuple[list, list]:
    """Pauta diária: tenta Claude API, cai no DuckDuckGo se falhar."""
    data_br = hoje.strftime("%d/%m/%Y")
    print("Coletando fontes...")

    texto_stf = coletar_fontes_stf()
    texto_stj = coletar_fontes_stj()

    stf, stj = processar_diario(texto_stf, texto_stj, hoje)

    if not stf:
        stf = buscar_duckduckgo(f"pauta STF julgamentos {data_br}")[:5]
    if not stj:
        stj = buscar_duckduckgo(f"pauta STJ julgamentos {data_br}")[:5]

    return stf[:5], stj[:5]


def coletar_resumo_semanal(hoje: datetime) -> tuple[list, list]:
    """Resumo semanal: tenta Claude API, cai no DuckDuckGo se falhar."""
    segunda  = hoje - timedelta(days=hoje.weekday())
    data_ini = segunda.strftime("%d/%m")
    data_fim = hoje.strftime("%d/%m/%Y")
    print("Coletando fontes para resumo semanal...")

    texto_stf = coletar_fontes_stf()
    # Complementa com busca semanal
    for r in buscar_duckduckgo(f"STF principais decisões semana {data_ini} a {data_fim}")[:2]:
        t = extrair_texto_pagina(r.get("url", ""), 3000)
        if t:
            texto_stf += f"\n\n---\n\n{t}"

    texto_stj = coletar_fontes_stj()
    for r in buscar_duckduckgo(f"STJ principais decisões semana {data_ini} a {data_fim}")[:2]:
        t = extrair_texto_pagina(r.get("url", ""), 3000)
        if t:
            texto_stj += f"\n\n---\n\n{t}"

    stf, stj = processar_semanal(texto_stf, texto_stj, hoje)

    if not stf:
        stf = buscar_duckduckgo(f"STF julgou decidiu semana {data_fim}")[:5]
    if not stj:
        stj = buscar_duckduckgo(f"STJ julgou decidiu semana {data_fim}")[:5]

    return stf[:6], stj[:6]


# ── Formatação do e-mail ───────────────────────────────────────

CARD_VERDE   = "background:#f0fff4;border-left:3px solid #2B6A4A"
CARD_AZUL    = "background:#f0f4ff;border-left:3px solid #1C2B4A"
CARD_SINTESE = "background:#fffbea;border-left:4px solid #c9a000"
COR_VERDE    = "#2B6A4A"
COR_AZUL     = "#1C2B4A"
COR_OURO     = "#7a5f00"


def _card(item: dict, cor: str) -> str:
    url_val = item.get("url", "")
    titulo  = item.get("titulo", "")
    trecho  = item.get("trecho", "")

    # Card especial para síntese analítica
    if "Síntese" in titulo or "📊" in titulo:
        link = ""
        return f"""
    <div style="{CARD_SINTESE};padding:14px 16px;margin:12px 0;border-radius:6px;">
      <strong style="font-size:14px;color:{COR_OURO};">{titulo}</strong><br>
      <span style="color:#555;font-size:13px;line-height:1.7;display:block;margin-top:6px;">
        {trecho.replace(chr(10), "<br>")}
      </span>
    </div>"""

    link = (
        f'<br><a href="https://{url_val}" style="color:{cor};font-size:12px;">Leia mais ›</a>'
        if url_val else ""
    )
    estilo_card = CARD_AZUL if cor == COR_AZUL else CARD_VERDE
    return f"""
    <div style="{estilo_card};padding:13px 15px;margin:8px 0;border-radius:4px;">
      <strong style="font-size:14px;color:{cor};">{titulo}</strong><br>
      <span style="color:#444;font-size:13px;line-height:1.65;display:block;margin-top:5px;">
        {trecho.replace(chr(10), "<br>")}
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
    data_br    = hoje.strftime("%d/%m/%Y")
    dia_semana = DIAS_PT[hoje.weekday()]

    stf_html = _secao(
        "STF — Supremo Tribunal Federal", "🏛️", COR_AZUL, stf,
        "Nenhuma pauta localizada para hoje — pode ser dia sem sessão ou a pauta ainda não foi publicada."
    )
    stj_html = _secao(
        "STJ — Superior Tribunal de Justiça", "⚖️", COR_VERDE, stj,
        "Nenhuma pauta localizada para hoje — pode ser dia sem sessão ou a pauta ainda não foi publicada."
    )
    fonte = "Claude AI + portais oficiais" if ANTHROPIC_API_KEY else "Portais oficiais + DuckDuckGo"

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:680px;
                        margin:0 auto;color:#333;background:#fff;">
    <div style="background:{COR_AZUL};padding:22px 24px;border-radius:8px 8px 0 0;">
      <h2 style="color:#fff;margin:0;font-size:20px;">⚖️ Pauta STJ/STF</h2>
      <p style="color:#aabbcc;margin:4px 0 0;font-size:13px;">
        {dia_semana}, {data_br}
      </p>
    </div>
    <div style="background:#f9f9fb;padding:20px 24px;
                border:1px solid #dde;border-radius:0 0 8px 8px;">
      {stf_html}
      {stj_html}
      <div style="background:#fff8e1;border:1px solid #f0c040;padding:12px 14px;
                  border-radius:6px;margin-top:24px;font-size:13px;">
        <strong>💡 Fique de olho:</strong> identifique nos resultados acima
        quais julgamentos podem impactar seus processos e clientes —
        atualize suas estratégias conforme necessário.
      </div>
      <p style="color:#bbb;font-size:11px;border-top:1px solid #eee;
                padding-top:12px;margin-top:20px;">
        Robô Jurídico STJ/STF • {data_br} • Fonte: {fonte}
      </p>
    </div>
    </body></html>"""


def html_semanal(stf: list, stj: list, hoje: datetime) -> str:
    segunda  = hoje - timedelta(days=hoje.weekday())
    periodo  = f"{segunda.strftime('%d/%m')} a {hoje.strftime('%d/%m/%Y')}"
    fonte    = "Claude AI + portais oficiais" if ANTHROPIC_API_KEY else "Portais oficiais + DuckDuckGo"

    stf_html = _secao(
        "STF — Destaques da semana", "🏛️", COR_AZUL, stf,
        "Nenhum destaque localizado para esta semana."
    )
    stj_html = _secao(
        "STJ — Destaques da semana", "⚖️", COR_VERDE, stj,
        "Nenhum destaque localizado para esta semana."
    )

    return f"""
    <html><body style="font-family:Arial,sans-serif;max-width:680px;
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
      <p style="color:#bbb;font-size:11px;border-top:1px solid #eee;
                padding-top:12px;margin-top:20px;">
        Robô Jurídico STJ/STF • Resumo semanal • Fonte: {fonte}
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
    modo_ia = "com Claude AI" if ANTHROPIC_API_KEY else "sem Claude AI (fallback)"
    print(f"Robô iniciado — {hoje.strftime('%d/%m/%Y %H:%M')} — modo: {MODO} — {modo_ia}")

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
