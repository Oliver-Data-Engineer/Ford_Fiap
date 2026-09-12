"""
models.py
Struct fixo do enriquecimento de comentários.

Diferente do resto do app (onde `output_schema` de um `Prompt` é só texto
sugestivo colado no prompt da LLM, nunca validado — ver
`src/agents/agent.py::_montar_system_prompt`), a saída do enriquecimento de
comentários É validada contra este modelo depois do parse do JSON. Se não
bater, o comentário é marcado como falha em vez de gravar lixo no warehouse.

É sempre esta mesma estrutura para todo comentário — não existem "modos" de
análise selecionáveis (decisão do usuário, ver docs/SPEC.md seção 12).
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class AnaliseComentarioStruct(BaseModel):
    sentimento: str
    motivo: str
    adjetivos: list[str] = Field(default_factory=list)
    objeto: str

    @field_validator("adjetivos", mode="before")
    @classmethod
    def _normalizar_adjetivos(cls, valor):
        if valor is None:
            return []
        if isinstance(valor, str):
            return [valor]
        return list(valor)

    @field_validator("sentimento", "motivo", "objeto", mode="before")
    @classmethod
    def _exigir_texto(cls, valor):
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError("campo obrigatório e não pode ser vazio")
        return valor.strip()
