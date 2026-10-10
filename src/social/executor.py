"""
executor.py (Redes Sociais)
Orquestração das execuções — mesma ideia de `src/pipelines/executor.py`, mas
simplificada (sem pipeline salva/schedule/DAG do Airflow nesta rodada, só
"executar agora"). O andamento fica em memória durante a execução; o
frontend consulta o status por `run_id` sem bloquear a interface.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from src.pipelines.models import ConfigLLM, StatusRun, agora_iso
from src.social import store, warehouse
from src.social.engine import SocialPipelineEngine
from src.social.models import ConfigColetaSocial, SocialRun

logger = logging.getLogger(__name__)

_EM_ANDAMENTO: dict[str, SocialRun] = {}
_LOCK = threading.RLock()
_TAREFAS: set[asyncio.Task] = set()


def _registrar(run: SocialRun) -> None:
    with _LOCK:
        _EM_ANDAMENTO[run.id] = run


def _desregistrar(run_id: str) -> None:
    with _LOCK:
        _EM_ANDAMENTO.pop(run_id, None)


def obter_status(run_id: str) -> SocialRun | None:
    with _LOCK:
        em_memoria = _EM_ANDAMENTO.get(run_id)
    return em_memoria or store.obter_run(run_id)


def iniciar_em_background(config: ConfigColetaSocial, llm: ConfigLLM) -> SocialRun:
    """Dispara a execução e devolve imediatamente o run em estado `pending`
    (régua completa já na resposta — o frontend desenha os 5 passos antes do
    primeiro começar). O chamador acompanha por `GET /social/runs/{run_id}`."""
    run = SocialRun(config=config)
    _registrar(run)

    async def trabalho() -> None:
        engine = SocialPipelineEngine()
        try:
            await engine.executar(config, llm, run)
        except Exception as exc:
            logger.exception("[social.executor] run %s abortou", run.id)
            run.status = StatusRun.FALHOU
            run.finished_at = agora_iso()
            run.trust.setdefault("erro_execucao", str(exc))
        finally:
            store.salvar_run(run)
            try:
                warehouse.registrar_execucao(run)
            except Exception:
                logger.exception("[social.executor] falha ao gravar warehouse do run %s", run.id)
            # O run permanece em memória por um instante para que o último
            # polling do frontend leia o estado final antes do fallback em disco.
            await asyncio.sleep(2)
            _desregistrar(run.id)

    tarefa = asyncio.create_task(trabalho())
    _TAREFAS.add(tarefa)
    tarefa.add_done_callback(_TAREFAS.discard)

    return run
