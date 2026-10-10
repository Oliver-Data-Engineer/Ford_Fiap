"""
indicadores.py
NPS e Likert derivados de sentimento — extraído de `src/api/analytics_api.py`
pra ser reaproveitado também por `src/analytics/dados_ficticios.py` (dashboard
em modo ilustrativo) sem criar um import circular entre os dois.

O app não coleta pesquisa de satisfação (não existe formulário Likert nem
pergunta de recomendação/NPS respondida por ninguém) — os dois índices são
aproximados a partir do único sinal de opinião que já existe: um sentimento
já classificado (pela IA, no modo real; pelo gerador fictício, no modo
ilustrativo). Mapeamento simples e documentado, nunca apresentado como
pesquisa real:
    positivo -> promotor / nota 5      negativo -> detrator / nota 1
    neutro, misto -> passivo / nota 3
NPS = (%promotores - %detratores) × 100, no intervalo -100..100 de sempre.
"""

LIKERT_POR_SENTIMENTO: dict[str, int] = {"positivo": 5, "neutro": 3, "misto": 3, "negativo": 1}

_LINHA_VAZIA = {"positivos": 0, "negativos": 0, "neutros": 0, "mistos": 0, "total": 0}


def _nps(linha: dict) -> float | None:
    total = linha.get("total") or 0
    if not total:
        return None
    return round((linha["positivos"] - linha["negativos"]) / total * 100, 1)


def _likert_medio(linha: dict) -> float | None:
    total = linha.get("total") or 0
    if not total:
        return None
    soma = (
        linha["positivos"] * LIKERT_POR_SENTIMENTO["positivo"]
        + linha["negativos"] * LIKERT_POR_SENTIMENTO["negativo"]
        + linha["neutros"] * LIKERT_POR_SENTIMENTO["neutro"]
        + linha["mistos"] * LIKERT_POR_SENTIMENTO["misto"]
    )
    return round(soma / total, 2)


def _com_indicadores(linha: dict) -> dict:
    return {**linha, "nps": _nps(linha), "likert_medio": _likert_medio(linha)}


def linha_agregada_vazia() -> dict:
    return dict(_LINHA_VAZIA)


def agregar_sentimento(linhas: list[dict]) -> dict:
    """Equivalente em Python do `_AGG_SENTIMENTO` SQL — soma positivos/
    negativos/neutros/mistos/total de uma lista de linhas já filtrada."""
    agregado = linha_agregada_vazia()
    chave_por_sentimento = {
        "positivo": "positivos", "negativo": "negativos",
        "neutro": "neutros", "misto": "mistos",
    }
    for linha in linhas:
        chave = chave_por_sentimento.get(linha.get("sentimento"))
        if chave:
            agregado[chave] += 1
        agregado["total"] += 1
    return agregado
