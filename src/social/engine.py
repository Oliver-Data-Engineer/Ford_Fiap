"""
engine.py (Redes Sociais)
Motor da pipeline de Redes Sociais — 5 etapas: COLETA (gera posts sintéticos)
→ RAW → REFINED (limpeza/dedup) → ENRIQUECIMENTO (classificação de sentimento
real via LLM) → TRUST (agrega insights). Mesmo espírito do
`src/pipelines/engine.py` do YouTube, mas sem concorrência/rede real — só a
etapa de enriquecimento é assíncrona de verdade (faz uma chamada ao LLM).
"""
from __future__ import annotations

import logging

from src.agents import Agente
from src.pipelines.models import ConfigLLM, StatusRun, StatusStep, agora_iso
from src.social import geradores, warehouse
from src.social.enrichment import enriquecer_posts, garantir_prompt
from src.social.models import ConfigColetaSocial, SocialRun, StepResultSocial, TipoStepSocial

logger = logging.getLogger(__name__)

# Amostra máxima enriquecida por execução — mesmo espírito do
# `MAX_COMENTARIOS_ENRIQUECIMENTO_AUTOMATICO` do YouTube: uma chamada de LLM
# só, sobre um lote limitado, em vez de crescer sem fim com `quantidade`.
MAX_POSTS_ENRIQUECIMENTO = 60


class SocialPipelineEngine:
    async def executar(self, config: ConfigColetaSocial, llm: ConfigLLM, run: SocialRun) -> None:
        run.status = StatusRun.EXECUTANDO
        contexto: dict = {"posts": []}

        try:
            warehouse.garantir_tabelas()
        except Exception:
            # Mesmo cuidado de src/govmode/engine.py e src/scraper/engine.py:
            # sem BigQuery configurado (ou fora do ar), a pipeline roda
            # normalmente — só o warehouse fica indisponível pra leitura.
            logger.warning("[social.engine] não foi possível garantir as tabelas do warehouse (run %s)", run.id)

        despacho = {
            TipoStepSocial.COLETA: self._passo_coleta,
            TipoStepSocial.RAW: self._passo_raw,
            TipoStepSocial.REFINED: self._passo_refined,
            TipoStepSocial.ENRIQUECIMENTO: self._passo_enriquecimento,
            TipoStepSocial.TRUST: self._passo_trust,
        }

        for passo in run.steps:
            passo.iniciar()
            try:
                await despacho[passo.type](passo, contexto, config, llm, run)
            except Exception as exc:
                logger.exception("[social.engine] passo %s do run %s falhou", passo.type.value, run.id)
                passo.falhar("UnexpectedError", str(exc))

        run.finished_at = agora_iso()
        falhou = any(s.status == StatusStep.FALHOU for s in run.steps)
        sucesso = any(s.status == StatusStep.OK for s in run.steps)
        if falhou and sucesso:
            run.status = StatusRun.PARCIAL
        elif falhou:
            run.status = StatusRun.FALHOU
        else:
            run.status = StatusRun.OK

    # ── Etapas ───────────────────────────────────────────────────────────────

    async def _passo_coleta(
        self, passo: StepResultSocial, contexto: dict, config: ConfigColetaSocial, llm: ConfigLLM, run: SocialRun,
    ) -> None:
        posts = geradores.gerar_posts(config.termo_busca, config.redes, config.quantidade)
        contexto["posts"] = [p.model_dump() for p in posts]
        run.raw["posts"] = contexto["posts"]
        passo.concluir(f"{len(posts)} post(s) sintético(s) gerado(s)")

    async def _passo_raw(
        self, passo: StepResultSocial, contexto: dict, config: ConfigColetaSocial, llm: ConfigLLM, run: SocialRun,
    ) -> None:
        total = len(contexto["posts"])
        if not total:
            passo.pular("nenhum post coletado")
            return
        passo.concluir(f"{total} post(s) em RAW")

    async def _passo_refined(
        self, passo: StepResultSocial, contexto: dict, config: ConfigColetaSocial, llm: ConfigLLM, run: SocialRun,
    ) -> None:
        vistos: set[str] = set()
        limpos: list[dict] = []
        for p in contexto["posts"]:
            texto = (p.get("texto") or "").strip()
            if not texto or texto.lower() in vistos:
                continue
            vistos.add(texto.lower())
            limpos.append(p)

        contexto["posts"] = limpos
        run.refined["posts"] = limpos
        run.refined["estatisticas"] = {
            "total_bruto": len(run.raw.get("posts") or []),
            "total_refinado": len(limpos),
            "por_rede": self._contagem_por_rede(limpos),
        }
        if not limpos:
            passo.pular("nenhum post restou após a limpeza")
            return
        passo.concluir(f"{len(limpos)} post(s) após limpeza/dedup")

    @staticmethod
    def _contagem_por_rede(posts: list[dict]) -> dict[str, int]:
        contagem: dict[str, int] = {}
        for p in posts:
            rede = p.get("rede") or "desconhecida"
            contagem[rede] = contagem.get(rede, 0) + 1
        return contagem

    async def _passo_enriquecimento(
        self, passo: StepResultSocial, contexto: dict, config: ConfigColetaSocial, llm: ConfigLLM, run: SocialRun,
    ) -> None:
        posts = (run.refined.get("posts") or [])[:MAX_POSTS_ENRIQUECIMENTO]
        if not posts:
            passo.pular("nenhum post para enriquecer")
            return

        prompt = garantir_prompt()
        agente = Agente(
            provider=llm.provider, model=llm.model,
            temperature=llm.temperature, max_tokens=llm.max_tokens,
        )
        resultados = await enriquecer_posts(agente, posts, prompt=prompt)

        run.trust["analise"] = resultados
        run.trust["llm"] = agente.descrever()
        for r in resultados:
            if r.get("usage"):
                self._somar_usage(run.usage, r["usage"])

        sucesso = sum(1 for r in resultados if r["success"])
        if sucesso == 0:
            passo.falhar("SocialEnrichmentError", f"Todos os {len(resultados)} post(s) falharam na análise.")
            return
        passo.concluir(f"{sucesso}/{len(resultados)} post(s) analisado(s) pela IA")

    @staticmethod
    def _somar_usage(usage: dict, incremento: dict) -> None:
        for chave in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[chave] = (usage.get(chave) or 0) + (incremento.get(chave) or 0)

    async def _passo_trust(
        self, passo: StepResultSocial, contexto: dict, config: ConfigColetaSocial, llm: ConfigLLM, run: SocialRun,
    ) -> None:
        analises = run.trust.get("analise") or []
        sucesso = [a for a in analises if a.get("success")]
        if not sucesso:
            passo.pular("sem análise de IA bem-sucedida para agregar")
            return

        por_sentimento: dict[str, int] = {}
        for a in sucesso:
            sentimento = (a.get("struct") or {}).get("sentimento") or "desconhecido"
            por_sentimento[sentimento] = por_sentimento.get(sentimento, 0) + 1

        run.trust["insights"] = {
            "total_analisado": len(analises),
            "total_sucesso": len(sucesso),
            "por_sentimento": por_sentimento,
        }
        passo.concluir(f"{len(sucesso)} insight(s) agregados")
