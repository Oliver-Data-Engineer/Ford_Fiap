"""
src/analytics/
Warehouse analítico do YouTube (Google BigQuery) — camada aditiva que
alimenta a aba Dashboard com dados cross-execução.

Não substitui `src/pipelines/db.py` (SQLite, escopo de uma única execução,
usado pelo modo "Tabela" do Playground) — ver docs/SPEC.md seção 12.
"""
