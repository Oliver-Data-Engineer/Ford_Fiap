"""
executor.py
Orquestração das execuções do Scraper Mode — mesmo padrão de
`src/govmode/executor.py` (registro em memória + task assíncrona de
background + histórico persistido + polling por `run_id`), mas para
`ScraperEngine`/`ScraperRun`.

Cada passo do `ScraperEngine` já grava no BigQuery conforme avança; o que
este módulo persiste no fim é só o registro da execução em si
(`ScraperRun`, pra histórico/auditoria).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from src.pipelines.models import agora_iso
from src.scraper import store
from src.scraper.engine import ScraperEngine, montar_steps
from src.scraper.models import ScraperRun, SiteScraper, SiteScraperVersao, StatusRun

logger = logging.getLogger(__name__)

_EM_ANDAMENTO: dict[str, ScraperRun] = {}
_LOCK = threading.RLock()
_TAREFAS: set[asyncio.Task] = set()


def _registrar(run: ScraperRun) -> None:
    with _LOCK:
        _EM_ANDAMENTO[run.id] = run


def _desregistrar(run_id: str) -> None:
    with _LOCK:
        _EM_ANDAMENTO.pop(run_id, None)


def obter_status(run_id: str) -> ScraperRun | None:
    with _LOCK:
        em_memoria = _EM_ANDAMENTO.get(run_id)
    return em_memoria or store.obter_run(run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Execução
# ─────────────────────────────────────────────────────────────────────────────

async def executar_agora(
    config: SiteScraperVersao,
    *,
    site_id: str | None = None,
    site_name: str = "Scraper",
    site_version: int | None = None,
    origem: str = "manual",
    filtros: dict[str, str] | None = None,
) -> ScraperRun:
    """Executa a configuração e devolve o run completo, já persistido.
    `filtros` (opcional) — ex.: `{"marca": "Fiat"}` — substitui os
    marcadores do `url_template` da primeira etapa de cada objetivo, pra
    consultar o site por uma marca/modelo/ano específico."""
    run = ScraperRun(site_id=site_id, site_name=site_name, site_version=site_version, origem=origem)
    _registrar(run)

    motor = ScraperEngine()
    try:
        await motor.executar(config, run, filtros=filtros)
    finally:
        store.salvar_run(run)
        if site_id:
            store.registrar_execucao_no_site(site_id, run.status.value, run.finished_at or "")
        _desregistrar(run.id)

    logger.info("[scraper] run %s (%s) terminou com status '%s'", run.id, site_name, run.status.value)
    return run


def iniciar_em_background(
    config: SiteScraperVersao,
    *,
    site_id: str | None = None,
    site_name: str = "Scraper",
    site_version: int | None = None,
    origem: str = "manual",
    filtros: dict[str, str] | None = None,
) -> ScraperRun:
    """Dispara a execução e devolve imediatamente o run em estado
    `pending`. O chamador acompanha por `GET /scraper/runs/{run_id}`."""
    run = ScraperRun(
        site_id=site_id, site_name=site_name, site_version=site_version,
        origem=origem, steps=montar_steps(config),
    )
    _registrar(run)

    async def trabalho() -> None:
        motor = ScraperEngine()
        try:
            await motor.executar(config, run, filtros=filtros)
        except Exception as exc:
            logger.exception("[scraper] run %s abortou", run.id)
            run.status = StatusRun.FALHOU
            run.finished_at = agora_iso()
            run.resultado.setdefault("erro_execucao", str(exc))
        finally:
            store.salvar_run(run)
            if site_id:
                store.registrar_execucao_no_site(site_id, run.status.value, run.finished_at or "")
            await asyncio.sleep(2)
            _desregistrar(run.id)

    tarefa = asyncio.create_task(trabalho())
    _TAREFAS.add(tarefa)
    tarefa.add_done_callback(_TAREFAS.discard)

    return run


# ─────────────────────────────────────────────────────────────────────────────
# Execução de um site persistido
# ─────────────────────────────────────────────────────────────────────────────

def _config_do_site(site: SiteScraper, version: int | None) -> SiteScraperVersao:
    return site.na_versao(version)


def disparar_site(
    site_id: str, *, version: int | None = None, origem: str = "manual",
    filtros: dict[str, str] | None = None,
) -> ScraperRun:
    """Executa um site cadastrado em background (chamada pela API). Recusa
    sites ainda não registrados — evita rodar contra seletores nunca
    validados na tela de teste."""
    site = store.obter_site(site_id)
    if site is None:
        raise ValueError(f"Site do scraper '{site_id}' não encontrado.")
    if not site.registrado:
        raise ValueError(f"Site '{site.name}' ainda não foi registrado — rode o teste e registre antes de executar.")

    config = _config_do_site(site, version)
    return iniciar_em_background(
        config, site_id=site.id, site_name=site.name, site_version=config.version, origem=origem, filtros=filtros,
    )


def executar_pipeline_sync(site_id: str, *, version: int | None = None, origem: str = "airflow") -> dict[str, Any]:
    """
    Ponto de entrada **síncrono** — o que a DAG do Airflow chama
    (`src/scraper/dag_generator.py`). Roda a mesma `ScraperEngine` do
    painel e devolve um resumo serializável, adequado para XCom.
    """
    site = store.obter_site(site_id)
    if site is None:
        raise ValueError(f"Site do scraper '{site_id}' não encontrado.")
    if not site.registrado:
        raise ValueError(f"Site '{site.name}' ainda não foi registrado.")

    config = _config_do_site(site, version)
    run = asyncio.run(
        executar_agora(config, site_id=site.id, site_name=site.name, site_version=config.version, origem=origem)
    )

    resumo = {
        "run_id": run.id,
        "site_id": run.site_id,
        "site_version": run.site_version,
        "status": run.status.value,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "steps": [
            {"type": s.type.value, "status": s.status.value, "resumo": s.resumo,
             "error": s.error.model_dump() if s.error else None}
            for s in run.steps
        ],
        "progresso": run.progresso,
    }

    if run.status == StatusRun.FALHOU:
        falhas = [s for s in run.steps if s.error]
        detalhe = falhas[0].error.message if falhas else "motivo não registrado"
        raise RuntimeError(f"Execução {run.id} falhou: {detalhe}")

    return resumo
