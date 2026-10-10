"""
scraper_api.py
Router FastAPI do Scraper Mode — sites cadastrados (cadeias de etapas por
objetivo, auto-discovery de seletores, tela de teste, registro) e leitura
dos dados coletados (ficha técnica/anúncios/comentários) no BigQuery.

Endpoints:
    GET    /scraper/config                        providers/modelos disponíveis
    GET    /scraper/sites
    POST   /scraper/sites
    GET    /scraper/runs/{run_id}                 status de uma execução
    GET    /scraper/sites/{id}
    PUT    /scraper/sites/{id}
    DELETE /scraper/sites/{id}
    POST   /scraper/sites/{id}/status
    GET    /scraper/sites/{id}/versions

    POST   /scraper/sites/{id}/objetivos/{objetivo}/etapas                    adiciona etapa
    PUT    /scraper/sites/{id}/objetivos/{objetivo}/etapas/{ordem}            edita etapa
    DELETE /scraper/sites/{id}/objetivos/{objetivo}/etapas/{ordem}            remove etapa
    POST   /scraper/sites/{id}/objetivos/{objetivo}/etapas/reorder            reordena
    POST   /scraper/sites/{id}/objetivos/{objetivo}/etapas/{ordem}/discover   IA (rascunho)
    POST   /scraper/sites/{id}/objetivos/{objetivo}/etapas/{ordem}/test       determinístico
    POST   /scraper/sites/{id}/objetivos/{objetivo}/etapas/{ordem}/detectar-filtros

    POST   /scraper/sites/{id}/discover           auto-discovery (todo objetivo × toda etapa)
    POST   /scraper/sites/{id}/register            exige toda etapa de todo objetivo testada

    POST   /scraper/sites/{id}/run
    GET    /scraper/sites/{id}/runs
    POST   /scraper/sites/{id}/generate-dag
    GET    /scraper/sites/{id}/dag

    GET    /scraper/dados/ficha-tecnica
    GET    /scraper/dados/anuncios
    GET    /scraper/dados/comentarios
    GET    /scraper/resumo

Atenção à ordem de declaração: `/runs/{run_id}` precede `/sites/{site_id}`,
mesmo cuidado de `src/api/gov_api.py`/`src/api/pipelines_api.py`.

`/discover`, `/test` e `/detectar-filtros` (site e etapa) não dependem do
BigQuery (só Playwright + LLM); `/register`/`/run`/`/dados/*` dependem —
503 sem `GOOGLE_APPLICATION_CREDENTIALS`, mesmo contrato de
`gov_api.py::_exigir_configurado`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from google.cloud import bigquery
from pydantic import BaseModel

from src.agents import listar_providers
from src.bigquery_warehouse import registros_serializaveis
from src.config import ENV_VAR_POR_PROVIDER, settings
from src.pipelines.models import ErroStep, StatusPipeline, agora_iso
from src.scraper import browser, discovery, executor, extractor, schemas, store, url_utils, warehouse
from src.scraper.dag_generator import descrever_schedule, gerar_codigo, gerar_dag, montar_cron, montar_dag_id
from src.scraper.modelos_llm import MODELOS_CURADOS
from src.scraper.models import (
    EtapaScraper,
    FiltroParam,
    MapaSeletor,
    SiteScraper,
    SiteScraperEntrada,
    TipoObjetivoScraper,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scraper", tags=["scraper"])

_EXTRATORES = {
    TipoObjetivoScraper.FICHA_TECNICA: extractor.extrair_ficha_tecnica,
    TipoObjetivoScraper.ANUNCIOS: extractor.extrair_anuncios,
    TipoObjetivoScraper.COMENTARIOS: extractor.extrair_comentarios,
}

_TABELA_POR_OBJETIVO = {
    TipoObjetivoScraper.FICHA_TECNICA: schemas.FICHA_TECNICA,
    TipoObjetivoScraper.ANUNCIOS: schemas.ANUNCIOS,
    TipoObjetivoScraper.COMENTARIOS: schemas.COMENTARIOS,
}


class StatusRequest(BaseModel):
    status: StatusPipeline


class ExecutarRequest(BaseModel):
    version: int | None = None
    filtros: dict[str, str] | None = None   # ex.: {"marca": "Fiat"} — substitui o url_template da etapa 0


class DiscoverRequest(BaseModel):
    objetivos: list[TipoObjetivoScraper] | None = None


class EtapaEntrada(BaseModel):
    url_amostra: str = ""
    campo_herdado: str | None = None


class EtapaAtualizacao(BaseModel):
    url_amostra: str | None = None
    selectors: dict[str, Any] | None = None
    campo_herdado: str | None = None
    filtros: list[dict[str, str]] | None = None
    url_template: str | None = None


class EtapaTestRequest(BaseModel):
    selectors: dict[str, Any] | None = None
    url: str | None = None
    campo_herdado: str | None = None


class ReorderRequest(BaseModel):
    ids: list[str]


class DetectarFiltrosRequest(BaseModel):
    url: str | None = None


def _validar_objetivos(objetivos: list[TipoObjetivoScraper]) -> None:
    if TipoObjetivoScraper.COMENTARIOS in objetivos and TipoObjetivoScraper.ANUNCIOS not in objetivos:
        raise HTTPException(
            400,
            detail={"erro": "O objetivo 'comentarios' exige 'anuncios' também selecionado nesse site."},
        )


def _resumo_site(site: SiteScraper) -> dict:
    dados = site.model_dump(mode="json")
    dados["schedule_legivel"] = descrever_schedule(site.schedule)
    dados["cron"] = montar_cron(site.schedule)
    dados["dag_id_sugerido"] = montar_dag_id(site)
    dados["objetivos_prontos"] = [o.value for o in site.objetivos_prontos()]
    return dados


def _exigir_site(site_id: str) -> SiteScraper:
    site = store.obter_site(site_id)
    if site is None:
        raise HTTPException(404, detail={"erro": f"Site '{site_id}' não encontrado."})
    return site


def _exigir_etapa(site: SiteScraper, objetivo: TipoObjetivoScraper, ordem: int) -> tuple[MapaSeletor, EtapaScraper]:
    mapa = site.seletores.get(objetivo.value)
    etapa = next((e for e in (mapa.etapas if mapa else []) if e.ordem == ordem), None)
    if mapa is None or etapa is None:
        raise HTTPException(404, detail={"erro": f"Etapa {ordem} do objetivo '{objetivo.value}' não encontrada."})
    return mapa, etapa


def _obter_ou_criar_mapa(site: SiteScraper, objetivo: TipoObjetivoScraper) -> MapaSeletor:
    mapa = site.seletores.get(objetivo.value)
    if mapa is None:
        mapa = MapaSeletor(objetivo=objetivo)
        site.seletores[objetivo.value] = mapa
    return mapa


def _exigir_configurado() -> None:
    if not settings.bigquery_configurado:
        raise HTTPException(
            503,
            detail={"erro": "BigQuery não configurado no servidor (dados coletados indisponíveis)."},
        )


def _executar_consulta(sql: str, params: list[bigquery.ScalarQueryParameter] | None = None) -> list[dict]:
    """Como `gov_api._executar_catalogo` — tolera tabela ainda inexistente
    (nenhuma execução rodou ainda) devolvendo lista vazia em vez de 502."""
    try:
        df = warehouse.query(sql, params)
    except Exception as exc:
        logger.info("[scraper_api] consulta sem dado ainda (%s): %s", type(exc).__name__, exc)
        return []
    return registros_serializaveis(df)


# ─────────────────────────────────────────────────────────────────────────────
# Config — providers/modelos disponíveis (pro dropdown do cadastro)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/config")
async def obter_config():
    """Mesmo formato de `GET /youtube/config` — por provider, se está
    configurado (tem credencial) + a lista curada de modelos conhecidos
    (`src/scraper/modelos_llm.py`; sempre vazia pro Ollama, que roda local
    sem catálogo fixo). O frontend sempre soma uma opção "Custom/outro..."
    além dessa lista — modelos novos que saírem depois não ficam
    bloqueados."""
    providers = []
    for item in listar_providers():
        nome = item["provider"]
        providers.append({
            **item,
            "configurado": settings.provider_configurado(nome),
            "env_var": ENV_VAR_POR_PROVIDER.get(nome, ""),
            "modelos": MODELOS_CURADOS.get(nome, []),
        })

    return {"providers": providers}


# ─────────────────────────────────────────────────────────────────────────────
# Sites — coleção
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sites")
async def listar():
    return {"sites": [_resumo_site(s) for s in store.listar_sites()]}


@router.post("/sites", status_code=201)
async def criar(body: SiteScraperEntrada):
    if not body.name.strip():
        raise HTTPException(400, detail={"erro": "O campo 'name' é obrigatório."})
    if not body.url_base.strip():
        raise HTTPException(400, detail={"erro": "O campo 'url_base' é obrigatório."})
    if not body.objetivos:
        raise HTTPException(400, detail={"erro": "Selecione ao menos um objetivo (ficha técnica/anúncios/comentários)."})
    _validar_objetivos(body.objetivos)

    faltando = [o.value for o in body.objetivos if not body.amostra_urls.get(o.value)]
    if faltando:
        raise HTTPException(400, detail={"erro": f"Informe a URL de amostra de: {', '.join(faltando)}."})

    site = store.criar_site(body)
    return _resumo_site(site)


# ─────────────────────────────────────────────────────────────────────────────
# Execuções (rota fixa — precede /sites/{site_id})
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}")
async def status_execucao(run_id: str):
    run = executor.obter_status(run_id)
    if run is None:
        raise HTTPException(404, detail={"erro": f"Execução '{run_id}' não encontrada."})
    return run.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# Sites — item
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sites/{site_id}")
async def obter(site_id: str):
    return _resumo_site(_exigir_site(site_id))


@router.put("/sites/{site_id}")
async def atualizar(site_id: str, body: SiteScraperEntrada):
    if not body.objetivos:
        raise HTTPException(400, detail={"erro": "Selecione ao menos um objetivo."})
    _validar_objetivos(body.objetivos)

    site = store.atualizar_site(site_id, body)
    if site is None:
        raise HTTPException(404, detail={"erro": f"Site '{site_id}' não encontrado."})
    return _resumo_site(site)


@router.delete("/sites/{site_id}")
async def remover(site_id: str):
    if not store.remover_site(site_id):
        raise HTTPException(404, detail={"erro": f"Site '{site_id}' não encontrado."})
    return {"removido": site_id}


@router.post("/sites/{site_id}/status")
async def alterar_status(site_id: str, body: StatusRequest):
    site = _exigir_site(site_id)
    site.status = body.status
    return _resumo_site(store.salvar_site(site))


@router.get("/sites/{site_id}/versions")
async def listar_versoes(site_id: str):
    site = _exigir_site(site_id)
    return {
        "site_id": site.id,
        "version": site.version,
        "versions": [v.model_dump(mode="json") for v in site.versions],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Etapas — CRUD por objetivo (cadeia de navegação, ex.: busca → detalhe)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sites/{site_id}/objetivos/{objetivo}/etapas", status_code=201)
async def adicionar_etapa(site_id: str, objetivo: TipoObjetivoScraper, body: EtapaEntrada):
    site = _exigir_site(site_id)
    if objetivo not in site.objetivos:
        raise HTTPException(400, detail={"erro": f"Objetivo '{objetivo.value}' não está habilitado neste site."})

    mapa = _obter_ou_criar_mapa(site, objetivo)
    nova = EtapaScraper(ordem=len(mapa.etapas), url_amostra=body.url_amostra, campo_herdado=body.campo_herdado)
    mapa.etapas.append(nova)
    site.registrado = False

    store.salvar_site(site)
    return {"etapa": nova.model_dump(mode="json"), "site": _resumo_site(site)}


@router.put("/sites/{site_id}/objetivos/{objetivo}/etapas/{ordem}")
async def editar_etapa(site_id: str, objetivo: TipoObjetivoScraper, ordem: int, body: EtapaAtualizacao):
    site = _exigir_site(site_id)
    _, etapa = _exigir_etapa(site, objetivo, ordem)

    # Só os três campos abaixo afetam o RESULTADO da extração — filtros/
    # url_template são metadados de navegação (§14.11), não mudam o que o
    # seletor lê. Reenviar os mesmos valores já testados (ex.: o botão
    # "Salvar" logo depois de um "Testar" bem-sucedido) não deve derrubar
    # `validado_em`; só uma mudança de verdade nesses campos invalida.
    afeta_extracao = (
        (body.url_amostra is not None and body.url_amostra != etapa.url_amostra)
        or (body.selectors is not None and body.selectors != etapa.selectors)
        or (body.campo_herdado is not None and (body.campo_herdado or None) != etapa.campo_herdado)
    )

    if body.url_amostra is not None:
        etapa.url_amostra = body.url_amostra
    if body.selectors is not None:
        etapa.selectors = body.selectors
    if body.campo_herdado is not None:
        etapa.campo_herdado = body.campo_herdado or None
    if body.filtros is not None:
        etapa.filtros = [FiltroParam(**f) for f in body.filtros]
        # Recalculado sempre que a lista de filtros muda — cobre tanto
        # filtro detectado (query string) quanto filtro adicionado à mão
        # (posicional no path, ex.: ".../busca/ford/2026") — ver
        # `url_utils.montar_template`. Ignora `body.url_template` quando
        # `filtros` também vem no corpo: o cálculo daqui é a fonte de
        # verdade, nunca um valor calculado no frontend.
        etapa.url_template = url_utils.montar_template(etapa.url_amostra, etapa.filtros)
    elif body.url_template is not None:
        etapa.url_template = body.url_template

    if afeta_extracao:
        etapa.validado_em = None  # mudança real na extração invalida o teste anterior desta etapa
        site.registrado = False

    store.salvar_site(site)
    return {"etapa": etapa.model_dump(mode="json"), "site": _resumo_site(site)}


@router.delete("/sites/{site_id}/objetivos/{objetivo}/etapas/{ordem}")
async def remover_etapa(site_id: str, objetivo: TipoObjetivoScraper, ordem: int):
    site = _exigir_site(site_id)
    mapa, _ = _exigir_etapa(site, objetivo, ordem)

    mapa.etapas = [e for e in mapa.etapas if e.ordem != ordem]
    for indice, etapa in enumerate(sorted(mapa.etapas, key=lambda e: e.ordem)):
        etapa.ordem = indice
        etapa.validado_em = None  # a cadeia mudou de forma — precisa testar de novo
    site.registrado = False

    store.salvar_site(site)
    return _resumo_site(site)


@router.post("/sites/{site_id}/objetivos/{objetivo}/etapas/reorder")
async def reordenar_etapas(site_id: str, objetivo: TipoObjetivoScraper, body: ReorderRequest):
    site = _exigir_site(site_id)
    mapa = site.seletores.get(objetivo.value)
    if mapa is None:
        raise HTTPException(404, detail={"erro": f"Objetivo '{objetivo.value}' não tem etapas."})

    por_id = {e.id: e for e in mapa.etapas}
    if set(body.ids) != set(por_id):
        raise HTTPException(400, detail={"erro": "A lista de ids não bate com as etapas existentes."})

    for indice, etapa_id in enumerate(body.ids):
        etapa = por_id[etapa_id]
        etapa.ordem = indice
        etapa.validado_em = None
    site.registrado = False

    store.salvar_site(site)
    return _resumo_site(site)


@router.post("/sites/{site_id}/objetivos/{objetivo}/etapas/{ordem}/discover")
async def descobrir_etapa_endpoint(site_id: str, objetivo: TipoObjetivoScraper, ordem: int):
    """Roda o LLM só nesta etapa — mescla o resultado como RASCUNHO (não
    valida, não registra; a validação acontece em `.../test`)."""
    site = _exigir_site(site_id)
    mapa, etapa = _exigir_etapa(site, objetivo, ordem)

    if not etapa.url_amostra:
        raise HTTPException(400, detail={"erro": "Informe a URL de amostra desta etapa antes de mapear."})

    ultima = ordem == max(e.ordem for e in mapa.etapas)
    resultado = await discovery.descobrir_etapa(site, objetivo, etapa, ultima=ultima)

    if isinstance(resultado, ErroStep):
        return {"sucesso": False, "erro": resultado.model_dump()}

    mapa.etapas = [resultado if e.ordem == ordem else e for e in mapa.etapas]
    store.salvar_site(site)
    return {"sucesso": True, "selectors": resultado.selectors}


@router.post("/sites/{site_id}/objetivos/{objetivo}/etapas/{ordem}/test")
async def testar_etapa(site_id: str, objetivo: TipoObjetivoScraper, ordem: int, body: EtapaTestRequest):
    """Extração síncrona e determinística (sem LLM) de UMA etapa — é o
    gate de validação antes do objetivo poder ser registrado (toda etapa
    da cadeia precisa passar por aqui)."""
    site = _exigir_site(site_id)
    mapa, etapa = _exigir_etapa(site, objetivo, ordem)

    selectors = body.selectors if body.selectors is not None else etapa.selectors
    if not selectors:
        raise HTTPException(400, detail={"erro": "Nenhum seletor informado nem previamente descoberto para esta etapa."})

    url = body.url or etapa.url_amostra
    if not url:
        raise HTTPException(400, detail={"erro": "Nenhuma URL informada nem configurada como amostra desta etapa."})

    html = await browser.buscar_html(url)
    if html is None:
        raise HTTPException(502, detail={"erro": f"Não foi possível carregar '{url}'.", "motivo": "network"})

    ultima = ordem == max(e.ordem for e in mapa.etapas)
    if ultima:
        linhas = _EXTRATORES[objetivo](html, selectors, url)
    else:
        linhas = extractor.extrair_linhas_genericas(html, selectors, url)

    sucesso = len(linhas) > 0

    etapa.selectors = selectors
    etapa.url_amostra = url
    if body.campo_herdado is not None:
        etapa.campo_herdado = body.campo_herdado or None
    if sucesso:
        etapa.validado_em = agora_iso()

    store.salvar_site(site)

    # Resumo em linguagem natural, gerado pela IA, sobre a amostra REAL já
    # extraída (determinística) — o usuário confirma rapidamente se bate
    # com o que esperava desta etapa, além de olhar a tabela crua abaixo.
    resumo_ia = await discovery.gerar_resumo_confirmacao(objetivo, linhas, site.llm)

    return {"sucesso": sucesso, "total": len(linhas), "preview": linhas[:20], "resumo_ia": resumo_ia}


@router.post("/sites/{site_id}/objetivos/{objetivo}/etapas/{ordem}/detectar-filtros")
async def detectar_filtros_endpoint(
    site_id: str, objetivo: TipoObjetivoScraper, ordem: int, body: DetectarFiltrosRequest | None = None,
):
    """Detecta parâmetros de query string da URL desta etapa e classifica
    cada um (LLM) contra o vocabulário fixo de filtros — o resultado vira
    um `url_template` reutilizável (persistido como rascunho, editável
    depois via `.../etapas/{ordem}` PUT)."""
    site = _exigir_site(site_id)
    _, etapa = _exigir_etapa(site, objetivo, ordem)

    url = (body.url if body else None) or etapa.url_amostra
    if not url:
        raise HTTPException(400, detail={"erro": "Nenhuma URL informada nem configurada como amostra desta etapa."})

    resultado = await discovery.detectar_filtros(url, site.llm)
    if isinstance(resultado, ErroStep):
        raise HTTPException(502, detail={"erro": resultado.message, "motivo": resultado.error_type})

    filtros, template = resultado
    etapa.filtros = filtros
    etapa.url_template = template
    store.salvar_site(site)

    return {"filtros": [f.model_dump() for f in filtros], "url_template": template}


# ─────────────────────────────────────────────────────────────────────────────
# Auto-discovery (site inteiro) → registro
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sites/{site_id}/discover")
async def descobrir(site_id: str, body: DiscoverRequest | None = None):
    """Roda o LLM em toda etapa de todo objetivo pedido — mescla cada
    resultado como RASCUNHO (não valida, não registra; a validação
    acontece etapa a etapa em `.../test`)."""
    site = _exigir_site(site_id)

    objetivos = (body.objetivos if body and body.objetivos else None) or site.objetivos
    if not objetivos:
        raise HTTPException(400, detail={"erro": "Nenhum objetivo configurado para este site."})

    faltando = [
        o.value for o in objetivos
        if not (mapa := site.seletores.get(o.value)) or not mapa.etapas or not all(e.url_amostra for e in mapa.etapas)
    ]
    if faltando:
        raise HTTPException(400, detail={"erro": f"Falta URL de amostra em alguma etapa de: {', '.join(faltando)}."})

    resultados = await discovery.descobrir_site(site, objetivos)

    resposta: dict[str, Any] = {}
    for objetivo, por_etapa in resultados.items():
        mapa = site.seletores.get(objetivo.value)
        resposta_objetivo: dict[str, Any] = {}
        for ordem, resultado in por_etapa.items():
            if isinstance(resultado, ErroStep):
                resposta_objetivo[str(ordem)] = {"sucesso": False, "erro": resultado.model_dump()}
                continue
            if mapa is not None:
                mapa.etapas = [resultado if e.ordem == ordem else e for e in mapa.etapas]
            resposta_objetivo[str(ordem)] = {"sucesso": True, "selectors": resultado.selectors}
        resposta[objetivo.value] = resposta_objetivo

    store.salvar_site(site)
    return {"resultados": resposta}


@router.post("/sites/{site_id}/register")
async def registrar(site_id: str):
    """Ativa o site — exige que TODA etapa de todo objetivo escolhido já
    tenha passado por `.../test` com sucesso (`validado_em` preenchido)."""
    site = _exigir_site(site_id)

    prontos = set(site.objetivos_prontos())
    pendentes = [o.value for o in site.objetivos if o not in prontos]
    if pendentes:
        raise HTTPException(400, detail={
            "erro": f"Objetivos com etapas ainda não testadas com sucesso: {', '.join(pendentes)}. "
                    "Rode /test em cada etapa antes de registrar.",
        })

    if not any(v.version == site.version for v in site.versions):
        site.versions.append(site.snapshot())
    site.version += 1
    site.versions.append(site.snapshot())
    site.registrado = True
    site.status = StatusPipeline.ATIVA

    return _resumo_site(store.salvar_site(site))


# ─────────────────────────────────────────────────────────────────────────────
# Execução / DAG
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sites/{site_id}/run")
async def executar_agora(site_id: str, body: ExecutarRequest | None = None):
    try:
        run = executor.disparar_site(
            site_id, version=(body.version if body else None), origem="manual",
            filtros=(body.filtros if body else None),
        )
    except ValueError as exc:
        raise HTTPException(404, detail={"erro": str(exc)}) from exc

    return {
        "run_id": run.id,
        "status": run.status.value,
        "steps": [{"type": s.type.value, "name": s.name, "status": s.status.value} for s in run.steps],
    }


@router.get("/sites/{site_id}/runs")
async def historico(site_id: str, limite: int = Query(default=20, ge=1, le=100)):
    runs = store.listar_runs(site_id, limite)

    return {
        "site_id": site_id,
        "runs": [
            {
                "id": r.id,
                "status": r.status.value,
                "origem": r.origem,
                "site_version": r.site_version,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "progresso": r.progresso,
                "steps": [
                    {"type": s.type.value, "status": s.status.value, "resumo": s.resumo}
                    for s in r.steps
                ],
            }
            for r in runs
        ],
    }


@router.post("/sites/{site_id}/generate-dag")
async def gerar(site_id: str):
    site = _exigir_site(site_id)
    if not site.registrado:
        raise HTTPException(400, detail={"erro": "Registre o site (tela de teste) antes de gerar a DAG."})

    try:
        resultado = gerar_dag(site)
    except OSError as exc:
        raise HTTPException(500, detail={"erro": f"Não foi possível gravar a DAG: {exc}"}) from exc

    site.dag_id = resultado["dag_id"]
    site.dag_path = resultado["caminho_relativo"]
    site.dag_version = site.version
    store.salvar_site(site)

    return {
        "dag_id": resultado["dag_id"],
        "caminho": resultado["caminho_relativo"],
        "dag_version": site.version,
        "schedule": montar_cron(site.schedule),
        "schedule_legivel": descrever_schedule(site.schedule),
        "codigo": resultado["codigo"],
    }


@router.get("/sites/{site_id}/dag")
async def obter_dag(site_id: str):
    site = _exigir_site(site_id)

    dag_id = site.dag_id or montar_dag_id(site)
    arquivo = settings.dags_dir / f"{dag_id}.py"

    if arquivo.is_file():
        codigo = arquivo.read_text(encoding="utf-8")
        gravada = True
        versao = site.dag_version
    else:
        codigo = gerar_codigo(site)
        gravada = False
        versao = None

    return {
        "dag_id": dag_id,
        "caminho": f"airflow/dags/{dag_id}.py",
        "gravada": gravada,
        "dag_version": versao,
        "site_version": site.version,
        "desatualizada": gravada and versao != site.version,
        "schedule": montar_cron(site.schedule),
        "schedule_legivel": descrever_schedule(site.schedule),
        "codigo": codigo,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dados coletados (BigQuery) + resumo
# ─────────────────────────────────────────────────────────────────────────────

def _filtro_dados(
    site_id: str | None, marca: str | None, modelo: str | None,
) -> tuple[str, list[bigquery.ScalarQueryParameter]]:
    condicoes = []
    params: list[bigquery.ScalarQueryParameter] = []

    if site_id:
        condicoes.append("site_id = @site_id")
        params.append(bigquery.ScalarQueryParameter("site_id", "STRING", site_id))
    if marca:
        condicoes.append("LOWER(marca) = @marca")
        params.append(bigquery.ScalarQueryParameter("marca", "STRING", marca.lower()))
    if modelo:
        condicoes.append("LOWER(modelo) = @modelo")
        params.append(bigquery.ScalarQueryParameter("modelo", "STRING", modelo.lower()))

    where = ("WHERE " + " AND ".join(condicoes)) if condicoes else ""
    return where, params


@router.get("/dados/ficha-tecnica")
async def dados_ficha_tecnica(
    site_id: str | None = None, marca: str | None = None, modelo: str | None = None,
    limite: int = Query(default=200, ge=1, le=1000),
):
    _exigir_configurado()
    where, params = _filtro_dados(site_id, marca, modelo)
    linhas = _executar_consulta(
        f"SELECT * FROM `{warehouse.table_id(schemas.FICHA_TECNICA)}` {where} "
        f"ORDER BY coletado_em DESC LIMIT {limite}",
        params,
    )
    return {"linhas": linhas}


@router.get("/dados/anuncios")
async def dados_anuncios(
    site_id: str | None = None, marca: str | None = None, modelo: str | None = None,
    limite: int = Query(default=200, ge=1, le=1000),
):
    _exigir_configurado()
    where, params = _filtro_dados(site_id, marca, modelo)
    linhas = _executar_consulta(
        f"SELECT * FROM `{warehouse.table_id(schemas.ANUNCIOS)}` {where} "
        f"ORDER BY coletado_em DESC LIMIT {limite}",
        params,
    )
    return {"linhas": linhas}


@router.get("/dados/comentarios")
async def dados_comentarios(
    site_id: str | None = None, marca: str | None = None, modelo: str | None = None,
    limite: int = Query(default=200, ge=1, le=1000),
):
    _exigir_configurado()
    where, params = _filtro_dados(site_id, marca, modelo)
    linhas = _executar_consulta(
        f"SELECT * FROM `{warehouse.table_id(schemas.COMENTARIOS)}` {where} "
        f"ORDER BY coletado_em DESC LIMIT {limite}",
        params,
    )
    return {"linhas": linhas}


@router.get("/resumo")
async def resumo(site_id: str | None = Query(default=None)):
    """KPIs do painel: sites cadastrados/registrados + total de linhas
    coletadas por objetivo (0 se BigQuery não estiver configurado — recurso
    aditivo, mesmo contrato de `gov_api.resumo`)."""
    sites = store.listar_sites()

    resposta: dict[str, Any] = {
        "total_sites": len(sites),
        "total_registrados": len([s for s in sites if s.registrado]),
        "total_ficha_tecnica": 0,
        "total_anuncios": 0,
        "total_comentarios": 0,
    }

    if not settings.bigquery_configurado:
        return resposta

    where, params = _filtro_dados(site_id, None, None)
    for tabela, chave in (
        (schemas.FICHA_TECNICA, "total_ficha_tecnica"),
        (schemas.ANUNCIOS, "total_anuncios"),
        (schemas.COMENTARIOS, "total_comentarios"),
    ):
        linhas = _executar_consulta(f"SELECT COUNT(*) AS total FROM `{warehouse.table_id(tabela)}` {where}", params)
        resposta[chave] = linhas[0]["total"] if linhas else 0

    return resposta
