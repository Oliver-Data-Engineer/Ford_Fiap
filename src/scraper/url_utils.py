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
    """Substitui, em `url`, o valor de cada filtro por `{papel}` (ou
    `{nome_param}` quando `papel == "outro"`, pra não colidir dois
    parâmetros "outro" diferentes no mesmo template). Parâmetros de query
    não classificados mantêm o valor original da amostra.

    Dois jeitos de localizar um filtro na URL:
      1. **Query string** — quando `nome_param` bate com uma chave literal
         da query (`?fabricante=Ford`), substitui o VALOR daquela chave.
         É o caso de `classificar_filtros` (auto-detecção), que só olha
         query string.
      2. **Segmento do path** — quando `nome_param` não é nenhuma chave da
         query (filtro adicionado à mão pelo usuário, ex.: a URL
         `.../busca/ford/2026?pn=1` não tem `?marca=ford` nenhum, "ford" é
         só um segmento posicional do path), procura `valor_amostra` como
         um segmento exato de `partes.path` e substitui o segmento
         inteiro. Não bate em nenhum dos dois → filtro é ignorado
         (a URL final não muda pra ele)."""
    papel_por_nome = {f.nome_param: f for f in filtros}
    partes = urlsplit(url)

    pares_originais = parse_qsl(partes.query, keep_blank_values=True)
    nomes_query = {nome for nome, _ in pares_originais}
    pares_template = []
    for nome, valor in pares_originais:
        filtro = papel_por_nome.get(nome)
        if filtro is None:
            pares_template.append((nome, valor))
            continue
        marcador = filtro.papel if filtro.papel != "outro" else filtro.nome_param
        pares_template.append((nome, "{" + marcador + "}"))
    nova_query = urlencode(pares_template, safe="{}").replace("%7B", "{").replace("%7D", "}")

    segmentos = partes.path.split("/")
    for filtro in filtros:
        if filtro.nome_param in nomes_query:
            continue  # já tratado como query acima
        alvo = filtro.valor_amostra or filtro.nome_param
        for indice, segmento in enumerate(segmentos):
            if segmento == alvo:
                marcador = filtro.papel if filtro.papel != "outro" else filtro.nome_param
                segmentos[indice] = "{" + marcador + "}"
                break
    novo_path = "/".join(segmentos)

    return urlunsplit((partes.scheme, partes.netloc, novo_path, nova_query, partes.fragment))
