"""
smoke_test.py — verificação ponta-a-ponta do pipeline sem tocar a rede.

Injeta dublês da YouTube API, da transcrição e do provider de LLM, roda o
engine e confere RAW / REFINED / TRUST, rastreabilidade, versionamento e a
geração da DAG.
"""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

# Permite rodar como `python tests/test_pipeline.py` a partir da raiz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Redireciona a persistência para um diretório temporário ──────────────────
import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_smoke_"))
config.DATA_DIR = TEMP / "data"
config.DAGS_DIR = TEMP / "airflow" / "dags"

from src.agents.base import ProviderResponse
from src.agents.providers import REGISTRO
from src.agents.providers.openai_compat import OpenAICompatProvider
from src.pipelines import store
from src.pipelines.engine import PipelineEngine
from src.pipelines.models import (
    ConfigLLM, ConfigSchedule, FonteYouTube, PassoPipeline, PipelineEntrada,
    PipelineRun, PipelineVersao, PromptEntrada, TipoEntrada, TipoStep,
)

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


# ─────────────────────────────────────────────────────────────────────────────
# Dublês
# ─────────────────────────────────────────────────────────────────────────────

class YouTubeFalso:
    """Reproduz a forma exata das respostas da YouTube Data API v3."""

    configurado = True

    async def search_videos(self, query, **kwargs):
        return {
            "kind": "youtube#searchListResponse",
            "items": [
                {"id": {"videoId": "vid00000011"}, "snippet": {"channelId": "canalA"}},
                {"id": {"videoId": "vid00000022"}, "snippet": {"channelId": "canalA"}},
                {"id": {"videoId": "vid00000033"}, "snippet": {"channelId": "canalB"}},
            ],
        }

    async def get_videos(self, ids, **kwargs):
        base = {
            "vid00000011": ("Review Ford Territory 2026", "canalA", "50000", "PT15M30S"),
            "vid00000022": ("Avaliação Territory Titanium", "canalA", "1200", "PT8M"),
            "vid00000033": ("Territory x Compass", "canalB", "98000", "PT22M10S"),
        }
        saida = []
        for vid in ids:
            if vid not in base:
                continue
            titulo, canal, views, dur = base[vid]
            saida.append({
                "id": vid,
                "snippet": {
                    "title": f"<b>{titulo}</b>",           # HTML para testar limpeza
                    "description": "Confira em  https://exemplo.com/x   já!!!!",
                    "channelId": canal,
                    "channelTitle": f"Canal {canal}",
                    "publishedAt": "2026-03-14T10:00:00Z",
                    "thumbnails": {"high": {"url": f"https://i.ytimg.com/{vid}.jpg"}},
                },
                "statistics": {"viewCount": views, "likeCount": "800", "commentCount": "120"},
                "contentDetails": {"duration": dur},
            })
        return saida

    async def get_channels(self, ids, **kwargs):
        return [
            {"id": c, "snippet": {"title": f"Canal {c}", "publishedAt": "2019-01-01T00:00:00Z"},
             "statistics": {"subscriberCount": "250000", "viewCount": "9000000", "videoCount": "410"}}
            for c in dict.fromkeys(i for i in ids if i)
        ]

    async def get_comments(self, video_id, **kwargs):
        textos = [
            "O carro é muito rápido!!!!",
            "o carro e muito rapido",              # duplicata após normalização
            "<b>Design</b> lindo, mas consumo alto",
            "   ",                                  # vazio após limpeza
            "Acabamento interno decepcionou",
        ]
        return [
            {
                "id": f"thread_{video_id}_{i}",
                "snippet": {
                    "totalReplyCount": i,
                    "topLevelComment": {
                        "id": f"cmt_{video_id}_{i}",
                        "snippet": {
                            "textDisplay": t,
                            "authorDisplayName": f"usuario{i}",
                            "likeCount": 10 - i,
                            "publishedAt": "2026-04-01T12:00:00Z",
                        },
                    },
                },
            }
            for i, t in enumerate(textos)
        ]


class TranscricaoFalsa:
    async def get_transcript(self, video_id, idiomas=None):
        if video_id == "vid00000022":
            from src.youtube import TranscriptError
            raise TranscriptError(f"sem legenda para {video_id}")
        return {
            "video_id": video_id,
            "language": "pt",
            "is_generated": True,
            "segments": [
                {"text": "Esse carro é muito rápido\n", "start": 0.0, "duration": 3.2},
                {"text": "o acabamento poderia ser melhor", "start": 3.2, "duration": 2.8},
            ],
        }


class LLMFalso(OpenAICompatProvider):
    """Provider registrado como 'fake' — devolve JSON estruturado plausível."""

    nome = "fake"
    modelo_padrao = "modelo-teste"
    exige_api_key = False
    chamadas = []

    async def complete(self, *, system_prompt, user_prompt, temperature=0.0,
                       max_tokens=None, json_mode=False):
        LLMFalso.chamadas.append({"system": system_prompt, "user": user_prompt})
        return ProviderResponse(
            text='```json\n{"velocidade": {"percepcao": "muito boa", '
                 '"evidencias": ["o carro e muito rapido"]}, '
                 '"qualidades": ["rapido", "bonito"]}\n```',
            usage={"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
        )


REGISTRO["fake"] = LLMFalso


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────

async def principal():
    print("\n=== 1. Prompts: criação e versionamento ===")

    prompt = store.criar_prompt(PromptEntrada(
        name="Percepção sobre o carro",
        description="Extrai percepções por categoria",
        prompt="Extraia as percepções sobre o carro e categorize.",
        input_type=TipoEntrada.VIDEO,
        output_format="json",
        output_schema={"velocidade": {"percepcao": "string"}},
    ))
    checar(prompt.version == 1, "prompt criado na versão 1")
    checar(len(prompt.versions) == 1, "snapshot da v1 arquivado")

    v1_texto = prompt.prompt
    atualizado = store.atualizar_prompt(prompt.id, PromptEntrada(
        name="Percepção sobre o carro",
        description="Extrai percepções por categoria",
        prompt="Versão nova do prompt: extraia percepções e evidências.",
        input_type=TipoEntrada.VIDEO,
        output_format="json",
        output_schema={"velocidade": {"percepcao": "string"}},
    ))
    checar(atualizado.version == 2, "editar o texto criou a versão 2")
    checar(atualizado.na_versao(1).prompt == v1_texto, "v1 recuperável e intacta")

    so_nome = store.atualizar_prompt(prompt.id, PromptEntrada(
        name="Percepção do produto",
        description="renomeado",
        prompt=atualizado.prompt,
        input_type=TipoEntrada.VIDEO,
        output_format="json",
        output_schema={"velocidade": {"percepcao": "string"}},
    ))
    checar(so_nome.version == 2, "renomear não consome versão")

    prompt_coment = store.criar_prompt(PromptEntrada(
        name="Sentimento dos comentários",
        prompt="Classifique o sentimento dos comentários.",
        input_type=TipoEntrada.COMMENTS,
    ))

    print("\n=== 2. Engine: RAW → REFINED → TRUST ===")

    config_pipeline = PipelineVersao(
        version=1,
        source=FonteYouTube(
            query="review Ford Territory 2026",
            max_results=3,
            min_views=5000,          # deve reprovar vid00000022 (1200 views)
            max_comentarios_por_video=10,
        ),
        steps=[
            PassoPipeline(type=TipoStep.YOUTUBE_SEARCH),
            PassoPipeline(type=TipoStep.TRANSCRIPT),
            PassoPipeline(type=TipoStep.COMMENTS),
            PassoPipeline(type=TipoStep.RAW),
            PassoPipeline(type=TipoStep.REFINED),
            PassoPipeline(type=TipoStep.AGENT, prompt_id=prompt.id, prompt_version=2),
            PassoPipeline(type=TipoStep.AGENT, prompt_id=prompt_coment.id),
            PassoPipeline(type=TipoStep.OUTPUT),
        ],
        llm=ConfigLLM(provider="fake", model="modelo-teste"),
        schedule=ConfigSchedule(mode="daily", hora="08:00"),
    )

    engine = PipelineEngine(youtube=YouTubeFalso(), transcript=TranscricaoFalsa())
    run = PipelineRun(pipeline_name="Smoke")
    await engine.executar(config_pipeline, run)

    for s in run.steps:
        marca = {"success": "OK  ", "failed": "FALHA", "skipped": "pulado"}.get(s.status.value, s.status.value)
        print(f"  [{marca}] {s.type.value:16} {s.resumo}")
        if s.error:
            print(f"           erro: {s.error.error_type} — {s.error.message}")

    checar(run.status.value == "success", f"execução concluída (status={run.status.value})")

    # ── RAW ──
    checar(len(run.raw["videos"]) == 3, "RAW guardou os 3 vídeos originais")
    checar(run.raw["videos"][0]["snippet"]["title"].startswith("<b>"),
           "RAW preservou o HTML original do título (imutável)")
    checar(len(run.raw["comments"]["vid00000011"]) == 5,
           "RAW guardou os 5 comentários crus, sem filtrar")

    # ── REFINED ──
    ref = run.refined
    checar(ref["estatisticas"]["total_videos"] == 2,
           "REFINED aplicou min_views (2 de 3 aprovados)")
    checar(ref["estatisticas"]["videos_descartados_por_filtro"] == 1,
           "vídeo reprovado registrado com motivo")
    checar("<b>" not in ref["videos"][0]["titulo"], "REFINED removeu tags HTML do título")
    checar("https://" not in ref["videos"][0]["descricao"], "REFINED removeu URLs")
    checar(ref["videos"][0]["descricao"].endswith("já!"), "REFINED colapsou pontuação repetida")
    checar(ref["videos"][0]["duracao_segundos"] == 930, "PT15M30S → 930 segundos")
    checar(ref["videos"][0]["views"] == 50000, "contador string → int")
    checar(ref["videos"][0]["canal_inscritos"] == 250000, "dados do canal anexados ao vídeo")

    coments = ref["comments"]["vid00000011"]
    checar(len(coments) == 3, f"REFINED deduplicou/limpou comentários (3 de 5, obtido {len(coments)})")
    checar(all(c["refined_record_id"].startswith("cmt_") for c in coments),
           "cada comentário tem refined_record_id para rastreio")

    transc = ref["transcripts"]["vid00000011"]
    checar("\n" not in transc["clean_text"], "transcrição unida em texto contínuo")
    checar(len(transc["segmentos"]) == 2, "segmentos com timestamp preservados")
    checar("vid00000022" not in ref["transcripts"], "vídeo reprovado saiu do REFINED")

    # determinismo
    from src.pipelines import refine
    checar(refine.refinar(run.raw) == refine.refinar(run.raw),
           "REFINED é determinístico (mesmo RAW → mesmo resultado)")

    # ── TRUST ──
    trust = run.trust
    checar(len(trust["analises"]) == 4,
           f"2 prompts × 2 vídeos = 4 análises (obtido {len(trust['analises'])})")
    checar(trust["estatisticas"]["analises_ok"] == 4, "todas as análises retornaram")

    primeira = trust["analises"][0]
    checar(primeira["output"]["velocidade"]["percepcao"] == "muito boa",
           "JSON extraído de dentro da cerca ```json")
    checar(primeira["agent"]["provider"] == "fake" and primeira["agent"]["model"] == "modelo-teste",
           "TRUST registra provider e modelo usados")
    checar(primeira["agent"]["prompt_version"] == 2, "TRUST registra a versão do prompt")
    checar(primeira["source"]["video_id"] in ("vid00000011", "vid00000033"),
           "TRUST aponta o vídeo de origem")
    checar("refined_record_ids" in primeira["source"],
           "TRUST aponta os registros REFINED de origem")
    checar(run.usage["total_tokens"] == 640, f"uso somado (obtido {run.usage['total_tokens']})")

    # entradas por tipo de prompt
    do_coments = [a for a in trust["analises"] if a["input_type"] == "comments"]
    checar(len(do_coments) == 2, "prompt de comentários rodou por vídeo")
    checar("comments" in json.loads(json.dumps(LLMFalso.chamadas[-1]))["user"] or
           "comentário" in LLMFalso.chamadas[-1]["user"].lower() or
           "texto" in LLMFalso.chamadas[-1]["user"].lower(),
           "prompt de comentários recebeu os comentários")
    checar("Responda EXCLUSIVAMENTE" in LLMFalso.chamadas[0]["system"],
           "instrução de JSON injetada no system prompt")

    print("\n=== 3. Pipeline: persistência e versionamento ===")

    pipeline = store.criar_pipeline(PipelineEntrada(
        name="Análise Ford Territory",
        description="Percepções sobre o Territory",
        source=config_pipeline.source,
        steps=config_pipeline.steps,
        llm=config_pipeline.llm,
        schedule=config_pipeline.schedule,
    ))
    checar(pipeline.version == 1, "pipeline criada na v1")

    nova_fonte = config_pipeline.source.model_copy(update={"max_results": 20})
    pipeline2 = store.atualizar_pipeline(pipeline.id, PipelineEntrada(
        name="Análise Ford Territory",
        description="Percepções sobre o Territory",
        source=nova_fonte,
        steps=config_pipeline.steps,
        llm=config_pipeline.llm,
        schedule=config_pipeline.schedule,
    ))
    checar(pipeline2.version == 2, "alterar a fonte criou a v2")
    checar(pipeline2.na_versao(1).source.max_results == 3, "v1 da pipeline preservada")

    store.salvar_run(run)
    checar(store.obter_run(run.id) is not None, "execução recuperável do histórico")

    print("\n=== 4. Geração da DAG ===")

    from src.airflow_gen import descrever_schedule, gerar_dag, montar_cron

    checar(montar_cron(ConfigSchedule(mode="daily", hora="08:00")) == "0 8 * * *",
           "agenda diária 08:00 → '0 8 * * *'")
    checar(montar_cron(ConfigSchedule(mode="weekly", hora="14:30", dia_semana=3)) == "30 14 * * 3",
           "agenda semanal → cron correto")
    checar(montar_cron(ConfigSchedule(mode="manual")) is None, "manual → sem schedule")
    checar(descrever_schedule(ConfigSchedule(mode="daily", hora="08:00")) == "Diário às 08:00",
           "descrição legível da agenda")

    resultado = gerar_dag(pipeline2)
    codigo = resultado["codigo"]

    checar(resultado["dag_id"] == "youtube_analise_ford_territory",
           f"dag_id gerado a partir do nome (obtido {resultado['dag_id']})")
    checar(Path(resultado["caminho"]).is_file(), "arquivo da DAG gravado em disco")
    compile(codigo, resultado["caminho"], "exec")
    print("  OK     código da DAG compila como Python válido")

    checar("executar_pipeline_sync" in codigo,
           "a DAG chama o executor compartilhado (não reimplementa a lógica)")
    checar("schedule='0 8 * * *'" in codigo, "cron embutido na DAG")
    checar("validar_configuracao" in codigo and "publicar_resultado" in codigo,
           "DAG tem as tasks de validação e publicação")
    checar(">>" in codigo, "dependências entre tasks declaradas")

    print("\n=== 5. Segurança: credenciais fora dos artefatos ===")

    import os
    os.environ["GROQ_API_KEY"] = "chave-secreta-para-teste-12345"
    os.environ["YOUTUBE_API_KEY"] = "chave-youtube-secreta-98765"

    codigo2 = gerar_dag(pipeline2)["codigo"]
    checar("chave-secreta-para-teste-12345" not in codigo2, "chave do LLM ausente na DAG")
    checar("chave-youtube-secreta-98765" not in codigo2, "chave do YouTube ausente na DAG")
    checar("GROQ_API_KEY" in codigo2 or "os.environ" in codigo2,
           "a DAG lê a credencial do ambiente")

    serializado = json.dumps(run.model_dump(mode="json"))
    checar("chave-secreta-para-teste-12345" not in serializado, "chave ausente no run persistido")
    checar("chave-youtube-secreta-98765" not in serializado, "chave do YouTube ausente no RAW")

    print("\n=== 6. Tolerância a falhas por etapa ===")

    class YouTubeQuebrado(YouTubeFalso):
        async def get_comments(self, video_id, **kwargs):
            from src.youtube import YouTubeAPIError
            raise YouTubeAPIError("cota estourada", status=403, motivo="quotaExceeded")

    run2 = PipelineRun(pipeline_name="Smoke falha")
    await PipelineEngine(youtube=YouTubeQuebrado(), transcript=TranscricaoFalsa()).executar(
        config_pipeline, run2
    )

    checar(run2.status.value == "partial",
           f"falha em uma etapa → status 'partial' (obtido {run2.status.value})")
    checar(run2.step(TipoStep.COMMENTS).status.value == "failed", "etapa de comentários marcada como falha")
    checar(run2.step(TipoStep.REFINED).status.value == "success",
           "etapas seguintes continuaram após falha tolerável")
    checar(len(run2.refined.get("videos", [])) == 2, "dados das etapas anteriores preservados")
    checar(len(run2.trust.get("insights", [])) > 0, "TRUST produzido mesmo sem comentários")

    class LLMQuebrado(LLMFalso):
        nome = "fake_ruim"

        async def complete(self, **kwargs):
            from src.agents.base import ProviderError
            raise ProviderError("limite de requisições", tipo="RateLimitError", retryable=True)

    REGISTRO["fake_ruim"] = LLMQuebrado

    run3 = PipelineRun(pipeline_name="Smoke LLM falha")
    config_ruim = config_pipeline.model_copy(update={"llm": ConfigLLM(provider="fake_ruim")})
    await PipelineEngine(youtube=YouTubeFalso(), transcript=TranscricaoFalsa()).executar(
        config_ruim, run3
    )

    agente_step = [s for s in run3.steps if s.type == TipoStep.AGENT][0]
    checar(agente_step.status.value == "failed", "falha do LLM marcada na etapa do agente")
    checar(agente_step.error.error_type == "RateLimitError", "tipo do erro do provider preservado")
    checar(agente_step.error.retryable is True, "erro sinalizado como reexecutável")
    checar(len(run3.refined.get("videos", [])) == 2,
           "REFINED preservado apesar da falha na IA (Regra 34)")

    print("\n=== 7. Contrato do Agente ===")

    from src.agents import Agente

    agente = Agente(provider="fake")
    ok = await agente.run({"transcript": "texto"}, prompt="analise")
    checar(ok.success is True, "AgentResult.success em caso de sucesso")
    checar(set(ok.to_dict()) == {"success", "provider", "model", "output",
                                 "raw_response", "usage", "error"},
           "contrato de saída padronizado")

    agente_ruim = Agente(provider="fake_ruim")
    erro = await agente_ruim.run("texto", prompt="analise")
    checar(erro.success is False and erro.output is None, "falha não levanta exceção")
    checar(erro.error.type == "RateLimitError", "erro tipado no contrato")

    class LLMTextoSolto(LLMFalso):
        nome = "fake_texto"

        async def complete(self, **kwargs):
            return ProviderResponse(text="isso não é json", usage={})

    REGISTRO["fake_texto"] = LLMTextoSolto
    quebrado = await Agente(provider="fake_texto").run("x", prompt="y", output_format="json")
    checar(quebrado.success is False and quebrado.error.type == "OutputParseError",
           "saída não-JSON vira OutputParseError com raw_response preservado")
    checar(quebrado.raw_response == "isso não é json", "resposta crua guardada para debug")

    texto = await Agente(provider="fake_texto").run("x", prompt="y", output_format="text")
    checar(texto.success is True and texto.output == "isso não é json",
           "output_format='text' aceita texto livre")

    print("\n=== 8. Fonte fixa vs dinâmica ===")

    config_fixa = PipelineVersao(
        version=1,
        source=FonteYouTube(
            query="essa query não deveria ser usada",
            video_ids=["https://www.youtube.com/watch?v=vid00000011"],
            fonte_fixa=True,
        ),
        steps=[
            PassoPipeline(type=TipoStep.YOUTUBE_SEARCH),
            PassoPipeline(type=TipoStep.RAW),
            PassoPipeline(type=TipoStep.REFINED),
        ],
        llm=ConfigLLM(provider="fake"),
        schedule=ConfigSchedule(),
    )
    run_fixa = PipelineRun(pipeline_name="Fonte fixa")
    await PipelineEngine(youtube=YouTubeFalso(), transcript=TranscricaoFalsa()).executar(
        config_fixa, run_fixa
    )

    checar(run_fixa.raw.get("search") is None,
           "fonte fixa nunca chama search_videos — 'search' do RAW continua vazio")
    checar([v["id"] for v in run_fixa.raw.get("videos", [])] == ["vid00000011"],
           "fonte fixa resolve exatamente o vídeo fixado, ignorando a query")

    config_fixa_vazia = config_fixa.model_copy(update={
        "source": FonteYouTube(query="", video_ids=[], fonte_fixa=True)
    })
    run_fixa_vazia = PipelineRun(pipeline_name="Fonte fixa sem vídeos")
    await PipelineEngine(youtube=YouTubeFalso(), transcript=TranscricaoFalsa()).executar(
        config_fixa_vazia, run_fixa_vazia
    )
    passo_busca = run_fixa_vazia.step(TipoStep.YOUTUBE_SEARCH)
    checar(passo_busca.status.value == "failed" and passo_busca.error.error_type == "NoVideosFound",
           "fonte fixa sem vídeo nenhum falha com mensagem clara")

    config_dinamica = config_fixa.model_copy(update={
        "source": FonteYouTube(
            query="review Ford Territory 2026",
            video_ids=["vid00000033"],
            fonte_fixa=False,
        )
    })
    run_dinamica = PipelineRun(pipeline_name="Fonte dinâmica")
    await PipelineEngine(youtube=YouTubeFalso(), transcript=TranscricaoFalsa()).executar(
        config_dinamica, run_dinamica
    )
    checar(run_dinamica.raw.get("search") is not None,
           "fonte dinâmica sempre executa a busca")
    ids_dinamica = {v["id"] for v in run_dinamica.raw.get("videos", [])}
    checar({"vid00000011", "vid00000022", "vid00000033"}.issubset(ids_dinamica),
           "fonte dinâmica mescla os resultados da busca com vídeos fixados manualmente (Modo B)")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações passaram.")
    print("=" * 62)

    shutil.rmtree(TEMP, ignore_errors=True)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
