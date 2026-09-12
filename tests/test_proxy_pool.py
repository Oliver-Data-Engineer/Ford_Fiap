"""
test_proxy_pool.py — verifica o pool de proxies e a retentativa automática
em `TranscriptAPI` quando o YouTube bloqueia a requisição (RequestBlocked).

Roda sem rede: o CSV é escrito num arquivo temporário e o cliente da
biblioteca é substituído por um dublê que decide, por chamada, se bloqueia
ou sucede — o que importa aqui é a lógica de rotação/retentativa, não a
biblioteca em si (essa parte já é coberta por tests/test_transcript.py
contra as exceções reais).
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.youtube import YouTubeAPI
from src.youtube.proxy_pool import ProxyPool, resolver_pool_padrao
from src.youtube.transcript import TranscriptAPI, TranscriptError
from youtube_transcript_api import RequestBlocked, TranscriptsDisabled

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


# ─────────────────────────────────────────────────────────────────────────────
# ProxyPool — parsing e rotação
# ─────────────────────────────────────────────────────────────────────────────

def testar_parsing_csv():
    print("\n=== ProxyPool: parsing do CSV ===")

    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "proxies.csv"
        caminho.write_text(
            "1.2.3.4:6754:userA:passA,\n"
            "5.6.7.8:6014:userA:passA,\n"
            "\n"                          # linha vazia — deve ser ignorada
            "linha-mal-formada\n"         # sem 4 partes — deve ser ignorada
            "9.10.11.12:6462:userA:passA",
            encoding="utf-8",
        )

        pool = ProxyPool.de_arquivo(caminho)
        checar(len(pool) == 3, f"3 proxies válidos carregados (obtido {len(pool)})")
        checar(pool.habilitado, "pool com proxies está habilitado")
        checar(
            pool.proximo() == "http://userA:passA@1.2.3.4:6754",
            "monta a URL http://usuario:senha@host:porta corretamente",
        )

    pool_ausente = ProxyPool.de_arquivo(Path(tempfile.gettempdir()) / "nao_existe_12345.csv")
    checar(not pool_ausente.habilitado, "arquivo ausente vira pool vazio (não fatal)")


def testar_rotacao():
    print("\n=== ProxyPool: rotação round-robin ===")

    pool = ProxyPool(["http://a", "http://b", "http://c"])
    sequencia = [pool.proximo() for _ in range(7)]
    checar(
        sequencia == ["http://a", "http://b", "http://c", "http://a", "http://b", "http://c", "http://a"],
        f"round-robin cicla na ordem certa (obtido {sequencia})",
    )

    pool_vazio = ProxyPool.vazio()
    checar(pool_vazio.proximo() is None, "pool vazio devolve None em vez de erro")
    checar(not pool_vazio.habilitado, "pool vazio não está habilitado")


# ─────────────────────────────────────────────────────────────────────────────
# TranscriptAPI — retentativa com o próximo proxy ao ser bloqueado
# ─────────────────────────────────────────────────────────────────────────────

def testar_direto_primeiro_proxy_so_depois_de_bloqueio():
    print("\n=== TranscriptAPI: tenta direto primeiro, proxy só depois de bloqueio ===")

    pool = ProxyPool(["http://proxy1", "http://proxy2", "http://proxy3"])
    api = TranscriptAPI(proxy_pool=pool)

    chamadas: list[str | None] = []

    class TranscricaoFalsa:
        language_code = "pt"
        is_generated = True

        def fetch(self):
            return [{"text": "ok", "start": 0.0, "duration": 1.0}]

    class ListaFalsa:
        def find_transcript(self, idiomas):
            return TranscricaoFalsa()

    class ClienteQueBloqueiaDuasVezes:
        """Bloqueia a 1ª chamada (direta) e a 2ª (proxy1), sucede na 3ª (proxy2)."""
        def __init__(self, proxy_config=None):
            self.proxy_config = proxy_config

        def list(self, video_id):
            proxy_url = self.proxy_config.http_url if self.proxy_config else None
            chamadas.append(proxy_url)
            if len(chamadas) < 3:
                raise RequestBlocked(video_id)
            return ListaFalsa()

    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteQueBloqueiaDuasVezes)):
        resultado = api._buscar_sync("vid_teste", ("pt",))

    checar(resultado["segments"][0]["text"] == "ok", "sucede na 3ª tentativa (2º proxy)")
    checar(chamadas == [None, "http://proxy1", "http://proxy2"],
           f"1ª tentativa é sempre direta (None); proxy só entra após bloqueio (obtido {chamadas})")


def testar_bloqueio_esgota_todas_tentativas():
    print("\n=== TranscriptAPI: bloqueio direto + em todos os proxies ===")

    pool = ProxyPool(["http://proxy1", "http://proxy2"])
    api = TranscriptAPI(proxy_pool=pool)
    tentativas = []

    class ClienteSempreBloqueado:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            tentativas.append(1)
            raise RequestBlocked(video_id)

    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteSempreBloqueado)):
        try:
            api._buscar_sync("vid_teste", ("pt",))
            checar(False, "deveria ter levantado TranscriptError")
        except TranscriptError as exc:
            checar(exc.motivo == "blocked", "motivo final continua 'blocked'")
            checar(len(tentativas) == 3,
                   f"1 direta + 2 proxies, todas esgotadas (obtido {len(tentativas)})")


def testar_erro_nao_bloqueio_nao_tenta_de_novo():
    print("\n=== TranscriptAPI: erro que não é bloqueio não gasta o pool ===")

    pool = ProxyPool(["http://proxy1", "http://proxy2", "http://proxy3"])
    api = TranscriptAPI(proxy_pool=pool)
    tentativas = []

    class ClienteSemLegenda:
        def __init__(self, proxy_config=None):
            pass

        def list(self, video_id):
            tentativas.append(1)
            raise TranscriptsDisabled(video_id)

    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteSemLegenda)):
        try:
            api._buscar_sync("vid_teste", ("pt",))
            checar(False, "deveria ter levantado TranscriptError")
        except TranscriptError as exc:
            checar(exc.motivo == "unavailable", "motivo continua 'unavailable', não 'blocked'")
            checar(len(tentativas) == 1,
                   f"falhou na 1ª tentativa, sem trocar de proxy à toa (obtido {len(tentativas)})")


def testar_sem_pool_comporta_como_antes():
    print("\n=== TranscriptAPI: sem proxy configurado, uma tentativa só ===")

    api = TranscriptAPI(proxy_pool=ProxyPool.vazio())
    tentativas = []

    class ClienteSemProxy:
        def __init__(self, proxy_config=None):
            checar(proxy_config is None, "nenhum proxy_config é passado quando o pool está vazio")

        def list(self, video_id):
            tentativas.append(1)
            raise RequestBlocked(video_id)

    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteSemProxy)):
        try:
            api._buscar_sync("vid_teste", ("pt",))
        except TranscriptError:
            pass

    checar(len(tentativas) == 1, "sem pool, não fica retentando (comportamento anterior preservado)")


# ─────────────────────────────────────────────────────────────────────────────
# TranscriptAPI — estratégia Webshare nativa (WebshareProxyConfig)
# ─────────────────────────────────────────────────────────────────────────────

def testar_webshare_tem_prioridade_sobre_lista_estatica():
    print("\n=== TranscriptAPI: Webshare nativo tem prioridade sobre a lista estática ===")

    # Ambos vêm da resolução padrão (nenhum passado explicitamente no
    # construtor) — é o caminho real, quando as duas variáveis de ambiente
    # calham de estar preenchidas ao mesmo tempo.
    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "proxies.csv"
        caminho.write_text("1.2.3.4:6754:userA:passA", encoding="utf-8")

        with patch("src.youtube.transcript.settings") as settings_falso:
            settings_falso.youtube_webshare_credenciais = ("usuario_teste", "senha_teste")
            settings_falso.youtube_proxy_list_path = str(caminho)
            api = TranscriptAPI()

    checar(api._webshare_credenciais == ("usuario_teste", "senha_teste"),
           "credenciais Webshare resolvidas do ambiente")
    checar(not api._obter_pool().habilitado,
           "a lista estática nem chega a ser carregada quando Webshare está configurado")


def testar_webshare_usa_config_fixo_apos_bloqueio_direto():
    print("\n=== TranscriptAPI: bloqueado direto, sucede no WebshareProxyConfig ===")

    api = TranscriptAPI(webshare_credenciais=("usuario_teste", "senha_teste"))
    configs_recebidos = []

    class TranscricaoFalsa:
        language_code = "pt"
        is_generated = True

        def fetch(self):
            return [{"text": "ok", "start": 0.0, "duration": 1.0}]

    class ListaFalsa:
        def find_transcript(self, idiomas):
            return TranscricaoFalsa()

    class ClienteFalso:
        """Bloqueia a tentativa direta (proxy_config=None); sucede via proxy."""
        def __init__(self, proxy_config=None):
            configs_recebidos.append(proxy_config)
            self.proxy_config = proxy_config

        def list(self, video_id):
            if self.proxy_config is None:
                raise RequestBlocked(video_id)
            return ListaFalsa()

    class WebshareProxyConfigFalso:
        def __init__(self, proxy_username, proxy_password):
            self.proxy_username = proxy_username
            self.proxy_password = proxy_password

    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteFalso)), \
         patch.object(TranscriptAPI, "_importar_webshare_config", staticmethod(lambda: WebshareProxyConfigFalso)):
        resultado = api._buscar_sync("vid_teste", ("pt",))

    checar(resultado["segments"][0]["text"] == "ok", "sucede usando o WebshareProxyConfig após bloqueio direto")
    checar(len(configs_recebidos) == 2, "2 tentativas: 1 direta (bloqueada) + 1 via Webshare (obtido {})".format(len(configs_recebidos)))
    checar(configs_recebidos[0] is None, "1ª tentativa é direta, sem proxy")
    checar(
        isinstance(configs_recebidos[1], WebshareProxyConfigFalso)
        and configs_recebidos[1].proxy_username == "usuario_teste",
        "2ª tentativa usa o WebshareProxyConfig com as credenciais certas",
    )


# ─────────────────────────────────────────────────────────────────────────────
# ProxyPool.de_url — baixa a lista em vez de ler de um arquivo local
# ─────────────────────────────────────────────────────────────────────────────

def testar_de_url_sucesso():
    print("\n=== ProxyPool.de_url: parsing de uma resposta HTTP ===")

    resposta_falsa = MagicMock()
    resposta_falsa.text = "1.2.3.4:6754:userA:passA\n5.6.7.8:6014:userA:passA"
    resposta_falsa.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=resposta_falsa) as get_falso:
        pool = ProxyPool.de_url("https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN-SECRETO/-/any/username/direct/-/")

    checar(len(pool) == 2, f"2 proxies parseados da resposta (obtido {len(pool)})")
    checar(pool.proximo() == "http://userA:passA@1.2.3.4:6754", "URL montada corretamente")
    checar(get_falso.call_args.kwargs.get("follow_redirects") is True, "segue redirecionamentos")


def testar_de_url_falha_de_rede_nao_e_fatal():
    print("\n=== ProxyPool.de_url: falha de rede vira pool vazio ===")

    import httpx as _httpx

    with patch("httpx.get", side_effect=_httpx.ConnectError("sem rede")):
        pool = ProxyPool.de_url("https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN-SECRETO/-/any/username/direct/-/")

    checar(not pool.habilitado, "falha ao baixar não quebra — só desabilita o proxy")


def testar_url_nunca_aparece_em_log_de_erro(capsys=None):
    print("\n=== ProxyPool.de_url: token da URL não vaza para o log ===")

    import httpx as _httpx
    import logging

    mensagens = []
    handler = logging.Handler()
    handler.emit = lambda record: mensagens.append(record.getMessage())
    logger_proxy = logging.getLogger("src.youtube.proxy_pool")
    logger_proxy.addHandler(handler)
    try:
        with patch("httpx.get", side_effect=_httpx.ConnectError("sem rede")):
            ProxyPool.de_url("https://proxy.webshare.io/api/v2/proxy/list/download/TOKEN-SECRETO/-/any/username/direct/-/")
    finally:
        logger_proxy.removeHandler(handler)

    texto_logado = " ".join(mensagens)
    checar("TOKEN-SECRETO" not in texto_logado, "o token da URL nunca aparece na mensagem de log")
    checar("proxy.webshare.io" in texto_logado, "o host aparece no log (só não o token)")


def testar_resolver_pool_padrao_prioridade_url_sobre_arquivo():
    print("\n=== resolver_pool_padrao: URL tem prioridade, com fallback pro arquivo ===")

    resposta_falsa = MagicMock()
    resposta_falsa.text = "9.9.9.9:1111:u:p"
    resposta_falsa.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmp:
        caminho = Path(tmp) / "fallback.csv"
        caminho.write_text("1.1.1.1:2222:u:p", encoding="utf-8")

        with patch("src.youtube.proxy_pool.settings") as settings_falso:
            settings_falso.youtube_proxy_list_url = "https://proxy.webshare.io/download/TOKEN"
            settings_falso.youtube_proxy_list_path = str(caminho)

            with patch("httpx.get", return_value=resposta_falsa):
                pool = resolver_pool_padrao()
            checar(pool.proximo() == "http://u:p@9.9.9.9:1111",
                   "URL usada quando ambas estão configuradas e a URL funciona")

            # agora a URL falha — deve cair pro arquivo, não desistir
            import httpx as _httpx
            with patch("httpx.get", side_effect=_httpx.ConnectError("sem rede")):
                pool_fallback = resolver_pool_padrao()
            checar(pool_fallback.proximo() == "http://u:p@1.1.1.1:2222",
                   "cai para o arquivo local quando a URL falha")


# ─────────────────────────────────────────────────────────────────────────────
# YouTubeAPI — NUNCA usa proxy (só a transcrição precisa; API oficial não
# tem o problema de bloqueio por IP, e proxy só adicionaria latência à toa)
# ─────────────────────────────────────────────────────────────────────────────

def testar_youtube_api_nunca_usa_proxy():
    print("\n=== YouTubeAPI: chamadas sempre diretas, nunca por proxy ===")

    api = YouTubeAPI(api_key="chave-teste")
    proxies_usados = []

    class ClienteAsyncFalso:
        def __init__(self, timeout=None, proxy=None):
            proxies_usados.append(proxy)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            resposta = MagicMock()
            resposta.status_code = 200
            resposta.json.return_value = {"items": []}
            return resposta

    with patch("src.youtube.client.httpx.AsyncClient", ClienteAsyncFalso):
        asyncio.run(api._get("videos", {"id": "x"}))
        asyncio.run(api._get("search", {"q": "y"}))

    checar(proxies_usados == [None, None],
           f"nenhuma chamada usa proxy, mesmo havendo mais de uma (obtido {proxies_usados})")


def principal() -> int:
    testar_parsing_csv()
    testar_rotacao()
    testar_direto_primeiro_proxy_so_depois_de_bloqueio()
    testar_bloqueio_esgota_todas_tentativas()
    testar_erro_nao_bloqueio_nao_tenta_de_novo()
    testar_sem_pool_comporta_como_antes()
    testar_webshare_tem_prioridade_sobre_lista_estatica()
    testar_webshare_usa_config_fixo_apos_bloqueio_direto()
    testar_de_url_sucesso()
    testar_de_url_falha_de_rede_nao_e_fatal()
    testar_url_nunca_aparece_em_log_de_erro()
    testar_resolver_pool_padrao_prioridade_url_sobre_arquivo()
    testar_youtube_api_nunca_usa_proxy()

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de proxy_pool passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
