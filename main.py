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

Documentação interativa:
    http://localhost:8000/docs
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


# ── Execução direta ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
