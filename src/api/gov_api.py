"""
gov_api.py
Router FastAPI do Gov Mode — pipelines nomeadas/versionadas do catálogo
FIPE (marcas/modelos/anos/código FIPE) e leitura do warehouse
(`src/govmode/warehouse.py`, dataset `analytics_ford`) para o mini
dashboard de consulta.

Endpoints:
    GET    /gov/pipelines
    POST   /gov/pipelines
    GET    /gov/runs/{run_id}                 status de uma execução
    GET    /gov/pipelines/{id}
    PUT    /gov/pipelines/{id}
    DELETE /gov/pipelines/{id}
    POST   /gov/pipelines/{id}/run
    POST   /gov/pipelines/{id}/status
    GET    /gov/pipelines/{id}/versions
    GET    /gov/pipelines/{id}/runs
    POST   /gov/pipelines/{id}/generate-dag
    GET    /gov/pipelines/{id}/dag

    GET    /gov/marcas                         catálogo — só leitura
    GET    /gov/modelos
    GET    /gov/fipe
    GET    /gov/resumo                         KPIs do mini dashboard

Atenção à ordem de declaração: `/runs/{run_id}` e as rotas fixas de
catálogo precisam vir antes de `/pipelines/{pipeline_id}`, mesmo cuidado
de `src/api/pipelines_api.py`.

Sem BigQuery configurado, os endpoints de catálogo devolvem 503 — mesmo
contrato de `src/api/analytics_api.py` (recurso opcional, aditivo).
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery
from pydantic import BaseModel

from src.bigquery_warehouse import registros_serializaveis
from src.config import settings
from src.govmode import schemas, warehouse
from src.govmode.dag_generator import descrever_schedule, gerar_codigo, gerar_dag, montar_cron, montar_dag_id
from src.govmode.executor import disparar_pipeline, obter_status
from src.govmode.models import GovPipelineEntrada, StatusPipeline
from src.govmode.store import (
    atualizar_pipeline,
    criar_pipeline,
    listar_pipelines,
    listar_runs,
    obter_pipeline,
    remover_pipeline,
    salvar_pipeline,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gov", tags=["gov"])


class StatusRequest(BaseModel):
    status: StatusPipeline


class ExecutarRequest(BaseModel):
    version: int | None = None


def _resumo(pipeline) -> dict:
    dados = pipeline.model_dump(mode="json")
    dados["schedule_legivel"] = descrever_schedule(pipeline.schedule)
    dados["cron"] = montar_cron(pipeline.schedule)
    dados["dag_id_sugerido"] = montar_dag_id(pipeline)
    return dados


def _exigir_configurado() -> None:
    if not settings.bigquery_configurado:
        raise HTTPException(
            503,
            detail={"erro": "BigQuery não configurado no servidor (catálogo FIPE indisponível)."},
        )


def _executar_catalogo(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> list[dict]:
    """Como `analytics_api._executar`, mas tolera tabela ainda inexistente
    (nenhuma pipeline rodou ainda) devolvendo lista vazia em vez de 502 —
    é um estado normal do catálogo recém-criado, não uma falha."""
    try:
        df = warehouse.query(sql, params)
    except Exception as exc:
        logger.info("[gov_api] consulta ao catálogo sem dado ainda (%s): %s", type(exc).__name__, exc)
        return []
    return registros_serializaveis(df)


# ─────────────────────────────────────────────────────────────────────────────
# Pipelines — coleção
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipelines")
async def listar():
    return {"pipelines": [_resumo(p) for p in listar_pipelines()]}


@router.post("/pipelines", status_code=201)
async def criar(body: GovPipelineEntrada):
    if not body.name.strip():
        raise HTTPException(400, detail={"erro": "O campo 'name' é obrigatório."})
    if not body.source.marcas_alvo:
        raise HTTPException(
            400,
            detail={"erro": "Informe ao menos uma marca-alvo (marcas_alvo) — rodar o catálogo "
                             "inteiro de uma vez não é suportado."},
        )

    pipeline = criar_pipeline(body)
    return _resumo(pipeline)


# ─────────────────────────────────────────────────────────────────────────────
# Execuções (rota fixa — precede /pipelines/{pipeline_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def status_execucao(run_id: str):
    """Estado de uma execução — inclui `progresso` (ex.: quantos modelos de
    quantos já tiveram o código FIPE resolvido), para a barra de progresso
    do painel enquanto a execução ainda está em andamento."""
    run = obter_status(run_id)
    if run is None:
        raise HTTPException(404, detail={"erro": f"Execução '{run_id}' não encontrada."})
    return run.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# Catálogo — só leitura (rotas fixas — também precedem /pipelines/{id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/marcas")
async def listar_marcas(tipo_veiculo: str = Query(default="carros")):
    """Marcas catalogadas, com a contagem de modelos já coletados de cada uma."""
    _exigir_configurado()

    marcas_tabela = warehouse.table_id(schemas.MARCAS_VEICULOS)
    modelos_tabela = warehouse.table_id(schemas.MODELOS_VEICULOS)

    linhas = _executar_catalogo(f"""
        SELECT m.marca_codigo, m.marca_nome,
               COUNT(DISTINCT mo.modelo_codigo) AS total_modelos
        FROM `{marcas_tabela}` m
        LEFT JOIN `{modelos_tabela}` mo
            ON mo.marca_codigo = m.marca_codigo AND mo.tipo_veiculo = m.tipo_veiculo
        WHERE m.tipo_veiculo = @tipo
        GROUP BY m.marca_codigo, m.marca_nome
        ORDER BY m.marca_nome
    """, [bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo)])

    return {"marcas": linhas}


@router.get("/modelos")
async def listar_modelos(marca_codigo: str, tipo_veiculo: str = Query(default="carros")):
    """Modelos de uma marca, com a contagem de anos já resolvidos no BigQuery."""
    _exigir_configurado()

    modelos_tabela = warehouse.table_id(schemas.MODELOS_VEICULOS)
    fipe_tabela = warehouse.table_id(schemas.MODELOS_COM_FIPE)

    linhas = _executar_catalogo(f"""
        SELECT mo.modelo_codigo, mo.modelo_nome,
               COUNT(DISTINCT f.ano_codigo) AS total_anos
        FROM `{modelos_tabela}` mo
        LEFT JOIN `{fipe_tabela}` f
            ON f.modelo_codigo = mo.modelo_codigo AND f.marca_codigo = mo.marca_codigo
            AND f.tipo_veiculo = mo.tipo_veiculo
        WHERE mo.marca_codigo = @marca AND mo.tipo_veiculo = @tipo
        GROUP BY mo.modelo_codigo, mo.modelo_nome
        ORDER BY mo.modelo_nome
    """, [
        bigquery.ScalarQueryParameter("marca", "STRING", marca_codigo),
        bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo),
    ])

    return {"modelos": linhas}


@router.get("/fipe")
async def listar_fipe(marca_codigo: str, modelo_codigo: str, tipo_veiculo: str = Query(default="carros")):
    """Todos os anos/códigos FIPE já resolvidos para um modelo."""
    _exigir_configurado()

    fipe_tabela = warehouse.table_id(schemas.MODELOS_COM_FIPE)

    linhas = _executar_catalogo(f"""
        SELECT ano_codigo, ano_modelo, codigo_fipe, atualizado_em
        FROM `{fipe_tabela}`
        WHERE marca_codigo = @marca AND modelo_codigo = @modelo AND tipo_veiculo = @tipo
        ORDER BY ano_codigo DESC
    """, [
        bigquery.ScalarQueryParameter("marca", "STRING", marca_codigo),
        bigquery.ScalarQueryParameter("modelo", "STRING", modelo_codigo),
        bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo),
    ])

    return {"anos": linhas}


@router.get("/resumo")
async def resumo(tipo_veiculo: str = Query(default="carros")):
    """KPIs do mini dashboard: total de marcas/modelos catalogados e
    cobertura de código FIPE (% de modelos com pelo menos um ano resolvido)."""
    _exigir_configurado()

    marcas_tabela = warehouse.table_id(schemas.MARCAS_VEICULOS)
    modelos_tabela = warehouse.table_id(schemas.MODELOS_VEICULOS)
    fipe_tabela = warehouse.table_id(schemas.MODELOS_COM_FIPE)
    param_tipo = [bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo)]

    total_marcas = _executar_catalogo(
        f"SELECT COUNT(DISTINCT marca_codigo) AS total FROM `{marcas_tabela}` WHERE tipo_veiculo = @tipo",
        param_tipo,
    )
    total_modelos = _executar_catalogo(
        f"SELECT COUNT(*) AS total FROM `{modelos_tabela}` WHERE tipo_veiculo = @tipo",
        param_tipo,
    )
    total_registros_fipe = _executar_catalogo(
        f"SELECT COUNT(*) AS total FROM `{fipe_tabela}` WHERE tipo_veiculo = @tipo",
        param_tipo,
    )
    modelos_com_fipe = _executar_catalogo(f"""
        SELECT COUNT(DISTINCT CONCAT(marca_codigo, '-', modelo_codigo)) AS total
        FROM `{fipe_tabela}` WHERE tipo_veiculo = @tipo
    """, param_tipo)

    qtd_marcas = total_marcas[0]["total"] if total_marcas else 0
    qtd_modelos = total_modelos[0]["total"] if total_modelos else 0
    qtd_modelos_com_fipe = modelos_com_fipe[0]["total"] if modelos_com_fipe else 0
    cobertura = round((qtd_modelos_com_fipe / qtd_modelos) * 100, 1) if qtd_modelos else 0.0

    return {
        "tipo_veiculo": tipo_veiculo,
        "total_marcas": qtd_marcas,
        "total_modelos": qtd_modelos,
        "total_registros_fipe": total_registros_fipe[0]["total"] if total_registros_fipe else 0,
        "modelos_com_fipe": qtd_modelos_com_fipe,
        "cobertura_fipe_pct": cobertura,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Pipelines — item
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipelines/{pipeline_id}")
async def obter(pipeline_id: str):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})
    return _resumo(pipeline)


@router.put("/pipelines/{pipeline_id}")
async def atualizar(pipeline_id: str, body: GovPipelineEntrada):
    """Alterar source/steps/schedule cria uma versão nova da pipeline."""
    if not body.source.marcas_alvo:
        raise HTTPException(400, detail={"erro": "Informe ao menos uma marca-alvo (marcas_alvo)."})

    pipeline = atualizar_pipeline(pipeline_id, body)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})
    return _resumo(pipeline)


@router.delete("/pipelines/{pipeline_id}")
async def remover(pipeline_id: str):
    if not remover_pipeline(pipeline_id):
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})
    return {"removida": pipeline_id}


@router.post("/pipelines/{pipeline_id}/status")
async def alterar_status(pipeline_id: str, body: StatusRequest):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})

    pipeline.status = body.status
    return _resumo(salvar_pipeline(pipeline))


@router.get("/pipelines/{pipeline_id}/versions")
async def listar_versoes(pipeline_id: str):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})

    return {
        "pipeline_id": pipeline.id,
        "version": pipeline.version,
        "versions": [v.model_dump(mode="json") for v in pipeline.versions],
    }


@router.post("/pipelines/{pipeline_id}/run")
async def executar(pipeline_id: str, body: ExecutarRequest | None = None):
    """Dispara a pipeline manualmente, em background — mesmo padrão de
    `POST /pipelines/{id}/run` do YouTube."""
    try:
        run = disparar_pipeline(pipeline_id, version=(body.version if body else None), origem="manual")
    except ValueError as exc:
        raise HTTPException(404, detail={"erro": str(exc)}) from exc

    return {
        "run_id": run.id,
        "status": run.status.value,
        "steps": [{"type": s.type.value, "name": s.name, "status": s.status.value} for s in run.steps],
    }


@router.get("/pipelines/{pipeline_id}/runs")
async def historico(pipeline_id: str, limite: int = Query(default=20, ge=1, le=100)):
    runs = listar_runs(pipeline_id, limite)

    return {
        "pipeline_id": pipeline_id,
        "runs": [
            {
                "id": r.id,
                "status": r.status.value,
                "origem": r.origem,
                "pipeline_version": r.pipeline_version,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "progresso": r.progresso,
                "steps": [
                    {"type": s.type.value, "status": s.status.value, "resumo": s.resumo}
                    for s in r.steps
                ],
            }
            for r in runs
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# DAG
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/pipelines/{pipeline_id}/generate-dag")
async def gerar(pipeline_id: str):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})

    try:
        resultado = gerar_dag(pipeline)
    except OSError as exc:
        raise HTTPException(500, detail={"erro": f"Não foi possível gravar a DAG: {exc}"}) from exc

    pipeline.dag_id = resultado["dag_id"]
    pipeline.dag_path = resultado["caminho_relativo"]
    pipeline.dag_version = pipeline.version
    salvar_pipeline(pipeline)

    return {
        "dag_id": resultado["dag_id"],
        "caminho": resultado["caminho_relativo"],
        "dag_version": pipeline.version,
        "schedule": montar_cron(pipeline.schedule),
        "schedule_legivel": descrever_schedule(pipeline.schedule),
        "codigo": resultado["codigo"],
    }


@router.get("/pipelines/{pipeline_id}/dag")
async def obter_dag(pipeline_id: str):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline Gov '{pipeline_id}' não encontrada."})

    dag_id = pipeline.dag_id or montar_dag_id(pipeline)
    arquivo = settings.dags_dir / f"{dag_id}.py"

    if arquivo.is_file():
        codigo = arquivo.read_text(encoding="utf-8")
        gravada = True
        versao = pipeline.dag_version
    else:
        codigo = gerar_codigo(pipeline)
        gravada = False
        versao = None

    return {
        "dag_id": dag_id,
        "caminho": f"airflow/dags/{dag_id}.py",
        "gravada": gravada,
        "dag_version": versao,
        "pipeline_version": pipeline.version,
        "desatualizada": gravada and versao != pipeline.version,
        "schedule": montar_cron(pipeline.schedule),
        "schedule_legivel": descrever_schedule(pipeline.schedule),
        "codigo": codigo,
    }
