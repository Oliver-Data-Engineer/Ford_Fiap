"""
normalizador.py
Consolida especificações técnicas de múltiplas fontes num formato padronizado.

Uso:
    from src.utils.normalizador import normalizar

    specs = normalizar(
        resultados_por_fonte={
            "carrosnaweb": [{"atributo": "Motor", "valor": "2.0 Turbo"}],
            "icarros":     [{"atributo": "Potência", "valor": "272 cv"}],
        },
        atributos_desejados=["motor", "potência"],
    )
    # => [{"atributo": "Motor", "valor": "2.0 Turbo", "fonte": "carrosnaweb",
    #      "categoria": "Motor & Transmissão"}, ...]
"""
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# ── Limites de caracteres por campo ──────────────────────────────────────────
MAX_ATRIBUTO = 200
MAX_VALOR    = 500
MAX_FONTE    = 100

# ── Categorização dos atributos ──────────────────────────────────────────────
# Taxonomia baseada nas 14 seções de "FIAP-Ford - Data sheet_Desafio_01_v02.xlsx"
# (ficha de equipamentos do Ford Ranger por versão) — cada categoria carrega um
# conjunto de palavras-chave derivadas dos próprios nomes de linha da planilha.
# Casamento por PALAVRA/FRASE INTEIRA (com \b), case/acento-insensitive — não
# por substring cru, que gera falso positivo (ex.: "aro" dentro de "farol",
# "eletric" dentro de "elétrico" usado como adjetivo genérico em teto/vidro/
# banco). A primeira categoria cujo termo aparecer no atributo vence. Sem
# correspondência → "Outros".
CATEGORIAS: list[str] = [
    "Motor & Transmissão",
    "Rodas & Pneus",
    "Conectividade",
    "Multimídia & Som",
    "Ar-condicionado",
    "Segurança",
    "Alta Tecnologia",
    "Vidros & Travas",
    "Acabamento Interno",
    "Teto Solar",
    "Bancos",
    "Iluminação",
    "Tração 4x4",
    "Outros",
]

_PALAVRAS_CHAVE_POR_CATEGORIA: dict[str, tuple[str, ...]] = {
    "Motor & Transmissão": (
        "motor", "potencia", "torque", "cilindrada", "cilindro", "cambio",
        "marchas", "turbo", "biturbo", "consumo", "autonomia", "flex",
        "diesel", "hibrido", "fhev", "phev", "bev", "combustivel",
        "transmissao", "paddle shift", "deslocamento", "rotacao maxima",
        "compressao", "valvulas",
    ),
    "Rodas & Pneus": ("roda", "liga leve", "pneu", "estepe", "aro"),
    "Conectividade": (
        "aplicativo", "assistente digital", "localizacao do veiculo",
        "ota", "wi-fi", "wifi", "geofencing", "hotspot", "conectividade",
        "atualizacoes ota",
    ),
    "Multimídia & Som": (
        "bluetooth", "camera", "gps", "navegador", "radio", "alto-falantes",
        "tweeters", "som", "head up display", "hud", "android auto",
        "car play", "carplay", "usb", "tela no painel", "multimidia",
        "subwoofer", "reconhecimento de voz", "carregamento wireless",
    ),
    "Ar-condicionado": ("ar condicionado", "climatiza"),
    "Segurança": (
        "airbag", "freio", "abs", "pressao dos pneus", "tpms", "capotamento",
        "controle de descida", "controle de tracao", "controle adaptativo de carga",
        "controle de reboque",
    ),
    "Alta Tecnologia": (
        "piloto automatico", "limitador de velocidade", "sensor de estacionamento",
        "estacionamento automatico", "ponto-cego", "ponto cego", "blis",
        "sistema de permanencia na faixa", "aeb", "colisao", "retrovisor",
        "keyless", "partida remota", "detector de fadiga", "sinais de transito",
        "modo de conducao", "drive mode", "ignicao remota", "sensor de chuva",
    ),
    "Vidros & Travas": ("vidro", "trava", "alarme", "porta-malas", "porta malas"),
    "Acabamento Interno": (
        "revestido em couro", "revestimento", "volante", "alcantara",
        "soft touch", "manopla",
    ),
    "Teto Solar": ("teto solar", "teto de vidro", "sunroof"),
    "Bancos": ("banco", "bancos", "assento", "recaro"),
    "Iluminação": ("farol", "farois", "led", "xenon", "lanterna", "drl", "iluminacao"),
    "Tração 4x4": (
        "4x4", "tracao", "awd", "diferencial", "santo antonio",
        "estribo", "terrain management", "suspensao off", "propulsao",
    ),
}

# Palavras-chave curtas/genéricas que só podem valer como palavra ISOLADA
# (ex.: "aro" não pode casar dentro de "farol"; "led" não pode casar dentro
# de outra palavra) — todo o resto casa como frase completa via \b...\b,
# que já cobre isso naturalmente; mantido aqui só por clareza/documentação.


def _sem_acento(texto: str) -> str:
    """Remove acentos pra casar palavras-chave sem depender de acentuação."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _contem_termo(alvo: str, termo: str) -> bool:
    """Casa `termo` (palavra ou frase) em `alvo` ancorado só no INÍCIO da
    palavra (\\b antes, sem \\b depois) — evita falso positivo tipo "aro"
    dentro de "farol" (não há fronteira de palavra ali), mas ainda aceita
    variações por sufixo comuns em português (plural: "pneu"→"pneus",
    "banco"→"bancos"; conjugação: "trava"→"travamento")."""
    padrao = r"\b" + re.escape(termo)
    return re.search(padrao, alvo) is not None


def categorizar_atributo(atributo: str) -> str:
    """
    Classifica um nome de atributo numa das categorias de `CATEGORIAS`, por
    palavra/frase inteira (case/acento-insensitive). Sem correspondência
    → "Outros" (mesmo papel de "Dados diversos" em fichas de comparação).
    """
    alvo = _sem_acento(atributo).lower()
    for categoria in CATEGORIAS[:-1]:  # todas menos "Outros"
        termos = _PALAVRAS_CHAVE_POR_CATEGORIA.get(categoria, ())
        if any(_contem_termo(alvo, termo) for termo in termos):
            return categoria
    return "Outros"


# ── Helpers internos ──────────────────────────────────────────────────────────

def _reparar_mojibake(texto):
    try:
        return texto.encode(chr(108)+chr(97)+chr(116)+chr(105)+chr(110)+chr(45)+chr(49)).decode(chr(117)+chr(116)+chr(102)+chr(45)+chr(56))
    except (UnicodeDecodeError, UnicodeEncodeError):
        return texto

def _limpar_texto(texto: str) -> str:
    """
    texto = _reparar_mojibake(texto)
    Aplica Unicode NFC e remove caracteres de controle
    (U+0000–U+001F e U+007F–U+009F).
    """
    texto = unicodedata.normalize("NFC", texto)
    texto = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", texto)
    return texto.strip()


def _truncar_campo(valor: str, limite: int, nome_campo: str) -> str:
    """Trunca o campo ao limite e registra aviso se necessário."""
    if len(valor) > limite:
        logger.warning(
            "[Normalizador] Campo '%s' truncado: %d → %d caracteres.",
            nome_campo, len(valor), limite,
        )
        return valor[:limite]
    return valor


def _filtrar_atributos(specs: list[dict], atributos_desejados: list[str]) -> list[dict]:
    """
    Mantém apenas as specs cujo 'atributo' contém pelo menos um dos termos
    informados (case-insensitive, substring). Descarta termos vazios/brancos.
    """
    # Descarta segmentos vazios ou só espaços
    termos = [t.strip().lower() for t in atributos_desejados if t.strip()]
    if not termos:
        return specs  # entrada vazia após limpeza → sem filtragem

    return [
        s for s in specs
        if any(termo in s["atributo"].lower() for termo in termos)
    ]


# ── API pública ───────────────────────────────────────────────────────────────

def normalizar(
    resultados_por_fonte: dict[str, list[dict]],
    atributos_desejados: list[str] | None = None,
) -> list[dict]:
    """
    Converte dados brutos de múltiplas fontes em lista padronizada de specs.

    Parâmetros
    ----------
    resultados_por_fonte:
        Dicionário onde a chave é o nome da fonte (ex: "carrosnaweb") e o
        valor é a lista de dicts {"atributo": str, "valor": str} retornada
        pelo respectivo scraper.

    atributos_desejados:
        Lista de termos para filtrar as specs por nome de atributo.
        None ou lista vazia → retorna tudo sem filtro.

    Retorna
    -------
    Lista de {"atributo": str, "valor": str, "fonte": str, "categoria": str}
    ordenada alfabeticamente por 'atributo' (case-insensitive). 'categoria' é
    atribuída por `categorizar_atributo()`, taxonomia fixa em `CATEGORIAS`.

    Comportamento para dados ausentes
    ----------------------------------
    Se 'atributos_desejados' contém termos não encontrados em nenhuma fonte,
    esses termos simplesmente não aparecem no resultado (não há criação de
    linhas "não disponível" genéricas). O campo "não disponível" só é usado
    quando o valor extraído está vazio ou em branco.
    """
    specs: list[dict] = []

    for fonte, itens in resultados_por_fonte.items():
        # Normaliza e valida o nome da fonte
        fonte_limpa = _limpar_texto(str(fonte))
        fonte_limpa = _truncar_campo(fonte_limpa, MAX_FONTE, "fonte")

        for item in itens:
            atributo_raw = _limpar_texto(str(item.get("atributo", "")))
            valor_raw    = _limpar_texto(str(item.get("valor", "")))

            # Ignora linhas sem atributo identificável
            if not atributo_raw:
                continue

            atributo = _truncar_campo(
                atributo_raw, MAX_ATRIBUTO,
                f"atributo:{atributo_raw[:40]}",
            )
            valor = _truncar_campo(
                valor_raw, MAX_VALOR,
                f"valor:{atributo_raw[:40]}",
            )

            specs.append({
                "atributo": atributo,
                "valor":    valor if valor else "não disponível",
                "fonte":    fonte_limpa,
                "categoria": categorizar_atributo(atributo),
            })

    # ── Filtro de atributos desejados ─────────────────────────────────────────
    if atributos_desejados:
        specs = _filtrar_atributos(specs, atributos_desejados)

    # ── Ordenação alfabética por atributo (A→Z, case-insensitive) ─────────────
    specs.sort(key=lambda s: s["atributo"].lower())

    return specs
