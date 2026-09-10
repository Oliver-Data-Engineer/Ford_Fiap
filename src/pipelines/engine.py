"""
engine.py
O motor de execução das pipelines.

Esta é a **única** implementação da regra de negócio (Regra 4 da spec):
o Playground e as DAGs do Airflow chamam exatamente este código.

Fluxo:
    SOURCE → RAW → REFINED → AGENTE(+PROMPT) → TRUST → OUTPUT

Cada etapa registra status próprio no `PipelineRun`, e uma falha não apaga o
resultado das etapas anteriores (Regra 34): a execução termina como `partial`
com os dados já produzidos preservados.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.agents import Agente
from src.pipelines import refine
from src.pipelines.models import (
    ConfigLLM,
    FonteYouTube,
    PassoPipeline,
    PipelineRun,
    PipelineVersao,
    PromptVersao,
    StatusRun,
    StatusStep,
    StepResult,
    TipoEntrada,
    TipoStep,
    agora_iso,
)
from src.pipelines.store import obter_prompt
from src.youtube import (
    TranscriptAPI,
    TranscriptError,
    YouTubeAPI,
    YouTubeAPIError,
    extrair_video_id,
)
from src.youtube.client import periodo_rfc3339

logger = logging.getLogger(__name__)

# Callback opcional chamado ao fim de cada etapa — permite ao frontend
# acompanhar a execução em andamento.
Progresso = Callable[[PipelineRun], Awaitable[None] | None]

# Etapas que compõem a pipeline padrão quando nenhuma é declarada
STEPS_PADRAO: list[TipoStep] = [
    TipoStep.YOUTUBE_SEARCH,
    TipoStep.TRANSCRIPT,
    TipoStep.COMMENTS,
    TipoStep.RAW,
    TipoStep.REFINED,
    TipoStep.TRUST,
]

# Rótulo exibido na régua de progresso do Playground
ROTULOS: dict[TipoStep, str] = {
    TipoStep.YOUTUBE_SEARCH: "YouTube Search",
    TipoStep.TRANSCRIPT: "Transcrição",
    TipoStep.COMMENTS: "Comentários",
    TipoStep.RAW: "RAW",
    TipoStep.REFINED: "REFINED",
    TipoStep.AGENT: "Agente",
    TipoStep.TRUST: "TRUST",
    TipoStep.OUTPUT: "Output",
}

# Teto de caracteres de transcrição enviados ao LLM por chamada.
# Transcrições longas estouram a janela de contexto de modelos pequenos.
MAX_CARACTERES_TRANSCRICAO = 24_000
MAX_COMENTARIOS_POR_PROMPT = 200

# Coletas simultâneas de transcrição/comentários
CONCORRENCIA_COLETA = 4


def montar_steps(config: PipelineVersao) -> list[StepResult]:
    """
    Régua de progresso da configuração, com todas as etapas em `pending`.

    Usada pelo executor antes de agendar a execução, para que a resposta
    imediata ao frontend já traga o desenho completo da pipeline.
    """
    return [
        StepResult(type=p.type, name=p.name or ROTULOS.get(p.type, p.type.value))
        for p in PipelineEngine.resolver_passos(config)
    ]


class PipelineEngine:
    """
    Executor de pipelines.

    As dependências externas entram pelo construtor para que testes possam
    injetar dublês sem tocar na rede.
    """

    def __init__(
        self,
        youtube: YouTubeAPI | None = None,
        transcript: TranscriptAPI | None = None,
    ):
        self._youtube = youtube or YouTubeAPI()
        self._transcript = transcript or TranscriptAPI()

    # ─────────────────────────────────────────────────────────────────────────
    # Entrada principal
    # ─────────────────────────────────────────────────────────────────────────

    async def executar(
        self,
        config: PipelineVersao,
        run: PipelineRun,
        progresso: Progresso | None = None,
    ) -> PipelineRun:
        """
        Executa a configuração `config`, gravando o andamento em `run`.

        Devolve o próprio `run` preenchido. Nunca levanta exceção: falhas ficam
        registradas na etapa correspondente.
        """
        passos = self.resolver_passos(config)
        # Só remonta a régua se o chamador ainda não a preencheu (o executor
        # preenche antes de agendar a task, para o frontend já ter o desenho).
        if not run.steps:
            run.steps = montar_steps(config)
        run.status = StatusRun.EXECUTANDO
        run.agente = {"provider": config.llm.provider, "model": config.llm.model}
        await self._notificar(progresso, run)

        # Estado compartilhado entre etapas
        contexto: dict[str, Any] = {
            "fonte": config.source,
            "llm": config.llm,
            "video_ids": [],
            "raw": {"search": None, "videos": [], "channels": [], "transcripts": {}, "comments": {}},
            "refined": {},
            "trust": {"analises": [], "insights": []},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

        houve_falha = False

        for passo, resultado in zip(passos, run.steps):
            if not passo.enabled:
                resultado.pular("etapa desabilitada na configuração")
                await self._notificar(progresso, run)
                continue

            resultado.iniciar()
            await self._notificar(progresso, run)

            try:
                await self._executar_passo(passo, contexto, run, resultado)
            except Exception as exc:  # nenhuma etapa derruba a execução inteira
                logger.exception("[engine] etapa '%s' falhou", passo.type.value)
                resultado.falhar(type(exc).__name__, str(exc))

            if resultado.status == StatusStep.FALHOU:
                houve_falha = True
                # Etapas de coleta são tolerantes: seguimos com o que já existe.
                # Uma falha em REFINED/TRUST interrompe — não há o que continuar.
                if passo.type in (TipoStep.REFINED, TipoStep.RAW):
                    break

            await self._notificar(progresso, run)

        run.raw = contexto["raw"]
        run.refined = contexto["refined"]
        run.trust = contexto["trust"]
        run.usage = contexto["usage"]
        run.finished_at = agora_iso()

        concluidas = [s for s in run.steps if s.status == StatusStep.OK]
        if houve_falha:
            run.status = StatusRun.PARCIAL if concluidas else StatusRun.FALHOU
        else:
            run.status = StatusRun.OK

        await self._notificar(progresso, run)
        return run

    # ─────────────────────────────────────────────────────────────────────────
    # Despacho de etapas
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def resolver_passos(config: PipelineVersao) -> list[PassoPipeline]:
        """
        Normaliza a lista de etapas.

        Sem etapas declaradas, monta a pipeline padrão. Etapas `agent` sempre
        entram depois de REFINED, e TRUST/OUTPUT sempre no fim — a ordem das
        camadas é invariante da arquitetura, não configuração do usuário.
        """
        if not config.steps:
            return [PassoPipeline(type=t) for t in STEPS_PADRAO]

        declarados = [p for p in config.steps]
        agentes = [p for p in declarados if p.type == TipoStep.AGENT]
        outros = [p for p in declarados if p.type not in (TipoStep.AGENT, TipoStep.TRUST, TipoStep.OUTPUT)]

        ordem = {t: i for i, t in enumerate(STEPS_PADRAO)}
        outros.sort(key=lambda p: ordem.get(p.type, 99))

        finais = [p for p in declarados if p.type == TipoStep.OUTPUT]
        return outros + agentes + [PassoPipeline(type=TipoStep.TRUST)] + finais

    async def _executar_passo(
        self,
        passo: PassoPipeline,
        contexto: dict[str, Any],
        run: PipelineRun,
        resultado: StepResult,
    ) -> None:
        despacho = {
            TipoStep.YOUTUBE_SEARCH: self._passo_busca,
            TipoStep.TRANSCRIPT: self._passo_transcricao,
            TipoStep.COMMENTS: self._passo_comentarios,
            TipoStep.RAW: self._passo_raw,
            TipoStep.REFINED: self._passo_refined,
            TipoStep.AGENT: self._passo_agente,
            TipoStep.TRUST: self._passo_trust,
            TipoStep.OUTPUT: self._passo_output,
        }

        handler = despacho.get(passo.type)
        if handler is None:
            resultado.pular(f"tipo de etapa não suportado: {passo.type}")
            return

        await handler(passo, contexto, run, resultado)

    # ─────────────────────────────────────────────────────────────────────────
    # SOURCE — busca e resolução dos vídeos
    # ─────────────────────────────────────────────────────────────────────────

    async def _passo_busca(self, passo, contexto, run, resultado) -> None:
        fonte: FonteYouTube = contexto["fonte"]

        if fonte.fonte_fixa:
            await self._passo_busca_fixa(fonte, contexto, resultado)
            return

        # IDs informados diretamente (Modo B: URL colada no Playground)
        ids_diretos = [
            vid for vid in (extrair_video_id(v) for v in fonte.video_ids) if vid
        ]

        ids_busca: list[str] = []
        if fonte.query.strip():
            inicio, fim = self._janela_temporal(fonte)
            try:
                busca = await self._youtube.search_videos(
                    fonte.query,
                    max_results=fonte.max_results,
                    order=fonte.order,
                    published_after=inicio,
                    published_before=fim,
                    relevance_language=fonte.relevance_language,
                    region_code=fonte.region_code,
                    channel_id=fonte.channel_id,
                    video_duration=fonte.video_duration,
                )
            except YouTubeAPIError as exc:
                if not ids_diretos:
                    resultado.falhar("YouTubeAPIError", str(exc), retryable=exc.motivo == "timeout")
                    return
                # Com vídeos já escolhidos à mão, a busca falhar não é fatal
                logger.warning("[engine] busca falhou, seguindo com ids diretos: %s", exc)
                resultado.concluir(
                    f"busca indisponível ({exc.motivo}); {len(ids_diretos)} vídeo(s) informado(s) manualmente"
                )
                contexto["video_ids"] = ids_diretos
                return

            contexto["raw"]["search"] = busca
            ids_busca = [
                item["id"]["videoId"]
                for item in busca.get("items", [])
                if (item.get("id") or {}).get("videoId")
            ]

        # dedup preservando ordem: manuais primeiro, depois os da busca
        todos = list(dict.fromkeys(ids_diretos + ids_busca))
        if not todos:
            resultado.falhar(
                "NoVideosFound",
                "Nenhum vídeo encontrado — revise a query, os filtros de data ou informe uma URL.",
            )
            return

        # Metadados completos (statistics/contentDetails) em lote
        try:
            videos = await self._youtube.get_videos(todos)
            canais = await self._youtube.get_channels(
                [(v.get("snippet") or {}).get("channelId") for v in videos if v.get("snippet")]
            )
        except YouTubeAPIError as exc:
            resultado.falhar("YouTubeAPIError", str(exc), retryable=exc.motivo == "timeout")
            return

        contexto["raw"]["videos"] = videos
        contexto["raw"]["channels"] = canais
        contexto["video_ids"] = [v["id"] for v in videos if v.get("id")]

        resultado.concluir(f"{len(contexto['video_ids'])} vídeo(s) resolvido(s)")

    async def _passo_busca_fixa(
        self, fonte: FonteYouTube, contexto: dict[str, Any], resultado: StepResult
    ) -> None:
        """
        Fonte fixa: ignora `query` por completo e reprocessa exatamente os
        vídeos fixados — é o que garante que uma pipeline agendada com lista
        fixa nunca "vaze" para uma busca nova, mesmo que `query` tenha ficado
        preenchida por engano na configuração salva.
        """
        ids_fixos = [
            vid for vid in (extrair_video_id(v) for v in fonte.video_ids) if vid
        ]
        if not ids_fixos:
            resultado.falhar(
                "NoVideosFound",
                "Fonte fixa selecionada, mas nenhum vídeo foi fixado nesta pipeline.",
            )
            return

        try:
            videos = await self._youtube.get_videos(ids_fixos)
            canais = await self._youtube.get_channels(
                [(v.get("snippet") or {}).get("channelId") for v in videos if v.get("snippet")]
            )
        except YouTubeAPIError as exc:
            resultado.falhar("YouTubeAPIError", str(exc), retryable=exc.motivo == "timeout")
            return

        contexto["raw"]["videos"] = videos
        contexto["raw"]["channels"] = canais
        contexto["video_ids"] = [v["id"] for v in videos if v.get("id")]

        resultado.concluir(f"{len(contexto['video_ids'])} vídeo(s) fixado(s)")

    @staticmethod
    def _janela_temporal(fonte: FonteYouTube) -> tuple[str | None, str | None]:
        """Datas explícitas têm precedência; senão usa a janela em meses."""
        if fonte.published_after or fonte.published_before:
            return fonte.published_after, fonte.published_before
        if fonte.meses_atras:
            return periodo_rfc3339(fonte.meses_atras)
        return None, None

    # ─────────────────────────────────────────────────────────────────────────
    # RAW — coleta de transcrição e comentários
    # ─────────────────────────────────────────────────────────────────────────

    async def _passo_transcricao(self, passo, contexto, run, resultado) -> None:
        ids: list[str] = contexto["video_ids"]
        if not ids:
            resultado.pular("nenhum vídeo para transcrever")
            return

        fonte: FonteYouTube = contexto["fonte"]
        idiomas = tuple(fonte.idiomas_transcricao or ("pt", "en"))
        limite = asyncio.Semaphore(CONCORRENCIA_COLETA)

        async def buscar(vid: str):
            async with limite:
                try:
                    return vid, await self._transcript.get_transcript(vid, idiomas)
                except TranscriptError as exc:
                    return vid, exc

        coletados = await asyncio.gather(*(buscar(v) for v in ids))

        indisponiveis: list[str] = []
        dependencia_ausente = False
        for vid, dado in coletados:
            if isinstance(dado, TranscriptError):
                indisponiveis.append(vid)
                dependencia_ausente = dependencia_ausente or dado.motivo == "missing_dependency"
            else:
                contexto["raw"]["transcripts"][vid] = dado

        obtidas = len(contexto["raw"]["transcripts"])

        # Sem nenhuma transcrição e por falta do pacote → é erro de ambiente
        if obtidas == 0 and dependencia_ausente:
            resultado.falhar(
                "MissingDependency",
                "Pacote 'youtube-transcript-api' não instalado — a etapa de transcrição não pode rodar.",
            )
            return

        detalhe = f"{obtidas}/{len(ids)} transcrição(ões) obtida(s)"
        if indisponiveis:
            detalhe += f" — sem legenda: {', '.join(indisponiveis[:5])}"
            if len(indisponiveis) > 5:
                detalhe += f" (+{len(indisponiveis) - 5})"
        resultado.concluir(detalhe)

    async def _passo_comentarios(self, passo, contexto, run, resultado) -> None:
        ids: list[str] = contexto["video_ids"]
        if not ids:
            resultado.pular("nenhum vídeo para coletar comentários")
            return

        fonte: FonteYouTube = contexto["fonte"]
        limite = asyncio.Semaphore(CONCORRENCIA_COLETA)

        async def buscar(vid: str):
            async with limite:
                try:
                    return vid, await self._youtube.get_comments(
                        vid, max_results=fonte.max_comentarios_por_video
                    )
                except YouTubeAPIError as exc:
                    return vid, exc

        coletados = await asyncio.gather(*(buscar(v) for v in ids))

        falhas: list[str] = []
        for vid, dado in coletados:
            if isinstance(dado, YouTubeAPIError):
                falhas.append(vid)
            else:
                contexto["raw"]["comments"][vid] = dado

        total = sum(len(c) for c in contexto["raw"]["comments"].values())

        if not contexto["raw"]["comments"] and falhas:
            resultado.falhar(
                "YouTubeAPIError",
                f"Falha ao coletar comentários de todos os {len(falhas)} vídeo(s).",
                retryable=True,
            )
            return

        detalhe = f"{total} comentário(s) de {len(contexto['raw']['comments'])} vídeo(s)"
        if falhas:
            detalhe += f" — {len(falhas)} vídeo(s) com falha"
        resultado.concluir(detalhe)

    async def _passo_raw(self, passo, contexto, run, resultado) -> None:
        """
        Consolida a camada RAW.

        Não transforma nada: apenas confere que há dado e conta o volume.
        RAW é imutável a partir daqui (Regra 5).
        """
        raw = contexto["raw"]
        if not raw.get("videos"):
            resultado.falhar("EmptyRaw", "Nenhum metadado de vídeo foi coletado.")
            return

        resultado.concluir(
            f"{len(raw['videos'])} vídeo(s), "
            f"{len(raw['transcripts'])} transcrição(ões), "
            f"{sum(len(c) for c in raw['comments'].values())} comentário(s)"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # REFINED
    # ─────────────────────────────────────────────────────────────────────────

    async def _passo_refined(self, passo, contexto, run, resultado) -> None:
        refinado = refine.refinar(contexto["raw"])

        aprovados, descartados = refine.aplicar_filtros(
            refinado["videos"], contexto["fonte"]
        )
        refinado["videos"] = aprovados
        refinado["videos_descartados"] = descartados
        refinado["estatisticas"]["total_videos"] = len(aprovados)
        refinado["estatisticas"]["videos_descartados_por_filtro"] = len(descartados)

        # Transcrições/comentários de vídeos reprovados saem do conjunto
        aprovados_ids = {v["video_id"] for v in aprovados}
        refinado["transcripts"] = {
            k: v for k, v in refinado["transcripts"].items() if k in aprovados_ids
        }
        refinado["comments"] = {
            k: v for k, v in refinado["comments"].items() if k in aprovados_ids
        }

        contexto["refined"] = refinado

        if not aprovados:
            resultado.falhar(
                "AllVideosFiltered",
                f"Todos os {len(descartados)} vídeo(s) foram descartados pelos filtros mínimos.",
            )
            return

        stats = refinado["estatisticas"]
        detalhe = (
            f"{stats['total_videos']} vídeo(s), "
            f"{stats['total_comentarios']} comentário(s) únicos"
        )
        if stats["comentarios_descartados"]:
            detalhe += f" ({stats['comentarios_descartados']} duplicados/vazios removidos)"
        if descartados:
            detalhe += f", {len(descartados)} vídeo(s) fora dos filtros"
        resultado.concluir(detalhe)

    # ─────────────────────────────────────────────────────────────────────────
    # AGENTE → TRUST
    # ─────────────────────────────────────────────────────────────────────────

    async def _passo_agente(self, passo, contexto, run, resultado) -> None:
        if not passo.prompt_id:
            resultado.falhar("MissingPrompt", "Etapa 'agent' sem prompt_id definido.")
            return

        prompt = obter_prompt(passo.prompt_id)
        if prompt is None:
            resultado.falhar("PromptNotFound", f"Prompt '{passo.prompt_id}' não encontrado.")
            return

        try:
            versao = prompt.na_versao(passo.prompt_version)
        except ValueError as exc:
            resultado.falhar("PromptVersionNotFound", str(exc))
            return

        refinado = contexto["refined"]
        if not refinado.get("videos"):
            resultado.pular("sem dados REFINED para analisar")
            return

        llm: ConfigLLM = contexto["llm"]
        agente = Agente(
            provider=llm.provider,
            model=llm.model,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
        )

        entradas = self._montar_entradas(versao, refinado)
        if not entradas:
            resultado.pular(
                f"nenhum dado do tipo '{versao.input_type.value}' disponível nos vídeos coletados"
            )
            return

        analises: list[dict] = []
        falhas: list[str] = []

        for entrada in entradas:
            saida = await agente.run(
                entrada["dados"],
                prompt=versao.prompt,
                output_schema=versao.output_schema,
                output_format=versao.output_format,
            )

            # Rastreabilidade: origem do dado + identidade exata do agente (Regra 21)
            analise = {
                "prompt_id": prompt.id,
                "prompt_name": prompt.name,
                "prompt_version": versao.version,
                "input_type": versao.input_type.value,
                "escopo": entrada["escopo"],
                "source": entrada["source"],
                "agent": {
                    **agente.descrever(),
                    "prompt_id": prompt.id,
                    "prompt_version": versao.version,
                },
                "success": saida.success,
                "output": saida.output,
                "usage": saida.usage,
                "error": saida.error.__dict__ if saida.error else None,
            }
            analises.append(analise)

            if saida.success:
                self._somar_usage(contexto["usage"], saida.usage)
            else:
                falhas.append(f"{entrada['escopo']}: {saida.error.message if saida.error else 'erro'}")

        contexto["trust"]["analises"].extend(analises)

        sucessos = sum(1 for a in analises if a["success"])
        if sucessos == 0:
            primeira = analises[0].get("error") or {}
            resultado.falhar(
                primeira.get("type", "LLMError"),
                f"Todas as {len(analises)} chamada(s) ao agente falharam. "
                f"Primeira falha: {primeira.get('message', 'desconhecida')}",
                retryable=bool(primeira.get("retryable")),
            )
            return

        detalhe = f"'{prompt.name}' v{versao.version} — {sucessos}/{len(analises)} análise(s) concluída(s)"
        if falhas:
            detalhe += f"; {len(falhas)} falha(s)"
        resultado.concluir(detalhe)

    def _montar_entradas(self, versao: PromptVersao, refinado: dict) -> list[dict]:
        """
        Recorta o REFINED conforme o `input_type` do prompt.

        Devolve uma lista de {escopo, source, dados} — cada item é uma chamada
        ao LLM.  `source` é o bloco de rastreabilidade que acompanha o insight.
        """
        videos: list[dict] = refinado.get("videos", [])
        transcricoes: dict = refinado.get("transcripts", {})
        comentarios: dict = refinado.get("comments", {})

        tipo = versao.input_type

        # Uma única chamada com o conjunto inteiro
        if tipo == TipoEntrada.DATASET:
            corpo = {
                "videos": [
                    {
                        "video_id": v["video_id"],
                        "titulo": v["titulo"],
                        "canal": v["canal_nome"],
                        "views": v["views"],
                        "transcricao": self._recortar(
                            (transcricoes.get(v["video_id"]) or {}).get("clean_text", "")
                        ),
                        "comentarios": [
                            c["texto"] for c in comentarios.get(v["video_id"], [])
                        ][:MAX_COMENTARIOS_POR_PROMPT],
                    }
                    for v in videos
                ]
            }
            if not any(v["transcricao"] or v["comentarios"] for v in corpo["videos"]):
                return []

            return [{
                "escopo": "dataset",
                "source": {
                    "video_ids": [v["video_id"] for v in videos],
                    "total_videos": len(videos),
                },
                "dados": corpo,
            }]

        # Uma chamada por vídeo
        entradas: list[dict] = []
        for video in videos:
            vid = video["video_id"]
            transcricao = transcricoes.get(vid) or {}
            comentarios_video = comentarios.get(vid, [])

            corpo: dict[str, Any] = {
                "video_id": vid,
                "titulo": video["titulo"],
                "canal": video["canal_nome"],
            }
            source: dict[str, Any] = {"video_id": vid, "video_url": video.get("url")}

            if tipo in (TipoEntrada.TRANSCRIPT, TipoEntrada.VIDEO):
                texto = self._recortar(transcricao.get("clean_text", ""))
                if texto:
                    corpo["transcript"] = texto
                    source["transcript_language"] = transcricao.get("language")

            if tipo in (TipoEntrada.COMMENTS, TipoEntrada.VIDEO):
                selecionados = comentarios_video[:MAX_COMENTARIOS_POR_PROMPT]
                if selecionados:
                    corpo["comments"] = [
                        {"texto": c["texto"], "likes": c["likes"]} for c in selecionados
                    ]
                    source["comment_ids"] = [c["comment_id"] for c in selecionados]
                    source["refined_record_ids"] = [
                        c["refined_record_id"] for c in selecionados
                    ]

            # Sem transcrição nem comentários não há o que analisar neste vídeo
            if "transcript" not in corpo and "comments" not in corpo:
                continue

            entradas.append({"escopo": vid, "source": source, "dados": corpo})

        return entradas

    @staticmethod
    def _recortar(texto: str) -> str:
        """Trunca transcrições longas, sinalizando o corte no próprio texto."""
        if len(texto) <= MAX_CARACTERES_TRANSCRICAO:
            return texto
        return texto[:MAX_CARACTERES_TRANSCRICAO] + " […transcrição truncada]"

    @staticmethod
    def _somar_usage(acumulado: dict, usage: dict) -> None:
        for chave in ("prompt_tokens", "completion_tokens", "total_tokens"):
            valor = usage.get(chave)
            if isinstance(valor, int):
                acumulado[chave] = acumulado.get(chave, 0) + valor

    async def _passo_trust(self, passo, contexto, run, resultado) -> None:
        """
        Consolida a camada TRUST.

        Achata as análises em uma lista de insights, cada um carregando sua
        origem — é o que responde "de onde veio essa percepção?".
        """
        analises = contexto["trust"]["analises"]
        if not analises:
            resultado.pular("nenhuma análise de agente foi executada")
            return

        insights: list[dict] = []
        for analise in analises:
            if not analise["success"]:
                continue
            insights.append({
                "escopo": analise["escopo"],
                "prompt": analise["prompt_name"],
                "output": analise["output"],
                "source": analise["source"],
                "agent": analise["agent"],
            })

        contexto["trust"]["insights"] = insights
        contexto["trust"]["estatisticas"] = {
            "total_analises": len(analises),
            "analises_ok": len(insights),
            "analises_falhas": len(analises) - len(insights),
        }

        if not insights:
            resultado.falhar(
                "EmptyTrust", "Nenhuma análise do agente produziu resultado válido."
            )
            return

        resultado.concluir(f"{len(insights)} insight(s) estruturado(s)")

    async def _passo_output(self, passo, contexto, run, resultado) -> None:
        """
        Etapa terminal.

        A persistência do run é responsabilidade do executor — aqui só se
        confere que existe resultado a entregar.
        """
        insights = contexto["trust"].get("insights") or []
        if not insights:
            resultado.pular("sem insights para publicar")
            return
        resultado.concluir(f"{len(insights)} registro(s) prontos para consumo")

    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    async def _notificar(progresso: Progresso | None, run: PipelineRun) -> None:
        if progresso is None:
            return
        try:
            retorno = progresso(run)
            if asyncio.iscoroutine(retorno):
                await retorno
        except Exception:
            # Progresso é observabilidade: se falhar, a execução continua.
            logger.exception("[engine] callback de progresso falhou")
