# Documentação da API

A API é construída com **FastAPI** e expõe todos os endpoints sob o prefixo `/specs`.  
O servidor Flask atua como **proxy reverso** — o browser sempre fala com o Flask (porta 5000), que encaminha as requisições para a FastAPI (porta 8000) sem expor a API diretamente ao browser.

Documentação interativa (Swagger UI): `http://localhost:8000/docs`  
Documentação alternativa (ReDoc): `http://localhost:8000/redoc`

---

## Arquitetura de comunicação

```
Browser
  │
  │  GET /specs/fabricantes        (origem Flask :5000)
  ▼
Flask app.py  ──proxy──►  FastAPI specs_api.py  ──►  CarrosWeb + iCarros
  │                              │
  │  ◄── JSON response ──────────┘
  │
  ▼
Browser (recebe a resposta)
```

O Flask registra a rota `/specs/<path:caminho>` que repassa qualquer chamada GET para a FastAPI. A chamada `POST /scraping/executar` também é proxied para `POST /specs/executar`.

---

## Endpoints

### `GET /specs/fabricantes`

Retorna a lista consolidada de fabricantes de automóveis disponíveis nas fontes externas (carrosnaweb.com.br e icarros.com.br).

**Parâmetros:** nenhum

**Resposta 200 — sucesso:**
```json
{
  "fabricantes": [
    { "id_busca": "ford",    "nome_exibicao": "Ford" },
    { "id_busca": "toyota",  "nome_exibicao": "Toyota" },
    { "id_busca": "chevrolet", "nome_exibicao": "Chevrolet" }
  ]
}
```

**Resposta 200 — com falha parcial** (uma fonte indisponível):
```json
{
  "fabricantes": [...],
  "fontes_com_erro": ["icarros"]
}
```

**Resposta 503 — todas as fontes indisponíveis:**
```json
{
  "erro": "Nenhuma fonte disponível no momento."
}
```

**Comportamento:**
- Consulta carrosnaweb e icarros em paralelo com timeout de 10 s por fonte
- Deduplica por `nome_exibicao` (case-insensitive), preserva a primeira ocorrência
- Ordena o resultado em ordem alfabética por `nome_exibicao`
- Se uma fonte falha, retorna a lista parcial das que responderam

**Exemplo de chamada:**
```bash
curl http://localhost:8000/specs/fabricantes
```

---

### `GET /specs/modelos?fabricante={fabricante}`

Retorna os modelos disponíveis para um fabricante.

**Parâmetros query:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `fabricante` | string | sim | Nome ou slug do fabricante (ex: `Ford`, `ford`) |

**Resposta 200 — sucesso:**
```json
{
  "modelos": [
    { "fabricante": "Ford", "id_busca": "Ranger",   "nome_exibicao": "Ranger" },
    { "fabricante": "Ford", "id_busca": "Ka",        "nome_exibicao": "Ka" },
    { "fabricante": "Ford", "id_busca": "EcoSport",  "nome_exibicao": "EcoSport" }
  ]
}
```

**Resposta 400 — parâmetro ausente/vazio:**
```json
{
  "erro": "Parâmetro 'fabricante' é obrigatório."
}
```

**Resposta 503 — todas as fontes indisponíveis:**
```json
{
  "erro": "Nenhuma fonte disponível no momento."
}
```

**Comportamento:**
- Limite de 500 itens no resultado
- Deduplica por `nome_exibicao`
- Consulta todas as fontes em paralelo com timeout de 10 s

**Exemplo de chamada:**
```bash
curl "http://localhost:8000/specs/modelos?fabricante=Ford"
```

---

### `GET /specs/versoes?fabricante={fabricante}&modelo={modelo}`

Retorna as versões disponíveis para um modelo específico.

**Parâmetros query:**

| Parâmetro | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `fabricante` | string | sim | Nome ou slug do fabricante |
| `modelo` | string | sim | Nome ou slug do modelo |

**Resposta 200 — sucesso:**
```json
{
  "versoes": [
    {
      "id_veiculo":  "ranger-raptor-3-0-v6-2026",
      "fabricante":  "Ford",
      "versao":      "Ranger Raptor 3.0 V6 2026",
      "ano":         "2026",
      "url_ficha":   "https://www.carrosnaweb.com.br/fichadetalhe.asp?codigo=12345"
    }
  ]
}
```

**Resposta 400 — parâmetros ausentes:**
```json
{
  "erro": "Parâmetros obrigatórios ausentes: modelo."
}
```

**Comportamento:**
- Limite de 200 itens no resultado
- Consulta todas as fontes em paralelo; agrega resultados de todas
- Se nenhuma versão encontrada mas fontes responderam: `{"versoes": []}`

**Exemplo de chamada:**
```bash
curl "http://localhost:8000/specs/versoes?fabricante=Ford&modelo=Ranger"
```

---

### `POST /specs/executar`

Executa a busca completa de especificações técnicas para um veículo. Este é o endpoint principal da plataforma.

**Body JSON:**

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `fabricante` | string | sim | Nome do fabricante |
| `modelo` | string | sim | Nome do modelo |
| `versao` | string | sim | Nome ou slug da versão |
| `atributos` | list[string] | não | Filtro de atributos (vazio = retorna tudo) |

**Exemplo de body:**
```json
{
  "fabricante": "Ford",
  "modelo":     "Ranger",
  "versao":     "Raptor",
  "atributos":  ["motor", "potência", "torque", "tração", "câmbio"]
}
```

**Resposta 200 — sucesso:**
```json
{
  "especificacoes": [
    { "atributo": "Câmbio",    "valor": "Automático 10 velocidades", "fonte": "carrosnaweb" },
    { "atributo": "Motor",     "valor": "3.0 V6 EcoBoost Turbo",     "fonte": "carrosnaweb" },
    { "atributo": "Potência",  "valor": "292 cv @ 5.500 rpm",         "fonte": "icarros" },
    { "atributo": "Torque",    "valor": "491 Nm @ 3.500 rpm",         "fonte": "icarros" },
    { "atributo": "Tração",    "valor": "4x4",                        "fonte": "carrosnaweb" }
  ]
}
```

**Resposta 200 — sem resultados:**
```json
{
  "especificacoes": []
}
```

**Resposta 400 — campos obrigatórios ausentes:**
```json
{
  "erro": "Campos obrigatórios ausentes: versao."
}
```

**Comportamento:**
1. Consulta ambas as fontes em paralelo com timeout de 30 s total
2. Para cada fonte: encontra a versão solicitada, obtém a `url_ficha`, extrai os pares atributo/valor
3. Passa todos os resultados pelo normalizador (`src/utils/normalizador.py`)
4. Se `atributos` for fornecido: filtra por substring case-insensitive
5. Retorna lista ordenada alfabeticamente por `atributo`

**Exemplo de chamada:**
```bash
curl -X POST http://localhost:8000/specs/executar \
  -H "Content-Type: application/json" \
  -d '{"fabricante":"Ford","modelo":"Ranger","versao":"Raptor","atributos":["motor","potência"]}'
```

---

## Proxy Flask

O Flask registra rotas que encaminham todas as chamadas da API para a FastAPI, evitando problemas de CORS no browser.

| Rota Flask | Método | Encaminha para |
|---|---|---|
| `/specs/<caminho>` | GET | `http://127.0.0.1:8000/specs/<caminho>` |
| `/scraping/executar` | POST | `http://127.0.0.1:8000/specs/executar` |

O timeout do proxy é 12 s para GETs e 35 s para o POST (margem acima dos 30 s do `AbortController` no browser).

---

## Normalização dos dados

O módulo `src/utils/normalizador.py` é responsável por consolidar os dados brutos de múltiplas fontes.

**Formato de entrada** (por fonte):
```python
{
  "carrosnaweb": [{"atributo": "Motor", "valor": "3.0 V6"}],
  "icarros":     [{"atributo": "Potência", "valor": "292 cv"}]
}
```

**Formato de saída:**
```python
[
  {"atributo": "Motor",    "valor": "3.0 V6", "fonte": "carrosnaweb"},
  {"atributo": "Potência", "valor": "292 cv", "fonte": "icarros"}
]
```

**Regras aplicadas:**
1. Normalização Unicode NFC e remoção de caracteres de controle
2. Truncamento com log se `atributo` > 200 chars ou `valor` > 500 chars
3. Valores vazios substituídos por `"não disponível"`
4. Filtro case-insensitive por substring (quando `atributos` fornecido)
5. Ordenação alfabética A→Z por `atributo`

---

## Fontes de dados

### carrosnaweb.com.br (`src/utils/CarrosWeb.py`)

| Método | Descrição |
|---|---|
| `obter_fabricantes()` | Parse do `<select id="fabricante">` em `/catalogo.asp` |
| `obter_modelos(fabricante)` | Parse do `<select id="varnome">` em `/catalogo.asp?fabricante=` |
| `extrair_versoes(url)` | Parse de `<td align="left"> <a href="fichadetalhe.asp">` |
| `obter_todas_urls_paginacao(url_base)` | Detecta total de páginas via `<font>Página X de Y</font>` |
| `extrair_ficha_tecnica(url_ficha)` | Parse de tabelas `<tr><td>` e `<dl><dt><dd>` na página da ficha |

### icarros.com.br (`src/utils/iCarros.py`)

| Método | Descrição |
|---|---|
| `obter_fabricantes()` | Parse de `<div class="card-base"> <p>` em `/catalogo-zero-km/marcas/` |
| `obter_modelos(fabricante)` | Parse de `<a class="complete-ad-card"> <h2>` na página da marca |
| `extrair_versoes(url)` | Parse de `<select class="versions-select">` na página do modelo |
| `obter_todas_urls_paginacao(url_base)` | Retorna apenas a URL base (iCarros não pagina dessa forma) |
| `extrair_ficha_tecnica(url_ficha)` | Parse de `div.sheet-theme-container` → `div.technical-sheet-item` com extração hierárquica de valor |

**Camada HTTP do iCarros:** usa `curl_cffi` com `impersonate="chrome120"` para bypass de TLS fingerprinting, executado via `run_in_executor` para não bloquear o event loop assíncrono.

---

## Códigos de status HTTP

| Status | Significado |
|---|---|
| 200 | Sucesso — resultado pode ser lista vazia |
| 400 | Parâmetros obrigatórios ausentes ou inválidos |
| 503 | Todas as fontes externas indisponíveis |
| 504 | Timeout ao consultar as fontes (via proxy Flask) |
