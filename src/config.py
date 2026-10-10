"""
config.py
Configuração central da aplicação — lê exclusivamente variáveis de ambiente
(opcionalmente carregadas de um arquivo .env na raiz do projeto).

Regra de segurança (Regra 33 da spec):
    Nenhuma chave de API deve aparecer em HTML, JavaScript, logs, dados RAW
    ou no código da DAG gerada.  Todo acesso a credencial passa por aqui.

Uso:
    from src.config import settings

    settings.youtube_api_key
    settings.llm_api_key("groq")
"""
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Raiz do projeto (…/Ford_Fiap) — este arquivo fica em …/Ford_Fiap/src/config.py
BASE_DIR = Path(__file__).resolve().parent.parent

# Diretório onde prompts/pipelines/execuções são persistidos
DATA_DIR = BASE_DIR / "data"

# Diretório onde as DAGs geradas são escritas
DAGS_DIR = BASE_DIR / "airflow" / "dags"


# ─────────────────────────────────────────────────────────────────────────────
# Carga do .env — implementação mínima, sem depender de python-dotenv
# ─────────────────────────────────────────────────────────────────────────────

def carregar_env(caminho: Path | None = None) -> None:
    """
    Lê um arquivo .env no formato CHAVE=valor e injeta em os.environ.

    Variáveis já presentes no ambiente têm precedência — o .env nunca
    sobrescreve o que foi definido pelo sistema/container.
    """
    arquivo = caminho or (BASE_DIR / ".env")
    if not arquivo.is_file():
        return

    try:
        conteudo = arquivo.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("[config] não foi possível ler %s: %s", arquivo, exc)
        return

    definidas_neste_arquivo: set[str] = set()

    for linha in conteudo.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")

        if not chave:
            continue

        # Chave repetida no próprio .env é quase sempre erro de edição (duas
        # versões da mesma variável, uma delas esquecida) — a primeira
        # ocorrência sempre vence, mas isso não pode ficar silencioso: já
        # causou um bug real (GOOGLE_APPLICATION_CREDENTIALS apontando pra
        # pasta errada porque uma segunda linha, correta, foi ignorada).
        if chave in definidas_neste_arquivo:
            logger.warning(
                "[config] '%s' está definida mais de uma vez em %s — "
                "usando a primeira ocorrência, as demais foram ignoradas.",
                chave, arquivo,
            )
            continue
        definidas_neste_arquivo.add(chave)

        if chave not in os.environ:
            os.environ[chave] = valor


carregar_env()


# Evita logar o mesmo aviso de configuração inválida a cada chamada de uma
# property — algumas (como `bigquery_configurado`) são lidas a cada execução
# de pipeline.
_avisos_emitidos: set[str] = set()


def _avisar_uma_vez(chave: str, mensagem: str) -> None:
    if chave in _avisos_emitidos:
        return
    _avisos_emitidos.add(chave)
    logger.warning(mensagem)


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

# Nome da variável de ambiente que guarda a chave de cada provider de LLM.
# Adicionar um provider novo é adicionar uma linha aqui — nada mais.
ENV_VAR_POR_PROVIDER: dict[str, str] = {
    "groq":      "GROQ_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "ollama":    "OLLAMA_API_KEY",   # normalmente vazio — Ollama roda local
}


class Settings:
    """Acesso somente-leitura às configurações do ambiente."""

    # ── YouTube ──────────────────────────────────────────────────────────────

    @property
    def youtube_api_key(self) -> str | None:
        return os.getenv("YOUTUBE_API_KEY") or None

    @property
    def youtube_proxy_list_url(self) -> str | None:
        """
        URL de download de uma lista de proxies (o link de "Download" do
        painel da Webshare, por exemplo) — sempre devolve o plano atual, ao
        contrário de um arquivo estático. Tem prioridade sobre
        `youtube_proxy_list_path` quando ambos configurados. Usado tanto na
        coleta de transcrições quanto nas chamadas à YouTube Data API v3.
        """
        return os.getenv("YOUTUBE_PROXY_LIST_URL") or None

    @property
    def youtube_proxy_list_path(self) -> str | None:
        """
        Caminho de um CSV `host:porta:usuario:senha` (uma linha por proxy),
        salvo em disco — fallback de `youtube_proxy_list_url` (ex.: quando
        não há rede para baixar a lista atual). Opcional — sem nenhum dos
        dois, a coleta roda normalmente sem proxy.
        """
        return os.getenv("YOUTUBE_PROXY_LIST") or None

    @property
    def youtube_webshare_credenciais(self) -> tuple[str, str] | None:
        """
        "Proxy Username"/"Proxy Password" da conta Webshare
        (dashboard.webshare.io/proxy/settings) — usa o gateway rotativo
        nativo da biblioteca (`WebshareProxyConfig`), que já tem retentativa
        embutida. Requer o pacote "Residential"; "Proxy Server"/"Static
        Residential"/gratuito não funcionam de forma confiável para isto.
        Tem prioridade sobre `YOUTUBE_PROXY_LIST` quando ambos estão setados.
        """
        usuario = os.getenv("YOUTUBE_WEBSHARE_USERNAME")
        senha = os.getenv("YOUTUBE_WEBSHARE_PASSWORD")
        return (usuario, senha) if usuario and senha else None

    # ── LLM ──────────────────────────────────────────────────────────────────

    @property
    def llm_provider_padrao(self) -> str:
        # `or` (não um 2º argumento de getenv) — uma linha `LLM_PROVIDER=`
        # em branco no .env também deve cair no padrão, não virar "".
        return os.getenv("LLM_PROVIDER") or "gemini"

    @property
    def llm_model_padrao(self) -> str:
        return os.getenv("LLM_MODEL") or "gemini-3.5-flash-lite"

    def llm_api_key(self, provider: str) -> str | None:
        """Chave do provider informado, lida da variável de ambiente própria."""
        env_var = ENV_VAR_POR_PROVIDER.get(provider.lower())
        if not env_var:
            return None
        return os.getenv(env_var) or None

    def llm_base_url(self, provider: str) -> str | None:
        """Base URL sobrescrita por ambiente (ex.: OLLAMA_BASE_URL)."""
        return os.getenv(f"{provider.upper()}_BASE_URL") or None

    def provider_configurado(self, provider: str) -> bool:
        """
        True se o provider tem credencial disponível.
        Ollama é local e não exige chave — considera-se sempre configurável.
        """
        if provider.lower() == "ollama":
            return True
        return bool(self.llm_api_key(provider))

    # ── BigQuery (warehouse analítico do Dashboard) ─────────────────────────

    @property
    def bigquery_dataset(self) -> str:
        """Nome do dataset BigQuery onde as tabelas analíticas do YouTube
        (canais/transcrições/comentários/comentários-análise) são gravadas.
        Separado do dataset `analytics_ford` usado pelo notebook de marcas
        FIPE — são domínios de dado diferentes."""
        return os.getenv("BIGQUERY_DATASET", "youtube_analytics")

    @property
    def bigquery_project_id(self) -> str | None:
        """Projeto GCP explícito, se o ADC não bastar para resolver um. Sem
        isso, `bigquery.Client()` resolve o projeto pelas credenciais ativas
        (mesmo comportamento do notebook `tabela_marcas.ipynb`)."""
        return os.getenv("GOOGLE_CLOUD_PROJECT") or None

    @property
    def bigquery_configurado(self) -> bool:
        """
        True se `GOOGLE_APPLICATION_CREDENTIALS` aponta para um arquivo que
        realmente existe — nunca expõe o valor, só a presença (Regra de
        segurança da seção 10 do SPEC). Sem isso, o warehouse fica
        desabilitado e a gravação analítica é pulada (recurso opcional, mas
        agora com um aviso claro no log em vez de falhar em silêncio — já
        aconteceu de a variável apontar pra uma pasta em vez de um arquivo).
        """
        caminho = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not caminho:
            return False

        if not Path(caminho).is_file():
            _avisar_uma_vez(
                "bigquery_credenciais_invalidas",
                f"[config] GOOGLE_APPLICATION_CREDENTIALS='{caminho}' não é um "
                "arquivo existente (pasta ou caminho errado?) — o warehouse "
                "BigQuery do Dashboard fica desabilitado até isso ser corrigido.",
            )
            return False

        return True

    # ── Dashboard analítico — dados reais vs. ilustrativos ──────────────────

    @property
    def dashboard_dados_reais(self) -> bool:
        """
        Por padrão (False) o Dashboard (`/analytics`) preenche KPIs, gráficos,
        filtros e a comparação de marcas com um conjunto fixo de dados
        ilustrativos — funciona sem BigQuery configurado, pensado pra
        demonstração. Só passa a consultar o warehouse de verdade quando esta
        flag é explicitamente ligada.
        """
        return os.getenv("DASHBOARD_REAL", "false").strip().lower() in ("true", "1")

    # ── Redes Sociais (Reddit/X/Instagram — dados sintéticos + IA real) ─────

    @property
    def social_bigquery_dataset(self) -> str:
        """Dataset onde os posts sintéticos de redes sociais e sua análise de
        IA são gravados — separado dos demais (domínio de dado próprio, mesmo
        projeto GCP)."""
        return os.getenv("SOCIAL_BIGQUERY_DATASET", "social_ford")

    # ── Gov Mode (catálogo FIPE — mesmo projeto GCP, dataset separado) ──────

    @property
    def govmode_bigquery_dataset(self) -> str:
        """Dataset onde o catálogo FIPE (marcas/modelos/código FIPE) é
        gravado — o mesmo `analytics_ford` do notebook `tabela_marcas.ipynb`,
        separado do `bigquery_dataset` do YouTube (domínios de dado
        diferentes, mesmo projeto GCP)."""
        return os.getenv("GOVMODE_BIGQUERY_DATASET", "analytics_ford")

    @property
    def fipe_api_token(self) -> str | None:
        """Token da API da FIPE (parallelum.com.br), enviado como
        `X-Subscription-Token` — só é exigido pelas chamadas que resolvem o
        código FIPE de um modelo/ano; marcas e modelos são coletados sem ele."""
        return os.getenv("FIPE_API_TOKEN") or None

    # ── Diretórios ───────────────────────────────────────────────────────────

    @property
    def data_dir(self) -> Path:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return DATA_DIR

    @property
    def dags_dir(self) -> Path:
        DAGS_DIR.mkdir(parents=True, exist_ok=True)
        return DAGS_DIR

    @property
    def database_url(self) -> str:
        """SQLite local que guarda as tabelas (vídeos/canais/transcrições/
        comentários/insights) geradas por cada execução de pipeline."""
        caminho = self.data_dir / "ford_fiap.db"
        return f"sqlite:///{caminho.as_posix()}"

    # ── Scraper (auto-discovery de sites de carro) ──────────────────────────

    @property
    def scraper_bigquery_dataset(self) -> str:
        """Dataset onde ficha técnica/anúncios/comentários coletados por
        sites cadastrados no Scraper Mode são gravados — separado do
        `govmode_bigquery_dataset` (domínios de dado diferentes, mesmo
        projeto GCP)."""
        return os.getenv("SCRAPER_BIGQUERY_DATASET", "scraper_ford")

    @property
    def scraper_proxy_list_url(self) -> str | None:
        """Mesma ideia de `youtube_proxy_list_url`, mas pro Scraper Mode —
        URL de download de uma lista de proxies, prioridade sobre
        `scraper_proxy_list_path`."""
        return os.getenv("SCRAPER_PROXY_LIST_URL") or None

    @property
    def scraper_proxy_list_path(self) -> str | None:
        """Fallback de `scraper_proxy_list_url` — CSV local
        `host:porta:usuario:senha`, mesmo formato do YouTube."""
        return os.getenv("SCRAPER_PROXY_LIST") or None

    @property
    def scraper_playwright_headless(self) -> bool:
        return os.getenv("SCRAPER_PLAYWRIGHT_HEADLESS", "true").strip().lower() not in ("false", "0", "")

    @property
    def scraper_playwright_timeout_ms(self) -> int:
        try:
            return int(os.getenv("SCRAPER_PLAYWRIGHT_TIMEOUT_MS", "20000"))
        except ValueError:
            return 20000

    @property
    def scraper_discovery_max_html_chars(self) -> int:
        """Corte do HTML simplificado antes de mandar pro LLM — evita gastar
        tokens (e estourar limite de contexto) com página inteira."""
        try:
            return int(os.getenv("SCRAPER_DISCOVERY_MAX_HTML_CHARS", "40000"))
        except ValueError:
            return 40000


settings = Settings()
