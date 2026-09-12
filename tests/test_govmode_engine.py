"""
test_govmode_engine.py — verifica GovEngine (src/govmode/engine.py) sem
tocar rede: dublê de FipeAPI (sem HTTP) + dublê do warehouse (sem
BigQuery real, um "banco" em memória que entende as consultas que o
engine faz).

Cobre especificamente o que é novo/arriscado nesta engine:
  1. Fluxo completo (marcas → modelos → fipe → meses de referência).
  2. Resumabilidade de verdade: rodar a pipeline duas vezes não duplica
     nem refaz chamadas já resolvidas (o ponto central do pedido do
     usuário — "todos os anos de cada modelo" multiplica muito o volume
     de chamadas, então isso precisa funcionar de verdade).
  3. Sem FIPE_API_TOKEN, o passo de código FIPE falha com uma mensagem
     clara em vez de um erro genérico — marcas/modelos continuam OK.
  4. Meses de referência (BrasilAPI) são interpretados corretamente.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.govmode import engine as govmode_engine
from src.govmode import schemas
from src.govmode.models import (
    FonteGovFipe,
    GovPipelineRun,
    GovPipelineVersao,
    PassoGovPipeline,
    TipoStepGov,
)
from src.pipelines.models import ConfigSchedule, StatusRun, StatusStep

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


# ─────────────────────────────────────────────────────────────────────────────
# Dublês
# ─────────────────────────────────────────────────────────────────────────────

class FakeWarehouse:
    """'Banco' em memória que entende as consultas específicas que
    GovEngine faz — o suficiente pra testar dedup/resumabilidade sem
    precisar de um SQL parser de verdade."""

    def __init__(self):
        self.tabelas = {nome: [] for nome in schemas.TABELAS}

    def table_id(self, nome):
        return f"projeto-teste.analytics_ford.{nome}"

    def upsert(self, nome, linhas):
        if not linhas:
            return
        if schemas.WRITE_DISPOSITION.get(nome) == "WRITE_TRUNCATE":
            self.tabelas[nome] = list(linhas)
        else:
            self.tabelas[nome].extend(linhas)

    def query(self, sql, params=None):
        params = params or []
        valores = {p.name: p.value for p in params}
        marcas_in = [v for k, v in valores.items() if k.startswith("m") and k != "marca"]

        if schemas.MODELOS_COM_FIPE in sql:
            linhas = [
                l for l in self.tabelas[schemas.MODELOS_COM_FIPE]
                if l["tipo_veiculo"] == valores.get("tipo") and l["marca_codigo"] in marcas_in
            ]
            cols = ["marca_codigo", "modelo_codigo", "ano_codigo"]
            return pd.DataFrame(linhas)[cols] if linhas else pd.DataFrame(columns=cols)

        if schemas.MODELOS_VEICULOS in sql and "marca_codigo IN" in sql:
            linhas = [
                l for l in self.tabelas[schemas.MODELOS_VEICULOS]
                if l["tipo_veiculo"] == valores.get("tipo") and l["marca_codigo"] in marcas_in
            ]
            cols = ["marca_codigo", "modelo_codigo", "modelo_nome"]
            return pd.DataFrame(linhas)[cols] if linhas else pd.DataFrame(columns=cols)

        if schemas.MODELOS_VEICULOS in sql:
            linhas = [
                l for l in self.tabelas[schemas.MODELOS_VEICULOS]
                if l["marca_codigo"] == valores.get("marca") and l["tipo_veiculo"] == valores.get("tipo")
            ]
            return pd.DataFrame(linhas)[["modelo_codigo"]] if linhas else pd.DataFrame(columns=["modelo_codigo"])

        if schemas.MARCAS_VEICULOS in sql:
            linhas = [
                l for l in self.tabelas[schemas.MARCAS_VEICULOS]
                if l["marca_nome"].lower() == valores.get("nome") and l["tipo_veiculo"] == valores.get("tipo")
            ]
            return pd.DataFrame(linhas)[["marca_codigo"]] if linhas else pd.DataFrame(columns=["marca_codigo"])

        return pd.DataFrame()


MODELOS_FORD = [
    {"nome": "TERRITORY", "codigo": "9163"},
    {"nome": "TERRITORY GHIA", "codigo": "9164"},
]
ANOS_POR_MODELO = [
    {"nome": "2026 Gasolina", "codigo": "2026-1"},
    {"nome": "2025 Gasolina", "codigo": "2025-1"},
]


class FakeFipeAPI:
    def __init__(self, token_configurado=True):
        self._token_configurado = token_configurado
        self.chamadas_codigo_fipe = 0

    @property
    def token_configurado(self):
        return self._token_configurado

    async def buscar_marcas(self, tipo_veiculo):
        return [{"nome": "Ford", "codigo": "22"}, {"nome": "Fiat", "codigo": "21"}]

    async def buscar_modelos(self, tipo_veiculo, marca_codigo):
        return MODELOS_FORD if marca_codigo == "22" else []

    async def buscar_anos(self, tipo_veiculo, marca_codigo, modelo_codigo):
        return ANOS_POR_MODELO

    async def buscar_codigo_fipe(self, tipo_veiculo, marca_codigo, modelo_codigo, ano_codigo):
        self.chamadas_codigo_fipe += 1
        return {"CodigoFipe": f"00{modelo_codigo}-{ano_codigo}", "Valor": "R$ 100.000,00"}

    async def buscar_meses_referencia(self):
        return [{"codigo": 332, "mes": "abril/2026"}, {"codigo": 331, "mes": "março/2026"}]


def config_padrao() -> GovPipelineVersao:
    return GovPipelineVersao(
        version=1,
        source=FonteGovFipe(tipo_veiculo="carros", marcas_alvo=["Ford"]),
        steps=[
            PassoGovPipeline(type=TipoStepGov.MARCAS),
            PassoGovPipeline(type=TipoStepGov.MODELOS),
            PassoGovPipeline(type=TipoStepGov.FIPE),
            PassoGovPipeline(type=TipoStepGov.REFERENCIA_MESES),
        ],
        schedule=ConfigSchedule(),
    )


def principal() -> int:
    # Sem isso, os asyncio.sleep(0.5) de "ritmo pra não bloquear por IP"
    # (propositais em produção) deixariam este teste lento à toa.
    govmode_engine.PAUSA_ENTRE_CHAMADAS = 0.001

    print("=== 1. Execução completa: marcas → modelos → fipe → meses de referência ===")

    fake_wh = FakeWarehouse()
    govmode_engine.warehouse.query = fake_wh.query
    govmode_engine.warehouse.upsert = fake_wh.upsert
    govmode_engine.warehouse.table_id = fake_wh.table_id
    govmode_engine.warehouse.garantir_tabelas = lambda: None

    api = FakeFipeAPI(token_configurado=True)
    motor = govmode_engine.GovEngine(api=api)
    config = config_padrao()
    run = GovPipelineRun(pipeline_name="Teste FIPE")

    run = asyncio.run(motor.executar(config, run))

    for s in run.steps:
        print(f"  [{s.status.value:8}] {s.type.value:18} {s.resumo}")

    checar(run.status == StatusRun.OK, f"execução concluída (status={run.status.value})")
    checar(run.step(TipoStepGov.MARCAS).status == StatusStep.OK, "passo marcas OK")
    checar(len(fake_wh.tabelas[schemas.MARCAS_VEICULOS]) == 2, "2 marcas gravadas (Ford + Fiat)")

    checar(run.step(TipoStepGov.MODELOS).status == StatusStep.OK, "passo modelos OK")
    checar(len(fake_wh.tabelas[schemas.MODELOS_VEICULOS]) == 2, "2 modelos da Ford gravados")

    checar(run.step(TipoStepGov.FIPE).status == StatusStep.OK, "passo fipe OK")
    checar(len(fake_wh.tabelas[schemas.MODELOS_COM_FIPE]) == 4,
           f"4 registros (2 modelos x 2 anos) — obtido {len(fake_wh.tabelas[schemas.MODELOS_COM_FIPE])}")
    checar(api.chamadas_codigo_fipe == 4, "4 chamadas de código FIPE na primeira execução")
    checar(run.progresso.get("fipe", {}).get("total") == 2, "progresso reporta total de 2 modelos")
    checar(run.progresso.get("fipe", {}).get("feito") == 2, "progresso reporta 2 modelos concluídos")

    checar(run.step(TipoStepGov.REFERENCIA_MESES).status == StatusStep.OK, "passo meses de referência OK")
    meses = fake_wh.tabelas[schemas.REFERENCIA_MESES_FIPE]
    checar(len(meses) == 2, "2 meses gravados")
    abril = next((m for m in meses if m["codigo"] == 332), None)
    checar(abril is not None and abril["mes"] == "04" and abril["mes_abrev"] == "abr" and abril["ano"] == 2026,
           "'abril/2026' interpretado como mes=04, mes_abrev=abr, ano=2026")

    print("\n=== 2. Resumabilidade: rodar de novo não duplica nem rechama a API ===")

    run2 = GovPipelineRun(pipeline_name="Teste FIPE (2a execução)")
    run2 = asyncio.run(motor.executar(config, run2))

    checar(run2.status == StatusRun.OK, f"segunda execução também conclui (status={run2.status.value})")
    checar(len(fake_wh.tabelas[schemas.MODELOS_VEICULOS]) == 2,
           "modelos não duplicaram na segunda execução (anti-join)")
    checar(len(fake_wh.tabelas[schemas.MODELOS_COM_FIPE]) == 4,
           "códigos FIPE não duplicaram na segunda execução (anti-join por modelo+ano)")
    checar(api.chamadas_codigo_fipe == 4,
           f"nenhuma chamada nova de código FIPE na segunda execução (permaneceu {api.chamadas_codigo_fipe})")
    checar(run2.step(TipoStepGov.FIPE).resumo.startswith("0 código(s) FIPE novo(s)"),
           f"resumo do passo reflete zero novidades (obtido: {run2.step(TipoStepGov.FIPE).resumo!r})")

    print("\n=== 3. Sem FIPE_API_TOKEN: passo de código FIPE falha com mensagem clara ===")

    fake_wh_2 = FakeWarehouse()
    govmode_engine.warehouse.query = fake_wh_2.query
    govmode_engine.warehouse.upsert = fake_wh_2.upsert
    govmode_engine.warehouse.table_id = fake_wh_2.table_id
    govmode_engine.warehouse.garantir_tabelas = lambda: None

    api_sem_token = FakeFipeAPI(token_configurado=False)
    motor_sem_token = govmode_engine.GovEngine(api=api_sem_token)
    run3 = GovPipelineRun(pipeline_name="Teste sem token")
    run3 = asyncio.run(motor_sem_token.executar(config_padrao(), run3))

    checar(run3.step(TipoStepGov.MARCAS).status == StatusStep.OK, "marcas continua OK sem token (não precisa dele)")
    checar(run3.step(TipoStepGov.MODELOS).status == StatusStep.OK, "modelos continua OK sem token (não precisa dele)")
    checar(run3.step(TipoStepGov.FIPE).status == StatusStep.FALHOU, "fipe falha sem token")
    checar(run3.step(TipoStepGov.FIPE).error.error_type == "MissingToken",
           "erro tipado como MissingToken, não uma exceção genérica")
    checar(run3.status == StatusRun.PARCIAL,
           f"execução geral fica 'partial' (outras etapas OK) — obtido {run3.status.value}")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações da engine do Gov Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
