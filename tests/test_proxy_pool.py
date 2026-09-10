"""
test_proxy_pool.py — verifica o pool de proxies e a retentativa automática
em `TranscriptAPI` quando o YouTube bloqueia a requisição (RequestBlocked).

Roda sem rede: o CSV é escrito num arquivo temporário e o cliente da
biblioteca é substituído por um dublê que decide, por chamada, se bloqueia
ou sucede — o que importa aqui é a lógica de rotação/retentativa, não a
biblioteca em si (essa parte já é coberta por tests/test_transcript.py
contra as exceções reais).
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.youtube.proxy_pool import ProxyPool
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

def testar_retentativa_com_bloqueio():
    print("\n=== TranscriptAPI: retentativa automática ao ser bloqueado ===")

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
        """Falha com RequestBlocked nas 2 primeiras chamadas, sucede na 3ª."""
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

    checar(resultado["segments"][0]["text"] == "ok", "sucede na 3ª tentativa, com o 3º proxy")
    checar(chamadas == ["http://proxy1", "http://proxy2", "http://proxy3"],
           f"cada tentativa usou um proxy diferente, em ordem (obtido {chamadas})")


def testar_bloqueio_esgota_todas_tentativas():
    print("\n=== TranscriptAPI: bloqueio em todos os proxies ===")

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
            checar(len(tentativas) == 2, f"parou depois de esgotar os 2 proxies (obtido {len(tentativas)})")


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
    checar(not api._proxy_pool.habilitado,
           "a lista estática nem chega a ser carregada quando Webshare está configurado")


def testar_webshare_usa_config_fixo_uma_tentativa():
    print("\n=== TranscriptAPI: Webshare nativo — uma passada, sem trocar proxy_config ===")

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
        def __init__(self, proxy_config=None):
            configs_recebidos.append(proxy_config)

        def list(self, video_id):
            return ListaFalsa()

    class WebshareProxyConfigFalso:
        def __init__(self, proxy_username, proxy_password):
            self.proxy_username = proxy_username
            self.proxy_password = proxy_password

    with patch.object(TranscriptAPI, "_importar", staticmethod(lambda: ClienteFalso)), \
         patch.object(TranscriptAPI, "_importar_webshare_config", staticmethod(lambda: WebshareProxyConfigFalso)):
        resultado = api._buscar_sync("vid_teste", ("pt",))

    checar(resultado["segments"][0]["text"] == "ok", "sucede usando o WebshareProxyConfig")
    checar(len(configs_recebidos) == 1, "uma única tentativa (retentativa é interna ao WebshareProxyConfig)")
    checar(
        isinstance(configs_recebidos[0], WebshareProxyConfigFalso)
        and configs_recebidos[0].proxy_username == "usuario_teste",
        "passou as credenciais certas para o WebshareProxyConfig",
    )


def principal() -> int:
    testar_parsing_csv()
    testar_rotacao()
    testar_retentativa_com_bloqueio()
    testar_bloqueio_esgota_todas_tentativas()
    testar_erro_nao_bloqueio_nao_tenta_de_novo()
    testar_sem_pool_comporta_como_antes()
    testar_webshare_tem_prioridade_sobre_lista_estatica()
    testar_webshare_usa_config_fixo_uma_tentativa()

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
