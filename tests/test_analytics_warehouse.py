"""
test_analytics_warehouse.py — verifica o warehouse analítico do YouTube
(src/analytics/) sem tocar rede nem credencial real de BigQuery: injeta um
cliente falso no lugar do `bigquery.Client()`, no mesmo espírito dos dublês
usados em test_proxy_pool.py/test_agentes.py.

Cobre:
  1. AnaliseComentarioStruct — a saída da LLM É validada (diferente do resto
     do app, onde output_schema é só sugestão de prompt, nunca verificada).
  2. BigQueryWarehouse — criação idempotente de dataset/tabela e o formato
     STRUCT (RECORD aninhado) de `comentarios_analise`.
  3. enrichment.enriquecer_comentarios — correlação por índice entre a
     entrada e a resposta da LLM, e tratamento de saída ausente/malformada.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config
import tempfile

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_analytics_"))
config.DATA_DIR = TEMP / "data"

import pandas as pd
from google.api_core.exceptions import NotFound
from pydantic import ValidationError

from src.agents.base import AgentResult
from src.analytics import schemas, warehouse
from src.analytics.enrichment import enriquecer_comentarios
from src.analytics.ids import transcript_id
from src.analytics.models import AnaliseComentarioStruct
from src.pipelines.models import PipelineRun, Prompt, PromptVersao, TipoEntrada

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


# ─────────────────────────────────────────────────────────────────────────────
# Dublê de bigquery.Client — sem rede, sem credencial
# ─────────────────────────────────────────────────────────────────────────────

class ClienteFalso:
    project = "projeto-teste"

    def __init__(self):
        self.datasets_criados = []
        self.tabelas_criadas = []
        self.datasets_existentes = set()
        self.tabelas_existentes = set()
        self.cargas = []  # (table_id, dataframe, job_config)
        self.tabelas_com_falha_simulada = set()  # nomes (não table_id completo) que devem falhar no load

    def get_dataset(self, dataset_id):
        if dataset_id not in self.datasets_existentes:
            raise NotFound(f"dataset {dataset_id} não existe")
        return dataset_id

    def create_dataset(self, dataset):
        self.datasets_criados.append(str(dataset.reference))
        self.datasets_existentes.add(str(dataset.reference))

    def get_table(self, table_id):
        if table_id not in self.tabelas_existentes:
            raise NotFound(f"tabela {table_id} não existe")
        return table_id

    def create_table(self, table):
        self.tabelas_criadas.append(str(table.reference))
        self.tabelas_existentes.add(str(table.reference))

    def load_table_from_dataframe(self, df, table_id, job_config=None):
        if any(nome in table_id for nome in self.tabelas_com_falha_simulada):
            raise RuntimeError(f"falha simulada de load para {table_id}")

        self.cargas.append((table_id, df, job_config))

        class JobFalso:
            def result(self_inner):
                return None

        return JobFalso()


def principal() -> int:
    print("=== 1. AnaliseComentarioStruct: validação da saída fixa ===")

    struct = AnaliseComentarioStruct(
        sentimento="positivo", motivo="elogiou o motor",
        adjetivos="potente", objeto="motor",
    )
    checar(struct.adjetivos == ["potente"], "string única em 'adjetivos' vira lista")

    try:
        AnaliseComentarioStruct(sentimento="", motivo="x", adjetivos=[], objeto="y")
        checar(False, "sentimento vazio deveria falhar a validação")
    except ValidationError:
        checar(True, "sentimento vazio rejeitado")

    try:
        AnaliseComentarioStruct(sentimento="positivo", motivo=None, adjetivos=[], objeto="y")
        checar(False, "motivo ausente deveria falhar a validação")
    except ValidationError:
        checar(True, "motivo ausente (None) rejeitado")

    print("\n=== 2. BigQueryWarehouse: criação idempotente de dataset/tabela ===")

    cliente = ClienteFalso()
    warehouse._client = cliente
    warehouse._tabelas_prontas.clear()

    table_id_1 = warehouse._preparar_tabela(cliente, schemas.CANAIS_BUSCADOS)
    checar(len(cliente.datasets_criados) == 1, "dataset criado na primeira chamada")
    checar(len(cliente.tabelas_criadas) == 1, "tabela criada na primeira chamada")

    table_id_2 = warehouse._preparar_tabela(cliente, schemas.CANAIS_BUSCADOS)
    checar(table_id_1 == table_id_2, "mesmo table_id nas duas chamadas")
    checar(len(cliente.tabelas_criadas) == 1, "segunda chamada não recria a tabela (cache local)")

    # Sem o cache local, mas já existente do lado do "servidor" — não recria
    warehouse._tabelas_prontas.clear()
    warehouse._preparar_tabela(cliente, schemas.CANAIS_BUSCADOS)
    checar(len(cliente.tabelas_criadas) == 1,
           "tabela já existente no servidor não é recriada mesmo sem cache local")

    print("\n=== 3. upsert(): grava a coluna output_struct como RECORD (STRUCT) ===")

    linha_analise = {
        "comment_id": "cmt_1",
        "video_id": "vid_1",
        "transcript_id": "tr_abc123",
        "run_id": "run_1",
        "prompt_id": "prompt_1",
        "prompt_version": 1,
        "provider": "fake",
        "model": "modelo-teste",
        "success": True,
        "output_struct": {
            "sentimento": "positivo", "motivo": "elogiou",
            "adjetivos": ["rapido", "bonito"], "objeto": "motor",
        },
        "coletado_em": "2026-01-01T00:00:00+00:00",
    }
    warehouse.upsert(schemas.COMENTARIOS_ANALISE, [linha_analise])

    checar(len(cliente.cargas) == 1, "upsert chamou load_table_from_dataframe uma vez")
    table_id, df, job_config = cliente.cargas[0]
    checar(schemas.COMENTARIOS_ANALISE in table_id, "carga foi para a tabela comentarios_analise")
    checar(df.iloc[0]["output_struct"]["sentimento"] == "positivo",
           "output_struct preservado como dict aninhado no DataFrame")
    checar(job_config.write_disposition == "WRITE_APPEND",
           "gravação é sempre append (warehouse nunca trunca)")

    schema_gravado = {f.name: f for f in job_config.schema}
    checar("output_struct" in schema_gravado and schema_gravado["output_struct"].field_type == "RECORD",
           "schema enviado ao BigQuery declara output_struct como RECORD")

    print("\n=== 4. enriquecer_comentarios: correlação por índice + falhas ===")

    prompt_fixo = Prompt(
        id="prompt-fixo-teste", name="teste", prompt="texto", input_type=TipoEntrada.COMMENTS,
        output_format="json", output_schema={"comentarios": []},
    )
    prompt_fixo.versions = [prompt_fixo.snapshot()]

    class AgenteFalsoOk:
        async def run(self, entrada, **kwargs):
            itens = [
                {
                    "indice": c["indice"], "sentimento": "positivo", "motivo": "gostou",
                    "adjetivos": ["bom"], "objeto": "carro",
                }
                for c in entrada["comentarios"]
            ]
            return AgentResult(
                success=True, provider="fake", model="modelo-teste",
                output={"comentarios": itens}, usage={"total_tokens": 20},
            )

    comentarios = [
        {"comment_id": "c1", "refined_record_id": "cmt_aaa", "texto": "Muito bom!"},
        {"comment_id": "c2", "refined_record_id": "cmt_bbb", "texto": "Não gostei do consumo."},
    ]

    resultados = asyncio.run(enriquecer_comentarios(AgenteFalsoOk(), comentarios, prompt=prompt_fixo))
    checar(len(resultados) == 2, "um resultado por comentário de entrada")
    checar(all(r["success"] for r in resultados), "todos validados com sucesso")
    checar(resultados[0]["comment_id"] == "c1" and resultados[1]["comment_id"] == "c2",
           "resultado mantém o comment_id original, não o índice devolvido pela LLM")

    class AgenteFalsoIncompleto:
        async def run(self, entrada, **kwargs):
            # Só devolve o primeiro item — o segundo índice fica sem resposta
            return AgentResult(
                success=True, provider="fake", model="modelo-teste",
                output={"comentarios": [
                    {"indice": 0, "sentimento": "positivo", "motivo": "ok", "adjetivos": [], "objeto": "carro"},
                ]},
                usage={"total_tokens": 10},
            )

    resultados_2 = asyncio.run(enriquecer_comentarios(AgenteFalsoIncompleto(), comentarios, prompt=prompt_fixo))
    checar(resultados_2[0]["success"] is True, "índice 0 com resposta: sucesso")
    checar(resultados_2[1]["success"] is False and resultados_2[1]["error"]["type"] == "MissingOutput",
           "índice 1 sem resposta da LLM: marcado como falha (MissingOutput)")

    class AgenteFalsoMalformado:
        async def run(self, entrada, **kwargs):
            return AgentResult(
                success=True, provider="fake", model="modelo-teste",
                output={"comentarios": [
                    {"indice": 0, "sentimento": "", "motivo": "", "adjetivos": [], "objeto": ""},
                    {"indice": 1, "sentimento": "negativo", "motivo": "consumo alto", "adjetivos": [], "objeto": "consumo"},
                ]},
                usage={"total_tokens": 10},
            )

    resultados_3 = asyncio.run(enriquecer_comentarios(AgenteFalsoMalformado(), comentarios, prompt=prompt_fixo))
    checar(resultados_3[0]["success"] is False and resultados_3[0]["error"]["type"] == "OutputParseError",
           "struct com campos vazios é rejeitado (OutputParseError), não gravado como está")
    checar(resultados_3[1]["success"] is True, "item bem formado ao lado de um malformado continua válido")

    class AgenteFalsoFalhou:
        async def run(self, entrada, **kwargs):
            return AgentResult.falha("fake", "modelo-teste", "ProviderError", "indisponível", retryable=True)

    resultados_4 = asyncio.run(enriquecer_comentarios(AgenteFalsoFalhou(), comentarios, prompt=prompt_fixo))
    checar(len(resultados_4) == 2 and all(not r["success"] for r in resultados_4),
           "chamada à LLM falhando marca todos os comentários do lote como falha, sem exceção")

    print("\n=== 5. coletado_em (TIMESTAMP): string ISO precisa virar datetime real ===")

    # Regressão do bug real: coletado_em é gravado como string ISO 8601
    # (datetime.isoformat()), mas o schema declara TIMESTAMP. Sem
    # normalização, a conversão pandas→arrow que load_table_from_dataframe
    # faz por baixo (antes de qualquer chamada de rede) falha com
    # ArrowTypeError — e isso valia pra TODAS as tabelas, não só uma.
    from google.cloud.bigquery import _pandas_helpers

    linha_com_timestamp_string = {
        "canal_id": "c1", "canal_nome": "Canal Teste", "run_id": "run_1",
        "inscritos": 100, "views_totais": 1000, "videos_totais": 10,
        "coletado_em": "2026-01-01T00:00:00+00:00",
    }
    df_normalizado = warehouse._normalizar_tipos(
        pd.DataFrame([linha_com_timestamp_string]), schemas.SCHEMA_CANAIS_BUSCADOS
    )
    checar(str(df_normalizado["coletado_em"].dtype).startswith("datetime64"),
           "_normalizar_tipos converte coletado_em (string) para datetime64 real")

    try:
        _pandas_helpers.dataframe_to_arrow(df_normalizado, schemas.SCHEMA_CANAIS_BUSCADOS)
        checar(True, "conversão pandas→arrow funciona com coletado_em normalizado")
    except Exception as exc:
        checar(False, f"conversão pandas→arrow ainda falha: {exc}")

    df_sem_normalizar = pd.DataFrame([linha_com_timestamp_string])
    try:
        _pandas_helpers.dataframe_to_arrow(df_sem_normalizar, schemas.SCHEMA_CANAIS_BUSCADOS)
        checar(False, "deveria ter falhado sem a normalização (regressão do bug original)")
    except Exception:
        checar(True, "sem normalizar, a mesma conversão falha (confirma que o bug era esse)")

    print("\n=== 6. registrar_execucao: uma tabela falhar não trava as outras ===")

    cliente2 = ClienteFalso()
    cliente2.tabelas_com_falha_simulada = {schemas.TRANSCRICOES_COLETADAS}
    warehouse._client = cliente2
    warehouse._tabelas_prontas.clear()
    warehouse._ultimo_erro = None

    run_teste = PipelineRun(
        pipeline_name="Teste",
        refined={
            "videos": [{"video_id": "v1"}],
            "channels": {"c1": {"nome": "Canal X", "inscritos": 10, "views_totais": 100, "videos_totais": 5}},
            "transcripts": {"v1": {"language": "pt", "gerada_automaticamente": True, "palavras": 50, "caracteres": 300}},
            "comments": {"v1": [{"comment_id": "cmt1", "autor": "fulano", "texto": "bom", "likes": 1, "respostas": 0}]},
        },
        trust={"enriquecimento_comentarios": [{
            "comment_id": "cmt1", "video_id": "v1", "success": True,
            "struct": {"sentimento": "positivo", "motivo": "gostou", "adjetivos": ["bom"], "objeto": "carro"},
            "prompt_id": "p1", "prompt_version": 1,
        }]},
        agente={"provider": "fake", "model": "modelo-teste"},
    )

    warehouse.registrar_execucao(run_teste)

    tabelas_gravadas = {table_id.rsplit(".", 1)[-1] for table_id, _, _ in cliente2.cargas}
    checar(schemas.CANAIS_BUSCADOS in tabelas_gravadas,
           "canais gravado normalmente")
    checar(schemas.TRANSCRICOES_COLETADAS not in tabelas_gravadas,
           "transcrições falhou (simulado) e não aparece nas cargas bem-sucedidas")
    checar(schemas.COMENTARIOS_COLETADOS in tabelas_gravadas,
           "comentários AINDA É gravado mesmo com transcrições falhando — isolamento funcionando")
    checar(schemas.COMENTARIOS_ANALISE in tabelas_gravadas,
           "comentarios_analise AINDA É gravado mesmo com transcrições falhando")

    status = warehouse.status()
    checar(status["ultimo_erro"] is not None and schemas.TRANSCRICOES_COLETADAS in status["ultimo_erro"],
           "status() reporta o erro específico da tabela que falhou")
    checar(schemas.CANAIS_BUSCADOS not in (status["ultimo_erro"] or {}),
           "status() não marca como erro uma tabela que gravou com sucesso")

    print("\n=== 7. registrar_execucao: transcrição grava texto bruto E refinado ===")

    cliente3 = ClienteFalso()
    warehouse._client = cliente3
    warehouse._tabelas_prontas.clear()
    warehouse._ultimo_erro = None

    run_transcricao = PipelineRun(
        pipeline_name="Teste transcrição",
        raw={
            "transcripts": {
                "v1": {
                    "video_id": "v1", "language": "pt", "is_generated": True,
                    "segments": [
                        {"text": "Esse carro &eacute; muito r&aacute;pido", "start": 0.0, "duration": 3.0},
                        {"text": "o acabamento podia ser melhor", "start": 3.0, "duration": 2.5},
                    ],
                }
            },
        },
        refined={
            "videos": [{"video_id": "v1"}],
            "channels": {},
            "transcripts": {
                "v1": {
                    "language": "pt", "gerada_automaticamente": True,
                    "palavras": 12, "caracteres": 70,
                    "clean_text": "Esse carro é muito rápido o acabamento podia ser melhor",
                }
            },
            "comments": {},
        },
        agente={"provider": "fake", "model": "modelo-teste"},
    )

    warehouse.registrar_execucao(run_transcricao)

    carga_transcricao = next(
        (c for c in cliente3.cargas if schemas.TRANSCRICOES_COLETADAS in c[0]), None
    )
    checar(carga_transcricao is not None, "tabela de transcrições foi gravada")

    if carga_transcricao:
        linha_transcricao = carga_transcricao[1].iloc[0]
        checar("&eacute;" in linha_transcricao["texto_bruto"] and "&aacute;" in linha_transcricao["texto_bruto"],
               "texto_bruto guarda os segmentos originais, sem limpeza (entidades HTML intactas)")
        checar(linha_transcricao["texto_refinado"] == "Esse carro é muito rápido o acabamento podia ser melhor",
               "texto_refinado guarda o clean_text já tratado (REFINED)")

    print("\n=== 8. registrar_execucao: analises_ia grava qualquer prompt do usuário (não só comentários) ===")

    cliente4 = ClienteFalso()
    warehouse._client = cliente4
    warehouse._tabelas_prontas.clear()
    warehouse._ultimo_erro = None

    run_analise_ia = PipelineRun(
        pipeline_name="Teste análise IA",
        refined={
            "videos": [{"video_id": "v1"}],
            "channels": {},
            "transcripts": {"v1": {"language": "pt", "gerada_automaticamente": True, "palavras": 10, "caracteres": 60}},
            "comments": {},
        },
        trust={
            "analises": [
                {
                    "prompt_id": "p1", "prompt_name": "Percepção sobre o carro", "prompt_version": 2,
                    "input_type": "transcript", "escopo": "v1",
                    "source": {"video_id": "v1", "video_url": "https://youtu.be/v1"},
                    "agent": {"provider": "fake", "model": "modelo-teste"},
                    "success": True,
                    "output": {"velocidade": {"percepcao": "muito boa"}},
                },
                {
                    "prompt_id": "p2", "prompt_name": "Dataset geral", "prompt_version": 1,
                    "input_type": "dataset", "escopo": "dataset",
                    "source": {"video_ids": ["v1"], "total_videos": 1},
                    "agent": {"provider": "fake", "model": "modelo-teste"},
                    "success": False,
                    "error": {"type": "OutputParseError", "message": "JSON inválido"},
                },
            ],
        },
        agente={"provider": "fake", "model": "modelo-teste"},
    )

    warehouse.registrar_execucao(run_analise_ia)

    carga_analise_ia = next((c for c in cliente4.cargas if schemas.ANALISES_IA in c[0]), None)
    checar(carga_analise_ia is not None, "tabela analises_ia foi gravada")

    if carga_analise_ia:
        df_analise_ia = carga_analise_ia[1]
        checar(len(df_analise_ia) == 2, "grava uma linha por análise, sucesso e falha incluídos")

        linha_transcricao = df_analise_ia[df_analise_ia["prompt_id"] == "p1"].iloc[0]
        checar(linha_transcricao["video_id"] == "v1", "análise por vídeo mantém o video_id")
        checar(linha_transcricao["transcript_id"] == transcript_id(run_analise_ia.id, "v1"),
               "análise sobre transcrição recebe o mesmo transcript_id sintético das outras tabelas")
        checar(json.loads(linha_transcricao["output_json"])["velocidade"]["percepcao"] == "muito boa",
               "output_json guarda a saída livre da LLM (schema definido pelo prompt, não fixo)")

        linha_dataset = df_analise_ia[df_analise_ia["prompt_id"] == "p2"].iloc[0]
        checar(linha_dataset["video_id"] is None, "análise de escopo 'dataset' não tem video_id")
        checar(linha_dataset["success"] == False, "falha da LLM é gravada (success=False), não descartada")
        checar(linha_dataset["error_type"] == "OutputParseError", "tipo do erro preservado")

    # Não deixa o cliente falso (nem o cache de tabelas) vazar para fora
    # deste processo de teste.
    warehouse._client = None
    warehouse._tabelas_prontas.clear()
    warehouse._ultimo_erro = None

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações do warehouse analítico passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
