# YouTube Playground, Pipelines e Agente LLM

Framework visual para experimentar e operacionalizar pipelines de análise de
conteúdo. A lógica que existia solta no `youtube.ipynb` foi separada em camadas
independentes: **dados**, **tratamento**, **inteligência** e **orquestração**.

O mesmo Playground serve para carros, produtos, marcas, canais ou qualquer
domínio textual — o que muda é o prompt, não o código.

---

## 1. Fluxo

```
                ┌─────────────────┐
                │ YouTube Search  │   Busca ou URL direta
                └────────┬────────┘
                         ▼
                  Seleção de vídeos
                         ▼
                ┌─────────────────┐
                │       RAW       │   Resposta original das APIs (imutável)
                └────────┬────────┘
                         ▼
                ┌─────────────────┐
                │     REFINED     │   Limpeza e normalização determinística
                └────────┬────────┘
                         ▼
                ┌─────────────────┐
                │     AGENTE      │   Prompt + LLM (provider intercambiável)
                └────────┬────────┘
                         ▼
                ┌─────────────────┐
                │      TRUST      │   Interpretação estruturada + rastreio
                └────────┬────────┘
             ┌───────────┴───────────┐
             ▼                       ▼
       Playground              Pipeline salva
                                     ▼
                               DAG do Airflow
                                     ▼
                                  Agenda
```

**Playground e Airflow executam o mesmo `PipelineEngine`.** Não existem duas
implementações da regra de negócio.

---

## 2. Camadas de dado

| Camada | O que é | Quem produz | Regra |
|---|---|---|---|
| **RAW** | Resposta original das APIs, sem tocar em nada | `src/youtube/` | Nunca é alterada — serve para auditoria, debug e reprocessamento |
| **REFINED** | Texto limpo, campos padronizados, duplicados removidos | `src/pipelines/refine.py` | Determinística: mesmo RAW → mesmo REFINED, sempre |
| **TRUST** | Percepções, categorias, sentimento, resumos | `src/agents/` | Interpretativa — é o único lugar onde o LLM opina |

### O que o REFINED faz

- desescapa entidades HTML e remove tags;
- remove URLs e caracteres de controle;
- colapsa espaços e pontuação repetida (`ruim!!!!` → `ruim!`);
- normaliza para Unicode NFC e troca aspas que quebram JSON;
- converte contadores string → inteiro (`"50000"` → `50000`);
- resolve duração ISO-8601 → segundos (`PT15M30S` → `930`);
- junta os segmentos da transcrição num texto contínuo, preservando timestamps;
- deduplica comentários ignorando caixa, acento e pontuação;
- aplica os filtros numéricos (views/likes/comentários/duração mínimos).

---

## 3. O Agente

`Agente` é o contrato genérico para LLMs. O restante da aplicação nunca sabe
qual provider está em uso.

```python
from src.agents import Agente

agente = Agente(provider="groq", model="openai/gpt-oss-20b")

resultado = await agente.run(
    input_data={"transcript": texto, "comments": comentarios},
    prompt=prompt_do_usuario,
    output_schema={"velocidade": {"percepcao": "string"}},
)
```

`prompt`, `output_schema` e `input_data` são componentes independentes: o mesmo
agente atende prompts, entradas e schemas diferentes.

### Contrato de saída

Sempre o mesmo formato, sucesso ou falha — o chamador nunca precisa de `try`:

```python
{
    "success": True,
    "output": {...},          # JSON já interpretado
    "raw_response": "...",    # texto cru, para debug
    "provider": "groq",
    "model": "openai/gpt-oss-20b",
    "usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160},
    "error": None,
}
```

Em caso de falha, `output` é `None` e `error` traz `{type, message, retryable}`.
Tipos possíveis: `AuthenticationError`, `RateLimitError`, `TimeoutError`,
`NetworkError`, `ServerError`, `InvalidRequestError`, `RefusalError`,
`MalformedResponseError`, `OutputParseError`.

### Providers disponíveis

| Provider | Modelo padrão | Variável de ambiente |
|---|---|---|
| `groq` | `openai/gpt-oss-20b` | `GROQ_API_KEY` |
| `openai` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| `anthropic` | `claude-opus-5` | `ANTHROPIC_API_KEY` |
| `gemini` | `gemini-3.5-flash-lite` | `GEMINI_API_KEY` |
| `ollama` | `llama3.1` | — (roda local) |

Trocar de provider é trocar uma string. Nada mais no projeto muda.

### Adicionar um provider novo

1. crie o adapter em `src/agents/providers/` como subclasse de `LLMProvider`
   (ou de `OpenAICompatProvider`, se o serviço falar o formato da OpenAI);
2. registre-o em `src/agents/providers/__init__.py`;
3. mapeie a variável de ambiente da chave em `src/config.py`.

Nenhum outro arquivo precisa ser tocado.

> **Nota sobre a implementação:** os adapters falam HTTP direto (`httpx`) com o
> endpoint REST de cada provider, em vez de usar o SDK de cada um. A requisição
> é idêntica à que o SDK faria, e isso mantém toda a chamada dentro do adapter —
> não existe nenhum `OpenAI(...)` espalhado pelo projeto.

---

## 4. Prompts

Um prompt é **configuração**, nunca código. Ele determina o que é extraído — não
existe lógica fixa para "velocidade", "design" ou "conforto"; esses são só
exemplos de prompt.

Prompts são versionados: editar o texto, o tipo de entrada, o formato ou o
schema cria uma versão nova; renomear não consome versão. Uma pipeline
referencia `(prompt_id, prompt_version)` — nunca copia o texto —, então uma
execução antiga sempre pode ser reproduzida com o prompt que de fato rodou.

### Tipos de entrada

| `input_type` | Quantas chamadas ao LLM | O que recebe |
|---|---|---|
| `transcript` | uma por vídeo | transcrição normalizada |
| `comments` | uma por vídeo | comentários limpos e deduplicados |
| `video` | uma por vídeo | transcrição + comentários |
| `dataset` | uma só | todos os vídeos de uma vez |

---

## 5. Rastreabilidade

Todo insight do TRUST responde "de onde veio isso?":

```json
{
  "escopo": "BmYsInLDmrI",
  "prompt": "Percepção sobre o carro",
  "output": { "velocidade": { "percepcao": "muito boa" } },
  "source": {
    "video_id": "BmYsInLDmrI",
    "video_url": "https://www.youtube.com/watch?v=BmYsInLDmrI",
    "transcript_language": "pt",
    "comment_ids": ["UgxK..."],
    "refined_record_ids": ["cmt_a1b2c3d4e5f6"]
  },
  "agent": {
    "provider": "groq",
    "model": "openai/gpt-oss-20b",
    "prompt_id": "prompt_f9708f766c87",
    "prompt_version": 2
  }
}
```

Cada execução (`PipelineRun`) guarda ainda a versão da pipeline, o provider, o
modelo, as três camadas de dado, o consumo de tokens e os timestamps.

---

## 6. Tratamento de erros

Cada etapa reporta o próprio status. **Uma falha nunca apaga o resultado das
etapas anteriores.**

| Status da execução | Significado |
|---|---|
| `success` | todas as etapas concluíram |
| `partial` | uma etapa falhou, mas houve resultado antes dela |
| `failed` | nada foi produzido |

Falhas de coleta são toleradas — um vídeo sem legenda ou com comentários
desabilitados não derruba a execução. Falhas em RAW ou REFINED interrompem,
porque não há o que continuar.

---

## 7. Estrutura de arquivos

```
src/
├── config.py                    Credenciais e diretórios (única porta de acesso)
│
├── youtube/                     CAMADA API — só comunicação externa
│   ├── client.py                YouTube Data API v3 (search, videos, channels, comments)
│   └── transcript.py            Transcrições
│
├── agents/                      CAMADA INTELIGÊNCIA — desacoplada do provider
│   ├── base.py                  LLMProvider, AgentResult, extração de JSON
│   ├── agent.py                 A classe Agente
│   └── providers/
│       ├── openai_compat.py     Base para APIs no formato OpenAI
│       ├── groq.py  openai.py  ollama.py
│       ├── anthropic.py         Formato próprio (Messages API)
│       └── gemini.py            Formato próprio (Generative Language API)
│
├── pipelines/                   CAMADA PROCESSAMENTO — não conhece provider
│   ├── models.py                Prompt, Pipeline, PipelineRun, StepResult
│   ├── refine.py                RAW → REFINED (determinístico)
│   ├── engine.py                O motor: SOURCE → RAW → REFINED → AGENTE → TRUST
│   ├── executor.py              Execução async/background e ponto de entrada do Airflow
│   └── store.py                 Persistência em JSON (data/)
│
├── airflow_gen/
│   └── generator.py             Pipeline → arquivo de DAG
│
└── api/                         CAMADA HTTP
    ├── youtube_api.py           /youtube/*
    ├── prompts_api.py           /prompts/*
    └── pipelines_api.py         /pipelines/*

app/
├── templates/youtube/index.html    Playground
├── templates/pipeline/index.html   Aba Pipelines
└── static/{youtube,pipeline}/      CSS e JS

airflow/dags/                    DAGs geradas (não editar à mão)
data/                            prompts.json, pipelines.json, runs.json
tests/                           Testes de arquitetura (sem rede)
```

---

## 8. Endpoints

O browser chama sempre `/api/*` no Flask (porta 5000), que repassa para a
FastAPI (porta 8000). O JavaScript nunca conhece a porta 8000 nem credencial
alguma.

### YouTube

| Método | Rota | O que faz |
|---|---|---|
| `GET` | `/youtube/config` | O que está configurado (só flags, nunca chaves) |
| `POST` | `/youtube/search` | Busca vídeos + filtros |
| `POST` | `/youtube/video` | Resolve uma URL/ID num card de vídeo |
| `POST` | `/youtube/transcript` | Transcrição de um vídeo (RAW + REFINED) |
| `POST` | `/youtube/comments` | Comentários de um vídeo (RAW + REFINED) |
| `POST` | `/youtube/playground/preview` | Normaliza a configuração sem executar |
| `POST` | `/youtube/playground/run` | Executa a pipeline ad-hoc; devolve `run_id` |

### Prompts

| Método | Rota |
|---|---|
| `GET` `POST` | `/prompts` |
| `GET` `PUT` `DELETE` | `/prompts/{id}` |
| `GET` | `/prompts/{id}/versions` |
| `POST` | `/prompts/{id}/test` |

### Pipelines

| Método | Rota |
|---|---|
| `GET` `POST` | `/pipelines` |
| `GET` | `/pipelines/runs/{run_id}` |
| `GET` `PUT` `DELETE` | `/pipelines/{id}` |
| `POST` | `/pipelines/{id}/run` |
| `POST` | `/pipelines/{id}/status` |
| `GET` | `/pipelines/{id}/versions` |
| `GET` | `/pipelines/{id}/runs` |
| `POST` | `/pipelines/{id}/generate-dag` |
| `GET` | `/pipelines/{id}/dag` |

---

## 9. Airflow

O botão **Gerar DAG** escreve um arquivo em `airflow/dags/`. O código gerado é
fino de propósito — ele só orquestra:

```
validar_configuracao  >>  executar_pipeline  >>  publicar_resultado
```

`executar_pipeline` chama `executor.executar_pipeline_sync()`, exatamente o
mesmo caminho do botão "Executar" do Playground.

A DAG carrega `dag_id`, `schedule`, `start_date`, `catchup`, `retries` e
`retry_delay` da configuração, e fixa a **versão** da pipeline que representa —
regenerar depois de editar a pipeline é explícito, e a aba Pipelines avisa
quando a DAG está defasada.

### Agendas suportadas

| Modo | Cron gerado |
|---|---|
| `manual` | nenhum (só execução manual) |
| `hourly` | `0 * * * *` |
| `daily` | `{min} {hora} * * *` |
| `weekly` | `{min} {hora} * * {dia}` |
| `monthly` | `{min} {hora} {dia} * *` |
| `cron` | a expressão informada |

### Rodar as DAGs

O Airflow **não** é dependência do projeto — o gerador só escreve o arquivo.
Para executá-las, aponte o `dags_folder` do seu Airflow para `airflow/dags/` e
garanta no worker:

- `YOUTUBE_API_KEY` e a chave do provider de LLM no ambiente;
- `FORD_FIAP_HOME` apontando para a raiz do projeto (ou use o caminho absoluto
  que o gerador grava);
- acesso de escrita a `data/`, onde o store persiste as execuções.

---

## 10. Segurança

Nenhuma credencial aparece em HTML, JavaScript, logs, dados RAW ou no código da
DAG gerada. Todo acesso passa por `src/config.py`, que lê exclusivamente
variáveis de ambiente (opcionalmente carregadas do `.env`).

`GET /youtube/config` devolve apenas se cada credencial existe e o nome da
variável esperada — nunca o valor. Os testes verificam isso explicitamente.

---

## 11. Testes

Rodam sem rede e sem chave de API — os dublês reproduzem o formato exato das
respostas de cada serviço.

```bash
python tests/test_pipeline.py   # RAW/REFINED/TRUST, versionamento, DAG, falhas
python tests/test_agentes.py    # formato da requisição de cada adapter de LLM
node   tests/test_realce.js     # realce de sintaxe do visualizador de DAG
```

No Windows, se o console reclamar de acentuação: `set PYTHONUTF8=1`.

O que cada um cobre:

| Teste | Verifica |
|---|---|
| `test_pipeline.py` | RAW imutável; REFINED determinístico e correto (HTML, URLs, dedup, ISO-8601); TRUST com rastreio completo; versionamento de prompts e pipelines; geração da DAG; credenciais fora dos artefatos; falha por etapa preservando o que veio antes; contrato do `Agente` |
| `test_agentes.py` | Endpoint, headers, corpo e leitura da resposta de cada provider; tradução de erros HTTP (401/429) para o contrato tipado; recusa da Anthropic (HTTP 200 com `stop_reason: refusal`) |
| `test_realce.js` | O realce de sintaxe nunca altera o texto do código nem vaza markup (regressão de um bug real: `class` é palavra reservada em Python **e** atributo HTML) |

---

## 12. Regras de arquitetura

| # | Regra |
|---|---|
| 1 | A camada API não conhece a UI |
| 2 | O JavaScript não executa regra de negócio — só chama `/api/*` |
| 3 | A pipeline usa `Agente`, nunca um cliente de provider |
| 4 | O Airflow reutiliza o mesmo `PipelineEngine` do Playground |
| 5 | RAW é imutável |
| 6 | REFINED é determinístico |
| 7 | TRUST é a única camada interpretativa |
| 8 | Prompt é configuração, não código |
| 9 | Provider é substituível |
| 10 | Tudo é rastreável: Input → RAW → REFINED → Agente → TRUST → Output |
