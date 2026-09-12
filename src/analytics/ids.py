"""
ids.py
IDs determinísticos usados para correlacionar as tabelas do warehouse.

`search_id` não é um campo novo — é sinônimo de `run_id` (cada
execução/busca do Playground já é um run_id único), documentado aqui para
não duplicar o conceito em outro lugar do código.

`transcript_id` não existia antes: a única chave de uma transcrição hoje é
o par `(run_id, video_id)` em `src/pipelines/db.py::TranscriptRow`.
Sintetizamos um id estável com a mesma técnica de
`refine.py::_hash_curto`/`refined_record_id`, para que `comentarios_coletados`
e `comentarios_analise` possam referenciar a transcrição de origem por uma
única coluna.
"""
from __future__ import annotations

import hashlib


def transcript_id(run_id: str, video_id: str) -> str:
    hash_curto = hashlib.sha1(f"{run_id}:{video_id}".encode("utf-8")).hexdigest()[:12]
    return f"tr_{hash_curto}"
