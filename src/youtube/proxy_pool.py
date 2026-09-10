"""
proxy_pool.py
Pool de proxies HTTP/HTTPS para contornar bloqueios de IP do YouTube na
coleta de transcrições (RequestBlocked/IpBlocked — ver transcript.py).

Formato do arquivo — uma linha por proxy, exportação padrão da Webshare:
    host:porta:usuario:senha

O caminho do arquivo nunca é hardcoded: vem de YOUTUBE_PROXY_LIST no `.env`.
O arquivo em si normalmente fica em `config/`, que já está no `.gitignore`
(Regra 33 da spec: credenciais nunca no código nem versionadas).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


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
        Lê um CSV `host:porta:usuario:senha` (uma linha por proxy) e monta o
        pool. Arquivo ausente/ilegível/malformado não é erro fatal — vira
        pool vazio, e a coleta continua funcionando sem proxy.
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

        proxies: list[str] = []
        for numero, linha in enumerate(texto.splitlines(), start=1):
            linha = linha.strip().rstrip(",")
            if not linha:
                continue

            partes = linha.split(":")
            if len(partes) != 4:
                logger.warning(
                    "[proxy_pool] linha %d ignorada (esperado host:porta:usuario:senha): %s",
                    numero, caminho,
                )
                continue

            host, porta, usuario, senha = partes
            proxies.append(f"http://{usuario}:{senha}@{host}:{porta}")

        logger.info("[proxy_pool] %d proxy(s) carregado(s) de %s", len(proxies), caminho)
        return cls(proxies)
