"""
analytics_api.py
Router FastAPI da aba Dashboard — leitura do warehouse analítico do YouTube
(BigQuery, `src/analytics/warehouse.py`). Só leitura: quem grava é
`src/pipelines/executor.py`, ao fim de cada execução (Regra 4/10 do SPEC).

Sem BigQuery configurado (`GOOGLE_APPLICATION_CREDENTIALS` ausente), todo
endpoint aqui devolve 503 — é um recurso opcional, aditivo (docs/SPEC.md
seção 12), nunca uma condição de falha do resto do app.

Todo filtro vindo do usuário entra como parâmetro de query do BigQuery
(`@nome`), nunca por f-string direto na SQL — evita injeção. Nomes de
tabela são constantes internas (`src/analytics/schemas.py`), esses sim
podem compor a string SQL.
"""
import logging

from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery

from src.analytics import warehouse
from src.analytics.schemas import CANAIS_BUSCADOS, COMENTARIOS_ANALISE
from src.bigquery_warehouse import _valor_serializavel, registros_serializaveis
from src.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _exigir_configurado() -> None:
    if not settings.bigquery_configurado:
        raise HTTPException(
            503,
            detail={"erro": "BigQuery não configurado no servidor (warehouse analítico indisponível)."},
        )


# `_valor_serializavel` importado de src.bigquery_warehouse — mantido
# acessível como `analytics_api._valor_serializavel` (mesmo nome de antes)
# porque tests/test_analytics_api.py chama direto; a lógica agora é
# compartilhada com src/api/gov_api.py, não duplicada.

def _executar(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> list[dict]:
    try:
        df = warehouse.query(sql, params)
    except Exception as exc:
        logger.exception("[analytics_api] falha ao consultar o warehouse")
        raise HTTPException(502, detail={"erro": f"Falha ao consultar o warehouse: {exc}"}) from exc

    return registros_serializaveis(df)


def _filtro_comum(canal_id: str | None, sentimento: str | None) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    condicoes = ["a.success"]
    params: list[bigquery.ScalarQueryParameter] = []

    if canal_id:
        condicoes.append("cn.canal_id = @canal_id")
        params.append(bigquery.ScalarQueryParameter("canal_id", "STRING", canal_id))
    if sentimento:
        condicoes.append("a.output_struct.sentimento = @sentimento")
        params.append(bigquery.ScalarQueryParameter("sentimento", "STRING", sentimento))

    return " AND ".join(condicoes), params


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/filtros
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/filtros")
async def obter_filtros():
    """Valores distintos para popular os dropdowns do Dashboard."""
    _exigir_configurado()

    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)

    canais = _executar(f"""
        SELECT DISTINCT canal_id, canal_nome
        FROM `{canais_tabela}`
        WHERE canal_nome IS NOT NULL
        ORDER BY canal_nome
        LIMIT 200
    """)

    sentimentos = _executar(f"""
        SELECT DISTINCT output_struct.sentimento AS sentimento
        FROM `{analise_tabela}`
        WHERE success AND output_struct.sentimento IS NOT NULL
        ORDER BY sentimento
    """)

    return {
        "canais": canais,
        "sentimentos": [r["sentimento"] for r in sentimentos],
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/resumo
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/resumo")
async def obter_resumo(canal_id: str | None = None, sentimento: str | None = None):
    """
    KPIs agregados: total de comentários enriquecidos e distribuição por
    sentimento/objeto, já filtrados por canal e/ou sentimento se informados.
    """
    _exigir_configurado()

    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)
    where, params = _filtro_comum(canal_id, sentimento)

    base_join = f"""
        FROM `{analise_tabela}` a
        LEFT JOIN `{canais_tabela}` cn ON cn.run_id = a.run_id
        WHERE {where}
    """

    total = _executar(f"SELECT COUNT(*) AS total {base_join}", params)
    total_videos = _executar(f"SELECT COUNT(DISTINCT a.video_id) AS total {base_join}", params)
    total_canais = _executar(f"SELECT COUNT(DISTINCT cn.canal_id) AS total {base_join}", params)
    por_sentimento = _executar(f"""
        SELECT a.output_struct.sentimento AS sentimento, COUNT(*) AS total
        {base_join}
        GROUP BY sentimento
        ORDER BY total DESC
    """, params)
    por_objeto = _executar(f"""
        SELECT a.output_struct.objeto AS objeto, COUNT(*) AS total
        {base_join}
        GROUP BY objeto
        ORDER BY total DESC
        LIMIT 15
    """, params)

    return {
        "total_comentarios_analisados": (total[0]["total"] if total else 0),
        "total_videos_analisados": (total_videos[0]["total"] if total_videos else 0),
        "total_canais": (total_canais[0]["total"] if total_canais else 0),
        "por_sentimento": por_sentimento,
        "por_objeto": por_objeto,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/comentarios
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/comentarios")
async def obter_comentarios(
    canal_id: str | None = None,
    sentimento: str | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Lista paginada de comentários já enriquecidos, filtrada — alimenta a
    tabela detalhada do Dashboard."""
    _exigir_configurado()

    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    comentarios_tabela = warehouse.table_id("comentarios_coletados")
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)
    where, params = _filtro_comum(canal_id, sentimento)

    # limite/offset já validados/coeridos a int pelo FastAPI (Query ge/le) —
    # seguros para compor a string diretamente, ao contrário de canal_id/
    # sentimento (texto livre do usuário, esses vão por parâmetro).
    linhas = _executar(f"""
        SELECT
            a.comment_id, a.video_id, a.run_id, a.transcript_id,
            cm.texto, cm.autor,
            cn.canal_nome,
            a.output_struct.sentimento AS sentimento,
            a.output_struct.motivo AS motivo,
            a.output_struct.adjetivos AS adjetivos,
            a.output_struct.objeto AS objeto,
            a.coletado_em
        FROM `{analise_tabela}` a
        LEFT JOIN `{comentarios_tabela}` cm
            ON cm.comment_id = a.comment_id AND cm.run_id = a.run_id
        LEFT JOIN `{canais_tabela}` cn ON cn.run_id = a.run_id
        WHERE {where}
        ORDER BY a.coletado_em DESC
        LIMIT {limite} OFFSET {offset}
    """, params)

    return {"comentarios": linhas, "limite": limite, "offset": offset}
