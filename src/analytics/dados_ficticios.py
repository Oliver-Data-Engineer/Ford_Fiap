"""
dados_ficticios.py
Conjunto fixo de dados ilustrativos para o Dashboard (`/analytics`), usado
quando `settings.dashboard_dados_reais` é False (padrão) — mesma forma de
resposta que os endpoints reais devolvem (ver `src/api/analytics_api.py`),
mas sem nenhuma consulta ao BigQuery. Existe pra a aba funcionar de
demonstração sem depender de nenhuma credencial.

O dataset é montado uma única vez, com seed fixa, na importação do módulo —
nunca gerado de novo a cada request, pra os números não "pularem" a cada
refresh da página. Cada endpoint só filtra/agrega essa lista fixa em Python,
reaproveitando os mesmos cálculos de NPS/Likert de `src/analytics/indicadores.py`.
"""
import random
from datetime import datetime, timedelta, timezone

from src.analytics.indicadores import _com_indicadores, agregar_sentimento

CANAIS_FICTICIOS = [
    {"canal_id": "UC_fic_01", "canal_nome": "Auto Show Brasil"},
    {"canal_id": "UC_fic_02", "canal_nome": "Motor Talk BR"},
    {"canal_id": "UC_fic_03", "canal_nome": "Carros & Cia"},
    {"canal_id": "UC_fic_04", "canal_nome": "Review de Carros"},
    {"canal_id": "UC_fic_05", "canal_nome": "Ford Fans BR"},
]

SENTIMENTOS = ["positivo", "negativo", "neutro", "misto"]

OBJETOS = [
    "motor", "consumo", "design", "porta-malas", "câmbio", "preço",
    "acabamento interno", "central multimídia", "suspensão", "revenda",
]

MODELOS_FORD = ["Territory", "Ka", "EcoSport", "Bronco", "Maverick", "Ranger"]

_TEMPLATES_POR_SENTIMENTO: dict[str, list[str]] = {
    "positivo": [
        "Comprei o Ford {modelo} mês passado e o {objeto} superou minhas expectativas, recomendo muito.",
        "O {objeto} do Ford {modelo} é excelente, muito melhor do que eu esperava pelo preço.",
        "Fiz um test-drive no Ford {modelo} e o {objeto} me convenceu na hora, carro muito bem feito.",
        "Depois de 6 meses com o Ford {modelo}, o {objeto} continua impecável, sem nenhum problema.",
        "Essa análise ficou justa — o {objeto} do Ford {modelo} realmente é um dos melhores da categoria.",
        "Já troquei de carro 3 vezes e o {objeto} do Ford {modelo} é disparado o melhor que já tive.",
    ],
    "negativo": [
        "O {objeto} do meu Ford {modelo} decepcionou, esperava mais por esse valor.",
        "Fui pesquisar o Ford {modelo} mas o {objeto} me deixou com pé atrás, vi vários relatos parecidos.",
        "Achei o {objeto} do Ford {modelo} abaixo da média do segmento, precisa melhorar.",
        "Levei na revisão por causa do {objeto} do Ford {modelo}, fiquei chateado com o atendimento.",
    ],
    "neutro": [
        "Alguém sabe informar como é o {objeto} do Ford {modelo} no dia a dia?",
        "Vi esse vídeo comparando o {objeto} de vários carros, o Ford {modelo} aparece no meio da tabela.",
        "Ainda estou em dúvida entre o Ford {modelo} e outras opções, principalmente pelo {objeto}.",
        "Alguém tem dado de consumo real do Ford {modelo} na cidade?",
    ],
    "misto": [
        "O {objeto} do Ford {modelo} é bom, mas o preço me pegou de surpresa.",
        "Gosto do Ford {modelo} no geral, só o {objeto} que ainda me deixa em dúvida.",
        "Pontos positivos e negativos: o {objeto} é ótimo, mas o pós-venda deixa a desejar.",
    ],
}

_MOTIVO_POR_SENTIMENTO = {
    "positivo": "elogia a experiência com o veículo",
    "negativo": "reclama de um ponto específico do veículo",
    "neutro": "faz uma pergunta ou comparação sem opinião definida",
    "misto": "mistura elogio e ressalva no mesmo comentário",
}

_ADJETIVOS_POR_SENTIMENTO = {
    "positivo": [["excelente", "confiável"], ["surpreendente", "robusto"], ["econômico", "confortável"]],
    "negativo": [["caro", "decepcionante"], ["fraco", "abaixo da média"]],
    "neutro": [["comum"], []],
    "misto": [["bom", "caro"], ["confortável", "caro"]],
}

_PESO_SENTIMENTO = {"positivo": 0.55, "negativo": 0.15, "neutro": 0.15, "misto": 0.15}

_AUTORES = [f"usuario_ficticio_{i:03d}" for i in range(1, 61)]

_QUANTIDADE_LINHAS = 300
_SEED = 20240915  # fixa — dataset estável entre reinícios da aplicação


def _gerar_dataset() -> list[dict]:
    rng = random.Random(_SEED)
    agora = datetime(2026, 9, 1, tzinfo=timezone.utc)
    linhas: list[dict] = []
    for i in range(_QUANTIDADE_LINHAS):
        sentimento = rng.choices(SENTIMENTOS, weights=[_PESO_SENTIMENTO[s] for s in SENTIMENTOS])[0]
        canal = rng.choice(CANAIS_FICTICIOS)
        objeto = rng.choice(OBJETOS)
        modelo = rng.choice(MODELOS_FORD)
        template = rng.choice(_TEMPLATES_POR_SENTIMENTO[sentimento])
        dias_atras = rng.randint(0, 45)
        linhas.append({
            "comment_id": f"fic_{i:04d}",
            "video_id": f"ficvideo_{i % 40:03d}",
            "run_id": "ficticio",
            "transcript_id": f"fictranscript_{i % 40:03d}",
            "texto": template.format(modelo=modelo, objeto=objeto),
            "autor": rng.choice(_AUTORES),
            "canal_id": canal["canal_id"],
            "canal_nome": canal["canal_nome"],
            "sentimento": sentimento,
            "motivo": _MOTIVO_POR_SENTIMENTO[sentimento],
            "adjetivos": rng.choice(_ADJETIVOS_POR_SENTIMENTO[sentimento]),
            "objeto": objeto,
            "likes": rng.randint(0, 120),
            "coletado_em": (agora - timedelta(days=dias_atras)).isoformat(),
        })
    return linhas


_DATASET: list[dict] = _gerar_dataset()


def _filtrar(
    canal_id: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
) -> list[dict]:
    linhas = _DATASET
    if canal_id:
        linhas = [l for l in linhas if l["canal_id"] == canal_id]
    if sentimento:
        linhas = [l for l in linhas if l["sentimento"] == sentimento]
    if marca:
        alvo = marca.lower()
        linhas = [l for l in linhas if alvo in l["texto"].lower() or alvo in l["objeto"].lower()]
    if modelo:
        alvo = modelo.lower()
        linhas = [l for l in linhas if alvo in l["texto"].lower() or alvo in l["objeto"].lower()]
    return linhas


def _contagem_por(linhas: list[dict], campo: str) -> list[dict]:
    contagem: dict[str, int] = {}
    for l in linhas:
        contagem[l[campo]] = contagem.get(l[campo], 0) + 1
    ordenado = sorted(contagem.items(), key=lambda kv: kv[1], reverse=True)
    return [{campo: chave, "total": total} for chave, total in ordenado]


def filtros_ficticios() -> dict:
    return {
        "canais": [dict(c) for c in CANAIS_FICTICIOS],
        "sentimentos": list(SENTIMENTOS),
    }


def resumo_ficticio(
    canal_id: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
) -> dict:
    linhas = _filtrar(canal_id, sentimento, marca, modelo)
    return {
        "total_comentarios_analisados": len(linhas),
        "total_videos_analisados": len({l["video_id"] for l in linhas}),
        "total_canais": len({l["canal_id"] for l in linhas}),
        "por_sentimento": _contagem_por(linhas, "sentimento"),
        "por_objeto": _contagem_por(linhas, "objeto")[:15],
    }


_COLUNAS_COMENTARIO = [
    "comment_id", "video_id", "run_id", "transcript_id", "texto", "autor",
    "canal_nome", "sentimento", "motivo", "adjetivos", "objeto", "coletado_em",
]


def comentarios_ficticios(
    canal_id: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
    limite: int = 50, offset: int = 0,
) -> dict:
    linhas = sorted(
        _filtrar(canal_id, sentimento, marca, modelo),
        key=lambda l: l["coletado_em"], reverse=True,
    )
    pagina = linhas[offset: offset + limite]
    return {
        "comentarios": [{c: l[c] for c in _COLUNAS_COMENTARIO} for l in pagina],
        "limite": limite,
        "offset": offset,
    }


def _agrupar_com_indicadores(linhas: list[dict], campo: str, limite: int) -> list[dict]:
    grupos: dict[str, list[dict]] = {}
    for l in linhas:
        grupos.setdefault(l[campo], []).append(l)
    agregados = [
        {campo: chave, **agregar_sentimento(sub)}
        for chave, sub in grupos.items()
    ]
    agregados.sort(key=lambda l: l["total"], reverse=True)
    return [_com_indicadores(l) for l in agregados[:limite]]


def satisfacao_ficticia(
    canal_id: str | None = None, sentimento: str | None = None,
    marca: str | None = None, modelo: str | None = None,
) -> dict:
    linhas = _filtrar(canal_id, sentimento, marca, modelo)

    tendencia_grupos: dict[str, list[dict]] = {}
    for l in linhas:
        dia = l["coletado_em"][:10]
        tendencia_grupos.setdefault(dia, []).append(l)
    tendencia = [
        {"dia": dia, **agregar_sentimento(sub)}
        for dia, sub in sorted(tendencia_grupos.items())
    ]

    engajamento_grupos: dict[str, list[dict]] = {}
    for l in linhas:
        engajamento_grupos.setdefault(l["sentimento"], []).append(l)
    engajamento = [
        {
            "sentimento": sentimento_chave,
            "likes_medio": round(sum(x["likes"] for x in sub) / len(sub), 1) if sub else None,
            "total": len(sub),
        }
        for sentimento_chave, sub in sorted(engajamento_grupos.items())
    ]

    return {
        "geral": _com_indicadores(agregar_sentimento(linhas)),
        "por_objeto": _agrupar_com_indicadores(linhas, "objeto", 12),
        "por_canal": _agrupar_com_indicadores(linhas, "canal_nome", 12),
        "tendencia": [_com_indicadores(l) for l in tendencia],
        "engajamento_por_sentimento": engajamento,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Comparação de marcas — Ford vs. concorrente escolhido
# ─────────────────────────────────────────────────────────────────────────────
# Números fixos (não é uma amostra do `_DATASET` acima) — pensados só pra
# ilustrar a seção "Comparação de marcas" do Dashboard no modo demonstração.
# No modo real (`DASHBOARD_REAL=true`) esse mesmo endpoint usa o `marca` já
# aceito por `_filtro_comum` sobre o warehouse de verdade — lá o resultado
# reflete o que estiver de fato nos comentários coletados, sem favorecer
# nenhuma marca.

FORD_FICTICIO = {"marca": "Ford", "nps": 62.4, "pct_positivo": 68.0, "engajamento_medio": 41.2, "mencoes": 214}

COMPARATIVO_FICTICIO: dict[str, dict] = {
    "Chevrolet": {"marca": "Chevrolet", "nps": 24.1, "pct_positivo": 47.0, "engajamento_medio": 28.6, "mencoes": 176},
    "Volkswagen": {"marca": "Volkswagen", "nps": 31.5, "pct_positivo": 51.0, "engajamento_medio": 33.4, "mencoes": 189},
    "Toyota": {"marca": "Toyota", "nps": 44.8, "pct_positivo": 58.0, "engajamento_medio": 36.9, "mencoes": 201},
    "Hyundai": {"marca": "Hyundai", "nps": 29.7, "pct_positivo": 49.0, "engajamento_medio": 30.1, "mencoes": 168},
}


def comparativo_ficticio(marca_concorrente: str) -> dict:
    concorrente = COMPARATIVO_FICTICIO.get(marca_concorrente, next(iter(COMPARATIVO_FICTICIO.values())))
    return {"ford": dict(FORD_FICTICIO), "concorrente": dict(concorrente)}
