"""
engine.py
Motor de execução das pipelines do Gov Mode — 4 passos, mesmo padrão de
dispatch de `src/pipelines/engine.py`, mas para o domínio do catálogo FIPE.

Fluxo: MARCAS → MODELOS → FIPE (todos os anos de cada modelo) →
REFERENCIA_MESES (independente dos três primeiros).

Resumível de verdade: MODELOS e FIPE consultam o próprio BigQuery antes de
chamar a API externa, e só buscam o que ainda não existe — interromper a
execução no meio e rodar de novo nunca duplica nem perde trabalho já feito.
MARCAS/REFERENCIA_MESES recarregam por inteiro (`WRITE_TRUNCATE`), então
não precisam desse cuidado.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.cloud import bigquery

from src.bigquery_warehouse import agora_iso
from src.govmode import schemas, warehouse
from src.govmode.client import FipeAPI, FipeAPIError
from src.govmode.models import (
    ROTULOS_STEP_GOV,
    STEPS_PADRAO_GOV,
    FonteGovFipe,
    GovPipelineRun,
    GovPipelineVersao,
    PassoGovPipeline,
    StepResultGov,
    TipoStepGov,
)
from src.pipelines.models import StatusRun, StatusStep

logger = logging.getLogger(__name__)

# Ritmo/lote — mesmos valores do notebook tabela_marcas.ipynb ("pausa
# obrigatória para a API não bloquear seu IP").
PAUSA_ENTRE_CHAMADAS = 0.5
TAMANHO_DO_LOTE = 50

MAPEAMENTO_MESES: dict[str, tuple[str, str]] = {
    "janeiro": ("01", "jan"), "fevereiro": ("02", "fev"), "março": ("03", "mar"),
    "abril": ("04", "abr"), "maio": ("05", "mai"), "junho": ("06", "jun"),
    "julho": ("07", "jul"), "agosto": ("08", "ago"), "setembro": ("09", "set"),
    "outubro": ("10", "out"), "novembro": ("11", "nov"), "dezembro": ("12", "dez"),
}


def montar_steps(config: GovPipelineVersao) -> list[StepResultGov]:
    """Régua de progresso com todas as etapas em `pending` — mesmo papel de
    `src/pipelines/engine.py::montar_steps`."""
    return [
        StepResultGov(type=p.type, name=p.name or ROTULOS_STEP_GOV.get(p.type, p.type.value))
        for p in GovEngine.resolver_passos(config)
    ]


class GovEngine:
    def __init__(self, api: FipeAPI | None = None):
        self._api = api or FipeAPI()

    # ─────────────────────────────────────────────────────────────────────────
    # Entrada principal
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def resolver_passos(config: GovPipelineVersao) -> list[PassoGovPipeline]:
        if not config.steps:
            return [PassoGovPipeline(type=t) for t in STEPS_PADRAO_GOV]
        return config.steps

    async def executar(self, config: GovPipelineVersao, run: GovPipelineRun) -> GovPipelineRun:
        """Nunca levanta exceção: falhas ficam registradas na etapa
        correspondente (mesmo contrato de `PipelineEngine.executar`)."""
        passos = self.resolver_passos(config)
        if not run.steps:
            run.steps = montar_steps(config)
        run.status = StatusRun.EXECUTANDO

        # Garante que as 4 tabelas existem antes de qualquer passo consultar
        # (ex.: modelos/fipe leem o BigQuery antes de escrever) — sem isso, a
        # primeira execução num dataset novo pode esbarrar em "table not
        # found" dependendo da ordem/falha parcial dos passos.
        try:
            warehouse.garantir_tabelas()
        except Exception as exc:
            logger.warning("[govmode] não foi possível garantir as tabelas antes da execução: %s", exc)

        contexto: dict[str, Any] = {"fonte": config.source, "progresso": {}}

        despacho = {
            TipoStepGov.MARCAS: self._passo_marcas,
            TipoStepGov.MODELOS: self._passo_modelos,
            TipoStepGov.FIPE: self._passo_fipe,
            TipoStepGov.REFERENCIA_MESES: self._passo_referencia_meses,
        }

        houve_falha = False
        for passo, resultado in zip(passos, run.steps):
            if not passo.enabled:
                resultado.pular("etapa desabilitada na configuração")
                continue

            resultado.iniciar()
            handler = despacho.get(passo.type)
            try:
                if handler is None:
                    resultado.pular(f"tipo de etapa não suportado: {passo.type}")
                else:
                    await handler(contexto, resultado)
            except Exception as exc:
                logger.exception("[govmode] etapa '%s' falhou", passo.type.value)
                resultado.falhar(type(exc).__name__, str(exc))

            if resultado.status == StatusStep.FALHOU:
                houve_falha = True

        run.progresso = contexto.get("progresso", {})
        run.finished_at = agora_iso()

        concluidas = [s for s in run.steps if s.status == StatusStep.OK]
        if houve_falha:
            run.status = StatusRun.PARCIAL if concluidas else StatusRun.FALHOU
        else:
            run.status = StatusRun.OK

        return run

    # ─────────────────────────────────────────────────────────────────────────
    # Passo 1 — Marcas (recarga completa)
    # ─────────────────────────────────────────────────────────────────────────

    async def _passo_marcas(self, contexto: dict, resultado: StepResultGov) -> None:
        fonte: FonteGovFipe = contexto["fonte"]

        try:
            marcas = await self._api.buscar_marcas(fonte.tipo_veiculo)
        except FipeAPIError as exc:
            resultado.falhar("FipeAPIError", str(exc), retryable=exc.motivo in ("timeout", "network"))
            return

        agora = agora_iso()
        linhas = [
            {
                "tipo_veiculo": fonte.tipo_veiculo,
                "marca_nome": m.get("nome"),
                "marca_codigo": str(m.get("codigo")),
                "atualizado_em": agora,
            }
            for m in marcas
            if m.get("nome") and m.get("codigo") is not None
        ]

        if not linhas:
            resultado.falhar("EmptyData", "Nenhuma marca foi devolvida pela API.")
            return

        # Disponível pro passo de modelos resolver o código sem outra
        # consulta ao BigQuery, quando os dois passos rodam na mesma execução.
        contexto["marcas_codigo_por_nome"] = {
            l["marca_nome"].lower(): l["marca_codigo"] for l in linhas
        }

        try:
            warehouse.upsert(schemas.MARCAS_VEICULOS, linhas)
        except Exception as exc:
            resultado.falhar("WarehouseError", str(exc))
            return

        resultado.concluir(f"{len(linhas)} marca(s) de '{fonte.tipo_veiculo}'")

    # ─────────────────────────────────────────────────────────────────────────
    # Passo 2 — Modelos (incremental, dedup por anti-join)
    # ─────────────────────────────────────────────────────────────────────────

    def _resolver_marca_codigo(self, tipo_veiculo: str, nome_marca: str) -> str | None:
        tabela = warehouse.table_id(schemas.MARCAS_VEICULOS)
        sql = (
            f"SELECT marca_codigo FROM `{tabela}` "
            "WHERE LOWER(marca_nome) = @nome AND tipo_veiculo = @tipo LIMIT 1"
        )
        params = [
            bigquery.ScalarQueryParameter("nome", "STRING", nome_marca.lower()),
            bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo),
        ]
        try:
            df = warehouse.query(sql, params)
        except Exception:
            return None
        return None if df.empty else str(df.iloc[0]["marca_codigo"])

    def _modelos_existentes(self, tipo_veiculo: str, marca_codigo: str) -> set[str]:
        tabela = warehouse.table_id(schemas.MODELOS_VEICULOS)
        sql = f"SELECT modelo_codigo FROM `{tabela}` WHERE marca_codigo = @marca AND tipo_veiculo = @tipo"
        params = [
            bigquery.ScalarQueryParameter("marca", "STRING", marca_codigo),
            bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo),
        ]
        try:
            df = warehouse.query(sql, params)
        except Exception:
            return set()  # tabela pode não existir ainda na primeira execução
        return set() if df.empty else set(df["modelo_codigo"].astype(str))

    async def _passo_modelos(self, contexto: dict, resultado: StepResultGov) -> None:
        fonte: FonteGovFipe = contexto["fonte"]
        if not fonte.marcas_alvo:
            resultado.pular("nenhuma marca-alvo configurada")
            return

        marcas_por_nome = contexto.get("marcas_codigo_por_nome") or {}
        pendentes_marca: list[tuple[str, str]] = []
        for nome in fonte.marcas_alvo:
            codigo = marcas_por_nome.get(nome.lower()) or self._resolver_marca_codigo(fonte.tipo_veiculo, nome)
            if codigo:
                pendentes_marca.append((nome, codigo))

        if not pendentes_marca:
            resultado.falhar(
                "MarcaNaoEncontrada",
                f"Nenhuma das marcas {fonte.marcas_alvo} foi encontrada em '{schemas.MARCAS_VEICULOS}' "
                "— rode o passo de marcas primeiro.",
            )
            return

        agora = agora_iso()
        total_novos = 0
        for nome, marca_codigo in pendentes_marca:
            try:
                modelos = await self._api.buscar_modelos(fonte.tipo_veiculo, marca_codigo)
            except FipeAPIError as exc:
                logger.warning("[govmode] falha ao buscar modelos de '%s': %s", nome, exc)
                await asyncio.sleep(PAUSA_ENTRE_CHAMADAS)
                continue

            existentes = self._modelos_existentes(fonte.tipo_veiculo, marca_codigo)
            novos = [
                {
                    "tipo_veiculo": fonte.tipo_veiculo,
                    "marca_codigo": marca_codigo,
                    "modelo_codigo": str(m.get("codigo")),
                    "modelo_nome": m.get("nome"),
                    "atualizado_em": agora,
                }
                for m in modelos
                if m.get("nome") and str(m.get("codigo")) not in existentes
            ]
            if novos:
                warehouse.upsert(schemas.MODELOS_VEICULOS, novos)
                total_novos += len(novos)

            await asyncio.sleep(PAUSA_ENTRE_CHAMADAS)

        contexto["marcas_processadas"] = [codigo for _, codigo in pendentes_marca]
        resultado.concluir(f"{total_novos} modelo(s) novo(s) de {len(pendentes_marca)} marca(s)")

    # ─────────────────────────────────────────────────────────────────────────
    # Passo 3 — Código FIPE (todos os anos por modelo, resumível por ano)
    # ─────────────────────────────────────────────────────────────────────────

    def _modelos_por_marcas(self, tipo_veiculo: str, marcas_codigo: list[str]) -> list[dict]:
        tabela = warehouse.table_id(schemas.MODELOS_VEICULOS)
        marcadores = ", ".join(f"@m{i}" for i in range(len(marcas_codigo)))
        sql = (
            f"SELECT marca_codigo, modelo_codigo, modelo_nome FROM `{tabela}` "
            f"WHERE tipo_veiculo = @tipo AND marca_codigo IN ({marcadores})"
        )
        params = [bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo)]
        params += [bigquery.ScalarQueryParameter(f"m{i}", "STRING", c) for i, c in enumerate(marcas_codigo)]
        try:
            df = warehouse.query(sql, params)
        except Exception:
            return []
        return [] if df.empty else df.to_dict(orient="records")

    def _fipe_existentes(self, tipo_veiculo: str, marcas_codigo: list[str]) -> set[tuple[str, str, str]]:
        tabela = warehouse.table_id(schemas.MODELOS_COM_FIPE)
        marcadores = ", ".join(f"@m{i}" for i in range(len(marcas_codigo)))
        sql = (
            f"SELECT marca_codigo, modelo_codigo, ano_codigo FROM `{tabela}` "
            f"WHERE tipo_veiculo = @tipo AND marca_codigo IN ({marcadores})"
        )
        params = [bigquery.ScalarQueryParameter("tipo", "STRING", tipo_veiculo)]
        params += [bigquery.ScalarQueryParameter(f"m{i}", "STRING", c) for i, c in enumerate(marcas_codigo)]
        try:
            df = warehouse.query(sql, params)
        except Exception:
            return set()
        if df.empty:
            return set()
        return set(zip(
            df["marca_codigo"].astype(str), df["modelo_codigo"].astype(str), df["ano_codigo"].astype(str)
        ))

    async def _passo_fipe(self, contexto: dict, resultado: StepResultGov) -> None:
        fonte: FonteGovFipe = contexto["fonte"]

        if not self._api.token_configurado:
            resultado.falhar(
                "MissingToken",
                "FIPE_API_TOKEN não configurado no ambiente — não é possível resolver código FIPE.",
            )
            return

        marcas_processadas: list[str] = contexto.get("marcas_processadas") or [
            c for c in (self._resolver_marca_codigo(fonte.tipo_veiculo, nome) for nome in fonte.marcas_alvo) if c
        ]
        if not marcas_processadas:
            resultado.pular("nenhuma marca processada — rode o passo de modelos primeiro")
            return

        modelos = self._modelos_por_marcas(fonte.tipo_veiculo, marcas_processadas)
        if not modelos:
            resultado.pular("nenhum modelo catalogado para essas marcas ainda")
            return

        existentes = self._fipe_existentes(fonte.tipo_veiculo, marcas_processadas)
        total = len(modelos)
        contexto["progresso"]["fipe"] = {"feito": 0, "total": total}

        lote: list[dict] = []
        feitos = 0
        falhas = 0
        novos_totais = 0
        agora = agora_iso()

        for item in modelos:
            marca_codigo = str(item["marca_codigo"])
            modelo_codigo = str(item["modelo_codigo"])

            try:
                anos = await self._api.buscar_anos(fonte.tipo_veiculo, marca_codigo, modelo_codigo)
            except FipeAPIError:
                falhas += 1
                feitos += 1
                contexto["progresso"]["fipe"] = {"feito": feitos, "total": total}
                await asyncio.sleep(PAUSA_ENTRE_CHAMADAS)
                continue

            for ano in anos:
                ano_codigo = str(ano.get("codigo"))
                chave = (marca_codigo, modelo_codigo, ano_codigo)
                if chave in existentes:
                    continue  # já resolvido numa execução anterior — resumabilidade

                try:
                    detalhe = await self._api.buscar_codigo_fipe(
                        fonte.tipo_veiculo, marca_codigo, modelo_codigo, ano_codigo
                    )
                except FipeAPIError:
                    falhas += 1
                    await asyncio.sleep(PAUSA_ENTRE_CHAMADAS)
                    continue

                codigo_fipe = detalhe.get("CodigoFipe")
                if codigo_fipe:
                    lote.append({
                        "tipo_veiculo": fonte.tipo_veiculo,
                        "marca_codigo": marca_codigo,
                        "modelo_codigo": modelo_codigo,
                        "modelo_nome": item.get("modelo_nome"),
                        "ano_codigo": ano_codigo,
                        "ano_modelo": ano.get("nome"),
                        "codigo_fipe": codigo_fipe,
                        "atualizado_em": agora,
                    })
                    existentes.add(chave)
                    novos_totais += 1

                    if len(lote) >= TAMANHO_DO_LOTE:
                        warehouse.upsert(schemas.MODELOS_COM_FIPE, lote)
                        lote = []

                await asyncio.sleep(PAUSA_ENTRE_CHAMADAS)

            feitos += 1
            contexto["progresso"]["fipe"] = {"feito": feitos, "total": total}

        if lote:
            warehouse.upsert(schemas.MODELOS_COM_FIPE, lote)

        detalhe = f"{novos_totais} código(s) FIPE novo(s) — {feitos}/{total} modelo(s) processado(s)"
        if falhas:
            detalhe += f", {falhas} chamada(s) com falha"
        resultado.concluir(detalhe)

    # ─────────────────────────────────────────────────────────────────────────
    # Passo 4 — Meses de referência (BrasilAPI, recarga completa, independente)
    # ─────────────────────────────────────────────────────────────────────────

    async def _passo_referencia_meses(self, contexto: dict, resultado: StepResultGov) -> None:
        try:
            meses = await self._api.buscar_meses_referencia()
        except FipeAPIError as exc:
            resultado.falhar("FipeAPIError", str(exc), retryable=exc.motivo in ("timeout", "network"))
            return

        linhas = []
        for item in meses:
            descricao = (item.get("mes") or "").strip()
            if "/" not in descricao:
                continue

            nome_mes, _, ano_texto = descricao.lower().partition("/")
            par = MAPEAMENTO_MESES.get(nome_mes.strip())
            ano_texto = ano_texto.strip()
            if not par or not ano_texto.isdigit() or item.get("codigo") is None:
                continue

            mes_num, mes_abrev = par
            linhas.append({
                "codigo": int(item["codigo"]),
                "descricao_original": descricao,
                "ano": int(ano_texto),
                "mes": mes_num,
                "mes_abrev": mes_abrev,
            })

        if not linhas:
            resultado.falhar("EmptyData", "Nenhum mês de referência pôde ser interpretado.")
            return

        try:
            warehouse.upsert(schemas.REFERENCIA_MESES_FIPE, linhas)
        except Exception as exc:
            resultado.falhar("WarehouseError", str(exc))
            return

        resultado.concluir(f"{len(linhas)} mês(es) de referência")
