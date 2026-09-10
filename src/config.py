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

    for linha in conteudo.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, _, valor = linha.partition("=")
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")

        if chave and chave not in os.environ:
            os.environ[chave] = valor


carregar_env()


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

    # ── LLM ──────────────────────────────────────────────────────────────────

    @property
    def llm_provider_padrao(self) -> str:
        return os.getenv("LLM_PROVIDER", "groq")

    @property
    def llm_model_padrao(self) -> str:
        return os.getenv("LLM_MODEL", "openai/gpt-oss-20b")

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


settings = Settings()
