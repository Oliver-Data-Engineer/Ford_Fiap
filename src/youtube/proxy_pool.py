"""
proxy_pool.py
Pool de proxies HTTP/HTTPS para contornar bloqueios de IP do YouTube — tanto
na coleta de transcrições (RequestBlocked/IpBlocked) quanto nas chamadas à
YouTube Data API v3.

Duas fontes possíveis, mesmo formato de linha (`host:porta:usuario:senha`,
exportação padrão da Webshare):

  • arquivo local (`ProxyPool.de_arquivo`) — um CSV salvo em disco.
  • URL de download (`ProxyPool.de_url`) — o link de "Download" do painel da
    Webshare, que sempre devolve a lista atual do plano. Preferível ao
    arquivo estático porque não fica desatualizado.

O caminho/URL nunca é hardcoded: vêm de variáveis de ambiente
(YOUTUBE_PROXY_LIST / YOUTUBE_PROXY_LIST_URL). A URL de download carrega um
token de acesso — nunca aparece em log (só o host, via `_mascarar_url`).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

from src.config import settings

logger = logging.getLogger(__name__)


def _mascarar_url(url: str) -> str:
    """Só o host — a URL de download da Webshare carrega um token no path."""
    try:
        return urlparse(url).netloc or "<url>"
    except ValueError:
        return "<url>"


def _parsear_linhas(texto: str, origem: str) -> list[str]:
    """
    Converte linhas `host:porta:usuario:senha` em URLs de proxy
    `http://usuario:senha@host:porta`. Linhas vazias ou malformadas são
    ignoradas (logadas, não fatais).
    """
    proxies: list[str] = []
    for numero, linha in enumerate(texto.splitlines(), start=1):
        linha = linha.strip().rstrip(",")
        if not linha:
            continue

        partes = linha.split(":")
        if len(partes) != 4:
            logger.warning(
                "[proxy_pool] linha %d ignorada (esperado host:porta:usuario:senha) em %s",
                numero, origem,
            )
            continue

        host, porta, usuario, senha = partes
        proxies.append(f"http://{usuario}:{senha}@{host}:{porta}")

    return proxies


class ProxyPool:
    """Rotação round-robin sobre uma lista de proxies HTTP autenticados."""

    def __init__(self, proxies: list[str]):
        self._proxies = proxies
        self._indice = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._proxies)

    @property
    def habilitado(self) -> bool:
        return bool(self._proxies)

    def proximo(self) -> str | None:
        """URL do próximo proxy na rotação (`http://usuario:senha@host:porta`)."""
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._indice % len(self._proxies)]
            self._indice += 1
            return proxy

    @classmethod
    def vazio(cls) -> "ProxyPool":
        return cls([])

    @classmethod
    def de_arquivo(cls, caminho: str | Path) -> "ProxyPool":
        """
        Lê um CSV `host:porta:usuario:senha` (uma linha por proxy) do disco.
        Arquivo ausente/ilegível não é erro fatal — vira pool vazio, e a
        coleta continua funcionando sem proxy.
        """
        caminho = Path(caminho)
        if not caminho.is_file():
            logger.warning("[proxy_pool] arquivo de proxies não encontrado: %s", caminho)
            return cls.vazio()

        try:
            texto = caminho.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("[proxy_pool] falha ao ler %s: %s", caminho, exc)
            return cls.vazio()

        proxies = _parsear_linhas(texto, origem=str(caminho))
        logger.info("[proxy_pool] %d proxy(s) carregado(s) de %s", len(proxies), caminho)
        return cls(proxies)

    @classmethod
    def de_url(cls, url: str, timeout: float = 15.0) -> "ProxyPool":
        """
        Baixa a lista de proxies de uma URL (o link de "Download" do painel
        da Webshare, por exemplo — sempre devolve o plano atual, ao
        contrário de um arquivo estático). Chamada síncrona — o chamador é
        responsável por rodar isto fora do event loop (worker thread) quando
        usado em contexto assíncrono.

        Falha de rede/formato não é fatal — vira pool vazio.
        """
        import httpx  # import local: evita custo de import em quem nunca usa proxy

        origem = _mascarar_url(url)
        try:
            resposta = httpx.get(url, timeout=timeout, follow_redirects=True)
            resposta.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("[proxy_pool] falha ao baixar lista de proxies de %s: %s", origem, exc)
            return cls.vazio()

        proxies = _parsear_linhas(resposta.text, origem=origem)
        logger.info("[proxy_pool] %d proxy(s) baixado(s) de %s", len(proxies), origem)
        return cls(proxies)


def resolver_pool_padrao() -> ProxyPool:
    """
    Pool padrão a partir do ambiente — compartilhado entre `TranscriptAPI` e
    `YouTubeAPI`, para as duas fontes de proxy usarem a mesma prioridade:

        YOUTUBE_PROXY_LIST_URL  (baixa a lista atual)
              ↓ (se falhar ou não estiver configurada)
        YOUTUBE_PROXY_LIST      (arquivo local salvo em disco)
              ↓
        desabilitado (nenhuma variável setada)

    Chamada síncrona — quem usa em contexto assíncrono deve rodar isto numa
    thread (`asyncio.to_thread`), já que `de_url` faz uma requisição HTTP.
    """
    if settings.youtube_proxy_list_url:
        pool = ProxyPool.de_url(settings.youtube_proxy_list_url)
        if pool.habilitado:
            return pool
        logger.warning(
            "[proxy_pool] YOUTUBE_PROXY_LIST_URL configurada mas sem proxies "
            "válidos — tentando YOUTUBE_PROXY_LIST como fallback"
        )

    if settings.youtube_proxy_list_path:
        return ProxyPool.de_arquivo(settings.youtube_proxy_list_path)

    return ProxyPool.vazio()
