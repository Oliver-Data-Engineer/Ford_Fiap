"""
browser.py
Busca de HTML renderizado via Playwright (Chromium headless) — usado tanto
na descoberta (`discovery.py`, HTML pra mandar pro LLM) quanto na extração
determinística (`engine.py`/`extractor.py`, HTML pra rodar os seletores já
salvos).

Usa o **`sync_api`** do Playwright, rodado numa thread à parte via
`loop.run_in_executor` — não o `async_api`. Motivo: o `async_api` lança o
Chromium via `asyncio.create_subprocess_exec`, que no Windows só funciona
sob `ProactorEventLoop`; o `uvicorn` (principalmente com `--reload`, que
`main.py` liga por padrão) roda sob `SelectorEventLoop` no Windows, que não
suporta subprocess — toda chamada batia em
`NotImplementedError` (`asyncio/base_events.py::_make_subprocess_transport`).
O `sync_api` abre o processo do Chromium via `subprocess.Popen` puro (não
via asyncio), então funciona sob qualquer event loop — rodá-lo numa thread
via `run_in_executor` evita esse conflito sem precisar trocar a política de
event loop do processo inteiro (o que arriscaria efeito colateral no resto
do app, que também é servido pelo mesmo loop).

Mesmo contrato de resiliência de `src.utils.CarrosWeb._get`: retry com
backoff exponencial (`2**attempt`, até 3 tentativas), nunca levanta exceção
— devolve `None` em falha total. Cada tentativa usa um proxy diferente do
pool (rotação em cada retry, não só entre requisições diferentes).
"""
from __future__ import annotations

import asyncio
import logging

from src.config import settings
from src.scraper.proxy import resolver_pool_padrao

logger = logging.getLogger(__name__)

_pool = None


def _obter_pool():
    global _pool
    if _pool is None:
        _pool = resolver_pool_padrao()
    return _pool


def _proxy_playwright(url_proxy: str | None) -> dict | None:
    """Converte `http://usuario:senha@host:porta` no formato que o
    Playwright espera em `new_context(proxy=...)`."""
    if not url_proxy:
        return None

    from urllib.parse import urlparse

    partes = urlparse(url_proxy)
    proxy: dict = {"server": f"{partes.scheme}://{partes.hostname}:{partes.port}"}
    if partes.username:
        proxy["username"] = partes.username
    if partes.password:
        proxy["password"] = partes.password
    return proxy


def _buscar_html_sincrono(url: str, proxy_url: str | None) -> str:
    """Busca de verdade, via `sync_api` — só é chamada de dentro de uma
    thread de worker (nunca do event loop principal), por `buscar_html`."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        navegador = p.chromium.launch(headless=settings.scraper_playwright_headless)
        try:
            contexto = navegador.new_context(proxy=_proxy_playwright(proxy_url))
            pagina = contexto.new_page()
            # "networkidle" (usado antes) trava/estoura timeout em sites reais
            # com ads/analytics/websockets que nunca ficam totalmente ociosos
            # (achado real testando contra carrosnaweb.com.br) —
            # "domcontentloaded" basta pra extrair a estrutura via seletor CSS
            # e é muito mais confiável na prática.
            pagina.goto(url, timeout=settings.scraper_playwright_timeout_ms, wait_until="domcontentloaded")
            return pagina.content()
        finally:
            navegador.close()


async def buscar_html(url: str, *, retries: int = 3) -> str | None:
    """
    Abre `url` num Chromium headless (numa thread à parte) e devolve o HTML.

    Nunca levanta exceção — devolve `None` se todas as tentativas falharem
    (timeout, proxy ruim, DNS, Playwright não instalado, etc.), mesmo
    contrato de `CarrosWeb._get`.
    """
    try:
        import playwright.sync_api  # noqa: F401 — só confirma que o pacote existe
    except ImportError:
        logger.error(
            "[scraper] pacote 'playwright' não instalado (ou 'playwright install "
            "chromium' não foi rodado) — auto-discovery/extração indisponível."
        )
        return None

    pool = _obter_pool()
    loop = asyncio.get_running_loop()

    for tentativa in range(retries):
        proxy_url = pool.proximo()
        try:
            return await loop.run_in_executor(None, _buscar_html_sincrono, url, proxy_url)
        except Exception as exc:
            logger.warning(
                "[scraper] tentativa %d/%d falhou ao buscar '%s': %s",
                tentativa + 1, retries, url, exc,
            )
            if tentativa < retries - 1:
                await asyncio.sleep(2 ** tentativa)

    return None
