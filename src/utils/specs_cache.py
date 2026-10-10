"""
specs_cache.py
Cache local (JSON em disco, `data/specs_catalogo_cache.json`) do catálogo
do módulo specs (CarrosWeb + iCarros): fabricantes, modelos por fabricante
e versões por (fabricante, modelo).

Por quê: fabricante/modelo/versão são navegação — dado estável (a lista de
modelos de uma marca não muda de um dia pro outro), diferente da ficha
técnica em si (extraída sob demanda, sempre ao vivo). Raspar isso de novo
a cada dropdown aberto deixa a navegação inteira refém do site de
terceiro ficar no ar — na prática, `carrosnaweb` já bloqueou/deu timeout
repetidas vezes em produção. Com o cache, só a PRIMEIRA consulta de cada
combinação bate no site de verdade; as seguintes vêm do disco, mesmo que
o site de origem esteja indisponível depois.

Escrita atômica (arquivo temporário + `os.replace`) e serializada por um
lock — mesmo padrão de `src/pipelines/store.py`, mas aqui o arquivo é um
dict só (não uma lista de registros Pydantic), então os helpers são
próprios em vez de reaproveitar `_carregar`/`_salvar` (que assumem lista).

Sem expiração: fabricante/modelo/versão não mudam com frequência que
justifique TTL — se precisar forçar uma atualização, apagar
`data/specs_catalogo_cache.json` (ou a chave específica) resolve.
"""
from __future__ import annotations

import json
import logging
import os
import threading

from src.config import settings

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
ARQUIVO = "specs_catalogo_cache.json"


def _caminho():
    return settings.data_dir / ARQUIVO


def _ler() -> dict:
    caminho = _caminho()
    if not caminho.is_file():
        return {"fabricantes": None, "modelos": {}, "versoes": {}, "fichas": {}}

    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[specs_cache] %s ilegível (%s) — tratando como vazio.", ARQUIVO, exc)
        return {"fabricantes": None, "modelos": {}, "versoes": {}, "fichas": {}}

    if not isinstance(dados, dict):
        return {"fabricantes": None, "modelos": {}, "versoes": {}, "fichas": {}}
    dados.setdefault("fabricantes", None)
    dados.setdefault("modelos", {})
    dados.setdefault("versoes", {})
    dados.setdefault("fichas", {})
    return dados


def _escrever(dados: dict) -> None:
    caminho = _caminho()
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporario, caminho)


def _chave_fabricante(fabricante: str) -> str:
    return fabricante.strip().lower()


def _chave_modelo(fabricante: str, modelo: str) -> str:
    return f"{fabricante.strip().lower()}::{modelo.strip().lower()}"


def _chave_ficha(fabricante: str, modelo: str, versao: str) -> str:
    return f"{fabricante.strip().lower()}::{modelo.strip().lower()}::{versao.strip().lower()}"


# ─────────────────────────────────────────────────────────────────────────────
# Fabricantes
# ─────────────────────────────────────────────────────────────────────────────

def obter_fabricantes() -> list[dict] | None:
    """`None` = nunca cacheado ainda (diferente de `[]`, que não deveria
    acontecer de verdade — uma resposta vazia nunca é gravada, ver
    `salvar_fabricantes`, pra não "travar" um resultado vazio por causa de
    um bloqueio temporário do site de origem)."""
    with _LOCK:
        return _ler().get("fabricantes")


def salvar_fabricantes(fabricantes: list[dict]) -> None:
    if not fabricantes:
        return
    with _LOCK:
        dados = _ler()
        dados["fabricantes"] = fabricantes
        _escrever(dados)


# ─────────────────────────────────────────────────────────────────────────────
# Modelos (por fabricante)
# ─────────────────────────────────────────────────────────────────────────────

def obter_modelos(fabricante: str) -> list[dict] | None:
    with _LOCK:
        return _ler()["modelos"].get(_chave_fabricante(fabricante))


def salvar_modelos(fabricante: str, modelos: list[dict]) -> None:
    if not modelos:
        return
    with _LOCK:
        dados = _ler()
        dados["modelos"][_chave_fabricante(fabricante)] = modelos
        _escrever(dados)


# ─────────────────────────────────────────────────────────────────────────────
# Versões (por fabricante + modelo)
# ─────────────────────────────────────────────────────────────────────────────

def obter_versoes(fabricante: str, modelo: str) -> list[dict] | None:
    with _LOCK:
        return _ler()["versoes"].get(_chave_modelo(fabricante, modelo))


def salvar_versoes(fabricante: str, modelo: str, versoes: list[dict]) -> None:
    if not versoes:
        return
    with _LOCK:
        dados = _ler()
        dados["versoes"][_chave_modelo(fabricante, modelo)] = versoes
        _escrever(dados)


# ─────────────────────────────────────────────────────────────────────────────
# Ficha técnica (por fabricante + modelo + versão)
# ─────────────────────────────────────────────────────────────────────────────
#
# Diferente de fabricantes/modelos/versões (catálogo/navegação), a ficha
# técnica é o dado final buscado sob demanda — mas raspar de novo a cada
# chamada repetida pro mesmo (fabricante, modelo, versão) tem o mesmo
# problema de depender do site de origem estar no ar toda vez. Mesmo
# padrão de cache das outras 3 chaves: primeira busca bem-sucedida grava,
# as seguintes vêm do disco.

def obter_ficha(fabricante: str, modelo: str, versao: str) -> list[dict] | None:
    with _LOCK:
        return _ler()["fichas"].get(_chave_ficha(fabricante, modelo, versao))


def salvar_ficha(fabricante: str, modelo: str, versao: str, especificacoes: list[dict]) -> None:
    if not especificacoes:
        return
    with _LOCK:
        dados = _ler()
        dados["fichas"][_chave_ficha(fabricante, modelo, versao)] = especificacoes
        _escrever(dados)
