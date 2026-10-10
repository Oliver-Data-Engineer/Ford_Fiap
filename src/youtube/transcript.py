"""
transcript.py
Cliente de transcrição de vídeos do YouTube.

Encapsula a biblioteca `youtube_transcript_api`, que não tem endpoint REST
público — é a única dependência externa síncrona da camada API, então as
chamadas são despachadas para uma thread para não travar o event loop.

A resposta devolvida preserva os segmentos originais (texto + start +
duration); juntar tudo num bloco de texto é trabalho da camada REFINED.

Proxy é FALLBACK, não padrão: toda busca tenta direto primeiro (rápido, e
cobre a maioria dos vídeos — a maior parte não está bloqueada). Só quando o
YouTube responde com RequestBlocked/IpBlocked é que o proxy entra em jogo —
isso evita pagar a latência de um proxy residencial em toda requisição só
para o caso raro de bloqueio. Duas estratégias de proxy, mutuamente
exclusivas (Webshare nativo tem prioridade quando ambas configuradas):

  • Webshare nativo (`YOUTUBE_WEBSHARE_USERNAME`/`_PASSWORD`) — usa
    `WebshareProxyConfig`, que fala com o gateway rotativo da Webshare
    (`p.webshare.io`) e já tem retentativa embutida.
  • Lista (`YOUTUBE_PROXY_LIST_URL`/`YOUTUBE_PROXY_LIST`) — rotação e
    retentativa próprias, via `GenericProxyConfig`.
"""
import asyncio
import logging
from typing import Any, Callable

from src.config import settings
from src.youtube.proxy_pool import ProxyPool, resolver_pool_padrao

logger = logging.getLogger(__name__)

# Ordem de preferência de idioma ao escolher a transcrição disponível
IDIOMAS_PADRAO = ("pt", "pt-BR", "en")

# Quantas vezes tentar via proxy (um proxy diferente a cada vez) depois que a
# tentativa direta foi bloqueada, na estratégia de lista. Sem sentido tentar
# mais que o tamanho do pool. (Webshare nativo tem sua própria retentativa
# embutida em `WebshareProxyConfig.retries_when_blocked` — conta como 1 aqui.)
MAX_TENTATIVAS_PROXY = 3


class TranscriptError(RuntimeError):
    """Transcrição indisponível ou falha ao buscá-la."""

    def __init__(self, mensagem: str, motivo: str = "unavailable"):
        super().__init__(mensagem)
        self.motivo = motivo


class TranscriptAPI:
    """
    Busca transcrições do YouTube.

    A importação de `youtube_transcript_api` é adiada para o momento do uso:
    a aplicação inteira continua subindo mesmo sem o pacote instalado, e só
    a etapa de transcrição falha — com mensagem clara.
    """

    def __init__(
        self,
        idiomas: tuple[str, ...] = IDIOMAS_PADRAO,
        proxy_pool: ProxyPool | None = None,
        webshare_credenciais: tuple[str, str] | None = None,
    ):
        self._idiomas = idiomas
        self._webshare_credenciais = (
            webshare_credenciais if webshare_credenciais is not None
            else settings.youtube_webshare_credenciais
        )
        # Resolvido só se/quando a tentativa direta for bloqueada — nunca
        # antes, para não pagar o custo de baixar a lista (via `de_url`) num
        # vídeo que nem precisava de proxy.
        self._proxy_pool_resolvido: ProxyPool | None = proxy_pool

    def _obter_pool(self) -> ProxyPool:
        """Resolve e cacheia o pool — chamado só depois de um bloqueio real.
        Nunca resolve a lista estática quando o Webshare nativo está
        configurado: esse tem prioridade (ver `_proxy_configs_para_retentativa`)
        e não deveria pagar o custo de baixar/ler a lista à toa — reforçado
        aqui também, pra nenhum chamador futuro de `_obter_pool()` acabar
        contornando essa prioridade sem querer."""
        if self._webshare_credenciais:
            return ProxyPool.vazio()
        if self._proxy_pool_resolvido is None:
            self._proxy_pool_resolvido = resolver_pool_padrao()
        return self._proxy_pool_resolvido

    @staticmethod
    def _importar():
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
        except ImportError as exc:
            raise TranscriptError(
                "Pacote 'youtube-transcript-api' não instalado. "
                "Rode: pip install youtube-transcript-api",
                motivo="missing_dependency",
            ) from exc
        return YouTubeTranscriptApi

    @staticmethod
    def _importar_erro_bloqueio():
        """RequestBlocked (e sua subclasse IpBlocked) — YouTube recusando por
        volume/IP de datacenter. Import isolado: só é preciso depois que
        `_importar()` já confirmou que o pacote existe."""
        from youtube_transcript_api import RequestBlocked
        return RequestBlocked

    @staticmethod
    def _importar_proxy_config():
        from youtube_transcript_api.proxies import GenericProxyConfig
        return GenericProxyConfig

    @staticmethod
    def _importar_webshare_config():
        from youtube_transcript_api.proxies import WebshareProxyConfig
        return WebshareProxyConfig

    def _proxy_config_webshare(self):
        WebshareProxyConfig = self._importar_webshare_config()
        usuario, senha = self._webshare_credenciais
        return WebshareProxyConfig(proxy_username=usuario, proxy_password=senha)

    def _proxy_configs_para_retentativa(self):
        """
        Configs de proxy a tentar, em ordem, depois que a tentativa direta
        foi bloqueada. Lista vazia = sem proxy disponível (bloqueio é final).
        """
        if self._webshare_credenciais:
            # Retentativa já é interna ao WebshareProxyConfig — uma "config"
            # aqui já representa várias tentativas do lado do gateway.
            return [self._proxy_config_webshare()]

        pool = self._obter_pool()
        if not pool.habilitado:
            return []

        GenericProxyConfig = self._importar_proxy_config()
        n = min(MAX_TENTATIVAS_PROXY, len(pool))
        return [
            GenericProxyConfig(http_url=url, https_url=url)
            for url in (pool.proximo() for _ in range(n))
        ]

    def _buscar_sync(self, video_id: str, idiomas: tuple[str, ...]) -> dict[str, Any]:
        """Execução bloqueante — sempre chamada dentro de um worker thread."""
        YouTubeTranscriptApi = self._importar()
        RequestBlocked = self._importar_erro_bloqueio()

        def tentar(proxy_config) -> tuple[Any, list]:
            api = YouTubeTranscriptApi(proxy_config=proxy_config)
            disponiveis = api.list(video_id)
            transcricao = disponiveis.find_transcript(list(idiomas))
            return transcricao, transcricao.fetch()

        try:
            transcricao, segmentos_brutos = tentar(None)  # sempre direto primeiro
        except Exception as exc:
            if not isinstance(exc, RequestBlocked):
                raise TranscriptError(
                    f"Transcrição indisponível para o vídeo {video_id}: {exc}",
                    motivo="unavailable",
                ) from exc

            # Só bloqueado é que justifica pagar o custo de um proxy.
            transcricao, segmentos_brutos = self._tentar_via_proxy(
                tentar, video_id, RequestBlocked, causa_original=exc
            )

        # A lib devolve dicts em versões antigas e objetos nas novas
        segmentos: list[dict] = []
        for item in segmentos_brutos:
            if isinstance(item, dict):
                segmentos.append({
                    "text": item.get("text", ""),
                    "start": item.get("start", 0.0),
                    "duration": item.get("duration", 0.0),
                })
            else:
                segmentos.append({
                    "text": getattr(item, "text", ""),
                    "start": getattr(item, "start", 0.0),
                    "duration": getattr(item, "duration", 0.0),
                })

        return {
            "video_id": video_id,
            "language": getattr(transcricao, "language_code", None),
            "is_generated": getattr(transcricao, "is_generated", None),
            "segments": segmentos,
        }

    def _tentar_via_proxy(
        self,
        tentar: Callable[[Any], tuple[Any, list]],
        video_id: str,
        RequestBlocked: type,
        causa_original: Exception,
    ) -> tuple[Any, list]:
        """Escalada pós-bloqueio: tenta de novo com cada proxy disponível, em ordem."""
        proxy_configs = self._proxy_configs_para_retentativa()

        if not proxy_configs:
            raise TranscriptError(
                f"Transcrição indisponível para o vídeo {video_id}: {causa_original}",
                motivo="blocked",
            ) from causa_original

        ultimo_erro: Exception = causa_original
        for indice, proxy_config in enumerate(proxy_configs, start=1):
            try:
                logger.info(
                    "[transcript] %s bloqueado direto — tentando via proxy (%d/%d)",
                    video_id, indice, len(proxy_configs),
                )
                return tentar(proxy_config)
            except Exception as exc:
                if not isinstance(exc, RequestBlocked):
                    raise TranscriptError(
                        f"Transcrição indisponível para o vídeo {video_id}: {exc}",
                        motivo="unavailable",
                    ) from exc
                ultimo_erro = exc

        raise TranscriptError(
            f"Transcrição indisponível para o vídeo {video_id}: {ultimo_erro}",
            motivo="blocked",
        ) from ultimo_erro

    async def get_transcript(
        self,
        video_id: str,
        idiomas: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """
        Transcrição de um vídeo.

        Retorna:
            {"video_id", "language", "is_generated", "segments": [...]}

        Levanta TranscriptError quando o vídeo não tem legendas disponíveis
        (ou quando a tentativa direta e todas as de proxy são bloqueadas).
        """
        return await asyncio.to_thread(
            self._buscar_sync, video_id, idiomas or self._idiomas
        )
