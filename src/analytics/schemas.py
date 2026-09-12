"""
schemas.py
Schemas BigQuery das tabelas do warehouse analítico do YouTube — mesmo
padrão do notebook `tabela_marcas.ipynb` (schema explícito via
`bigquery.SchemaField`, uma constante por tabela).

`run_id` é o `search_id`: cada execução/busca do Playground já é um run_id
único, não é um campo novo. `transcript_id` é sintetizado por
`src/analytics/ids.py` (não existia uma chave estável de transcrição antes).
"""
from __future__ import annotations

from google.cloud import bigquery

CANAIS_BUSCADOS = "canais_buscados"
TRANSCRICOES_COLETADAS = "transcricoes_coletadas"
COMENTARIOS_COLETADOS = "comentarios_coletados"
COMENTARIOS_ANALISE = "comentarios_analise"
ANALISES_IA = "analises_ia"

SCHEMA_CANAIS_BUSCADOS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("canal_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("canal_nome", "STRING"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED",
                          description="search_id — id da execução/busca que coletou este canal"),
    bigquery.SchemaField("inscritos", "INT64"),
    bigquery.SchemaField("views_totais", "INT64"),
    bigquery.SchemaField("videos_totais", "INT64"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

SCHEMA_TRANSCRICOES_COLETADAS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("transcript_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED", description="search_id"),
    bigquery.SchemaField("video_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("idioma", "STRING"),
    bigquery.SchemaField("gerada_automaticamente", "BOOL"),
    bigquery.SchemaField("palavras", "INT64"),
    bigquery.SchemaField("caracteres", "INT64"),
    bigquery.SchemaField(
        "texto_bruto", "STRING",
        description="Transcrição completa extraída, como veio da API (segmentos "
                     "originais concatenados) — antes de qualquer limpeza.",
    ),
    bigquery.SchemaField(
        "texto_refinado", "STRING",
        description="Transcrição tratada (REFINED) — texto normalizado/limpo "
                     "(refine.py::limpar_texto), o mesmo usado como entrada dos prompts.",
    ),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

SCHEMA_COMENTARIOS_COLETADOS: list[bigquery.SchemaField] = [
    bigquery.SchemaField("comment_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("refined_record_id", "STRING"),
    bigquery.SchemaField("transcript_id", "STRING"),
    bigquery.SchemaField("video_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED", description="search_id"),
    bigquery.SchemaField("autor", "STRING"),
    bigquery.SchemaField("texto", "STRING"),
    bigquery.SchemaField("likes", "INT64"),
    bigquery.SchemaField("respostas", "INT64"),
    bigquery.SchemaField("publicado_em", "STRING"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

# `output_struct` é um STRUCT nativo do BigQuery (RECORD) numa única coluna —
# sempre traz a resposta da LLM no mesmo formato fixo, independente do
# comentário ou da execução.
SCHEMA_COMENTARIOS_ANALISE: list[bigquery.SchemaField] = [
    bigquery.SchemaField("comment_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("video_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("transcript_id", "STRING"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED", description="search_id"),
    bigquery.SchemaField("prompt_id", "STRING"),
    bigquery.SchemaField("prompt_version", "INT64"),
    bigquery.SchemaField("provider", "STRING"),
    bigquery.SchemaField("model", "STRING"),
    bigquery.SchemaField("success", "BOOL"),
    bigquery.SchemaField("output_struct", "RECORD", fields=[
        bigquery.SchemaField("sentimento", "STRING"),
        bigquery.SchemaField("motivo", "STRING"),
        bigquery.SchemaField("adjetivos", "STRING", mode="REPEATED"),
        bigquery.SchemaField("objeto", "STRING"),
    ]),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

# `comentarios_analise` é o struct fixo do enriquecimento automático (sempre
# sentimento/motivo/adjetivos/objeto). `analises_ia` é diferente: espelha
# `src/pipelines/db.py::InsightRow` (SQLite) — uma linha por chamada de
# QUALQUER prompt do usuário via a etapa `agent` (Regra 8: prompt é
# configuração, schema livre), rodando sobre transcrição, comentários,
# vídeo completo ou o dataset inteiro (`input_type`). Por isso `output_json`
# é texto livre (a saída bruta da LLM), não um RECORD fixo como o de cima —
# não dá pra saber o formato de antemão, quem definiu foi o prompt.
SCHEMA_ANALISES_IA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED", description="search_id"),
    bigquery.SchemaField("video_id", "STRING", description="nulo quando o escopo é o dataset inteiro"),
    bigquery.SchemaField("transcript_id", "STRING",
                          description="preenchido quando o prompt usa transcrição (input_type transcript/video)"),
    bigquery.SchemaField("escopo", "STRING", description="video_id do vídeo analisado, ou 'dataset'"),
    bigquery.SchemaField("prompt_id", "STRING"),
    bigquery.SchemaField("prompt_name", "STRING"),
    bigquery.SchemaField("prompt_version", "INT64"),
    bigquery.SchemaField("input_type", "STRING", description="transcript | comments | video | dataset"),
    bigquery.SchemaField("provider", "STRING"),
    bigquery.SchemaField("model", "STRING"),
    bigquery.SchemaField("success", "BOOL"),
    bigquery.SchemaField("output_json", "STRING",
                          description="saída bruta da LLM serializada em JSON — schema livre, definido pelo prompt"),
    bigquery.SchemaField("error_type", "STRING"),
    bigquery.SchemaField("error_message", "STRING"),
    bigquery.SchemaField("coletado_em", "TIMESTAMP"),
]

TABELAS: dict[str, list[bigquery.SchemaField]] = {
    CANAIS_BUSCADOS: SCHEMA_CANAIS_BUSCADOS,
    TRANSCRICOES_COLETADAS: SCHEMA_TRANSCRICOES_COLETADAS,
    COMENTARIOS_COLETADOS: SCHEMA_COMENTARIOS_COLETADOS,
    COMENTARIOS_ANALISE: SCHEMA_COMENTARIOS_ANALISE,
    ANALISES_IA: SCHEMA_ANALISES_IA,
}
