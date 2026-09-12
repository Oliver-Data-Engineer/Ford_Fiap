"""
test_govmode_dag.py — verifica src/govmode/dag_generator.py: geração de DAG
do Airflow pra pipelines do Gov Mode. Mesmo espírito da seção "4. Geração
da DAG" de tests/test_pipeline.py (YouTube), aplicado ao template do Gov
Mode — e confirma que os dois templates não se confundem (tags, prefixo de
dag_id, credencial verificada).
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_govmode_dag_"))
config.DATA_DIR = TEMP / "data"
config.DAGS_DIR = TEMP / "airflow" / "dags"

from src.airflow_gen.generator import descrever_schedule, montar_cron
from src.govmode.dag_generator import gerar_codigo, gerar_dag, montar_dag_id
from src.govmode.models import FonteGovFipe, GovPipeline, PassoGovPipeline, TipoStepGov
from src.pipelines.models import ConfigSchedule

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def principal() -> int:
    print("=== 1. Cron/agenda: mesmas funções genéricas do YouTube, reaproveitadas sem cópia ===")

    checar(montar_cron(ConfigSchedule(mode="daily", hora="08:00")) == "0 8 * * *",
           "agenda diária 08:00 → '0 8 * * *' (mesma função de src.airflow_gen.generator)")
    checar(montar_cron(ConfigSchedule(mode="manual")) is None, "manual → sem schedule")
    checar(descrever_schedule(ConfigSchedule(mode="weekly", hora="09:00", dia_semana=2))
           == "Semanal — terça às 09:00", "descrição legível da agenda")

    print("\n=== 2. Geração da DAG do Gov Mode ===")

    pipeline = GovPipeline(
        name="Extração FIPE Ford",
        description="Marcas, modelos e código FIPE da Ford",
        source=FonteGovFipe(tipo_veiculo="carros", marcas_alvo=["Ford"]),
        steps=[
            PassoGovPipeline(type=TipoStepGov.MARCAS),
            PassoGovPipeline(type=TipoStepGov.MODELOS),
            PassoGovPipeline(type=TipoStepGov.FIPE),
            PassoGovPipeline(type=TipoStepGov.REFERENCIA_MESES),
        ],
        schedule=ConfigSchedule(mode="daily", hora="08:00"),
    )
    pipeline.versions = [pipeline.snapshot()]

    checar(montar_dag_id(pipeline) == "gov_fipe_extracao_fipe_ford",
           f"dag_id prefixado com 'gov_fipe_', não 'youtube_' (obtido {montar_dag_id(pipeline)})")

    resultado = gerar_dag(pipeline)
    codigo = resultado["codigo"]

    checar(Path(resultado["caminho"]).is_file(), "arquivo da DAG gravado em disco")
    compile(codigo, resultado["caminho"], "exec")
    print("  OK     código da DAG compila como Python válido")

    checar("from src.govmode.executor import executar_pipeline_sync" in codigo,
           "a DAG chama o executor do Gov Mode, não o do YouTube")
    checar("from src.pipelines.executor import executar_pipeline_sync" not in codigo,
           "a DAG NÃO chama o executor do YouTube (templates não se confundem)")
    checar("schedule='0 8 * * *'" in codigo, "cron embutido na DAG")
    checar("validar_configuracao" in codigo and "publicar_resultado" in codigo,
           "DAG tem as tasks de validação e publicação")
    checar('tags=["gov", "fipe", "bigquery"]' in codigo,
           "tags são do Gov Mode, não ['youtube', 'pipeline', 'llm']")
    checar("GOOGLE_APPLICATION_CREDENTIALS" in codigo and "YOUTUBE_API_KEY" not in codigo,
           "valida GOOGLE_APPLICATION_CREDENTIALS, não YOUTUBE_API_KEY (domínio diferente)")
    checar("FIPE_API_TOKEN" in codigo, "valida FIPE_API_TOKEN (exigido só pelo passo de código FIPE)")

    print("\n=== 3. Segurança: nenhuma credencial real entra no artefato gerado ===")

    os.environ["FIPE_API_TOKEN"] = "token-secreto-de-teste-12345"
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "config/segredo-service-account.json"

    codigo2 = gerar_dag(pipeline)["codigo"]
    checar("token-secreto-de-teste-12345" not in codigo2, "token da FIPE ausente do código gerado")
    checar("segredo-service-account.json" not in codigo2, "caminho da credencial GCP ausente do código gerado")
    checar("os.environ" in codigo2, "a DAG lê credenciais do ambiente do worker, não de valores fixos")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações da geração de DAG do Gov Mode passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
