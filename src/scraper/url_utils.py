"""
url_utils.py
Parsing e template de query string — 100% determinístico, sem LLM (mesmo
espírito de `extractor.py`). Só `discovery.classificar_filtros` (que
chama o LLM pra decidir o "papel" de cada parâmetro) usa isto; a
Regra 11 (só `discovery.py` chama o modelo) continua valendo — este
módulo nunca importa `src.agents`.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.scraper.models import FiltroParam


def extrair_query_params(url: str) -> list[dict[str, str]]:
    """`[{"nome_param": "fabricante", "valor_amostra": "Ford"}, ...]` — na
    ordem em que aparecem na URL. URL sem query string devolve `[]`."""
    query = urlsplit(url).query
    if not query:
        return []
    return [
        {"nome_param": nome, "valor_amostra": valor}
        for nome, valor in parse_qsl(query, keep_blank_values=True)
    ]


def montar_template(url: str, filtros: list[FiltroParam]) -> str:
    """Substitui, na query string de `url`, o valor de cada parâmetro
    classificado por `{papel}` (ou `{nome_param}` quando `papel == "outro"`,
    pra não colidir dois parâmetros "outro" diferentes no mesmo template).
    Parâmetros não classificados mantêm o valor original da amostra."""
    papel_por_nome = {f.nome_param: f for f in filtros}
    partes = urlsplit(url)

    pares_originais = parse_qsl(partes.query, keep_blank_values=True)
    pares_template = []
    for nome, valor in pares_originais:
        filtro = papel_por_nome.get(nome)
        if filtro is None:
            pares_template.append((nome, valor))
            continue
        marcador = filtro.papel if filtro.papel != "outro" else filtro.nome_param
        pares_template.append((nome, "{" + marcador + "}"))

    nova_query = urlencode(pares_template, safe="{}").replace("%7B", "{").replace("%7D", "}")
    return urlunsplit((partes.scheme, partes.netloc, partes.path, nova_query, partes.fragment))
