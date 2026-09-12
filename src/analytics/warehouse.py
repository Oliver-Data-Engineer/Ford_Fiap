"""
warehouse.py
Cliente do warehouse analítico do YouTube — Google BigQuery, mesmo padrão do
notebook `tabela_marcas.ipynb` (client.get_table/NotFound→create_table,
load_table_from_dataframe, client.query(...).to_dataframe()), generalizado
por tabela via `src/analytics/schemas.py`.

Aditivo: não substitui `src/pipelines/db.py` (SQLite, usado pelo modo
"Tabela" do Playground) — este módulo alimenta especificamente a aba
Dashboard, com dados cross-execução.

Nunca deve derrubar a execução do Playground/pipeline: quem chama
(`src/pipelines/executor.py`) envolve as chamadas daqui em try/except só-log.
O cliente só é resolvido de fato na primeira gravação/consulta real — nunca
no import do módulo (mesmo cuidado que `YouTubeAPI`/`TranscriptAPI` já têm
com o proxy pool, docs/SPEC.md seção 8).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from src.analytics import schemas
from src.analytics.ids import transcript_id as gerar_transcript_id
from src.bigquery_warehouse import normalizar_tipos_timestamp as _normalizar_tipos
from src.config import settings
from src.pipelines.models import PipelineRun

logger = logging.getLogger(__name__)

_client: bigquery.Client | None = None
_tabelas_prontas: set[str] = set()

# Último erro por tabela — não a exceção crua (pode conter caminho de
# credencial), só uma mensagem segura + quando aconteceu. Por tabela (não
# uma mensagem única) porque uma tabela falhar não pode mais mascarar o
# diagnóstico de outra (ver `registrar_execucao`/`upsert`).
# `src/api/youtube_api.py::obter_config` expõe isto via `status()`, pra dar
# visibilidade sem precisar olhar o log do servidor (já aconteceu de uma
# falha de credencial ficar muda por sessões inteiras).
_ultimo_erro: dict[str, dict[str, str]] | None = None


def _agora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def status() -> dict[str, Any]:
    """Estado do warehouse para diagnóstico — nunca inclui a exceção crua
    nem o valor de `GOOGLE_APPLICATION_CREDENTIALS`. `ultimo_erro` é um mapa
    `{nome_da_tabela: {"quando", "mensagem"}}` — vazio/None quando todas as
    tabelas gravaram com sucesso na última tentativa de cada uma."""
    return {
        "configurado": settings.bigquery_configurado,
        "ultimo_erro": _ultimo_erro or None,
    }


def _obter_client() -> bigquery.Client:
    global _client
    if _client is None:
        kwargs: dict[str, Any] = {}
        if settings.bigquery_project_id:
            kwargs["project"] = settings.bigquery_project_id
        _client = bigquery.Client(**kwargs)
    return _client


def _dataset_id(client: bigquery.Client) -> str:
    return f"{client.project}.{settings.bigquery_dataset}"


def _table_id(client: bigquery.Client, nome: str) -> str:
    return f"{_dataset_id(client)}.{nome}"


def _preparar_tabela(client: bigquery.Client, nome: str) -> str:
    """Idempotente — cria dataset/tabela só na primeira vez que são usados."""
    table_id = _table_id(client, nome)
    if table_id in _tabelas_prontas:
        return table_id

    dataset_id = _dataset_id(client)
    try:
        client.get_dataset(dataset_id)
    except NotFound:
        client.create_dataset(bigquery.Dataset(dataset_id))
        logger.info("[analytics] dataset %s criado.", dataset_id)

    try:
        client.get_table(table_id)
    except NotFound:
        client.create_table(bigquery.Table(table_id, schema=schemas.TABELAS[nome]))
        logger.info("[analytics] tabela %s criada.", table_id)

    _tabelas_prontas.add(table_id)
    return table_id


def upsert(nome: str, linhas: list[dict]) -> None:
    """Grava `linhas` na tabela `nome` — append-only (cada busca soma
    linhas novas, nenhuma tabela do warehouse trunca).

    Quem chama (`registrar_execucao`) isola cada tabela em seu próprio
    try/except — uma falha aqui não pode mais impedir as demais tabelas de
    serem gravadas (ver `registrar_execucao`). Esta função só acrescenta o
    registro de diagnóstico em `_ultimo_erro[nome]` antes de relançar a
    exceção, para que `status()` consiga explicar o motivo por tabela.
    """
    global _ultimo_erro
    if not linhas:
        return

    try:
        client = _obter_client()
        table_id = _preparar_tabela(client, nome)

        schema = schemas.TABELAS[nome]
        df = _normalizar_tipos(pd.DataFrame(linhas), schema)
        job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_APPEND")
        job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
        job.result()
    except Exception as exc:
        if _ultimo_erro is None:
            _ultimo_erro = {}
        _ultimo_erro[nome] = {"quando": _agora_iso(), "mensagem": f"{type(exc).__name__}: {exc}"}
        raise

    if _ultimo_erro is not None:
        _ultimo_erro.pop(nome, None)


def query(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> pd.DataFrame:
    """
    `client.query(sql, job_config=...).to_dataframe()` — usado pelas leituras
    do Dashboard. `params` vira `@nome` na query (query parametrizada da API
    do BigQuery) — nunca interpolar filtro vindo do usuário direto na string
    SQL (injeção); nomes de tabela são constantes internas, esses sim podem
    entrar por f-string.
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params or [])
    return _obter_client().query(sql, job_config=job_config).to_dataframe()


def table_id(nome: str) -> str:
    """Nome totalmente qualificado (`projeto.dataset.tabela`) — usado para
    montar SQL de leitura fora deste módulo (ex.: `src/api/analytics_api.py`)."""
    return _table_id(_obter_client(), nome)


# ─────────────────────────────────────────────────────────────────────────────
# Gravação a partir de uma execução (Fase 2 + persistência do enriquecimento)
# ─────────────────────────────────────────────────────────────────────────────

def registrar_execucao(run: PipelineRun) -> None:
    """
    Espelha, no warehouse, os mesmos dados que `src/pipelines/db.py::
    salvar_tabelas_execucao` já grava no SQLite local — mas aqui é
    cross-execução (append-only), para alimentar a aba Dashboard. Também
    grava os structs do enriquecimento automático de comentários
    (`contexto["trust"]["enriquecimento_comentarios"]`, produzido pelo passo
    `_passo_enriquecimento_comentarios` do engine).

    Pulado silenciosamente se BigQuery não estiver configurado — é um
    recurso opcional, aditivo, nunca uma condição de falha.
    """
    if not settings.bigquery_configurado:
        return

    refined = run.refined or {}
    if not refined.get("videos"):
        return

    raw = run.raw or {}
    run_id = run.id
    agora = _agora_iso()

    canais = [
        {
            "canal_id": canal_id,
            "canal_nome": canal.get("nome"),
            "run_id": run_id,
            "inscritos": canal.get("inscritos"),
            "views_totais": canal.get("views_totais"),
            "videos_totais": canal.get("videos_totais"),
            "coletado_em": agora,
        }
        for canal_id, canal in (refined.get("channels") or {}).items()
    ]

    transcricoes = []
    transcript_ids: dict[str, str] = {}
    raw_transcripts = raw.get("transcripts") or {}
    for video_id, transcricao in (refined.get("transcripts") or {}).items():
        tid = gerar_transcript_id(run_id, video_id)
        transcript_ids[video_id] = tid

        bruto = raw_transcripts.get(video_id) or {}
        segmentos_brutos = bruto.get("segments") or []
        texto_bruto = "\n".join(
            seg.get("text", "") for seg in segmentos_brutos if seg.get("text")
        ) or None

        transcricoes.append({
            "transcript_id": tid,
            "run_id": run_id,
            "video_id": video_id,
            "idioma": transcricao.get("language"),
            "gerada_automaticamente": transcricao.get("gerada_automaticamente"),
            "palavras": transcricao.get("palavras"),
            "caracteres": transcricao.get("caracteres"),
            "texto_bruto": texto_bruto,
            "texto_refinado": transcricao.get("clean_text"),
            "coletado_em": agora,
        })

    comentarios = []
    for video_id, lista in (refined.get("comments") or {}).items():
        for comentario in lista:
            comentarios.append({
                "comment_id": comentario.get("comment_id"),
                "refined_record_id": comentario.get("refined_record_id"),
                "transcript_id": transcript_ids.get(video_id),
                "video_id": video_id,
                "run_id": run_id,
                "autor": comentario.get("autor"),
                "texto": comentario.get("texto"),
                "likes": comentario.get("likes"),
                "respostas": comentario.get("respostas"),
                "publicado_em": comentario.get("publicado_em"),
                "coletado_em": agora,
            })

    agente_info = run.agente or {}
    analises = []
    for item in (run.trust or {}).get("enriquecimento_comentarios") or []:
        if not item.get("success"):
            continue
        struct = item.get("struct") or {}
        analises.append({
            "comment_id": item.get("comment_id"),
            "video_id": item.get("video_id"),
            "transcript_id": transcript_ids.get(item.get("video_id")),
            "run_id": run_id,
            "prompt_id": item.get("prompt_id"),
            "prompt_version": item.get("prompt_version"),
            "provider": agente_info.get("provider"),
            "model": agente_info.get("model"),
            "success": True,
            "output_struct": {
                "sentimento": struct.get("sentimento"),
                "motivo": struct.get("motivo"),
                "adjetivos": struct.get("adjetivos") or [],
                "objeto": struct.get("objeto"),
            },
            "coletado_em": agora,
        })

    # analises_ia é diferente de comentarios_analise: cobre QUALQUER prompt
    # do usuário rodado via etapa `agent` (Percepção sobre o carro, análise
    # de transcrição, etc. — Regra 8, schema livre) — não só o enriquecimento
    # fixo de comentários. Espelha o que já existe em SQLite
    # (`db.py::InsightRow`), mas cross-execução no warehouse.
    analises_ia = []
    for analise in (run.trust or {}).get("analises") or []:
        source = analise.get("source") or {}
        video_id = source.get("video_id")  # None para escopo "dataset"
        erro = analise.get("error") or {}
        agente_analise = analise.get("agent") or {}
        saida = analise.get("output")

        analises_ia.append({
            "run_id": run_id,
            "video_id": video_id,
            "transcript_id": transcript_ids.get(video_id) if video_id else None,
            "escopo": analise.get("escopo"),
            "prompt_id": analise.get("prompt_id"),
            "prompt_name": analise.get("prompt_name"),
            "prompt_version": analise.get("prompt_version"),
            "input_type": analise.get("input_type"),
            "provider": agente_analise.get("provider"),
            "model": agente_analise.get("model"),
            "success": bool(analise.get("success")),
            "output_json": (
                json.dumps(saida, ensure_ascii=False, default=str) if saida is not None else None
            ),
            "error_type": erro.get("type"),
            "error_message": erro.get("message"),
            "coletado_em": agora,
        })

    # Cada tabela isolada em seu próprio try/except: uma falhar (schema,
    # permissão, rede) não pode mais impedir as outras de serem gravadas —
    # bug real já visto (canais gravava, o resto nunca era nem tentado
    # porque a exceção da tabela seguinte abortava a função inteira).
    for nome, linhas in (
        (schemas.CANAIS_BUSCADOS, canais),
        (schemas.TRANSCRICOES_COLETADAS, transcricoes),
        (schemas.COMENTARIOS_COLETADOS, comentarios),
        (schemas.COMENTARIOS_ANALISE, analises),
        (schemas.ANALISES_IA, analises_ia),
    ):
        if not linhas:
            continue
        try:
            upsert(nome, linhas)
        except Exception:
            logger.exception("[analytics] falha ao gravar '%s' do run %s", nome, run_id)

    logger.info("[analytics] warehouse atualizado para o run %s.", run_id)
