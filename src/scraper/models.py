"""
models.py
Modelos do Scraper Mode (auto-discovery de sites de carro) — paralelo ao
Gov Mode (`src/govmode/models.py`), não uma extensão dele. Ver docs/SPEC.md
seção 14.2 para o racional completo de "por que um domínio paralelo".

Reaproveitados diretamente de `src.pipelines.models`, por serem genéricos:
`ConfigLLM`, `ConfigSchedule`, `StatusPipeline`/`StatusRun`/`StatusStep`,
`ErroStep`, `agora_iso`/`novo_id`.

**Não** reaproveitados: `Pipeline`/`PipelineVersao`/`StepResult` — `source`/
`type` são tipados concretamente pro domínio YouTube, mesmo problema já
documentado em `src/govmode/models.py`. `SiteScraper`/`StepResultScraper`
existem aqui como equivalentes próprios.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.pipelines.models import (
    ConfigLLM,
    ConfigSchedule,
    ErroStep,
    StatusPipeline,
    StatusRun,
    StatusStep,
    agora_iso,
    novo_id,
)


class TipoObjetivoScraper(str, Enum):
    FICHA_TECNICA = "ficha_tecnica"
    ANUNCIOS = "anuncios"
    COMENTARIOS = "comentarios"       # só faz sentido junto de ANUNCIOS — validado na API


ROTULOS_OBJETIVO: dict[TipoObjetivoScraper, str] = {
    TipoObjetivoScraper.FICHA_TECNICA: "Ficha técnica",
    TipoObjetivoScraper.ANUNCIOS: "Anúncios/preços",
    TipoObjetivoScraper.COMENTARIOS: "Comentários",
}


class TipoStepScraper(str, Enum):
    """Um step de execução por objetivo escolhido — mesmo papel de
    `TipoStepGov`, mas cada instância roda `descobrir`/`extrair` do próprio
    objetivo, não um pipeline fixo de 4 passos."""
    FICHA_TECNICA = "ficha_tecnica"
    ANUNCIOS = "anuncios"
    COMENTARIOS = "comentarios"


class StepResultScraper(BaseModel):
    """Equivalente a `StepResultGov` — mesmos mutadores, `type: TipoStepScraper`."""
    type: TipoStepScraper
    name: str
    status: StatusStep = StatusStep.PENDENTE
    started_at: str | None = None
    finished_at: str | None = None
    resumo: str = ""
    error: ErroStep | None = None

    def iniciar(self) -> None:
        self.status = StatusStep.EXECUTANDO
        self.started_at = agora_iso()

    def concluir(self, resumo: str = "") -> None:
        self.status = StatusStep.OK
        self.resumo = resumo
        self.finished_at = agora_iso()

    def falhar(self, tipo: str, mensagem: str, retryable: bool = False) -> None:
        self.status = StatusStep.FALHOU
        self.error = ErroStep(error_type=tipo, message=mensagem, retryable=retryable)
        self.finished_at = agora_iso()

    def pular(self, motivo: str = "") -> None:
        self.status = StatusStep.PULADO
        self.resumo = motivo
        self.finished_at = agora_iso()


# ─────────────────────────────────────────────────────────────────────────────
# Seletores por objetivo — sempre a identidade marca/modelo/versao/ano, mais
# os campos específicos do objetivo. Todo campo (exceto o "seletor de linha"
# que ancora a extração) é opcional: um site pode não ter, por exemplo,
# "localização" no anúncio — extrator tolera ausência campo a campo.
# ─────────────────────────────────────────────────────────────────────────────

class SeletorFichaTecnica(BaseModel):
    marca: str | None = None
    modelo: str | None = None
    versao: str | None = None
    ano: str | None = None
    linha_atributo: str            # seletor da linha/par atributo-valor (repetido)
    nome_atributo: str              # seletor relativo a linha_atributo
    valor_atributo: str


class SeletorAnuncios(BaseModel):
    item_listagem: str              # seletor do card/linha de cada anúncio (repetido)
    marca: str | None = None
    modelo: str | None = None
    versao: str | None = None
    ano: str | None = None
    preco: str
    url_anuncio: str | None = None
    localizacao: str | None = None
    vendedor: str | None = None
    publicado_em: str | None = None


class SeletorComentarios(BaseModel):
    item_comentario: str            # seletor do bloco de cada comentário (repetido)
    marca: str | None = None
    modelo: str | None = None
    versao: str | None = None
    ano: str | None = None
    autor: str | None = None
    texto: str
    nota: str | None = None
    publicado_em: str | None = None


SELETOR_POR_OBJETIVO: dict[TipoObjetivoScraper, type[BaseModel]] = {
    TipoObjetivoScraper.FICHA_TECNICA: SeletorFichaTecnica,
    TipoObjetivoScraper.ANUNCIOS: SeletorAnuncios,
    TipoObjetivoScraper.COMENTARIOS: SeletorComentarios,
}


class SeletorEtapaIntermediaria(BaseModel):
    """Forma genérica de seletor pra uma etapa de NAVEGAÇÃO — não a última
    etapa de um objetivo, que usa o `Seletor*` flat de sempre
    (`SELETOR_POR_OBJETIVO`). Uma linha repetida (`linha`) + campos livres
    nomeados relativos a ela (`campos`) — um desses nomes é escolhido pelo
    usuário como `EtapaScraper.campo_herdado` (o valor que vira a URL da
    próxima etapa, ex.: "url_ficha", "url_anuncio")."""
    linha: str
    campos: dict[str, str] = Field(default_factory=dict)


class FiltroParam(BaseModel):
    """Um parâmetro de query string de uma etapa, classificado como filtro
    dinâmico por `discovery.classificar_filtros` (ou editado à mão) —
    junto de `EtapaScraper.url_template`, permite montar a URL da etapa
    pra uma consulta real (marca/modelo/ano específicos), não só reusar a
    URL de amostra fixa."""
    nome_param: str                 # nome literal na query string, ex.: "fabricante"
    papel: str = "outro"             # marca | modelo | versao | ano | id | outro
    valor_amostra: str = ""          # valor original capturado na URL de amostra


PAPEIS_FILTRO = ("marca", "modelo", "versao", "ano", "id", "outro")


class EtapaScraper(BaseModel):
    """Um passo de navegação dentro de um objetivo — sua própria URL de
    amostra + seu próprio mapa de seletores, descoberto/testado
    independentemente (mesmo mecanismo de sempre, um nível mais fundo).
    A cadeia de etapas de um objetivo generaliza o que
    `src/utils/CarrosWeb.py` já faz à mão pra um site só (fabricante →
    modelo → versões, cada linha carregando um `url_ficha` → a ficha em
    si): a ÚLTIMA etapa usa o `Seletor*` flat do objetivo
    (`SELETOR_POR_OBJETIVO`); qualquer etapa anterior usa
    `SeletorEtapaIntermediaria` e declara `campo_herdado`."""
    id: str = Field(default_factory=lambda: novo_id("etapa"))
    ordem: int = 0
    url_amostra: str = ""
    url_template: str = ""            # preenchido por .../detectar-filtros; "" até lá
    filtros: list[FiltroParam] = Field(default_factory=list)
    selectors: dict[str, Any] = Field(default_factory=dict)
    campo_herdado: str | None = None  # chave em `selectors`/`campos`; None só na ÚLTIMA etapa
    llm: dict[str, str] = Field(default_factory=dict)   # {"provider":..., "model":...}
    descoberto_em: str | None = None
    validado_em: str | None = None


class MapaSeletor(BaseModel):
    """Envelope por objetivo — uma cadeia ORDENADA de etapas (uma só, no
    caso comum de página única). `pronto()` substitui o antigo
    `validado_em` no nível do objetivo: exige que TODA etapa da cadeia já
    tenha passado por `.../test`; `/register` exige isso pra todo objetivo
    escolhido no site."""
    objetivo: TipoObjetivoScraper
    etapas: list[EtapaScraper] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _compat_formato_antigo(cls, dados: Any) -> Any:
        """Compatibilidade com o formato anterior (uma etapa só, campos
        `selectors`/`url_amostra`/`llm`/`descoberto_em`/`validado_em` no
        nível raiz do objetivo, sem chave `etapas`) — sites já cadastrados
        antes desta mudança (ex.: `data/scraper_sites.json`) continuam
        carregando sem precisar reeditar o JSON à mão."""
        if isinstance(dados, dict) and "etapas" not in dados and "selectors" in dados:
            return {
                "objetivo": dados.get("objetivo"),
                "etapas": [{
                    "ordem": 0,
                    "url_amostra": dados.get("url_amostra", ""),
                    "selectors": dados.get("selectors") or {},
                    "campo_herdado": None,
                    "llm": dados.get("llm") or {},
                    "descoberto_em": dados.get("descoberto_em"),
                    "validado_em": dados.get("validado_em"),
                }],
            }
        return dados

    def etapa_final(self) -> EtapaScraper | None:
        return self.etapas[-1] if self.etapas else None

    def pronto(self) -> bool:
        return bool(self.etapas) and all(e.validado_em for e in self.etapas)


class SiteScraperVersao(BaseModel):
    """Snapshot congelado de uma versão — nunca alterado depois de criado,
    mesmo papel de `GovPipelineVersao`."""
    version: int
    url_base: str
    objetivos: list[TipoObjetivoScraper]
    amostra_urls: dict[str, str] = Field(default_factory=dict)
    seletores: dict[str, MapaSeletor] = Field(default_factory=dict)
    llm: ConfigLLM
    schedule: ConfigSchedule
    created_at: str = Field(default_factory=agora_iso)


class SiteScraper(BaseModel):
    """Site cadastrado no Scraper Mode — nomeado e versionado, mesmo padrão
    de `GovPipeline`, armazenado à parte (`data/scraper_sites.json`,
    `src/scraper/store.py`)."""
    id: str = Field(default_factory=lambda: novo_id("site_scraper"))
    name: str
    description: str = ""
    version: int = 1
    status: StatusPipeline = StatusPipeline.PAUSADA
    registrado: bool = False        # só True depois de POST .../register

    url_base: str = ""
    objetivos: list[TipoObjetivoScraper] = Field(default_factory=list)
    amostra_urls: dict[str, str] = Field(default_factory=dict)   # {objetivo.value: url}
    seletores: dict[str, MapaSeletor] = Field(default_factory=dict)   # {objetivo.value: MapaSeletor}

    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    schedule: ConfigSchedule = Field(default_factory=ConfigSchedule)
    versions: list[SiteScraperVersao] = Field(default_factory=list)

    dag_id: str | None = None
    dag_path: str | None = None
    dag_version: int | None = None
    last_run_at: str | None = None
    last_run_status: str | None = None
    created_at: str = Field(default_factory=agora_iso)
    updated_at: str = Field(default_factory=agora_iso)

    def snapshot(self) -> SiteScraperVersao:
        return SiteScraperVersao(
            version=self.version,
            url_base=self.url_base,
            objetivos=self.objetivos,
            amostra_urls=self.amostra_urls,
            seletores=self.seletores,
            llm=self.llm,
            schedule=self.schedule,
        )

    def na_versao(self, version: int | None) -> SiteScraperVersao:
        if version is None or version == self.version:
            return self.snapshot()
        for v in self.versions:
            if v.version == version:
                return v
        raise ValueError(f"Site do scraper '{self.id}' não possui versão {version}.")

    def objetivos_prontos(self) -> list[TipoObjetivoScraper]:
        """Objetivos cuja cadeia INTEIRA de etapas já passou por /test —
        é o que /register exige antes de ativar o site."""
        return [
            objetivo for objetivo in self.objetivos
            if (mapa := self.seletores.get(objetivo.value)) and mapa.pronto()
        ]


class SiteScraperEntrada(BaseModel):
    """Corpo aceito na criação/edição de um site via API."""
    name: str
    description: str = ""
    url_base: str = ""
    objetivos: list[TipoObjetivoScraper] = Field(default_factory=list)
    amostra_urls: dict[str, str] = Field(default_factory=dict)
    llm: ConfigLLM = Field(default_factory=ConfigLLM)
    schedule: ConfigSchedule = Field(default_factory=ConfigSchedule)
    status: StatusPipeline = StatusPipeline.PAUSADA


class ScraperRun(BaseModel):
    """Registro de uma execução — mesmo papel de `GovPipelineRun`.
    `resultado` guarda quantas linhas cada objetivo gravou."""
    id: str = Field(default_factory=lambda: novo_id("scraper_run"))
    site_id: str | None = None
    site_name: str = "Scraper"
    site_version: int | None = None
    origem: str = "manual"           # manual | playground | airflow
    status: StatusRun = StatusRun.PENDENTE
    steps: list[StepResultScraper] = Field(default_factory=list)
    progresso: dict[str, Any] = Field(default_factory=dict)
    resultado: dict[str, int] = Field(default_factory=dict)
    started_at: str = Field(default_factory=agora_iso)
    finished_at: str | None = None

    def step(self, tipo: TipoStepScraper) -> StepResultScraper | None:
        return next((s for s in self.steps if s.type == tipo), None)
