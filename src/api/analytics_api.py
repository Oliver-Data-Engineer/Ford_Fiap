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

from src.analytics import dados_ficticios, warehouse
from src.analytics.indicadores import _com_indicadores, linha_agregada_vazia
from src.analytics.schemas import CANAIS_BUSCADOS, COMENTARIOS_ANALISE, COMENTARIOS_COLETADOS
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


def _filtro_comum(
    canal_id: str | None, sentimento: str | None,
    marca: str | None = None, modelo: str | None = None,
) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    condicoes = ["a.success"]
    params: list[bigquery.ScalarQueryParameter] = []

    if canal_id:
        condicoes.append("cn.canal_id = @canal_id")
        params.append(bigquery.ScalarQueryParameter("canal_id", "STRING", canal_id))
    if sentimento:
        condicoes.append("a.output_struct.sentimento = @sentimento")
        params.append(bigquery.ScalarQueryParameter("sentimento", "STRING", sentimento))
    # marca/modelo: best-effort, NÃO é join de identidade (docs/SPEC.md
    # §13.7 — unificar FIPE/scraper/YouTube por nome geraria falso
    # positivo). Não existe coluna de carro no warehouse do YouTube — é
    # uma busca textual literal no comentário e no tópico já inferido pela
    # IA (`objeto`), esperado não achar nada na maioria dos carros.
    if marca:
        condicoes.append("(LOWER(cm.texto) LIKE @marca_like OR LOWER(a.output_struct.objeto) LIKE @marca_like)")
        params.append(bigquery.ScalarQueryParameter("marca_like", "STRING", f"%{marca.lower()}%"))
    if modelo:
        condicoes.append("(LOWER(cm.texto) LIKE @modelo_like OR LOWER(a.output_struct.objeto) LIKE @modelo_like)")
        params.append(bigquery.ScalarQueryParameter("modelo_like", "STRING", f"%{modelo.lower()}%"))

    return " AND ".join(condicoes), params


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/filtros
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/filtros")
async def obter_filtros():
    """Valores distintos para popular os dropdowns do Dashboard."""
    if not settings.dashboard_dados_reais:
        return {**dados_ficticios.filtros_ficticios(), "dados_reais": False}

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
        "dados_reais": True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/resumo
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/resumo")
async def obter_resumo(
    canal_id: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
):
    """
    KPIs agregados: total de comentários enriquecidos e distribuição por
    sentimento/objeto, já filtrados por canal e/ou sentimento se informados.
    `marca`/`modelo` (opcionais): busca textual best-effort, ver `_filtro_comum`.
    """
    if not settings.dashboard_dados_reais:
        return dados_ficticios.resumo_ficticio(canal_id, sentimento, marca, modelo)

    _exigir_configurado()

    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)
    comentarios_tabela = warehouse.table_id(COMENTARIOS_COLETADOS)
    where, params = _filtro_comum(canal_id, sentimento, marca, modelo)

    base_join = f"""
        FROM `{analise_tabela}` a
        LEFT JOIN `{canais_tabela}` cn ON cn.run_id = a.run_id
        LEFT JOIN `{comentarios_tabela}` cm ON cm.comment_id = a.comment_id AND cm.run_id = a.run_id
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
    marca: str | None = None,
    modelo: str | None = None,
    limite: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Lista paginada de comentários já enriquecidos, filtrada — alimenta a
    tabela detalhada do Dashboard. `marca`/`modelo` (opcionais): busca
    textual best-effort, ver `_filtro_comum`."""
    if not settings.dashboard_dados_reais:
        return dados_ficticios.comentarios_ficticios(canal_id, sentimento, marca, modelo, limite, offset)

    _exigir_configurado()

    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    comentarios_tabela = warehouse.table_id(COMENTARIOS_COLETADOS)
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)
    where, params = _filtro_comum(canal_id, sentimento, marca, modelo)

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


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/satisfacao — NPS/Likert derivados do sentimento
# ─────────────────────────────────────────────────────────────────────────────
#
# O app não coleta pesquisa de satisfação (não existe formulário Likert nem
# pergunta de recomendação/NPS respondida por ninguém) — os dois índices são
# aproximados a partir do único sinal de opinião que já existe:
# `comentarios_analise.output_struct.sentimento`, classificado pela IA.
# Mapeamento simples e documentado, nunca apresentado como pesquisa real:
#   positivo -> promotor / nota 5      negativo -> detrator / nota 1
#   neutro, misto -> passivo / nota 3
# NPS = (%promotores - %detratores) × 100, no intervalo -100..100 de sempre.

_AGG_SENTIMENTO = """
        SUM(CASE WHEN a.output_struct.sentimento = 'positivo' THEN 1 ELSE 0 END) AS positivos,
        SUM(CASE WHEN a.output_struct.sentimento = 'negativo' THEN 1 ELSE 0 END) AS negativos,
        SUM(CASE WHEN a.output_struct.sentimento = 'neutro' THEN 1 ELSE 0 END) AS neutros,
        SUM(CASE WHEN a.output_struct.sentimento = 'misto' THEN 1 ELSE 0 END) AS mistos,
        COUNT(*) AS total
"""


@router.get("/satisfacao")
async def obter_satisfacao(
    canal_id: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
):
    """
    NPS e Likert derivados do sentimento (ver módulo acima), na mesma amostra
    filtrada de `/analytics/resumo`/`/comentarios` — geral, por tópico
    (objeto), por canal, tendência diária e engajamento (likes) por sentimento.
    """
    if not settings.dashboard_dados_reais:
        return dados_ficticios.satisfacao_ficticia(canal_id, sentimento, marca, modelo)

    _exigir_configurado()

    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)
    comentarios_tabela = warehouse.table_id(COMENTARIOS_COLETADOS)
    where, params = _filtro_comum(canal_id, sentimento, marca, modelo)

    base_join = f"""
        FROM `{analise_tabela}` a
        LEFT JOIN `{canais_tabela}` cn ON cn.run_id = a.run_id
        LEFT JOIN `{comentarios_tabela}` cm ON cm.comment_id = a.comment_id AND cm.run_id = a.run_id
        WHERE {where}
    """

    geral = _executar(f"SELECT {_AGG_SENTIMENTO} {base_join}", params)
    geral_linha = _com_indicadores(geral[0] if geral else linha_agregada_vazia())

    por_objeto = _executar(f"""
        SELECT a.output_struct.objeto AS objeto, {_AGG_SENTIMENTO}
        {base_join}
        GROUP BY objeto
        ORDER BY total DESC
        LIMIT 12
    """, params)

    por_canal = _executar(f"""
        SELECT cn.canal_nome AS canal_nome, {_AGG_SENTIMENTO}
        {base_join}
        GROUP BY canal_nome
        ORDER BY total DESC
        LIMIT 12
    """, params)

    tendencia = _executar(f"""
        SELECT CAST(DATE(a.coletado_em) AS STRING) AS dia, {_AGG_SENTIMENTO}
        {base_join}
        GROUP BY dia
        ORDER BY dia
    """, params)

    engajamento = _executar(f"""
        SELECT a.output_struct.sentimento AS sentimento,
               AVG(cm.likes) AS likes_medio,
               COUNT(*) AS total
        {base_join}
        GROUP BY sentimento
        ORDER BY sentimento
    """, params)

    return {
        "geral": geral_linha,
        "por_objeto": [_com_indicadores(r) for r in por_objeto],
        "por_canal": [_com_indicadores(r) for r in por_canal],
        "tendencia": [_com_indicadores(r) for r in tendencia],
        "engajamento_por_sentimento": engajamento,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /analytics/comparativo-marcas — Ford vs. concorrente escolhido
# ─────────────────────────────────────────────────────────────────────────────

MARCAS_COMPARAVEIS = ["Chevrolet", "Volkswagen", "Toyota", "Hyundai"]


def _indicadores_marca(marca: str, canais_tabela: str, analise_tabela: str, comentarios_tabela: str) -> dict:
    where, params = _filtro_comum(None, None, marca=marca)
    base_join = f"""
        FROM `{analise_tabela}` a
        LEFT JOIN `{canais_tabela}` cn ON cn.run_id = a.run_id
        LEFT JOIN `{comentarios_tabela}` cm ON cm.comment_id = a.comment_id AND cm.run_id = a.run_id
        WHERE {where}
    """
    agregado = _executar(f"SELECT {_AGG_SENTIMENTO} {base_join}", params)
    linha = _com_indicadores(agregado[0] if agregado else linha_agregada_vazia())
    engajamento = _executar(f"SELECT AVG(cm.likes) AS likes_medio {base_join}", params)
    likes_medio = engajamento[0]["likes_medio"] if engajamento else None

    total = linha["total"] or 0
    return {
        "marca": marca,
        "nps": linha["nps"],
        "pct_positivo": round(linha["positivos"] / total * 100, 1) if total else None,
        "engajamento_medio": round(likes_medio, 1) if likes_medio is not None else None,
        "mencoes": total,
    }


@router.get("/comparativo-marcas")
async def obter_comparativo_marcas(marca_concorrente: str = "Chevrolet"):
    """
    Ford vs. uma marca concorrente escolhida — NPS, % positivo, engajamento
    médio e menções.

    No modo ilustrativo (padrão, `DASHBOARD_REAL=false`) usa números fixos
    com a Ford sempre à frente, pensados só pra demonstração. No modo real
    usa a mesma busca textual best-effort de `_filtro_comum(marca=...)`
    sobre o warehouse (ver módulo `_filtro_comum` acima) — aqui o resultado
    reflete o que estiver de fato nos comentários coletados, sem favorecer
    nenhuma marca.
    """
    if not settings.dashboard_dados_reais:
        return dados_ficticios.comparativo_ficticio(marca_concorrente)

    _exigir_configurado()
    canais_tabela = warehouse.table_id(CANAIS_BUSCADOS)
    analise_tabela = warehouse.table_id(COMENTARIOS_ANALISE)
    comentarios_tabela = warehouse.table_id(COMENTARIOS_COLETADOS)

    return {
        "ford": _indicadores_marca("Ford", canais_tabela, analise_tabela, comentarios_tabela),
        "concorrente": _indicadores_marca(marca_concorrente, canais_tabela, analise_tabela, comentarios_tabela),
    }
