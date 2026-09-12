"""
main.py
Ponto de entrada da API FastAPI.

Inicia com:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Endpoints disponíveis:
    /specs/*      especificações técnicas de veículos
    /youtube/*    busca, coleta e Playground de pipelines do YouTube
    /prompts/*    prompts de IA versionados
    /pipelines/*  pipelines, execuções e DAGs do Airflow
    /analytics/*  leitura do warehouse analítico (Dashboard) — BigQuery
    /gov/*        catálogo FIPE (marca/modelo/ano) e suas pipelines — BigQuery

Documentação interativa:
    http://localhost:8000/docs
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.analytics_api import router as analytics_router
from src.api.gov_api import router as gov_router
from src.api.pipelines_api import router as pipelines_router
from src.api.prompts_api import router as prompts_router
from src.api.specs_api import router as specs_router
from src.api.youtube_api import router as youtube_router

# ── Configuração básica de logging ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Aplicação ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ford FIAP — API de Inteligência de Produto",
    description=(
        "Especificações técnicas da concorrência (carrosnaweb, icarros) e "
        "pipelines de análise de conteúdo do YouTube — coleta, tratamento "
        "(RAW/REFINED), interpretação por LLM (TRUST) e geração de DAGs."
    ),
    version="2.0.0",
)

# ── CORS — permite que o Flask (porta 5000) e o browser consumam a API ────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # em produção: restrinja para a origem do Flask
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(specs_router)
app.include_router(youtube_router)
app.include_router(prompts_router)
app.include_router(pipelines_router)
app.include_router(analytics_router)
app.include_router(gov_router)


# ── Execução direta ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    # Sem escopo, `reload=True` observa TODO o diretório do projeto (fallback
    # do uvicorn quando reload_dirs/includes não são passados) — inclui
    # `venv/` (milhares de arquivos), `data/*.json`/`*.db` (que o próprio app
    # reescreve a cada execução do Playground!) e qualquer edição de `.env`/
    # `*.ipynb`. Qualquer uma dessas mudanças mata o worker no meio de uma
    # requisição em andamento, que o Flask então recebe truncada/vazia e
    # devolve como "Resposta inválida da API." (502) — não é bug de lógica,
    # é o reload derrubando a conexão. Restringir a só `.py` fora dessas
    # pastas evita esse 502 intermitente.
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_includes=["*.py"],
        reload_excludes=["venv/*", "data/*", "config/*", "airflow/dags/*"],
    )
