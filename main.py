"""
main.py
Ponto de entrada da API FastAPI.

Inicia com:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Endpoints disponíveis (prefixo /specs):
    GET  /specs/fabricantes
    GET  /specs/modelos?fabricante=
    GET  /specs/versoes?fabricante=&modelo=
    POST /specs/executar

Documentação interativa:
    http://localhost:8000/docs
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.specs_api import router as specs_router

# ── Configuração básica de logging ────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Aplicação ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Ford FIAP — API de Especificações de Veículos",
    description=(
        "Busca especificações técnicas da concorrência a partir de "
        "múltiplas fontes (carrosnaweb.com.br, icarros.com.br) e devolve "
        "uma lista padronizada de atributos/valores."
    ),
    version="1.0.0",
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


# ── Execução direta ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
