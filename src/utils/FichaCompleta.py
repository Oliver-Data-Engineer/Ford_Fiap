"""
FichaCompleta.py
Scraper assíncrono para fichacompleta.com.br — 3ª fonte de especificações
técnicas, adicionada para resiliência (CarrosWeb e iCarros já tinham
histórico de bloqueio/erro intermitente — ver `deteccao_bloqueio.py`).

Interface pública (compatível com specs_api.py — mesma de CarrosWeb.py/
iCarros.py):
    obter_fabricantes()                        -> list[{id_busca, nome_exibicao}]
    obter_modelos(fabricante)                  -> list[{fabricante, id_busca, nome_exibicao}]
    montar_url_modelo(fabricante, modelo)      -> str
    extrair_versoes(url)                       -> list[{id_veiculo, fabricante, versao, ano, url_ficha}]
    obter_todas_urls_paginacao(url_base)       -> list[str]
    extrair_ficha_tecnica(url_ficha)           -> list[{atributo, valor}]

Estrutura HTML confirmada com o usuário (páginas reais do site):
    Marcas:    /carros/marcas/          <a class="marca-card"><span class="marca-card__name">
    Modelos:   /carros/{marca}/         <a class="mod-card"><span class="mod-card__name">
    Versões:   /carros/{marca}/{modelo}/  <div class="ver-card"><a class="ver-card__link">
                 <span class="ver-card__year">/<span class="ver-card__name">
    Ficha:     /carros/{marca}/{slug-versao}  <div class="ent-spec-item">
                 <span class="ent-spec-label">/<span class="ent-spec-value">
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional

from bs4 import BeautifulSoup
from curl_cffi import requests

from src.utils.deteccao_bloqueio import parece_bloqueado_ou_erro
from src.utils.html_encoding import decodificar_html_bytes


def _slugify(text: str) -> str:
    """Converte texto para slug compatível com as URLs do fichacompleta.com.br."""
    text = text.lower().strip()
    for src, dst in [
        ("àáâãä", "a"), ("èéêë", "e"), ("ìíîï", "i"),
        ("òóôõö", "o"), ("ùúûü", "u"), ("ç", "c"),
    ]:
        for c in src:
            text = text.replace(c, dst)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _limpar(texto: str) -> str:
    if not texto:
        return ""
    return " ".join(texto.split()).strip()


def _slug_do_href(href: str) -> str:
    """Último segmento não vazio de um href tipo '/carros/ford/ranger/'."""
    partes = [p for p in href.split("/") if p]
    return partes[-1] if partes else ""


class FichaCompleta:
    base_url = "https://www.fichacompleta.com.br"

    async def _get(self, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
        for attempt in range(1, retries + 1):
            try:
                resp = requests.get(url, impersonate="chrome", timeout=15.0, verify=False)

                if resp.status_code == 404:
                    print(f"[!] Página não encontrada (404): {url}")
                    return None

                if resp.status_code != 200:
                    print(f"[!] Erro {resp.status_code}. Tentativa {attempt}/{retries}. Aguardando...")
                    await asyncio.sleep(2 ** attempt)
                    continue

                html = decodificar_html_bytes(resp.content)

                if parece_bloqueado_ou_erro(html):
                    print(f"[!] Página de bloqueio/erro. Tentativa {attempt}/{retries}. Aguardando...")
                    await asyncio.sleep(2 ** attempt)
                    continue

                return BeautifulSoup(html, "html.parser")

            except Exception as exc:
                print(f"[!] Exceção na requisição: {exc}")
                if attempt == retries:
                    return None
                await asyncio.sleep(2 ** attempt)

        return None

    # ── Fabricantes ───────────────────────────────────────────────────────────

    async def obter_fabricantes(self) -> list[dict]:
        url = f"{self.base_url}/carros/marcas/"
        soup = await self._get(url)

        if not soup:
            raise ConnectionError("[FichaCompleta] Falha ao acessar a lista de marcas.")

        fabricantes: list[dict] = []
        for card in soup.find_all("a", class_="marca-card"):
            nome_tag = card.find("span", class_="marca-card__name")
            if not nome_tag:
                continue
            nome = _limpar(nome_tag.get_text())
            if not nome:
                continue
            slug = _slug_do_href(card.get("href", "")) or _slugify(nome)
            fabricantes.append({"id_busca": slug, "nome_exibicao": nome})

        if not fabricantes:
            raise ValueError("[FichaCompleta] Nenhuma marca encontrada na página de marcas.")

        return fabricantes

    # ── Modelos ───────────────────────────────────────────────────────────────

    async def obter_modelos(self, fabricante: str) -> list[dict]:
        slug_marca = _slugify(fabricante)
        url = f"{self.base_url}/carros/{slug_marca}/"
        soup = await self._get(url)

        if not soup:
            raise ConnectionError(
                f"[FichaCompleta] Falha ao acessar os modelos da marca '{fabricante}'."
            )

        modelos: list[dict] = []
        for card in soup.find_all("a", class_="mod-card"):
            nome_tag = card.find("span", class_="mod-card__name")
            if not nome_tag:
                continue
            nome = _limpar(nome_tag.get_text())
            if not nome:
                continue
            slug_modelo = _slug_do_href(card.get("href", "")) or _slugify(nome)
            modelos.append({
                "fabricante": slug_marca,
                "id_busca": slug_modelo,
                "nome_exibicao": nome,
            })

        return modelos

    # ── Versões ───────────────────────────────────────────────────────────────

    def montar_url_modelo(self, fabricante: str, modelo: str) -> str:
        """URL de catálogo (listagem de versões) pra um (fabricante, modelo)
        — esquema próprio do fichacompleta.com.br. `specs_api.py` chama isto
        em vez de montar a URL na mão, mesma razão de `iCarros.py::montar_url_modelo`:
        cada fonte tem seu próprio esquema de URL."""
        return f"{self.base_url}/carros/{_slugify(fabricante)}/{_slugify(modelo)}/"

    async def extrair_versoes(self, url: str) -> list[dict]:
        soup = await self._get(url)
        if not soup:
            return []

        partes_url = [p for p in url.split("/") if p]
        # URL: .../carros/{marca}/{modelo}/  → índice de "carros" + 1 = marca
        try:
            idx = partes_url.index("carros")
            slug_marca = partes_url[idx + 1] if idx + 1 < len(partes_url) else ""
        except ValueError:
            slug_marca = ""

        versoes: list[dict] = []
        vistos: set[str] = set()  # dedup por href — a página de exemplo trouxe
                                   # o mesmo bloco de versões repetido 3x

        for card in soup.find_all("div", class_="ver-card"):
            link = card.find("a", class_="ver-card__link", href=True)
            if not link:
                continue

            href = link["href"]
            if href in vistos:
                continue
            vistos.add(href)

            ano_tag = link.find("span", class_="ver-card__year")
            nome_tag = link.find("span", class_="ver-card__name")
            nome = _limpar(nome_tag.get_text()) if nome_tag else ""
            if not nome:
                continue

            id_veiculo = _slug_do_href(href) or _slugify(nome)
            url_ficha = href if href.startswith("http") else f"{self.base_url}{href}"

            versoes.append({
                "id_veiculo": id_veiculo,
                "fabricante": slug_marca,
                "versao": nome,
                "ano": _limpar(ano_tag.get_text()) if ano_tag else "",
                "url_ficha": url_ficha,
            })

        return versoes

    # ── Paginação ─────────────────────────────────────────────────────────────

    async def obter_todas_urls_paginacao(self, url_base: str) -> list[str]:
        """O fichacompleta.com.br lista todas as versões numa única página —
        mantido para compatibilidade com specs_api.py (igual iCarros.py)."""
        return [url_base]

    # ── Ficha técnica ─────────────────────────────────────────────────────────

    async def extrair_ficha_tecnica(self, url_ficha: str) -> list[dict]:
        """
        Extrai pares atributo/valor de todos os grupos da ficha técnica.

        O site já agrupa por categoria própria (`h3.ent-ficha-group__title`:
        "Desempenho", "Motor", "Transmissão"...), mas essa categoria nativa é
        descartada de propósito — `src/utils/normalizador.py::categorizar_atributo`
        já reclassifica qualquer atributo pela taxonomia fixa usada pelas
        outras 2 fontes, e usar a categoria nativa aqui quebraria a
        consistência entre fontes numa mesma resposta de `/specs/executar`.
        """
        soup = await self._get(url_ficha)

        if not soup:
            return []

        specs: list[dict] = []
        for item in soup.find_all("div", class_="ent-spec-item"):
            label_tag = item.find("span", class_="ent-spec-label")
            valor_tag = item.find("span", class_="ent-spec-value")
            if not label_tag or not valor_tag:
                continue

            atributo = _limpar(label_tag.get_text())
            valor = _limpar(valor_tag.get_text())
            if atributo and valor:
                specs.append({"atributo": atributo, "valor": valor})

        return specs
