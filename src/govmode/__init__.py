"""
src/govmode/
Gov Mode — catálogo FIPE (marca/modelo/ano/código) extraído de
parallelum.com.br (marcas/modelos/código FIPE) e brasilapi.com.br (meses de
referência), gravado no BigQuery (dataset `analytics_ford`, mesmo padrão do
notebook `tabela_marcas.ipynb`).

Sistema de pipelines nomeadas/versionadas **paralelo** ao do YouTube
(`src/pipelines/`), não uma extensão dele — `Pipeline.source` lá é tipado
concretamente como `FonteYouTube`, então um "source" de formato totalmente
diferente (marcas/modelos FIPE) não cabe no mesmo modelo sem reescrever
algo que já está em produção. O que É genérico (`ConfigSchedule`, o motor
de cron) vem importado direto de `src.pipelines`/`src.airflow_gen` — só o
que é específico de FIPE mora aqui. Ver docs/SPEC.md seção 13.
"""
