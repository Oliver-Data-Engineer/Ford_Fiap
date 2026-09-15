"""
iCarros.py
Scraper assíncrono para icarros.com.br.

Interface pública (compatível com specs_api.py):
    obter_fabricantes()                        -> list[{id_busca, nome_exibicao}]
    obter_modelos(fabricante)                  -> list[{fabricante, id_busca, nome_exibicao}]
    extrair_versoes(url)                       -> list[{id_veiculo, fabricante, versao, ano, url_ficha}]
    obter_todas_urls_paginacao(url_base)       -> list[str]
    extrair_ficha_tecnica(url_ficha)           -> list[{atributo, valor}]

O iCarros usa:
  - URLs slugificadas:  /catalogo-zero-km/marcas/{marca}/{modelo}/{versao}/ficha-tecnica
  - HTTP:               curl_cffi impersonate="chrome120"  (bypass TLS fingerprinting)
  - HTML de marcas:     <div class="card-base">  com <p> e <a href>
  - HTML de modelos:    <a class="complete-ad-card"> com <h2>
  - HTML de versões:    <select class="versions-select"> / <select class="years-select">
  - HTML de fichas:     <div class="sheet-theme-container" id="MOTORIZACAO|...">
                          <div class="technical-sheet-item|brakes-suspension-item">
                            <span class="...bold">Label</span>
                            <span class="...regular">Valor</span>
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from src.utils.html_encoding import decodificar_html_bytes

try:
    from curl_cffi import requests as cffi_requests
    _CFFI_OK = True
except ImportError:
    import requests as cffi_requests  # type: ignore[no-redef]
    _CFFI_OK = False

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    """Converte texto para slug compatível com as URLs do iCarros."""
    text = text.lower().strip()
    for src, dst in [
        ("àáâãä", "a"), ("èéêë", "e"), ("ìíîï", "i"),
        ("òóôõö", "o"), ("ùúûü", "u"), ("ç", "c"),
    ]:
        for c in src:
            text = text.replace(c, dst)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def _limpar(texto: str) -> str:
    """Normaliza espaços e retorna string limpa (nunca 'N/D')."""
    if not texto:
        return ""
    return " ".join(texto.split()).strip()


def _is_blocked(html: str) -> bool:
    """Detecta páginas de captcha / Cloudflare."""
    markers = [
        "cf-browser-verification", "just a moment",
        "enable javascript and cookies", "captcha",
        "access denied", "bot detection", "verifying you are human",
    ]
    lower = html.lower()
    return any(m in lower for m in markers) and len(html) < 20_000


# ─────────────────────────────────────────────────────────────────────────────
# Classe principal
# ─────────────────────────────────────────────────────────────────────────────

class CarrosWeb:
    """
    Scraper do icarros.com.br.

    O nome da classe é mantido como 'CarrosWeb' para manter compatibilidade
    com specs_api.py (que instancia ambos os scrapers pelo mesmo nome de classe).
    """

    base_url = "https://www.icarros.com.br"

    # Headers que imitam um Chrome real — reduz bloqueios
    _HEADERS = {
        "accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    # ── Camada HTTP ───────────────────────────────────────────────────────────

    async def _get(self, url: str, retries: int = 3) -> Optional[BeautifulSoup]:
        """
        Faz GET assíncrono usando curl_cffi (impersonation Chrome120).
        Retorna BeautifulSoup ou None em caso de erro/bloqueio/404.
        """
        for attempt in range(1, retries + 1):
            try:
                # curl_cffi é síncrono — executa em thread para não bloquear o loop
                loop = asyncio.get_event_loop()
                resp = await loop.run_in_executor(
                    None,
                    lambda: cffi_requests.get(
                        url,
                        headers=self._HEADERS,
                        impersonate="chrome120" if _CFFI_OK else None,
                        timeout=30,
                        verify=False,  # ignora erros de cert SSL (redes corporativas/VPN)
                    ),
                )

                if resp.status_code == 404:
                    logger.warning("[iCarros] 404 — URL inexistente: %s", url)
                    return None

                if resp.status_code != 200:
                    logger.warning(
                        "[iCarros] HTTP %d. Tentativa %d/%d: %s",
                        resp.status_code, attempt, retries, url,
                    )
                    await asyncio.sleep(2 ** attempt)
                    continue

                if _is_blocked(resp.text):
                    logger.warning("[iCarros] Bloqueio/captcha detectado: %s", url)
                    # Não faz sentido tentar de novo imediatamente
                    return None

                # Decodifica os bytes brutos: tenta UTF-8, cai em cp1252 para sites legados
                html = decodificar_html_bytes(resp.content)
                return BeautifulSoup(html, "html.parser")

            except Exception as exc:
                logger.warning(
                    "[iCarros] Exceção na tentativa %d/%d (%s): %s",
                    attempt, retries, url, exc,
                )
                if attempt == retries:
                    return None
                await asyncio.sleep(2 ** attempt)

        return None

    # ── Fabricantes ───────────────────────────────────────────────────────────

    async def obter_fabricantes(self) -> list[dict]:
        """
        Retorna lista de fabricantes a partir da página de marcas do iCarros.

        Formato de retorno:
            [{"id_busca": "ford", "nome_exibicao": "Ford"}, ...]

        O campo 'id_busca' é o slug da marca usado nas URLs subsequentes.
        """
        url = f"{self.base_url}/catalogo-zero-km/marcas/"
        soup = await self._get(url)

        if not soup:
            raise ConnectionError("[iCarros] Falha ao acessar o catálogo de marcas.")

        fabricantes: list[dict] = []

        # Cada marca aparece num <div class="card-base"> dentro de um <a href>
        for card in soup.find_all("div", class_="card-base"):
            nome_tag = card.find("p")
            if not nome_tag:
                continue

            nome = _limpar(nome_tag.get_text())
            if not nome:
                continue

            # Extrai o slug do href do elemento pai <a>
            parent_a = card.find_parent("a")
            slug = ""
            if parent_a and parent_a.get("href"):
                # href: /catalogo-zero-km/marcas/ford/  → slug = "ford"
                parts = [p for p in parent_a["href"].split("/") if p]
                slug = parts[-1] if parts else _slugify(nome)
            else:
                slug = _slugify(nome)

            fabricantes.append({
                "id_busca":      slug,
                "nome_exibicao": nome,
            })

        if not fabricantes:
            raise ValueError("[iCarros] Nenhuma marca encontrada na página de catálogo.")

        return fabricantes

    # ── Modelos ───────────────────────────────────────────────────────────────

    async def obter_modelos(self, fabricante: str) -> list[dict]:
        """
        Retorna modelos de um fabricante.

        'fabricante' pode ser o nome (ex: "Ford") ou o slug (ex: "ford").
        Formato de retorno:
            [{"fabricante": "ford", "id_busca": "ranger", "nome_exibicao": "Ranger"}, ...]
        """
        slug_marca = _slugify(fabricante)
        url = f"{self.base_url}/catalogo-zero-km/marcas/{slug_marca}/"
        soup = await self._get(url)

        if not soup:
            raise ConnectionError(
                f"[iCarros] Falha ao acessar os modelos da marca '{fabricante}'."
            )

        modelos: list[dict] = []

        # Cada modelo aparece num <a class="complete-ad-card"> com <h2> dentro
        for card in soup.find_all("a", class_="complete-ad-card"):
            titulo = card.find("h2")
            if not titulo:
                continue

            nome = _limpar(titulo.get_text())
            href = card.get("href", "")
            # href: /catalogo-zero-km/marcas/ford/ranger/  → slug = "ranger"
            parts = [p for p in href.split("/") if p]
            slug_modelo = parts[-1] if parts else _slugify(nome)

            modelos.append({
                "fabricante":    slug_marca,
                "id_busca":      slug_modelo,
                "nome_exibicao": nome,
            })

        return modelos

    # ── Versões ───────────────────────────────────────────────────────────────

    async def extrair_versoes(self, url: str) -> list[dict]:
        """
        Extrai versões disponíveis a partir de uma URL de modelo.

        O iCarros renderiza os selects de anos e versões dinamicamente via JS.
        Esta implementação tenta extrair do HTML estático; se vazio, retorna [].

        Formato de retorno:
            [{"id_veiculo": slug, "fabricante": str, "versao": str,
              "ano": str, "url_ficha": str}, ...]
        """
        soup = await self._get(url)
        if not soup:
            return []

        versoes: list[dict] = []

        # Extrai informações de contexto da URL para preencher fabricante
        # URL: .../marcas/{marca}/{modelo}/
        partes_url = [p for p in url.split("/") if p]
        # índice de "marcas" → marca está logo depois
        try:
            idx = partes_url.index("marcas")
            slug_marca  = partes_url[idx + 1] if idx + 1 < len(partes_url) else ""
            slug_modelo = partes_url[idx + 2] if idx + 2 < len(partes_url) else ""
        except ValueError:
            slug_marca  = ""
            slug_modelo = ""

        # Tenta encontrar select de versões
        select_versoes = soup.find("select", class_=re.compile(r"versions-select", re.I))
        if not select_versoes:
            # Fallback: qualquer select cujos valores pareçam slugs de rota
            for sel in soup.find_all("select"):
                opts = sel.find_all("option")
                if opts and "/" in opts[0].get("value", ""):
                    select_versoes = sel
                    break

        # Tenta encontrar select de anos
        select_anos = soup.find("select", class_=re.compile(r"years-select", re.I))
        if not select_anos:
            for sel in soup.find_all("select"):
                opts = sel.find_all("option")
                if opts and re.search(r"20\d{2}", opts[0].get("value", "")):
                    select_anos = sel
                    break

        # Monta dicionário ano-valor para lookup
        anos_por_opcao: dict[int, str] = {}
        if select_anos:
            for i, opt in enumerate(select_anos.find_all("option")):
                val = opt.get("value", "").strip()
                txt = _limpar(opt.get_text())
                if val and txt:
                    anos_por_opcao[i] = txt

        if select_versoes:
            for i, opt in enumerate(select_versoes.find_all("option")):
                val = opt.get("value", "").strip()
                txt = _limpar(opt.get_text())
                if not val or not txt:
                    continue

                # val: "/catalogo-zero-km/marcas/ford/ranger-raptor/3-0-gtdi-cd-raptor-auto-4wd-2026-2026/"
                parts = [p for p in val.split("/") if p]
                slug_versao = parts[-1] if parts else _slugify(txt)

                # Tenta extrair ano do slug (padrão: termina com -AAAA-AAAA ou -AAAA)
                ano = ""
                m_ano = re.search(r"-(\d{4})(?:-\d{4})?$", slug_versao)
                if m_ano:
                    ano = m_ano.group(1)
                elif i in anos_por_opcao:
                    ano = anos_por_opcao[i]

                url_ficha = (
                    f"{self.base_url}{val.rstrip('/')}/ficha-tecnica"
                    if val.startswith("/")
                    else f"{self.base_url}/catalogo-zero-km/marcas/"
                         f"{slug_marca}/{slug_modelo}/{slug_versao}/ficha-tecnica"
                )

                versoes.append({
                    "id_veiculo": slug_versao,
                    "fabricante": slug_marca,
                    "versao":     txt,
                    "ano":        ano,
                    "url_ficha":  url_ficha,
                })

        # Se o select não existia no HTML estático, tenta construir URL de ficha
        # direto a partir do slug do modelo (cobre casos de página sem JS renderizado)
        if not versoes and slug_marca and slug_modelo:
            logger.info(
                "[iCarros] Select de versões vazio — usando URL de ficha direta para %s/%s",
                slug_marca, slug_modelo,
            )
            versoes.append({
                "id_veiculo": slug_modelo,
                "fabricante": slug_marca,
                "versao":     slug_modelo.replace("-", " ").title(),
                "ano":        "",
                "url_ficha":  (
                    f"{self.base_url}/catalogo-zero-km/marcas/"
                    f"{slug_marca}/{slug_modelo}/ficha-tecnica"
                ),
            })

        return versoes

    # ── Paginação ─────────────────────────────────────────────────────────────

    async def obter_todas_urls_paginacao(self, url_base: str) -> list[str]:
        """
        O iCarros não usa paginação tradicional — retorna apenas a URL base.
        Mantido para compatibilidade com specs_api.py.
        """
        return [url_base]

    # ── Ficha técnica ─────────────────────────────────────────────────────────

    async def extrair_ficha_tecnica(self, url_ficha: str) -> list[dict]:
        """
        Extrai pares atributo/valor da ficha técnica de uma versão específica.

        O iCarros 2024+ usa a estrutura:
            <div class="sheet-theme-container" id="MOTORIZACAO">
              <div class="technical-sheet-item">
                <span class="...bold">Potência</span>
                <span class="...regular">272 cv</span>
              </div>
            </div>

        Também suporta a estrutura legada (tabelas HTML).

        Retorna lista de {"atributo": str, "valor": str} ou [] em caso de erro.
        """
        soup = await self._get(url_ficha)

        if not soup:
            logger.warning("[iCarros] Falha ao acessar ficha técnica: %s", url_ficha)
            return []

        specs: list[dict] = []

        # ── Estrutura moderna (2024+) ─────────────────────────────────────────
        containers = soup.find_all("div", class_="sheet-theme-container", id=True)

        # Fallback: busca containers pelo id sem exigir a classe exata
        if not containers:
            containers = [
                c for c in soup.find_all("div", id=True)
                if "sheet-theme-container" in " ".join(c.get("class", []))
            ]

        for container in containers:
            items = container.find_all(
                "div",
                class_=re.compile(r"technical-sheet-item|brakes-suspension-item"),
            )

            for item in items:
                # Nome do atributo — span com "bold" no nome da classe
                label_tag = item.find(
                    "span",
                    class_=re.compile(r"ids_textStyle_label_medium_bold"),
                )
                if not label_tag:
                    continue

                atributo = _limpar(label_tag.get_text())
                if not atributo:
                    continue

                valor = self._extrair_valor(item)
                if not valor or valor == "N/D":
                    continue

                specs.append({"atributo": atributo, "valor": valor})

        # ── Estrutura legada (tabelas HTML) ───────────────────────────────────
        if not specs:
            specs = self._extrair_legado(soup)

        if not specs:
            logger.warning(
                "[iCarros] Nenhuma seção de ficha técnica encontrada: %s", url_ficha
            )

        return specs

    # ── Extração de valor (estrutura moderna) ─────────────────────────────────

    def _extrair_valor(self, item) -> str:
        """
        Hierarquia de extração para um item de ficha técnica moderna:

          A) span.ids_textStyle_label_medium_regular  → valores de texto
          B) p.ids_textStyle_label_small_regular      → parágrafos (freios/suspensão)
          C) img[src*=check_circle]                   → equipamento presente → "Sim"
          D) fallback: primeiro span que não seja o label bold
        """
        # A — valor principal (texto)
        val_span = item.find(
            "span",
            class_=re.compile(r"ids_textStyle_label_medium_regular"),
        )
        if val_span:
            for br in val_span.find_all("br"):
                br.replace_with(" | ")
            raw = _limpar(val_span.get_text()).strip(" |")
            if raw and raw != "N/D":
                return raw

        # B — parágrafo (freios / suspensão)
        val_p = item.find(
            "p",
            class_=re.compile(r"ids_textStyle_label_small_regular"),
        )
        if val_p:
            raw = _limpar(val_p.get_text())
            if raw and raw != "N/D":
                return raw

        # C — ícone de presença (equipamentos booleanos)
        if item.find("img", src=re.compile(r"check_circle", re.I)):
            return "Sim"

        # D — fallback: primeiro span não-bold com conteúdo
        label_cls = ""
        bold_tag = item.find("span", class_=re.compile(r"bold"))
        if bold_tag:
            label_cls = " ".join(bold_tag.get("class", []))

        for sp in item.find_all("span"):
            cls = " ".join(sp.get("class", []))
            if "bold" in cls.lower():
                continue
            txt = _limpar(sp.get_text())
            if txt and txt != "N/D":
                return txt

        return "N/D"

    # ── Extração legada (tabelas / dl) ────────────────────────────────────────

    def _extrair_legado(self, soup: BeautifulSoup) -> list[dict]:
        """
        Extração de fichas técnicas em formato legado (tabelas HTML e <dl>).
        Usado como fallback quando a estrutura moderna não é encontrada.
        """
        rows: list[dict] = []
        seen: set[str] = set()

        # Tabelas — busca pares <td>rótulo</td><td>valor</td>
        for sel in [
            ".ficha-tecnica table", "table.fichaTable",
            "table.tabelaFicha", "table",
        ]:
            for table in soup.select(sel):
                for row in table.find_all("tr"):
                    cols = row.find_all(["td", "th"])
                    if len(cols) >= 2:
                        atributo = _limpar(cols[0].get_text())
                        valor    = _limpar(cols[1].get_text())
                        if atributo and valor and atributo not in seen:
                            seen.add(atributo)
                            rows.append({"atributo": atributo, "valor": valor})

        # Listas de definição
        for dl in soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                atributo = _limpar(dt.get_text())
                valor    = _limpar(dd.get_text())
                if atributo and valor and atributo not in seen:
                    seen.add(atributo)
                    rows.append({"atributo": atributo, "valor": valor})

        return rows

