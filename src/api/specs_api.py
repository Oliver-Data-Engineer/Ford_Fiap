"""
specs_api.py
Router FastAPI com todos os endpoints RESTful de busca de especificações
técnicas de veículos.

Endpoints:
    GET  /specs/fabricantes
    GET  /specs/modelos?fabricante=
    GET  /specs/versoes?fabricante=&modelo=
    POST /specs/executar

Cada endpoint consulta as fontes CarrosWeb e iCarros em paralelo,
trata falhas parciais e devolve respostas padronizadas conforme os
requisitos 2–8 da spec vehicle-specs-search.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery
from pydantic import BaseModel

from src.bigquery_warehouse import registros_serializaveis
from src.config import settings
from src.pipelines.models import agora_iso
from src.scraper import schemas as scraper_schemas
from src.scraper import warehouse as scraper_warehouse
from src.utils import specs_cache
from src.utils.CarrosWeb import CarrosWeb
from src.utils.FichaCompleta import FichaCompleta
from src.utils.iCarros import CarrosWeb as ICarros  # mesma interface, URL base diferente
from src.utils.normalizador import categorizar_atributo, normalizar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/specs", tags=["specs"])

# Instâncias únicas dos scrapers — são stateless, seguro reutilizar entre requests
_carrosweb = CarrosWeb()
_icarros   = ICarros()
_fichacompleta = FichaCompleta()

# Mapa nome → instância, usado para iterar as fontes uniformemente
FONTES: dict[str, CarrosWeb] = {
    "carrosnaweb":   _carrosweb,
    "icarros":       _icarros,
    "fichacompleta": _fichacompleta,
}

# Timeouts (segundos)
TIMEOUT_LISTAGEM = 10   # fabricantes / modelos / versões
TIMEOUT_EXECUCAO = 30   # busca completa + extração de ficha

# ─────────────────────────────────────────────────────────────────────────────
# Health check de startup — ver `verificar_saude_todas_fontes()`, chamada
# pelo startup hook de main.py
# ─────────────────────────────────────────────────────────────────────────────

# Um catálogo real tem dezenas de fabricantes — abaixo disso é sinal de
# página quebrada/bloqueada servida como HTTP 200 (o bug relatado: o
# CarrosWeb "respondia OK" mas não trazia o catálogo de verdade).
LIMIAR_SAUDAVEL_FABRICANTES = 5

_SAUDE_FONTES: dict[str, bool] = {}


async def _verificar_saude_fonte(nome: str, fonte) -> bool:
    """
    Uma fonte é "saudável" se `obter_fabricantes()` funciona E devolve um
    catálogo de tamanho plausível — cobre tanto exceção (site fora do ar)
    quanto o caso "respondeu 200 mas é a página errada" (poucos ou nenhum
    fabricante extraído).
    """
    try:
        fabricantes = await asyncio.wait_for(fonte.obter_fabricantes(), timeout=TIMEOUT_LISTAGEM)
    except Exception as exc:
        logger.warning("[specs_api] health check — fonte '%s' falhou: %s", nome, exc)
        return False

    if len(fabricantes or []) < LIMIAR_SAUDAVEL_FABRICANTES:
        logger.warning(
            "[specs_api] health check — fonte '%s' devolveu só %d fabricante(s) "
            "(esperado dezenas) — tratando como indisponível até reiniciar o processo.",
            nome, len(fabricantes or []),
        )
        return False

    return True


async def verificar_saude_todas_fontes() -> None:
    """
    Chamada uma vez no startup do FastAPI (`main.py`). Roda o health check
    das 3 fontes em paralelo — nunca bloqueia o startup por mais que
    `TIMEOUT_LISTAGEM`.
    """
    nomes = list(FONTES.keys())
    resultados = await asyncio.gather(
        *(_verificar_saude_fonte(nome, fonte) for nome, fonte in FONTES.items())
    )
    _SAUDE_FONTES.update(zip(nomes, resultados))
    logger.info("[specs_api] health check de startup concluído: %s", _SAUDE_FONTES)


def _fontes_saudaveis() -> dict:
    """
    Fontes a usar nas consultas normais: todas, exceto as marcadas como
    não-saudáveis pelo health check de startup. Se TODAS estiverem
    marcadas como não-saudáveis (ex.: as 3 falharam, ou o health check
    nunca rodou), degrada pra usar todas mesmo assim — o health check
    nunca deve ser capaz de deixar um endpoint sem nenhuma fonte.
    """
    saudaveis = {nome: fonte for nome, fonte in FONTES.items() if _SAUDE_FONTES.get(nome, True)}
    return saudaveis or FONTES


# ─────────────────────────────────────────────────────────────────────────────
# Modelos Pydantic
# ─────────────────────────────────────────────────────────────────────────────

class ExecutarRequest(BaseModel):
    fabricante: str
    modelo:     str
    versao:     str
    atributos:  list[str] | None = None  # None ou [] → retorna tudo


# ─────────────────────────────────────────────────────────────────────────────
# Helper: executa coroutine com timeout e captura erros
# ─────────────────────────────────────────────────────────────────────────────

async def _executar(coro, timeout: float) -> tuple:
    """
    Retorna (resultado, None) em caso de sucesso,
    ou (None, mensagem_de_erro) em caso de falha/timeout.
    """
    try:
        resultado = await asyncio.wait_for(coro, timeout=timeout)
        return resultado, None
    except asyncio.TimeoutError:
        return None, "timeout"
    except Exception as exc:
        return None, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# GET /specs/fabricantes — Req 2
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/fabricantes")
async def listar_fabricantes(ignorar_cache: bool = False):
    """
    Retorna lista deduplicada de fabricantes consultando todas as fontes.

    Resposta 200:
        {"fabricantes": [{"id_busca": str, "nome_exibicao": str}, ...]}

    Resposta 503:
        {"erro": "Nenhuma fonte disponível no momento."}

    Cache local (`src/utils/specs_cache.py`): fabricante é catálogo
    estável — a primeira consulta bem-sucedida grava em disco, e as
    próximas devolvem direto do cache, sem depender do site de origem
    estar no ar. `?ignorar_cache=true` força uma consulta ao vivo mesmo
    com cache disponível (útil pra atualizar manualmente).
    """
    cache = specs_cache.obter_fabricantes()
    if cache and not ignorar_cache:
        return {"fabricantes": cache, "cache": True}

    listas: dict[str, list] = {}
    fontes_com_erro: list[str] = []

    # Consulta todas as fontes saudáveis em paralelo
    tarefas = {
        nome: _executar(fonte.obter_fabricantes(), TIMEOUT_LISTAGEM)
        for nome, fonte in _fontes_saudaveis().items()
    }
    for nome, coro in tarefas.items():
        resultado, erro = await coro
        if erro:
            logger.error("[specs_api] fabricantes – fonte '%s' falhou: %s", nome, erro)
            fontes_com_erro.append(nome)
        else:
            listas[nome] = resultado

    if not listas:
        if cache:  # ao vivo falhou de vez, mas tínhamos cache — melhor que 503
            return {"fabricantes": cache, "cache": True}
        raise HTTPException(
            status_code=503,
            detail={"erro": "Nenhuma fonte disponível no momento."},
        )

    # Deduplicação por nome_exibicao (case-insensitive), preserva primeira ocorrência
    vistos: set[str] = set()
    fabricantes: list[dict] = []
    for lista in listas.values():
        for fab in lista:
            chave = fab.get("nome_exibicao", "").strip().lower()
            if chave and chave not in vistos:
                vistos.add(chave)
                fabricantes.append(fab)

    fabricantes.sort(key=lambda f: f.get("nome_exibicao", "").lower())
    specs_cache.salvar_fabricantes(fabricantes)

    resposta: dict = {"fabricantes": fabricantes}
    if fontes_com_erro:
        resposta["fontes_com_erro"] = fontes_com_erro
    return resposta


# ─────────────────────────────────────────────────────────────────────────────
# GET /specs/modelos?fabricante= — Req 3
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/modelos")
async def listar_modelos(fabricante: str = Query(...), ignorar_cache: bool = False):
    """
    Retorna modelos de um fabricante consultando todas as fontes.

    Parâmetros query:
        fabricante (obrigatório)

    Resposta 200:
        {"modelos": [...], "fontes_com_erro": [...]}   (fontes_com_erro apenas se houve falha)

    Resposta 400:
        {"erro": "..."}   — parâmetro ausente/vazio

    Resposta 503:
        {"erro": "..."}   — todas as fontes falharam

    Cache local por fabricante (ver `/fabricantes`) — mesma lógica de
    escrita/leitura, `?ignorar_cache=true` força consulta ao vivo.
    """
    if not fabricante.strip():
        raise HTTPException(
            status_code=400,
            detail={"erro": "Parâmetro 'fabricante' é obrigatório."},
        )

    cache = specs_cache.obter_modelos(fabricante)
    if cache and not ignorar_cache:
        return {"modelos": cache, "cache": True}

    listas: dict[str, list] = {}
    fontes_com_erro: list[str] = []

    tarefas = {
        nome: _executar(fonte.obter_modelos(fabricante), TIMEOUT_LISTAGEM)
        for nome, fonte in _fontes_saudaveis().items()
    }
    for nome, coro in tarefas.items():
        resultado, erro = await coro
        if erro:
            logger.error("[specs_api] modelos – fonte '%s' falhou: %s", nome, erro)
            fontes_com_erro.append(nome)
        else:
            listas[nome] = resultado

    if not listas:
        if cache:
            return {"modelos": cache, "cache": True}
        raise HTTPException(
            status_code=503,
            detail={"erro": "Nenhuma fonte disponível no momento."},
        )

    # Consolida e deduplica por nome_exibicao
    vistos: set[str] = set()
    modelos: list[dict] = []
    for lista in listas.values():
        for mod in lista:
            chave = mod.get("nome_exibicao", "").strip().lower()
            if chave and chave not in vistos:
                vistos.add(chave)
                modelos.append(mod)

    modelos = modelos[:500]  # limite máximo definido nos requisitos
    specs_cache.salvar_modelos(fabricante, modelos)

    resposta: dict = {"modelos": modelos}
    if fontes_com_erro:
        resposta["fontes_com_erro"] = fontes_com_erro
    return resposta


# ─────────────────────────────────────────────────────────────────────────────
# GET /specs/versoes?fabricante=&modelo= — Req 4
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/versoes")
async def listar_versoes(
    fabricante: str = Query(...),
    modelo:     str = Query(...),
    ignorar_cache: bool = False,
):
    """
    Retorna versões de um modelo consultando todas as fontes.

    Parâmetros query:
        fabricante (obrigatório), modelo (obrigatório)

    Resposta 200:
        {"versoes": [...], "fontes_com_erro": [...]}

    Resposta 400 / 503 — mesma semântica de /modelos.

    Cache local por (fabricante, modelo) — mesma lógica de `/fabricantes`.
    """
    ausentes = [p for p, v in (("fabricante", fabricante), ("modelo", modelo)) if not v.strip()]
    if ausentes:
        raise HTTPException(
            status_code=400,
            detail={"erro": f"Parâmetros obrigatórios ausentes: {', '.join(ausentes)}."},
        )

    cache = specs_cache.obter_versoes(fabricante, modelo)
    if cache and not ignorar_cache:
        return {"versoes": cache, "cache": True}

    async def _versoes_de_fonte(nome: str, fonte) -> list:
        """Busca todas as páginas de versões numa única fonte."""
        url_base = fonte.montar_url_modelo(fabricante, modelo)
        urls = await fonte.obter_todas_urls_paginacao(url_base)
        versoes: list[dict] = []
        for url in urls:
            versoes.extend(await fonte.extrair_versoes(url))
        return versoes

    listas: dict[str, list] = {}
    fontes_com_erro: list[str] = []

    tarefas = {
        nome: _executar(_versoes_de_fonte(nome, fonte), TIMEOUT_LISTAGEM)
        for nome, fonte in _fontes_saudaveis().items()
    }
    for nome, coro in tarefas.items():
        resultado, erro = await coro
        if erro:
            logger.error("[specs_api] versoes – fonte '%s' falhou: %s", nome, erro)
            fontes_com_erro.append(nome)
        else:
            listas[nome] = resultado

    if not listas:
        if cache:
            return {"versoes": cache, "cache": True}
        raise HTTPException(
            status_code=503,
            detail={"erro": "Nenhuma fonte disponível no momento."},
        )

    versoes: list[dict] = []
    for lista in listas.values():
        versoes.extend(lista)

    versoes = versoes[:200]  # limite máximo
    specs_cache.salvar_versoes(fabricante, modelo, versoes)

    resposta: dict = {"versoes": versoes}
    if fontes_com_erro:
        resposta["fontes_com_erro"] = fontes_com_erro
    return resposta


# ─────────────────────────────────────────────────────────────────────────────
# POST /specs/executar — Req 8
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/executar")
async def executar_busca(body: ExecutarRequest, ignorar_cache: bool = False):
    """
    Executa a busca completa de especificações técnicas para um veículo.

    Body JSON:
        fabricante  (obrigatório)
        modelo      (obrigatório)
        versao      (obrigatório)
        atributos   (opcional) — lista de termos; vazio/ausente → retorna tudo

    Query:
        ignorar_cache (opcional) — força uma busca ao vivo mesmo com cache
        local disponível, mesmo contrato de `/fabricantes|modelos|versoes`.

    Resposta 200:
        {"especificacoes": [{"atributo": str, "valor": str, "fonte": str}, ...]}
        "cache": True (veio do cache local) ou "bigquery" (as 3 fontes
        falharam ao vivo e não havia cache local — devolvido do histórico
        já gravado no BigQuery) só aparecem quando aplicável.

    Resposta 400:
        {"erro": "..."}   — campos obrigatórios ausentes

    Cache local por (fabricante, modelo, versão) — mesma lógica de
    `/fabricantes|modelos|versoes` (ver `src/utils/specs_cache.py`), pra
    não raspar de novo o mesmo carro a cada chamada repetida.
    """
    # Valida campos obrigatórios
    ausentes = [
        campo for campo in ("fabricante", "modelo", "versao")
        if not getattr(body, campo, "").strip()
    ]
    if ausentes:
        raise HTTPException(
            status_code=400,
            detail={"erro": f"Campos obrigatórios ausentes: {', '.join(ausentes)}."},
        )

    cache = specs_cache.obter_ficha(body.fabricante, body.modelo, body.versao)
    if cache and not ignorar_cache:
        return {"especificacoes": cache, "cache": True}

    async def _ficha_de_fonte(nome: str, fonte) -> dict:
        """
        Localiza a versão solicitada na fonte e extrai sua ficha técnica.
        Retorna {"url_ficha": str, "specs": list[dict]} — specs vazia se não
        encontrar ou em caso de erro (url_ficha também vazia nesse caso).
        """
        try:
            # 1. Descobre a URL da versão solicitada
            url_base = fonte.montar_url_modelo(body.fabricante, body.modelo)
            urls_pagina = await fonte.obter_todas_urls_paginacao(url_base)

            versoes: list[dict] = []
            for url in urls_pagina:
                versoes.extend(await fonte.extrair_versoes(url))

            if not versoes:
                logger.warning(
                    "[specs_api] executar – nenhuma versão encontrada em '%s' "
                    "para %s %s", nome, body.fabricante, body.modelo,
                )
                return {"url_ficha": "", "specs": []}

            # 2. Encontra a versão mais próxima pelo nome (substring, case-insensitive)
            alvo = body.versao.strip().lower()
            versao_encontrada = next(
                (v for v in versoes if alvo in v.get("versao", "").lower()),
                versoes[0],  # fallback: usa a primeira versão disponível
            )

            url_ficha = versao_encontrada.get("url_ficha", "")
            if not url_ficha:
                logger.warning(
                    "[specs_api] executar – url_ficha vazia para versão '%s' em '%s'",
                    versao_encontrada.get("versao"), nome,
                )
                return {"url_ficha": "", "specs": []}

            # 3. Extrai a ficha técnica
            specs = await fonte.extrair_ficha_tecnica(url_ficha)
            return {"url_ficha": url_ficha, "specs": specs}

        except Exception as exc:
            logger.error(
                "[specs_api] executar – erro na fonte '%s': %s", nome, exc
            )
            return {"url_ficha": "", "specs": []}

    # Busca em paralelo em todas as fontes saudáveis, com timeout global
    tarefas = {
        nome: _executar(_ficha_de_fonte(nome, fonte), TIMEOUT_EXECUCAO)
        for nome, fonte in _fontes_saudaveis().items()
    }

    resultados_por_fonte: dict[str, list[dict]] = {}
    url_ficha_por_fonte: dict[str, str] = {}
    for nome, coro in tarefas.items():
        resultado, erro = await coro
        if erro:
            logger.error(
                "[specs_api] executar – fonte '%s' excedeu timeout ou falhou: %s",
                nome, erro,
            )
        else:
            resultados_por_fonte[nome] = (resultado or {}).get("specs") or []
            url_ficha_por_fonte[nome] = (resultado or {}).get("url_ficha") or ""

    # Normaliza — atributos_desejados=None ou [] → retorna tudo
    atributos = body.atributos if body.atributos else None
    especificacoes = normalizar(resultados_por_fonte, atributos_desejados=atributos)

    if especificacoes:
        specs_cache.salvar_ficha(body.fabricante, body.modelo, body.versao, especificacoes)
        _salvar_no_warehouse(body, especificacoes, url_ficha_por_fonte)
    elif not cache:
        # As fontes saudáveis falharam/vieram vazias ao vivo e não há cache
        # local — histórico do BigQuery como último recurso antes de
        # devolver lista vazia (evita perder um resultado só porque as
        # fontes ao vivo estão indisponíveis agora).
        historico = _buscar_historico_bigquery(body.fabricante, body.modelo, body.versao)
        if historico:
            return {"especificacoes": historico, "cache": "bigquery"}

    return {"especificacoes": especificacoes}


def _buscar_historico_bigquery(fabricante: str, modelo: str, versao: str) -> list[dict] | None:
    """
    Último recurso quando as fontes ao vivo falham/voltam vazias e não há
    cache local: reaproveita o histórico já gravado em `scraper_ficha_tecnica`
    (a mesma tabela que `_salvar_no_warehouse` alimenta) pra devolver a
    última ficha técnica conhecida em vez de uma lista vazia.

    Tolerante a BigQuery não configurado / tabela ainda inexistente /
    consulta falhando — nunca levanta, só devolve None (mesmo padrão de
    `scraper_api.py::_executar_consulta`).
    """
    if not settings.bigquery_configurado:
        return None

    tabela = scraper_warehouse.table_id(scraper_schemas.FICHA_TECNICA)
    params = [
        bigquery.ScalarQueryParameter("marca", "STRING", fabricante.strip().lower()),
        bigquery.ScalarQueryParameter("modelo", "STRING", modelo.strip().lower()),
        bigquery.ScalarQueryParameter("versao", "STRING", versao.strip().lower()),
    ]

    try:
        df = scraper_warehouse.query(
            f"""
            SELECT atributo, valor, site_id AS fonte
            FROM `{tabela}`
            WHERE LOWER(marca) = @marca AND LOWER(modelo) = @modelo AND LOWER(versao) = @versao
              AND coletado_em = (
                SELECT MAX(coletado_em) FROM `{tabela}`
                WHERE LOWER(marca) = @marca AND LOWER(modelo) = @modelo AND LOWER(versao) = @versao
              )
            """,
            params,
        )
    except Exception as exc:
        logger.info(
            "[specs_api] histórico BigQuery indisponível (%s): %s",
            type(exc).__name__, exc,
        )
        return None

    if df.empty:
        return None

    linhas = registros_serializaveis(df)
    for linha in linhas:
        linha["categoria"] = categorizar_atributo(linha["atributo"])
    return linhas or None


def _salvar_no_warehouse(
    body: ExecutarRequest,
    especificacoes: list[dict],
    url_ficha_por_fonte: dict[str, str],
) -> None:
    """
    Grava as especificações extraídas na mesma tabela BigQuery que o Scraper
    Mode usa (`scraper_ficha_tecnica`) — uma busca manual vira histórico
    igual a uma coleta automática. Melhor esforço: nunca deve derrubar a
    resposta da busca, só registra aviso se BigQuery não estiver configurado
    ou se a gravação falhar.
    """
    if not especificacoes:
        return
    if not settings.bigquery_configurado:
        logger.info("[specs_api] BigQuery não configurado — busca não será persistida.")
        return

    agora = agora_iso()
    linhas = [
        {
            "site_id": spec["fonte"],
            "site_nome": spec["fonte"],
            "marca": body.fabricante,
            "modelo": body.modelo,
            "versao": body.versao,
            "ano": None,
            "atributo": spec["atributo"],
            "valor": spec["valor"],
            "url_origem": (
                url_ficha_por_fonte.get(spec["fonte"])
                or getattr(FONTES.get(spec["fonte"]), "base_url", "")
                or "desconhecida"
            ),
            "coletado_em": agora,
        }
        for spec in especificacoes
    ]

    try:
        scraper_warehouse.upsert(scraper_schemas.FICHA_TECNICA, linhas)
    except Exception as exc:
        logger.warning("[specs_api] falha ao gravar ficha técnica no BigQuery: %s", exc)
