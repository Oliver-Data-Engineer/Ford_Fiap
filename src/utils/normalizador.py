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
    # => [{"atributo": "Motor", "valor": "2.0 Turbo", "fonte": "carrosnaweb"}, ...]
"""
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# ── Limites de caracteres por campo ──────────────────────────────────────────
MAX_ATRIBUTO = 200
MAX_VALOR    = 500
MAX_FONTE    = 100


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
    Lista de {"atributo": str, "valor": str, "fonte": str} ordenada
    alfabeticamente por 'atributo' (case-insensitive).

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
            })

    # ── Filtro de atributos desejados ─────────────────────────────────────────
    if atributos_desejados:
        specs = _filtrar_atributos(specs, atributos_desejados)

    # ── Ordenação alfabética por atributo (A→Z, case-insensitive) ─────────────
    specs.sort(key=lambda s: s["atributo"].lower())

    return specs
