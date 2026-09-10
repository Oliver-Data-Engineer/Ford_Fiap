"""
transcript.py
Cliente de transcrição de vídeos do YouTube.

Encapsula a biblioteca `youtube_transcript_api`, que não tem endpoint REST
público — é a única dependência externa síncrona da camada API, então as
chamadas são despachadas para uma thread para não travar o event loop.

A resposta devolvida preserva os segmentos originais (texto + start +
duration); juntar tudo num bloco de texto é trabalho da camada REFINED.

Duas estratégias de proxy, mutuamente exclusivas (Webshare nativo tem
prioridade quando ambas estão configuradas):

  • Webshare nativo (`YOUTUBE_WEBSHARE_USERNAME`/`_PASSWORD`) — usa
    `WebshareProxyConfig`, que fala com o gateway rotativo da Webshare
    (`p.webshare.io`) e já tem retentativa embutida. Só funciona de verdade
    com o pacote "Residential" da Webshare — "Proxy Server"/"Static
    Residential"/gratuito continuam bloqueados pelo YouTube mesmo passando
    por aqui (a própria biblioteca detecta e explica isso no erro).
  • Lista estática (`YOUTUBE_PROXY_LIST`) — um CSV de proxies fixos
    (`host:porta:usuario:senha`); a rotação e a retentativa entre eles são
    feitas aqui mesmo, via `GenericProxyConfig`.
"""
import asyncio
import logging
from typing import Any

from src.config import settings
from src.youtube.proxy_pool import ProxyPool

logger = logging.getLogger(__name__)

# Ordem de preferência de idioma ao escolher a transcrição disponível
IDIOMAS_PADRAO = ("pt", "pt-BR", "en")

# Quantas vezes tentar de novo (com um proxy diferente a cada vez) quando o
# YouTube bloqueia a requisição, na estratégia de lista estática. Sem sentido
# tentar mais que o tamanho do pool — se todos os proxies levarem ao mesmo
# bloqueio, insistir não ajuda. (A estratégia Webshare nativa tem sua própria
# retentativa embutida em `WebshareProxyConfig.retries_when_blocked`.)
MAX_TENTATIVAS_BLOQUEIO = 3


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
        # A lista estática só entra em jogo se o Webshare nativo não estiver
        # configurado — os dois nunca operam ao mesmo tempo.
        self._proxy_pool = (
            proxy_pool if proxy_pool is not None
            else (ProxyPool.vazio() if self._webshare_credenciais else self._pool_padrao())
        )

    @staticmethod
    def _pool_padrao() -> ProxyPool:
        caminho = settings.youtube_proxy_list_path
        return ProxyPool.de_arquivo(caminho) if caminho else ProxyPool.vazio()

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
        """
        `WebshareProxyConfig` fixo, reaproveitado em toda tentativa — a
        rotação entre IPs acontece do lado do gateway da Webshare, não aqui.
        """
        WebshareProxyConfig = self._importar_webshare_config()
        usuario, senha = self._webshare_credenciais
        return WebshareProxyConfig(proxy_username=usuario, proxy_password=senha)

    def _buscar_sync(self, video_id: str, idiomas: tuple[str, ...]) -> dict[str, Any]:
        """Execução bloqueante — sempre chamada dentro de um worker thread."""
        YouTubeTranscriptApi = self._importar()
        RequestBlocked = self._importar_erro_bloqueio()

        usando_webshare = bool(self._webshare_credenciais)
        proxy_config_webshare = self._proxy_config_webshare() if usando_webshare else None

        if usando_webshare:
            # A retentativa já acontece dentro do WebshareProxyConfig
            # (retries_when_blocked) — uma única passada aqui é suficiente.
            tentativas = 1
        elif self._proxy_pool.habilitado:
            tentativas = min(MAX_TENTATIVAS_BLOQUEIO, len(self._proxy_pool))
        else:
            tentativas = 1

        transcricao = None
        segmentos_brutos = None

        for tentativa in range(1, tentativas + 1):
            if usando_webshare:
                proxy_config = proxy_config_webshare
            elif self._proxy_pool.habilitado:
                proxy_url = self._proxy_pool.proximo()
                GenericProxyConfig = self._importar_proxy_config()
                proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
            else:
                proxy_config = None

            try:
                api = YouTubeTranscriptApi(proxy_config=proxy_config)
                disponiveis = api.list(video_id)
                transcricao = disponiveis.find_transcript(list(idiomas))
                segmentos_brutos = transcricao.fetch()
                break  # sucesso
            except TranscriptError:
                raise
            except Exception as exc:
                # RequestBlocked/IpBlocked é o YouTube recusando por volume/IP
                # de datacenter — não tem relação com o vídeo em si. Só nesse
                # caso vale trocar de proxy e tentar de novo; qualquer outro
                # motivo (sem legenda, vídeo indisponível…) falha na hora.
                bloqueado = isinstance(exc, RequestBlocked)

                if bloqueado and tentativa < tentativas:
                    logger.info(
                        "[transcript] %s bloqueado na tentativa %d — tentando de novo (%d/%d)",
                        video_id, tentativa, tentativa + 1, tentativas,
                    )
                    continue

                raise TranscriptError(
                    f"Transcrição indisponível para o vídeo {video_id}: {exc}",
                    motivo="blocked" if bloqueado else "unavailable",
                ) from exc

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
        (ou quando todas as tentativas de contornar um bloqueio de IP falham).
        """
        return await asyncio.to_thread(
            self._buscar_sync, video_id, idiomas or self._idiomas
        )
