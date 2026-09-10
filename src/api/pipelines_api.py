"""
pipelines_api.py
Router FastAPI de pipelines, execuções e DAGs.

Endpoints:
    GET    /pipelines
    POST   /pipelines
    GET    /pipelines/runs/{run_id}          status de uma execução
    GET    /pipelines/{id}
    PUT    /pipelines/{id}
    DELETE /pipelines/{id}
    POST   /pipelines/{id}/run
    POST   /pipelines/{id}/status
    GET    /pipelines/{id}/versions
    GET    /pipelines/{id}/runs
    POST   /pipelines/{id}/generate-dag
    GET    /pipelines/{id}/dag

Atenção à ordem de declaração: `/runs/{run_id}` precisa vir antes de
`/{pipeline_id}`, senão o FastAPI trata "runs" como um id de pipeline.
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.airflow_gen import descrever_schedule, gerar_dag, montar_cron, montar_dag_id
from src.config import settings
from src.pipelines import db
from src.pipelines.executor import disparar_pipeline, obter_status
from src.pipelines.models import PipelineEntrada, StatusPipeline
from src.pipelines.store import (
    atualizar_pipeline,
    criar_pipeline,
    listar_pipelines,
    listar_runs,
    obter_pipeline,
    remover_pipeline,
    salvar_pipeline,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class StatusRequest(BaseModel):
    status: StatusPipeline


class ExecutarRequest(BaseModel):
    version: int | None = None


def _validar_fonte(entrada: PipelineEntrada) -> None:
    """Contrato de FonteYouTube.fonte_fixa (Regra: 'tudo precisa ser rastreável')."""
    fonte = entrada.source
    if fonte.fonte_fixa and not fonte.video_ids:
        raise HTTPException(
            400,
            detail={"erro": "Fonte fixa requer ao menos um vídeo selecionado (video_ids)."},
        )
    if not fonte.fonte_fixa and not fonte.query.strip():
        raise HTTPException(
            400,
            detail={"erro": "Fonte dinâmica requer um termo de busca (query)."},
        )


def _resumo(pipeline) -> dict:
    """Linha da tabela da aba Pipelines — agenda já traduzida para leitura."""
    dados = pipeline.model_dump(mode="json")
    dados["schedule_legivel"] = descrever_schedule(pipeline.schedule)
    dados["cron"] = montar_cron(pipeline.schedule)
    dados["dag_id_sugerido"] = montar_dag_id(pipeline)
    return dados


# ─────────────────────────────────────────────────────────────────────────────
# Coleção
# ─────────────────────────────────────────────────────────────────────────────

@router.get("")
async def listar():
    return {"pipelines": [_resumo(p) for p in listar_pipelines()]}


@router.post("", status_code=201)
async def criar(body: PipelineEntrada):
    """
    Persiste a configuração atual como pipeline — é o botão "Gerar Pipeline"
    do Playground.
    """
    if not body.name.strip():
        raise HTTPException(400, detail={"erro": "O campo 'name' é obrigatório."})
    _validar_fonte(body)

    pipeline = criar_pipeline(body)
    return _resumo(pipeline)


# ─────────────────────────────────────────────────────────────────────────────
# Execuções (rota fixa — precede /{pipeline_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def status_execucao(run_id: str, incluir_dados: bool = Query(default=True)):
    """
    Estado de uma execução — é o endpoint que o Playground consulta em loop.

    `incluir_dados=false` devolve só a régua de etapas, para polling barato
    enquanto a execução ainda está no meio.
    """
    run = obter_status(run_id)
    if run is None:
        raise HTTPException(404, detail={"erro": f"Execução '{run_id}' não encontrada."})

    dados = run.model_dump(mode="json")
    if not incluir_dados:
        for camada in ("raw", "refined", "trust"):
            dados.pop(camada, None)

    return dados


@router.get("/runs/{run_id}/tables")
async def tabelas_execucao(run_id: str):
    """
    As tabelas persistidas de uma execução (vídeos, canais, transcrições,
    comentários, insights) — o que alimenta o modo "Tabela" do Playground.

    404 se a execução nunca existiu; listas vazias se existiu mas não gerou
    dados (ex.: falhou antes do REFINED).
    """
    if obter_status(run_id) is None:
        raise HTTPException(404, detail={"erro": f"Execução '{run_id}' não encontrada."})

    return db.tabelas_da_execucao(run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Item
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{pipeline_id}")
async def obter(pipeline_id: str):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})
    return _resumo(pipeline)


@router.put("/{pipeline_id}")
async def atualizar(pipeline_id: str, body: PipelineEntrada):
    """Alterar source/steps/llm/schedule cria uma versão nova da pipeline."""
    _validar_fonte(body)
    pipeline = atualizar_pipeline(pipeline_id, body)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})
    return _resumo(pipeline)


@router.delete("/{pipeline_id}")
async def remover(pipeline_id: str):
    if not remover_pipeline(pipeline_id):
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})
    return {"removida": pipeline_id}


@router.post("/{pipeline_id}/status")
async def alterar_status(pipeline_id: str, body: StatusRequest):
    """Ativa ou pausa a pipeline (a DAG correspondente deve ser pausada no Airflow)."""
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})

    pipeline.status = body.status
    return _resumo(salvar_pipeline(pipeline))


@router.get("/{pipeline_id}/versions")
async def listar_versoes(pipeline_id: str):
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})

    return {
        "pipeline_id": pipeline.id,
        "version": pipeline.version,
        "versions": [v.model_dump(mode="json") for v in pipeline.versions],
    }


@router.post("/{pipeline_id}/run")
async def executar(pipeline_id: str, body: ExecutarRequest | None = None):
    """
    Dispara a pipeline manualmente, em background.

    Roda o mesmo engine que o Airflow chamaria — só muda a origem registrada.
    """
    try:
        run = disparar_pipeline(
            pipeline_id,
            version=(body.version if body else None),
            origem="manual",
        )
    except ValueError as exc:
        raise HTTPException(404, detail={"erro": str(exc)}) from exc

    return {
        "run_id": run.id,
        "status": run.status.value,
        "steps": [{"type": s.type.value, "name": s.name, "status": s.status.value}
                  for s in run.steps],
    }


@router.get("/{pipeline_id}/runs")
async def historico(pipeline_id: str, limite: int = Query(default=20, ge=1, le=100)):
    """Histórico de execuções — sem as camadas de dado, só o desfecho."""
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
                "total_videos": r.refined.get("estatisticas", {}).get("total_videos", 0),
                "total_insights": len(r.trust.get("insights", [])),
                "usage": r.usage,
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

@router.post("/{pipeline_id}/generate-dag")
async def gerar(pipeline_id: str):
    """
    Gera o arquivo da DAG em `airflow/dags/` e registra a versão gerada.

    O código produzido só orquestra: a lógica continua em `src.pipelines`.
    """
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})

    try:
        resultado = gerar_dag(pipeline)
    except OSError as exc:
        raise HTTPException(
            500,
            detail={"erro": f"Não foi possível gravar a DAG: {exc}"},
        ) from exc

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


@router.get("/{pipeline_id}/dag")
async def obter_dag(pipeline_id: str):
    """
    Código da DAG para o dropdown "Código da DAG".

    Devolve o arquivo em disco quando ele existe; caso contrário, uma prévia
    gerada na hora, sinalizada por `gravada: false`.
    """
    pipeline = obter_pipeline(pipeline_id)
    if pipeline is None:
        raise HTTPException(404, detail={"erro": f"Pipeline '{pipeline_id}' não encontrada."})

    from src.airflow_gen import gerar_codigo

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
