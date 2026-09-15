"""
extractor.py
Extração determinística — SEM LLM. Usa o `MapaSeletor` já descoberto/
validado (`src/scraper/models.py`) pra ler os dados reais da página via
BeautifulSoup. É a camada que dá o "sem custo de LLM, sem alucinação" do
Scraper Mode: o LLM (`discovery.py`) só define a FORMA uma vez; daqui pra
frente é puro seletor CSS.

Tolerante campo a campo — um atributo/campo ausente vira `None`, nunca
derruba a linha inteira; uma linha que dá erro é pulada (logada), nunca
propaga.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


def _valor_de(elemento: Tag | None, *, preferir_link: bool = False) -> str | None:
    if elemento is None:
        return None

    if preferir_link:
        href = elemento.get("href") or (elemento.select_one("a[href]") or {}).get("href")
        if href:
            return href

    texto = elemento.get_text(strip=True)
    if texto:
        return texto

    if elemento.name == "a" and elemento.get("href"):
        return elemento.get("href")
    if elemento.name == "img" and elemento.get("src"):
        return elemento.get("src")

    return None


def _selecionar_um(raiz: Tag | BeautifulSoup, seletor: str | None) -> Tag | None:
    if not seletor:
        return None
    try:
        return raiz.select_one(seletor)
    except Exception:
        return None


def extrair_ficha_tecnica(html: str, selectors: dict, url: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")

    marca = _valor_de(_selecionar_um(soup, selectors.get("marca")))
    modelo = _valor_de(_selecionar_um(soup, selectors.get("modelo")))
    versao = _valor_de(_selecionar_um(soup, selectors.get("versao")))
    ano = _valor_de(_selecionar_um(soup, selectors.get("ano")))

    linha_seletor = selectors.get("linha_atributo")
    if not linha_seletor:
        return []

    linhas: list[dict] = []
    try:
        elementos = soup.select(linha_seletor)
    except Exception as exc:
        logger.warning("[scraper] seletor 'linha_atributo' inválido: %s", exc)
        return []

    for elemento in elementos:
        try:
            nome = _valor_de(_selecionar_um(elemento, selectors.get("nome_atributo")))
            valor = _valor_de(_selecionar_um(elemento, selectors.get("valor_atributo")))
            if not nome:
                continue
            linhas.append({
                "marca": marca, "modelo": modelo, "versao": versao, "ano": ano,
                "atributo": nome, "valor": valor, "url_origem": url,
            })
        except Exception as exc:
            logger.debug("[scraper] linha de ficha técnica ignorada (%s): %s", url, exc)

    return linhas


def extrair_anuncios(html: str, selectors: dict, url: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")

    item_seletor = selectors.get("item_listagem")
    if not item_seletor:
        return []

    try:
        itens = soup.select(item_seletor)
    except Exception as exc:
        logger.warning("[scraper] seletor 'item_listagem' inválido: %s", exc)
        return []

    linhas: list[dict] = []
    for item in itens:
        try:
            preco_texto = _valor_de(_selecionar_um(item, selectors.get("preco")))
            if not preco_texto:
                continue
            linhas.append({
                "marca": _valor_de(_selecionar_um(item, selectors.get("marca"))),
                "modelo": _valor_de(_selecionar_um(item, selectors.get("modelo"))),
                "versao": _valor_de(_selecionar_um(item, selectors.get("versao"))),
                "ano": _valor_de(_selecionar_um(item, selectors.get("ano"))),
                "preco_texto": preco_texto,
                "preco_numerico": _preco_para_numero(preco_texto),
                "url_anuncio": _valor_de(_selecionar_um(item, selectors.get("url_anuncio")), preferir_link=True) or url,
                "localizacao": _valor_de(_selecionar_um(item, selectors.get("localizacao"))),
                "vendedor": _valor_de(_selecionar_um(item, selectors.get("vendedor"))),
                "publicado_em": _valor_de(_selecionar_um(item, selectors.get("publicado_em"))),
            })
        except Exception as exc:
            logger.debug("[scraper] item de anúncio ignorado (%s): %s", url, exc)

    return linhas


def extrair_comentarios(html: str, selectors: dict, url: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")

    item_seletor = selectors.get("item_comentario")
    if not item_seletor:
        return []

    try:
        itens = soup.select(item_seletor)
    except Exception as exc:
        logger.warning("[scraper] seletor 'item_comentario' inválido: %s", exc)
        return []

    linhas: list[dict] = []
    for item in itens:
        try:
            texto = _valor_de(_selecionar_um(item, selectors.get("texto")))
            if not texto:
                continue
            linhas.append({
                "marca": _valor_de(_selecionar_um(item, selectors.get("marca"))),
                "modelo": _valor_de(_selecionar_um(item, selectors.get("modelo"))),
                "versao": _valor_de(_selecionar_um(item, selectors.get("versao"))),
                "ano": _valor_de(_selecionar_um(item, selectors.get("ano"))),
                "autor": _valor_de(_selecionar_um(item, selectors.get("autor"))),
                "texto": texto,
                "nota": _nota_para_numero(_valor_de(_selecionar_um(item, selectors.get("nota")))),
                "publicado_em": _valor_de(_selecionar_um(item, selectors.get("publicado_em"))),
                "url_origem": url,
            })
        except Exception as exc:
            logger.debug("[scraper] item de comentário ignorado (%s): %s", url, exc)

    return linhas


def _preco_para_numero(texto: str | None) -> float | None:
    if not texto:
        return None
    limpo = "".join(c for c in texto if c.isdigit() or c in ",.")
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _nota_para_numero(texto: str | None) -> float | None:
    if not texto:
        return None
    limpo = texto.strip().replace(",", ".")
    try:
        return float("".join(c for c in limpo if c.isdigit() or c == "."))
    except ValueError:
        return None


def extrair_linhas_genericas(html: str, selectors: dict, url: str) -> list[dict]:
    """
    Extração genérica pra uma etapa de NAVEGAÇÃO (não a última de um
    objetivo) — `selectors` no formato `{"linha": "...", "campos":
    {"nome": "seletor relativo", ...}}` (dump de
    `SeletorEtapaIntermediaria`). Uma chave por campo configurado no
    registro devolvido; campos cujo nome sugere um link ("url"/"link"/
    "href" no nome) preferem o atributo `href`/`src` sobre o texto do
    elemento, e o valor é resolvido pra absoluto (`urljoin`) — é esse
    valor que `engine.py` usa como URL da próxima etapa quando o campo é
    o `campo_herdado`.
    """
    soup = BeautifulSoup(html or "", "html.parser")

    linha_seletor = selectors.get("linha")
    campos = selectors.get("campos") or {}
    if not linha_seletor or not campos:
        return []

    try:
        elementos = soup.select(linha_seletor)
    except Exception as exc:
        logger.warning("[scraper] seletor 'linha' inválido: %s", exc)
        return []

    linhas: list[dict] = []
    for elemento in elementos:
        try:
            registro: dict[str, Any] = {}
            for nome_campo, seletor_campo in campos.items():
                eh_link = any(marcador in nome_campo.lower() for marcador in ("url", "link", "href"))
                valor = _valor_de(_selecionar_um(elemento, seletor_campo), preferir_link=eh_link)
                registro[nome_campo] = urljoin(url, valor) if eh_link and valor else valor
            linhas.append(registro)
        except Exception as exc:
            logger.debug("[scraper] linha de etapa intermediária ignorada (%s): %s", url, exc)

    return linhas
