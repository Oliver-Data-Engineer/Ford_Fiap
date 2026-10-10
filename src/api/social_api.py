"""
social_api.py
Router FastAPI da aba Redes Sociais — domínio paralelo ao YouTube/Gov Mode/
Scraper Mode (docs/SPEC.md §13.2/14.2: sistemas independentes, nunca
unificados). Não existe integração real com Reddit/X/Instagram: o Playground
gera posts sintéticos e roda uma classificação de sentimento de verdade via
LLM sobre eles (`src/social/engine.py`); os endpoints de leitura abaixo
consultam o warehouse próprio (`src/social/warehouse.py`, dataset
`settings.social_bigquery_dataset`) — sem nenhuma execução ainda, devolvem
listas vazias, nunca erro (aba nova, warehouse pode estar sem nenhum dado).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from pydantic import BaseModel, Field

from src.analytics.indicadores import _com_indicadores, linha_agregada_vazia
from src.bigquery_warehouse import registros_serializaveis
from src.config import settings
from src.pipelines.models import ConfigLLM
from src.social import executor, schemas, warehouse
from src.social.models import REDES, ConfigColetaSocial

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social", tags=["social"])


# ─────────────────────────────────────────────────────────────────────────────
# GET /social/config
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config")
async def obter_config():
    provider = settings.llm_provider_padrao
    return {
        "llm": {
            "provider_padrao": provider,
            "model_padrao": settings.llm_model_padrao,
            "configurado": settings.provider_configurado(provider),
        },
        "warehouse": warehouse.status(),
        "redes_suportadas": list(REDES),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /social/playground/run
# ─────────────────────────────────────────────────────────────────────────────

class ExecutarRequest(BaseModel):
    termo_busca: str = ""
    redes: list[str] = Field(default_factory=lambda: list(REDES))
    quantidade: int = 40
    provider: str | None = None
    model: str | None = None


@router.post("/playground/run")
async def executar_playground(body: ExecutarRequest):
    redes = [r for r in body.redes if r in REDES] or list(REDES)
    quantidade = max(1, min(body.quantidade, 200))

    config = ConfigColetaSocial(termo_busca=body.termo_busca.strip(), redes=redes, quantidade=quantidade)
    llm = ConfigLLM(provider=body.provider or settings.llm_provider_padrao, model=body.model)

    run = executor.iniciar_em_background(config, llm)
    return {
        "run_id": run.id,
        "status": run.status.value,
        "steps": [{"type": s.type.value, "name": s.name, "status": s.status.value} for s in run.steps],
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /social/runs/{run_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def obter_run(run_id: str, incluir_dados: bool = True):
    run = executor.obter_status(run_id)
    if run is None:
        raise HTTPException(404, detail={"erro": f"Execução '{run_id}' não encontrada."})

    resposta = {
        "id": run.id,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "usage": run.usage,
        "config": run.config.model_dump(),
        "steps": [
            {
                "type": s.type.value, "name": s.name, "status": s.status.value,
                "resumo": s.resumo, "error": s.error.model_dump() if s.error else None,
            }
            for s in run.steps
        ],
    }
    if incluir_dados:
        resposta["raw"] = run.raw
        resposta["refined"] = run.refined
        resposta["trust"] = run.trust
    return resposta


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do warehouse — mesmo padrão de src/api/analytics_api.py
# ─────────────────────────────────────────────────────────────────────────────

def _executar(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> list[dict]:
    """Igual a `analytics_api._executar`, mas nunca devolve erro: sem
    BigQuery configurado, ou sem nenhuma execução ainda (tabela sequer
    existe), devolve lista vazia — a aba Redes Sociais é nova, é normal não
    ter dado nenhum ainda, e o Playground (que não depende do warehouse)
    continua funcionando de qualquer forma."""
    if not settings.bigquery_configurado:
        return []
    try:
        df = warehouse.query(sql, params)
    except NotFound:
        return []
    except Exception as exc:
        logger.exception("[social_api] falha ao consultar o warehouse")
        raise HTTPException(502, detail={"erro": f"Falha ao consultar o warehouse: {exc}"}) from exc

    return registros_serializaveis(df)


def _filtro_comum(
    rede: str | None, sentimento: str | None,
    marca: str | None = None, modelo: str | None = None,
) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    condicoes = ["a.success"]
    params: list[bigquery.ScalarQueryParameter] = []

    if rede:
        condicoes.append("a.rede = @rede")
        params.append(bigquery.ScalarQueryParameter("rede", "STRING", rede))
    if sentimento:
        condicoes.append("a.output_struct.sentimento = @sentimento")
        params.append(bigquery.ScalarQueryParameter("sentimento", "STRING", sentimento))
    # marca/modelo: mesma busca textual best-effort de `analytics_api._filtro_comum`
    # — não é join de identidade (docs/SPEC.md §13.7/14.2).
    if marca:
        condicoes.append("(LOWER(p.texto) LIKE @marca_like OR LOWER(a.output_struct.objeto) LIKE @marca_like)")
        params.append(bigquery.ScalarQueryParameter("marca_like", "STRING", f"%{marca.lower()}%"))
    if modelo:
        condicoes.append("(LOWER(p.texto) LIKE @modelo_like OR LOWER(a.output_struct.objeto) LIKE @modelo_like)")
        params.append(bigquery.ScalarQueryParameter("modelo_like", "STRING", f"%{modelo.lower()}%"))

    return " AND ".join(condicoes), params


def _base_join(where: str) -> str:
    analise_tabela = warehouse.table_id(schemas.POSTS_ANALISE)
    posts_tabela = warehouse.table_id(schemas.POSTS_SOCIAIS)
    return f"""
        FROM `{analise_tabela}` a
        LEFT JOIN `{posts_tabela}` p ON p.post_id = a.post_id AND p.run_id = a.run_id
        WHERE {where}
    """


_AGG_SENTIMENTO = """
        SUM(CASE WHEN a.output_struct.sentimento = 'positivo' THEN 1 ELSE 0 END) AS positivos,
        SUM(CASE WHEN a.output_struct.sentimento = 'negativo' THEN 1 ELSE 0 END) AS negativos,
        SUM(CASE WHEN a.output_struct.sentimento = 'neutro' THEN 1 ELSE 0 END) AS neutros,
        SUM(CASE WHEN a.output_struct.sentimento = 'misto' THEN 1 ELSE 0 END) AS mistos,
        COUNT(*) AS total
"""


@router.get("/filtros")
async def obter_filtros():
    if not settings.bigquery_configurado:
        return {"redes": list(REDES), "sentimentos": []}

    analise_tabela = warehouse.table_id(schemas.POSTS_ANALISE)
    redes = _executar(f"""
        SELECT DISTINCT rede FROM `{analise_tabela}`
        WHERE rede IS NOT NULL ORDER BY rede
    """)
    sentimentos = _executar(f"""
        SELECT DISTINCT output_struct.sentimento AS sentimento FROM `{analise_tabela}`
        WHERE success AND output_struct.sentimento IS NOT NULL ORDER BY sentimento
    """)
    return {
        "redes": [r["rede"] for r in redes] or list(REDES),
        "sentimentos": [r["sentimento"] for r in sentimentos],
    }


@router.get("/resumo")
async def obter_resumo(
    rede: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
):
    if not settings.bigquery_configurado:
        return {"total_posts_analisados": 0, "por_sentimento": [], "por_objeto": [], "por_rede": []}

    where, params = _filtro_comum(rede, sentimento, marca, modelo)
    base_join = _base_join(where)

    total = _executar(f"SELECT COUNT(*) AS total {base_join}", params)
    por_sentimento = _executar(f"""
        SELECT a.output_struct.sentimento AS sentimento, COUNT(*) AS total
        {base_join} GROUP BY sentimento ORDER BY total DESC
    """, params)
    por_objeto = _executar(f"""
        SELECT a.output_struct.objeto AS objeto, COUNT(*) AS total
        {base_join} GROUP BY objeto ORDER BY total DESC LIMIT 15
    """, params)
    por_rede = _executar(f"""
        SELECT a.rede AS rede, COUNT(*) AS total
        {base_join} GROUP BY rede ORDER BY total DESC
    """, params)

    return {
        "total_posts_analisados": (total[0]["total"] if total else 0),
        "por_sentimento": por_sentimento,
        "por_objeto": por_objeto,
        "por_rede": por_rede,
    }


@router.get("/posts")
async def obter_posts(
    rede: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    if not settings.bigquery_configurado:
        return {"posts": [], "limite": limite, "offset": offset}

    where, params = _filtro_comum(rede, sentimento, marca, modelo)
    analise_tabela = warehouse.table_id(schemas.POSTS_ANALISE)
    posts_tabela = warehouse.table_id(schemas.POSTS_SOCIAIS)

    linhas = _executar(f"""
        SELECT
            a.post_id, a.run_id, a.rede,
            p.texto, p.autor, p.curtidas,
            a.output_struct.sentimento AS sentimento,
            a.output_struct.motivo AS motivo,
            a.output_struct.adjetivos AS adjetivos,
            a.output_struct.objeto AS objeto,
            a.coletado_em
        FROM `{analise_tabela}` a
        LEFT JOIN `{posts_tabela}` p ON p.post_id = a.post_id AND p.run_id = a.run_id
        WHERE {where}
        ORDER BY a.coletado_em DESC
        LIMIT {limite} OFFSET {offset}
    """, params)

    return {"posts": linhas, "limite": limite, "offset": offset}


@router.get("/satisfacao")
async def obter_satisfacao(
    rede: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
):
    if not settings.bigquery_configurado:
        return {
            "geral": _com_indicadores(linha_agregada_vazia()),
            "por_objeto": [], "por_rede": [], "tendencia": [], "engajamento_por_sentimento": [],
        }

    where, params = _filtro_comum(rede, sentimento, marca, modelo)
    base_join = _base_join(where)

    geral = _executar(f"SELECT {_AGG_SENTIMENTO} {base_join}", params)
    geral_linha = _com_indicadores(geral[0] if geral else linha_agregada_vazia())

    por_objeto = _executar(f"""
        SELECT a.output_struct.objeto AS objeto, {_AGG_SENTIMENTO}
        {base_join} GROUP BY objeto ORDER BY total DESC LIMIT 12
    """, params)
    por_rede = _executar(f"""
        SELECT a.rede AS rede, {_AGG_SENTIMENTO}
        {base_join} GROUP BY rede ORDER BY total DESC
    """, params)
    tendencia = _executar(f"""
        SELECT CAST(DATE(a.coletado_em) AS STRING) AS dia, {_AGG_SENTIMENTO}
        {base_join} GROUP BY dia ORDER BY dia
    """, params)
    engajamento = _executar(f"""
        SELECT a.output_struct.sentimento AS sentimento, AVG(p.curtidas) AS curtidas_medias, COUNT(*) AS total
        {base_join} GROUP BY sentimento ORDER BY sentimento
    """, params)

    return {
        "geral": geral_linha,
        "por_objeto": [_com_indicadores(r) for r in por_objeto],
        "por_rede": [_com_indicadores(r) for r in por_rede],
        "tendencia": [_com_indicadores(r) for r in tendencia],
        "engajamento_por_sentimento": engajamento,
    }


@router.get("/comparativo-marcas")
async def obter_comparativo_marcas(marca_concorrente: str = "Chevrolet"):
    """Ford vs. marca concorrente escolhida, a partir do mesmo sinal de
    sentimento dos posts sintéticos já classificados pela IA — sempre lê o
    warehouse de verdade (não existe modo ilustrativo aqui: a comparação já
    nasce de uma classificação real de IA, só o texto de entrada é sintético)."""

    def indicadores(marca: str) -> dict:
        if not settings.bigquery_configurado:
            return {"marca": marca, "nps": None, "pct_positivo": None, "engajamento_medio": None, "mencoes": 0}

        where, params = _filtro_comum(None, None, marca=marca)
        base_join = _base_join(where)
        agregado = _executar(f"SELECT {_AGG_SENTIMENTO} {base_join}", params)
        linha = _com_indicadores(agregado[0] if agregado else linha_agregada_vazia())
        engajamento = _executar(f"SELECT AVG(p.curtidas) AS curtidas_medias {base_join}", params)
        curtidas_medias = engajamento[0]["curtidas_medias"] if engajamento else None

        total = linha["total"] or 0
        return {
            "marca": marca,
            "nps": linha["nps"],
            "pct_positivo": round(linha["positivos"] / total * 100, 1) if total else None,
            "engajamento_medio": round(curtidas_medias, 1) if curtidas_medias is not None else None,
            "mencoes": total,
        }

    return {"ford": indicadores("Ford"), "concorrente": indicadores(marca_concorrente)}
