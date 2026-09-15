"""
proxy.py
Resolver de pool de proxy do Scraper Mode — mesma prioridade de
`src.youtube.proxy_pool.resolver_pool_padrao()`, mas lendo as variáveis
próprias (`SCRAPER_PROXY_LIST_URL`/`SCRAPER_PROXY_LIST`).

Reaproveita a classe `ProxyPool` (`src/youtube/proxy_pool.py`) verbatim —
ela já é domain-agnostic (round-robin sobre uma lista de URLs de proxy),
não existe motivo pra reimplementar isso aqui.
"""
from __future__ import annotations

import logging

from src.config import settings
from src.youtube.proxy_pool import ProxyPool

logger = logging.getLogger(__name__)


def resolver_pool_padrao() -> ProxyPool:
    """
    SCRAPER_PROXY_LIST_URL (baixa a lista atual)
          ↓ (se falhar ou não estiver configurada)
    SCRAPER_PROXY_LIST (arquivo local salvo em disco)
          ↓
    desabilitado (nenhuma variável setada — coleta roda sem proxy)

    Chamada síncrona — `de_url` faz uma requisição HTTP; quem usa em
    contexto assíncrono deve rodar isto numa thread (`asyncio.to_thread`).
    """
    if settings.scraper_proxy_list_url:
        pool = ProxyPool.de_url(settings.scraper_proxy_list_url)
        if pool.habilitado:
            return pool
        logger.warning(
            "[scraper] SCRAPER_PROXY_LIST_URL configurada mas sem proxies "
            "válidos — tentando SCRAPER_PROXY_LIST como fallback"
        )

    if settings.scraper_proxy_list_path:
        return ProxyPool.de_arquivo(settings.scraper_proxy_list_path)

    return ProxyPool.vazio()
