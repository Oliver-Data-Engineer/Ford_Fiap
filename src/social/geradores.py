"""
geradores.py
Gera posts sintéticos de Reddit/X/Instagram — não existe integração real com
nenhuma dessas redes (exigiria credenciais próprias de cada uma, fora de
escopo). O texto gerado é só o INSUMO: quem decide o sentimento de cada post
é o LLM de verdade, no step de enriquecimento (`src/social/enrichment.py`) —
aqui não se atribui sentimento a nenhum post.

Os templates de Ford tendem a ser mais elogiosos e os de concorrentes mais
neutros/críticos (pedido explícito do usuário — "mostrando a Ford com os
dados melhores"), mas o texto de cada post é só uma frase realista; a
classificação de sentimento em si emerge da IA, não é hardcoded aqui.
"""
from __future__ import annotations

import random

from src.social.models import PostSocial

MODELOS_FORD = ["Territory", "Ka", "EcoSport", "Bronco", "Maverick", "Ranger"]

MARCAS_CONCORRENTES: dict[str, list[str]] = {
    "Chevrolet": ["Onix", "Tracker", "S10"],
    "Volkswagen": ["Polo", "T-Cross", "Nivus"],
    "Toyota": ["Corolla", "Hilux", "Yaris"],
    "Hyundai": ["HB20", "Creta"],
}

OBJETOS = [
    "motor", "consumo", "design", "porta-malas", "câmbio", "preço",
    "acabamento interno", "central multimídia", "suspensão", "revenda",
]

_AUTORES_POR_REDE: dict[str, list[str]] = {
    "reddit": [f"u/carro_fan_{i:02d}" for i in range(1, 30)],
    "x": [f"@{n}_br" for n in ("motorhead", "carrostop", "revisaobr", "garagemtech", "estradabr")],
    "instagram": [f"@{n}.oficial" for n in ("motorvlog", "carrosdodia", "reviewbr", "garagem360")],
}

_TEMPLATES_FORD: dict[str, list[str]] = {
    "reddit": [
        "Comprei um Ford {modelo} há {meses} meses e tô surpreso com o {objeto}, recomendo demais.",
        "Discussão: o {objeto} do Ford {modelo} realmente compensa o preço? Na minha experiência, sim.",
        "Fiz alguns milhares de km com o Ford {modelo}, o {objeto} continua excelente, sem susto nenhum.",
    ],
    "x": [
        "Ford {modelo} surpreendendo no {objeto}, muito satisfeito com a compra #Ford{modelo}",
        "Test-drive do Ford {modelo} hoje, o {objeto} é show de bola #Ford",
        "{modelo} da Ford com um {objeto} que não deixa a desejar, virei fã.",
    ],
    "instagram": [
        "Rodando com meu Ford {modelo}, o {objeto} tá impecável! #ford #carrobrasil",
        "Dia de revisão do Ford {modelo}, {objeto} nota 10 como sempre.",
        "Quem tem Ford {modelo} sabe: {objeto} surpreendente, virei apaixonado.",
    ],
}

_TEMPLATES_CONCORRENTE: dict[str, list[str]] = {
    "reddit": [
        "Alguém mais meio decepcionado com o {objeto} do {marca} {modelo}? Esperava mais por esse preço.",
        "Comparei o {marca} {modelo} com outras opções, o {objeto} ficou abaixo do que eu queria.",
        "Tô na dúvida sobre o {marca} {modelo}, o {objeto} parece ok mas nada excepcional.",
    ],
    "x": [
        "{marca} {modelo} até que anda bem, mas o {objeto} deixa a desejar às vezes.",
        "Fui ver o {marca} {modelo} na concessionária, {objeto} não me convenceu tanto.",
        "{modelo} da {marca}: {objeto} mediano, esperava mais pelo hype.",
    ],
    "instagram": [
        "Test-drive do {marca} {modelo} hoje, {objeto} até que é ok, nada de mais.",
        "{marca} {modelo}: bonito por fora, mas o {objeto} não empolgou tanto.",
    ],
}

_PROPORCAO_FORD = 0.6  # ~60% Ford / 40% concorrente — dá volume pra comparação


def gerar_posts(termo_busca: str, redes: list[str], quantidade: int = 40) -> list[PostSocial]:
    redes_validas = [r for r in redes if r in _AUTORES_POR_REDE] or list(_AUTORES_POR_REDE)
    seed = f"{termo_busca}|{'-'.join(sorted(redes_validas))}|{quantidade}"
    rng = random.Random(seed)

    posts: list[PostSocial] = []
    for _ in range(quantidade):
        rede = rng.choice(redes_validas)
        objeto = rng.choice(OBJETOS)
        autor = rng.choice(_AUTORES_POR_REDE[rede])

        if rng.random() < _PROPORCAO_FORD:
            modelo = rng.choice(MODELOS_FORD)
            template = rng.choice(_TEMPLATES_FORD[rede])
            texto = template.format(modelo=modelo, objeto=objeto, meses=rng.randint(1, 24))
        else:
            marca = rng.choice(list(MARCAS_CONCORRENTES))
            modelo = rng.choice(MARCAS_CONCORRENTES[marca])
            template = rng.choice(_TEMPLATES_CONCORRENTE[rede])
            texto = template.format(marca=marca, modelo=modelo, objeto=objeto)

        posts.append(PostSocial(
            rede=rede,
            autor=autor,
            texto=texto,
            curtidas=rng.randint(0, 300),
            respostas=rng.randint(0, 40),
        ))

    return posts
