"""
prompts.py
Prompts fixos do auto-discovery, um por objetivo — mesmo padrão de prompt
fixo de `src/analytics/enrichment.py` (não é um "modo" que o usuário edita,
é configuração de sistema). `output_schema` de cada um é só texto
descritivo pro prompt (`Agente.run` não valida contra ele) — a validação de
verdade acontece em `discovery.py`, contra o Pydantic `Seletor*` certo
(`src/scraper/models.py`).
"""
from __future__ import annotations

from typing import Any

from src.scraper.models import TipoObjetivoScraper

_INSTRUCAO_COMUM = (
    "Você recebe o HTML simplificado de uma página de um site de carros. "
    "Analise a ESTRUTURA do HTML (tags, classes, ids) e devolva SELETORES "
    "CSS que localizem cada campo pedido — nunca os valores em si, só os "
    "seletores. Se um campo não existir na página, use null. Responda "
    "SOMENTE com o JSON pedido, sem explicação, sem markdown."
)

PROMPT_FICHA_TECNICA = (
    _INSTRUCAO_COMUM
    + "\n\nEsta página mostra a FICHA TÉCNICA de um carro (tabela/lista de "
    "atributos como motor, potência, torque, câmbio, consumo, etc.). "
    "Identifique:\n"
    "- linha_atributo: seletor CSS do elemento que se REPETE uma vez por "
    "atributo (a linha da tabela, o <dt>/<dd> do par, etc.).\n"
    "- nome_atributo: seletor RELATIVO a linha_atributo que aponta pro nome "
    "do atributo (ex.: \"Potência\").\n"
    "- valor_atributo: seletor RELATIVO a linha_atributo que aponta pro "
    "valor (ex.: \"150 cv\").\n"
    "- marca, modelo, versao, ano: seletores (absolutos na página, fora do "
    "loop de atributos) de onde aparecem esses dados de identificação do "
    "carro, se existirem na página.\n\n"
    "Responda em JSON: {\"marca\": \"...\"|null, \"modelo\": \"...\"|null, "
    "\"versao\": \"...\"|null, \"ano\": \"...\"|null, "
    "\"linha_atributo\": \"...\", \"nome_atributo\": \"...\", "
    "\"valor_atributo\": \"...\"}"
)

PROMPT_ANUNCIOS = (
    _INSTRUCAO_COMUM
    + "\n\nEsta página mostra uma LISTAGEM DE ANÚNCIOS de carros à venda. "
    "Identifique:\n"
    "- item_listagem: seletor CSS do elemento que se REPETE uma vez por "
    "anúncio (o card/linha da listagem).\n"
    "- preco, marca, modelo, versao, ano, url_anuncio, localizacao, "
    "vendedor, publicado_em: seletores RELATIVOS a item_listagem (use null "
    "pros que não existirem).\n\n"
    "Responda em JSON: {\"item_listagem\": \"...\", \"preco\": \"...\", "
    "\"marca\": \"...\"|null, \"modelo\": \"...\"|null, \"versao\": \"...\"|null, "
    "\"ano\": \"...\"|null, \"url_anuncio\": \"...\"|null, "
    "\"localizacao\": \"...\"|null, \"vendedor\": \"...\"|null, "
    "\"publicado_em\": \"...\"|null}"
)

PROMPT_COMENTARIOS = (
    _INSTRUCAO_COMUM
    + "\n\nEsta página mostra COMENTÁRIOS/AVALIAÇÕES sobre um anúncio ou "
    "carro. Identifique:\n"
    "- item_comentario: seletor CSS do elemento que se REPETE uma vez por "
    "comentário.\n"
    "- texto, autor, nota, publicado_em, marca, modelo, versao, ano: "
    "seletores RELATIVOS a item_comentario (use null pros que não "
    "existirem).\n\n"
    "Responda em JSON: {\"item_comentario\": \"...\", \"texto\": \"...\", "
    "\"autor\": \"...\"|null, \"nota\": \"...\"|null, "
    "\"publicado_em\": \"...\"|null, \"marca\": \"...\"|null, "
    "\"modelo\": \"...\"|null, \"versao\": \"...\"|null, \"ano\": \"...\"|null}"
)

SCHEMA_FICHA_TECNICA: dict[str, Any] = {
    "marca": "string|null", "modelo": "string|null", "versao": "string|null", "ano": "string|null",
    "linha_atributo": "string — seletor CSS", "nome_atributo": "string — seletor CSS relativo",
    "valor_atributo": "string — seletor CSS relativo",
}

SCHEMA_ANUNCIOS: dict[str, Any] = {
    "item_listagem": "string — seletor CSS", "preco": "string — seletor CSS relativo",
    "marca": "string|null", "modelo": "string|null", "versao": "string|null", "ano": "string|null",
    "url_anuncio": "string|null", "localizacao": "string|null", "vendedor": "string|null",
    "publicado_em": "string|null",
}

SCHEMA_COMENTARIOS: dict[str, Any] = {
    "item_comentario": "string — seletor CSS", "texto": "string — seletor CSS relativo",
    "autor": "string|null", "nota": "string|null", "publicado_em": "string|null",
    "marca": "string|null", "modelo": "string|null", "versao": "string|null", "ano": "string|null",
}

PROMPT_CONFIRMACAO = (
    "Você recebe uma amostra de dados que acabaram de ser extraídos de uma "
    "página de um site de carros, pelo objetivo '{objetivo}'. Escreva 1-2 "
    "frases curtas, em português, resumindo o que foi encontrado — pra um "
    "humano confirmar rapidamente se bate com o que esperava daquela página "
    "(ex.: qual marca/modelo parece ser, quantos registros vieram, e se "
    "algum valor parece claramente errado, vazio ou fora do lugar). Seja "
    "direto e específico à amostra recebida — nunca invente informação que "
    "não esteja nela. Responda só com o texto do resumo, sem formatação, "
    "sem markdown, sem repetir estas instruções."
)

PROMPT_ETAPA_NAVEGACAO = (
    _INSTRUCAO_COMUM
    + "\n\nEsta página é uma etapa de NAVEGAÇÃO — não tem os dados finais "
    "ainda, mas uma LISTA de itens (ex.: marcas, modelos, resultados de "
    "busca), cada um levando a outra página com mais detalhe. Identifique:\n"
    "- linha: seletor CSS do elemento que se REPETE uma vez por item da "
    "lista.\n"
    "- campos: um objeto com um seletor RELATIVO a linha por campo "
    "relevante que você conseguir identificar nesse item (ex.: \"nome\", "
    "\"ano\", e principalmente o link/URL pra próxima página — nomeie esse "
    "campo de forma que fique óbvio que é um link, ex.: \"url_detalhe\").\n\n"
    "Responda em JSON: {\"linha\": \"...\", \"campos\": {\"nome_do_campo\": "
    "\"seletor CSS relativo\", ...}}"
)

SCHEMA_ETAPA_NAVEGACAO: dict[str, Any] = {
    "linha": "string — seletor CSS do item repetido",
    "campos": {"nome_do_campo": "string — seletor CSS relativo a linha (um deles deve ser o link pra próxima página)"},
}

PROMPT_FILTROS = (
    "Você recebe uma lista de parâmetros de query string extraídos de uma "
    "URL de um site de carros, cada um com seu nome e o valor de exemplo "
    "capturado. Para CADA parâmetro da lista, na mesma ordem em que foram "
    "recebidos, classifique seu papel escolhendo EXATAMENTE uma destas "
    "opções: \"marca\", \"modelo\", \"versao\", \"ano\", \"id\" (identificador "
    "único de um item específico, ex.: código do anúncio/veículo), ou "
    "\"outro\" (paginação, ordenação, tracking/utm, ou qualquer coisa que "
    "não seja um filtro de busca de carro). Responda em JSON: "
    "{\"parametros\": [{\"nome_param\": \"...\", \"papel\": \"...\"}, ...]} — "
    "um item por parâmetro recebido, na mesma ordem, com o \"nome_param\" "
    "idêntico ao recebido."
)

SCHEMA_FILTROS: dict[str, Any] = {
    "parametros": [{"nome_param": "string — idêntico ao recebido", "papel": "marca|modelo|versao|ano|id|outro"}],
}

PROMPT_POR_OBJETIVO: dict[TipoObjetivoScraper, str] = {
    TipoObjetivoScraper.FICHA_TECNICA: PROMPT_FICHA_TECNICA,
    TipoObjetivoScraper.ANUNCIOS: PROMPT_ANUNCIOS,
    TipoObjetivoScraper.COMENTARIOS: PROMPT_COMENTARIOS,
}

SCHEMA_POR_OBJETIVO: dict[TipoObjetivoScraper, dict[str, Any]] = {
    TipoObjetivoScraper.FICHA_TECNICA: SCHEMA_FICHA_TECNICA,
    TipoObjetivoScraper.ANUNCIOS: SCHEMA_ANUNCIOS,
    TipoObjetivoScraper.COMENTARIOS: SCHEMA_COMENTARIOS,
}
