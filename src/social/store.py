"""
store.py (Redes Sociais)
Persistência do histórico de execuções — mesmo padrão de baixo nível de
`src/pipelines/store.py` (arquivo JSON em `data/`, escrita atômica via
arquivo temporário + `os.replace`, serializada por lock), mas próprio: não
reaproveita o arquivo/funções de lá porque são tipadas para `PipelineRun`
(YouTube), não `SocialRun`.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from src.config import settings
from src.social.models import SocialRun

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
ARQUIVO_RUNS = "social_runs.json"
MAX_RUNS_HISTORICO = 100


def _caminho() -> Path:
    return settings.data_dir / ARQUIVO_RUNS


def _ler() -> list[dict]:
    caminho = _caminho()
    if not caminho.is_file():
        return []
    try:
        conteudo = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[social.store] %s ilegível (%s) — tratando como vazio.", ARQUIVO_RUNS, exc)
        return []
    return conteudo if isinstance(conteudo, list) else []


def _escrever(registros: list[dict]) -> None:
    caminho = _caminho()
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(
        json.dumps(registros, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporario, caminho)


def _carregar() -> list[SocialRun]:
    itens: list[SocialRun] = []
    for bruto in _ler():
        try:
            itens.append(SocialRun.model_validate(bruto))
        except Exception as exc:
            logger.warning("[social.store] registro inválido em %s ignorado: %s", ARQUIVO_RUNS, exc)
    return itens


def salvar_run(run: SocialRun) -> SocialRun:
    with _LOCK:
        runs = _carregar()
        indice = next((i for i, r in enumerate(runs) if r.id == run.id), None)
        if indice is None:
            runs.append(run)
        else:
            runs[indice] = run
        runs.sort(key=lambda r: r.started_at, reverse=True)
        _escrever([r.model_dump(mode="json") for r in runs[:MAX_RUNS_HISTORICO]])
    return run


def obter_run(run_id: str) -> SocialRun | None:
    with _LOCK:
        runs = _carregar()
    return next((r for r in runs if r.id == run_id), None)


def listar_runs(limite: int = 50) -> list[SocialRun]:
    with _LOCK:
        runs = _carregar()
    runs.sort(key=lambda r: r.started_at, reverse=True)
    return runs[:limite]
