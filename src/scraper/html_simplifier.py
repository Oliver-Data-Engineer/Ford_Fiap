"""
html_simplifier.py
Reduz o HTML de uma página antes de mandar pro LLM — tira ruído (script,
style, svg, comentários, atributos `style`/`on*` inline) e corta num
tamanho máximo. É isso, nunca o HTML cru, que `discovery.py` envia ao
`Agente` (custo de token + qualidade do resultado).
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment

_ATRIBUTOS_DESCARTADOS = ("style",)


def simplificar(html: str, max_chars: int) -> str:
    soup = BeautifulSoup(html or "", "html.parser")

    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()

    for comentario in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comentario.extract()

    for tag in soup.find_all(True):
        for atributo in list(tag.attrs):
            if atributo in _ATRIBUTOS_DESCARTADOS or atributo.startswith("on"):
                del tag.attrs[atributo]

    texto = str(soup)
    texto = re.sub(r"\s+", " ", texto).strip()

    return texto[:max_chars]
