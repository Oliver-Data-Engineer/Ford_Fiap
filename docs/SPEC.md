# SPEC — YouTube Pipeline Playground (Ford_Fiap)

> Documento de continuidade. Objetivo: qualquer modelo/agente que pegue este
> projeto depois consegue entender o que existe, por quê, e onde mexer —
> sem precisar reconstruir o raciocínio do zero. Escrito ao final da sessão
> em que o subsistema inteiro (Playground + Pipelines + Agente + DAG + banco
> + tema Ford + proxies) foi criado.
>
> Não é um tutorial de uso — isso já existe em [`youtube_pipeline.md`](./youtube_pipeline.md)
> e [`setup.md`](./setup.md). Este documento é sobre **arquitetura, decisões
> e estado atual**, para orientar a próxima mudança.

---

## 1. Linha do tempo desta sessão (nessa ordem)

1. **Análise do `youtube.ipynb`** e separação em camadas (API vs Pipeline vs
   Agente), a partir de uma spec longa do usuário — resultou em todo o
   `src/` novo descrito na seção 3.
2. **Redesign visual Ford** — tema claro (`#00095B` navy, pílulas, Poppins)
   aplicado ao app inteiro, substituindo o tema escuro original. Reescrita
   estrutural do Playground (busca unificada, galeria, tabelas) e da aba
   Pipelines (modal expandido, badges).
3. **Correção de bug real**: `[hidden]` sendo sobrescrito por `display`
   declarado em classes de autor (`.campo`, `.btn`, `.aviso--bloco`) —
   corrigido com uma regra global em `base.css`.
4. **Banco local** (SQLite + SQLAlchemy) para persistir as tabelas
   Vídeos/Canais/Transcrições/Comentários/Insights de cada execução.
5. **Campo `fonte_fixa`** no modelo de pipeline — formaliza explicitamente
   "sempre os mesmos vídeos" vs "busca nova a cada execução".
6. **Diagnóstico e correção de mensagem** para bloqueio de IP do YouTube na
   coleta de transcrições (`RequestBlocked`/`IpBlocked` distinguido de
   "vídeo sem legenda").
7. **Suporte a proxy** para contornar esse bloqueio — três estratégias
   (Webshare nativo, lista por URL de download, lista estática local),
   testado ao vivo com credenciais reais do usuário. Primeira tentativa (10
   IPs de datacenter) confirmou a causa raiz mas não resolveu — YouTube
   bloqueia datacenter em faixa inteira.
8. **Segunda lista, 20 IPs residenciais de verdade** (Charter, Telmex, RCN —
   ISPs reais, confirmado por ASN) via URL de download da Webshare — **isso
   resolveu**. Testado ao vivo com sucesso na transcrição, na YouTube Data
   API v3 (`search`/`videos`/`comments`) e na pipeline inteira de ponta a
   ponta. Proxy agora cobre as duas APIs externas do YouTube, não só
   transcrição. Ver seção 8.

Todo o trabalho do item 1–2 foi commitado pelo próprio usuário no meio da
sessão (commit `ebcb6ce`, branch `feature/youtube_mode`). Os itens 3–7 podem
ainda estar pendentes de commit — checar `git status`.

---

## 2. Princípios que não podem ser quebrados

Essas regras vieram da spec original do usuário e foram mantidas em toda
mudança subsequente. Qualquer PR que as violar está errado, mesmo que os
testes passem.

| # | Regra | Onde se manifesta |
|---|---|---|
| 1 | API não conhece UI | `src/youtube/`, `src/agents/providers/` são puros, sem import de Flask/Jinja |
| 2 | JS não executa regra de negócio | `script.js` só chama `/api/*` e desenha o retorno |
| 3 | Pipeline usa `Agente`, nunca um cliente de provider direto | `engine.py` importa `src.agents.Agente`, nunca `openai`/`groq` |
| 4 | **Airflow reutiliza o mesmo `PipelineEngine`** do Playground | DAG gerada chama `executor.executar_pipeline_sync()` — não há lógica duplicada |
| 5 | RAW é imutável | `refine.py` nunca modifica o dict `raw` recebido |
| 6 | REFINED é determinístico | `refine.refinar()` roda 2x sobre o mesmo RAW → mesmo resultado (testado) |
| 7 | TRUST é a única camada interpretativa | Só `_passo_agente`/`_passo_trust` em `engine.py` chamam o LLM |
| 8 | Prompt é configuração, não código | Nenhum texto de prompt hardcoded na lógica — tudo vem de `Prompt` salvo |
| 9 | Provider é substituível | Trocar `provider="groq"` por `"anthropic"` não muda mais nada |
| 10 | Tudo é rastreável | Todo insight carrega `source` (video_id/comment_id) + `agent` (provider/model/prompt_id/version) |
| 11 | Seletor de scraper nunca é confiável sem passar pela tela de teste; o LLM define a forma **uma vez** (cacheada/versionada), nunca é chamado de novo a cada extração | `src/scraper/discovery.py` só roda por ação explícita do usuário (`/discover`); `src/scraper/engine.py` nunca importa `src.agents` — extração é puro seletor CSS |

---

## 3. Mapa do código (`src/`)

```
src/
├── config.py                 Settings — ÚNICO ponto que lê variáveis de ambiente
│
├── youtube/                  CAMADA API — só comunicação externa
│   ├── client.py             YouTube Data API v3 via httpx (search/videos/channels/comments)
│   ├── transcript.py         youtube_transcript_api + proxy (Webshare nativo ou lista estática)
│   └── proxy_pool.py         Pool de proxies HTTP, rotação round-robin
│
├── agents/                   CAMADA INTELIGÊNCIA — desacoplada do provider
│   ├── base.py                LLMProvider (contrato), AgentResult, extração de JSON
│   ├── agent.py                A classe `Agente`
│   └── providers/
│       ├── openai_compat.py   Base para APIs formato OpenAI (chat/completions)
│       ├── groq.py, openai.py, ollama.py   subclasses triviais de openai_compat
│       ├── anthropic.py       Formato próprio (Messages API — sem temperature!)
│       └── gemini.py          Formato próprio (Generative Language API)
│
├── pipelines/                 CAMADA PROCESSAMENTO — não conhece provider nem UI
│   ├── models.py               Prompt, Pipeline, PipelineRun, FonteYouTube (Pydantic)
│   ├── refine.py                RAW → REFINED (determinístico, sem LLM)
│   ├── engine.py                O motor: SOURCE → RAW → REFINED → ENRIQUECIMENTO → AGENTE → TRUST
│   ├── executor.py              Execução async/background + entrada síncrona do Airflow
│   ├── store.py                 Persistência JSON (prompts/pipelines/runs) em data/
│   └── db.py                    Persistência SQLite (tabelas Vídeos/Canais/Transcrições/Comentários/Insights)
│
├── analytics/                 CAMADA WAREHOUSE — dados cross-execução do Dashboard (BigQuery)
│   ├── warehouse.py             Cliente BigQuery: dataset/tabela idempotentes, upsert, query
│   ├── schemas.py                Os 4 SchemaField por tabela (comentarios_analise tem STRUCT nativo)
│   ├── ids.py                    transcript_id sintético (search_id = run_id, sem campo novo)
│   ├── models.py                 AnaliseComentarioStruct — struct fixo, validado de verdade
│   └── enrichment.py              Prompt fixo + enriquecer_comentarios() (correlação por índice)
│
├── airflow_gen/
│   └── generator.py            Pipeline (config) → arquivo de DAG (.py) em airflow/dags/
│
├── bigquery_warehouse.py       BigQueryWarehouse genérico — extraído de analytics/warehouse.py, reaproveitado por govmode/ e scraper/
│
├── govmode/                    Catálogo FIPE (marca/modelo/ano) — domínio paralelo, não extensão do YouTube (seção 13.2)
│   ├── models.py, engine.py, executor.py, store.py, warehouse.py, schemas.py, dag_generator.py
│   └── client.py                FipeAPI (parallelum.com.br + brasilapi.com.br)
│
├── scraper/                    Auto-discovery de sites de carro — domínio paralelo (seção 14), mesmo padrão do Gov Mode
│   ├── models.py                 SiteScraper/MapaSeletor/ScraperRun (Pydantic) — seletores por objetivo, versionado
│   ├── discovery.py              ÚNICA camada que chama o LLM — HTML simplificado → Agente → Seletor* validado
│   ├── prompts.py, html_simplifier.py, browser.py   prompt fixo por objetivo, limpeza de HTML, fetch via Playwright
│   ├── extractor.py              Extração determinística (BeautifulSoup), SEM LLM — usa o MapaSeletor já salvo
│   ├── engine.py, executor.py    Mesmo shape do Gov Mode — 1 "passo" por objetivo, falha isolada por passo
│   ├── store.py                  data/scraper_sites.json, data/scraper_runs.json
│   ├── warehouse.py, schemas.py  scraper_ficha_tecnica/scraper_anuncios/scraper_comentarios (BigQuery)
│   ├── dag_generator.py
│   └── proxy.py                  Resolver de ProxyPool (src/youtube/proxy_pool.py, reaproveitado sem alteração)
│
└── api/                        CAMADA HTTP (FastAPI)
    ├── youtube_api.py           /youtube/*
    ├── prompts_api.py           /prompts/*
    ├── pipelines_api.py         /pipelines/*
    ├── analytics_api.py         /analytics/* (leitura do warehouse — Dashboard)
    ├── gov_api.py                /gov/* (catálogo FIPE + pipelines — seção 13)
    ├── scraper_api.py            /scraper/* (auto-discovery de sites de carro — seção 14)
    └── specs_api.py             /specs/* (módulo anterior, especificações técnicas — não tocado nesta sessão)
```

```
app/
├── app.py                      Flask — serve templates + proxy /api/* → FastAPI (porta 8000)
├── templates/
│   ├── _layout/                 dashboard_base.html (shell) + _sidebar.html
│   ├── busca/                   Home/seleção de veículo (reescrita nesta sessão, tema Ford)
│   ├── youtube/                 O Playground (reescrito nesta sessão)
│   ├── pipeline/                Gestão de pipelines + DAGs (reescrito nesta sessão)
│   ├── analytics/                Dashboard analítico (filtros + gráficos + tabela, sessão seguinte)
│   ├── gov/                      Catálogo FIPE + pipelines próprias (reescrito na sessão do Gov Mode, seção 13)
│   ├── scraper/                  Auto-discovery de sites de carro (seção 14)
│   └── dashboard/, scraping/, compare/, x/   Não tocados estruturalmente — só herdam o tema
└── static/
    ├── shared/base.css           Tokens de design (paleta, tipografia, radius, sombra) — fonte única do tema
    ├── shared/sidebar.css        Navegação lateral
    ├── youtube/{style.css,script.js}   O maior arquivo do projeto (script.js ~1300 linhas)
    ├── pipeline/{style.css,script.js}
    ├── analytics/{style.css,script.js}  Filtros + Chart.js (CDN) + tabela paginada
    ├── gov/{style.css,script.js}        Catálogo FIPE + pipelines (seção 13) — replica classes de pipeline/ e analytics/, sem carregá-los
    └── scraper/{style.css,script.js}    Auto-discovery de sites de carro (seção 14) — mesma replicação de classes
```

```
tests/
├── test_pipeline.py    Engine completo: RAW/REFINED/TRUST, versionamento, DAG, fonte_fixa, falhas por etapa
├── test_db.py           SQLAlchemy: as 5 tabelas, join de canal embutido, idempotência
├── test_agentes.py      Formato de requisição de cada adapter de LLM (Groq/OpenAI/Anthropic/Gemini) + erros HTTP
├── test_transcript.py   Classificação RequestBlocked→'blocked' vs TranscriptsDisabled→'unavailable' (biblioteca real)
├── test_proxy_pool.py   Rotação, retentativa, prioridade Webshare > lista estática
├── test_analytics_warehouse.py  Struct validado, dataset/tabela idempotentes, correlação por índice na LLM, coletado_em TIMESTAMP, texto bruto/refinado
├── test_analytics_api.py        Serialização JSON de tipos numpy/pandas do BigQuery (ndarray/Timestamp/NaT) — regressão do 500 em /analytics/comentarios
└── test_realce.js       Realce de sintaxe do visualizador de DAG (regressão de bug de markup)
```

Rodar tudo:
```bash
for f in tests/test_pipeline.py tests/test_db.py tests/test_agentes.py tests/test_transcript.py tests/test_proxy_pool.py tests/test_analytics_warehouse.py tests/test_analytics_api.py; do python "$f"; done
node tests/test_realce.js
```
Todos rodam **sem rede e sem chave de API** (dublês reproduzem o formato exato de cada serviço). Total: ~190 verificações.

---

## 4. Modelo de dados — `src/pipelines/models.py`

Os quatro objetos centrais, todos Pydantic, persistidos em JSON via `store.py`:

- **`Prompt`** — versionado (`version`, `versions: list[PromptVersao]`). Editar o
  texto/`input_type`/`output_format`/`output_schema` cria versão nova; renomear não.
  `input_type` ∈ `{transcript, comments, video, dataset}` — decide quantas
  chamadas ao LLM e o que cada uma recebe (ver `engine._montar_entradas`).
- **`Pipeline`** — também versionado. Contém `source: FonteYouTube`,
  `steps: list[PassoPipeline]`, `llm: ConfigLLM`, `schedule: ConfigSchedule`.
- **`FonteYouTube`** — todos os filtros do Playground (query, datas, idioma,
  duração, mínimos de views/likes/comentários) **+ `fonte_fixa: bool`**
  (adicionado nesta sessão — ver seção 6).
- **`PipelineRun`** — registro de uma execução: `steps: list[StepResult]`
  (status por etapa) + `raw`/`refined`/`trust` (os três níveis de dado
  completos, como dict).

---

## 5. Tema Ford — tokens e onde mexer

Tudo em `app/static/shared/base.css`, `:root`. Trocar aqui propaga pro app
inteiro (a maioria das páginas só usa `var(--...)`, nada hardcoded).

```css
--bg-base: #ffffff;           --accent-home: #00095b;      /* navy Ford */
--bg-panel-raised: #f4f5f7;   --accent-youtube: #b3423a;
--text-primary: #16233a;      --accent-pipeline: #1d4ed8;
--text-muted: #5b6472;        /* ... um accent por "modo" (sidebar) */
--radius-pill: 999px;         --font-sans: 'Poppins', ...;
--shadow-md: 0 4px 16px rgba(16,24,40,.10);
```

**`[hidden] { display: none !important; }`** também está aqui (logo após o
reset `* { box-sizing }`) — é a correção do bug de `[hidden]` sendo
sobrescrito. **Não remover.** Qualquer classe nova que declare `display` e
também seja alternada via atributo `hidden` (não `.classList`) depende
dessa regra para funcionar.

Páginas que precisaram de reescrita manual (não herdavam tokens):
`busca` (tinha paleta própria completa) e `scraping` (referenciava tokens
inexistentes como `--surface-2`/`--warning` com fallback hardcoded — corrigido
para os nomes reais).

**Não usar o logo oval registrado da Ford** — risco de marca. O glifo
genérico de carro no sidebar foi só recolorido, não substituído.

---

## 6. `fonte_fixa` — como funciona de ponta a ponta

Decisão do usuário: o modal de criar pipeline precisa deixar **explícito**
se a origem é uma lista fixa de vídeos ou uma busca que roda de novo a cada
execução — não pode ser inferência silenciosa.

- **Modelo**: `FonteYouTube.fonte_fixa: bool = False`.
- **Validação** (`pipelines_api.py::_validar_fonte`): `fonte_fixa=True` exige
  `video_ids` não-vazio; `fonte_fixa=False` exige `query` não-vazia. 400 com
  mensagem clara se violado — tanto em `POST /pipelines` quanto `PUT`.
- **Engine** (`engine.py::_passo_busca`): se `fonte_fixa`, delega para
  `_passo_busca_fixa` — ignora `query` completamente, resolve só
  `video_ids`. Se `False` (padrão), comportamento **idêntico ao original**:
  busca se `query` setada, mescla com `video_ids` manuais se houver (Modo B
  do Playground). Essa é uma decisão deliberada: em vez de reescrever a
  lógica de busca dinâmica, só foi adicionado um atalho no topo da função
  para o caso fixo — minimiza risco de regressão.
- **Frontend** (`youtube/script.js`): o botão "Executar pipeline" do
  Playground (execução ad-hoc) SEMPRE limpa `query` quando há vídeos
  marcados (`fixa = selecionados.length > 0`) — replica o comportamento
  histórico. O **modal de gerar pipeline** tem um toggle explícito
  (`data-pl-fonte` radio) que o usuário escolhe conscientemente.
- **UI**: badge "Fonte fixa"/"Fonte dinâmica" em cada card da aba Pipelines
  (`pipeline/script.js::cardPipeline`).

---

## 7. Banco local — `src/pipelines/db.py`

SQLite via SQLAlchemy 2.0 (estilo `Mapped`/`mapped_column`), arquivo em
`data/ford_fiap.db` (caminho: `settings.database_url`). **Aditivo** —
não substitui `store.py` (prompts/pipelines/runs continuam em JSON).

5 tabelas, uma por tipo de dado que uma execução produz:

| Tabela | Chave | Nota |
|---|---|---|
| `videos` | (run_id, video_id) | **já vem com `canal_inscritos`/`canal_views_totais`** — join feito no REFINED, não é preciso juntar de novo |
| `channels` | (run_id, canal_id) | |
| `transcripts` | (run_id, video_id) | |
| `comments` | (run_id, comment_id) | |
| `insights` | id autoincrement | resultado de cada chamada ao agente — `video_id` nulo quando `escopo='dataset'` |

- `salvar_tabelas_execucao(run)` — chamada pelo `executor.py` (`_persistir_tabelas`,
  dentro de um try/except que só loga, nunca derruba a resposta) em **todo**
  fim de execução, Playground ou pipeline salva. Não faz nada se
  `run.refined` estiver vazio (falhou antes do REFINED).
- Upsert por `session.merge()` (composite key) para vídeos/canais/transcrições/
  comentários — idempotente. Insights são delete+insert por `run_id` (sem
  chave natural estável entre reexecuções).
- `tabelas_da_execucao(run_id)` — leitura, alimenta `GET /pipelines/runs/{run_id}/tables`,
  que por sua vez alimenta o modo "Tabela" do Playground (toggle ao lado de
  "Ver JSON bruto", nas abas REFINED/TRUST — RAW não tem modo tabela, não
  faz sentido pra um payload não-normalizado).

---

## 8. Proxies — YouTube Data API v3 + transcrição, RESOLVIDO e testado ao vivo

**Contexto**: `youtube_transcript_api` é bloqueado pelo YouTube quando as
requisições vêm de IP de datacenter (`RequestBlocked`/`IpBlocked`) — comum
em ambientes de nuvem/CI. Isso **não é bug do projeto**, é o YouTube do lado
de fora recusando.

**O que existe** (`src/youtube/proxy_pool.py`, usado por `transcript.py` e
`client.py`):

- **`ProxyPool`** — rotação round-robin sobre uma lista de proxies HTTP
  autenticados (`http://usuario:senha@host:porta`), construída de duas
  formas:
  - `ProxyPool.de_url(url)` — baixa a lista de um endpoint (o link de
    "Download" do painel da Webshare, por exemplo). **Preferível**: sempre
    reflete o plano atual, nunca fica desatualizado como um arquivo salvo.
  - `ProxyPool.de_arquivo(caminho)` — lê um CSV local, mesmo formato de
    linha. Fallback de rede/offline.
  - `resolver_pool_padrao()` — a função compartilhada que decide qual usar:
    `YOUTUBE_PROXY_LIST_URL` (tenta primeiro) → `YOUTUBE_PROXY_LIST`
    (fallback se a URL falhar ou não estiver setada) → desabilitado.
  - A URL de download carrega um **token de acesso** — nunca aparece em log
    (`_mascarar_url` só expõe o host), nunca deve ser hardcoded no código.
- **Webshare nativo** (`YOUTUBE_WEBSHARE_USERNAME`/`_PASSWORD`, só em
  `transcript.py`) — usa `WebshareProxyConfig` da própria biblioteca, fala
  com o gateway rotativo `p.webshare.io`, tem retentativa embutida. Tem
  prioridade sobre a lista (URL ou arquivo) quando configurado.
- **Retentativa em `TranscriptAPI`** — até 3 tentativas, um proxy diferente
  a cada vez, **somente** quando o erro é `RequestBlocked`/`IpBlocked`;
  qualquer outro motivo (vídeo sem legenda) falha na hora, sem gastar
  tentativa.
- **Rotação em `YouTubeAPI`** (`client.py`) — cada chamada HTTP
  (`search.list`/`videos.list`/`channels.list`/`commentThreads.list`) sai
  por um proxy diferente da mesma rotação. Resolvido de forma lazy e segura
  para concorrência (`asyncio.Lock`) na primeira chamada de cada instância —
  nunca no `__init__`, porque resolver o pool pode envolver uma requisição
  de rede (`de_url`), e isso nunca pode bloquear o event loop.

**Histórico do diagnóstico** — duas listas de proxy testadas ao vivo:

1. **10 IPs de datacenter** (arquivo estático, `config/Webshare 10
   proxies.csv` — nunca commitado). ASN **Leaseweb UK Limited**. Bloqueados
   pelo YouTube tanto via rotação manual quanto via gateway nativo da
   Webshare — a própria biblioteca detectou e explicou: a conta tinha o
   produto "Proxy Server"/"Static Residential", não "Residential". YouTube
   blocklista datacenter em faixa inteira; trocar de IP na mesma faixa não
   ajuda.
2. **20 IPs residenciais de verdade** (URL de download, `YOUTUBE_PROXY_LIST_URL`
   no `.env` real). ASNs confirmados: **Charter Communications** (EUA),
   **Telmex Colombia**, **RCN** (EUA) — provedores de internet residencial
   genuínos. **Isso resolveu o bloqueio.**

**Resultado do teste ao vivo com a lista residencial** (vídeo
`BmYsInLDmrI`, o mesmo do erro original que abriu essa investigação):

| Teste | Resultado |
|---|---|
| `TranscriptAPI.get_transcript()` via proxy | ✅ 477 segmentos, pt, 2.7s |
| `YouTubeAPI.search_videos()` via proxy | ✅ 3 vídeos, 1.8s |
| `YouTubeAPI.get_video()` / `get_comments()` via proxy | ✅ ambos OK |
| `PipelineEngine.executar()` completo (search→transcript→comments→RAW→REFINED) | ✅ `status=success`, 2 vídeos, 2/2 transcrições, 20 comentários |

**Reaproveitado por outros domínios**: `ProxyPool` é domain-agnostic desde o
início (só rotaciona uma lista de URLs de proxy) — o Scraper Mode (seção 14)
usa a classe **sem alteração nenhuma**, só um segundo `resolver_pool_padrao()`
próprio (`src/scraper/proxy.py`) lendo variáveis de ambiente próprias
(`SCRAPER_PROXY_LIST_URL`/`SCRAPER_PROXY_LIST`) — mesma prioridade
URL-depois-arquivo, dataset/domínio de proxy separado do YouTube.

Como bônus não previsto: rotear por esse proxy residencial também contornou
um problema de verificação de certificado TLS que existe no ambiente de
sandbox onde esse desenvolvimento rodou (`SSL: CERTIFICATE_VERIFY_FAILED`
em chamadas diretas a `googleapis.com`/`proxy.webshare.io` — o Windows
confia nesses certificados via `curl`/schannel, mas o `certifi` do Python
nessa sandbox não). **Isso é uma particularidade do ambiente de
desenvolvimento, não do código** — não tentar "corrigir" desabilitando
verificação de certificado em lugar nenhum; se aparecer de novo, é esse
mesmo problema de sandbox, não uma regressão.

**Se o próximo agente for mexer nisso**: a lógica está validada e
funcionando com uma lista residencial real. Se o usuário trocar de lista/
provedor e o bloqueio voltar, o primeiro passo é sempre confirmar o **tipo**
de proxy (ASN via `ipinfo.io`/`whois`) antes de suspeitar do código — já
aconteceu duas vezes nesta sessão de a causa ser "proxy errado", não bug.

---

## 9. Endpoints atuais (FastAPI, prefixo `/api` no Flask)

```
GET    /youtube/config
POST   /youtube/search
POST   /youtube/video
POST   /youtube/transcript
POST   /youtube/comments
POST   /youtube/playground/preview
POST   /youtube/playground/run

GET    /prompts                      POST /prompts
GET    /prompts/{id}                 PUT  /prompts/{id}    DELETE /prompts/{id}
GET    /prompts/{id}/versions
POST   /prompts/{id}/test

GET    /pipelines                    POST /pipelines
GET    /pipelines/runs/{run_id}
GET    /pipelines/runs/{run_id}/tables      ← novo nesta sessão (banco local)
GET    /pipelines/{id}                PUT  /pipelines/{id}   DELETE /pipelines/{id}
POST   /pipelines/{id}/status
GET    /pipelines/{id}/versions
POST   /pipelines/{id}/run
GET    /pipelines/{id}/runs
POST   /pipelines/{id}/generate-dag
GET    /pipelines/{id}/dag

GET    /analytics/filtros                   ← novo (seção 12) — dropdowns do Dashboard
GET    /analytics/resumo                    ← novo — KPIs + distribuição sentimento/objeto
GET    /analytics/comentarios               ← novo — tabela paginada de comentarios_analise

GET    /gov/pipelines                POST /gov/pipelines        ← novo (seção 13)
GET    /gov/runs/{run_id}
GET    /gov/pipelines/{id}            PUT  /gov/pipelines/{id}   DELETE /gov/pipelines/{id}
POST   /gov/pipelines/{id}/status
GET    /gov/pipelines/{id}/versions
POST   /gov/pipelines/{id}/run
GET    /gov/pipelines/{id}/runs
POST   /gov/pipelines/{id}/generate-dag
GET    /gov/pipelines/{id}/dag
GET    /gov/marcas   /gov/modelos   /gov/fipe   /gov/resumo      ← catálogo FIPE, só leitura

GET    /specs/fabricantes  /modelos  /versoes     POST /specs/executar   (módulo anterior, intocado)
```

Ordem de rota importa em `pipelines_api.py` e em `gov_api.py`:
`/runs/{run_id}...` e as rotas fixas (`/marcas`, `/modelos`, `/fipe`,
`/resumo`) precisam vir **antes** de `/{pipeline_id}...` no arquivo, senão
FastAPI casa esses nomes como se fossem um `pipeline_id`.

---

## 10. Segurança — o que nunca deve mudar

- Toda credencial passa por `src/config.py` (única porta de leitura de
  variável de ambiente). Nunca em HTML, JS, logs, RAW ou DAG gerada.
- `GET /youtube/config` devolve só booleanos de presença, nunca o valor.
- `.gitignore` cobre: `.env`, `config/` (proxies, service accounts),
  `data/` (banco + JSON de prompts/pipelines/runs), `airflow/dags/*.py`
  (artefato regenerável, não código-fonte — um foi commitado por engano no
  meio da sessão, removido do working tree).
- O arquivo `config/Webshare 10 proxies.csv` com credenciais reais de proxy
  **não deve nunca ser commitado** — já está coberto por `config/` no
  `.gitignore`, mas vale checagem manual antes de qualquer `git add` amplo.
- O mesmo vale para o JSON da service account do BigQuery
  (`GOOGLE_APPLICATION_CREDENTIALS`, seção 12) — guardar em `config/` (já
  coberto pelo `.gitignore`), nunca hardcoded, nunca logado.

---

## 11. O que NÃO foi feito (próximos passos possíveis, não iniciados)

- Nenhuma mudança nas páginas `dashboard`, `compare`, `x` além de herdar o
  tema — a interação/funcionalidade delas é anterior a esta sessão e não
  foi tocada. (`gov` deixou de fazer parte desta lista — reescrita por
  completo na sessão do Gov Mode, seção 13.)
- Sem browsing histórico de dados entre execuções na UI (o banco guarda tudo
  por `run_id`, mas não há uma tela "ver todas as execuções desta pipeline
  numa tabela consolidada" — hoje é run a run).
- `WebshareProxyConfig.filter_ip_locations` (restringir país) não está
  exposto em `TranscriptAPI` — só relevante se voltar a usar o gateway
  nativo da Webshare (hoje a lista via URL residencial já resolve o caso).
- `ProxyPool` não tem cache de expiração para `de_url()` — cada instância de
  `YouTubeAPI`/`TranscriptAPI` baixa a lista uma vez e reusa para toda a sua
  vida (um run de pipeline inteiro). Se o plano da Webshare trocar de IPs
  no meio de um processo de longa duração, só pega a lista nova numa
  instância nova. Não era um problema real até agora — considerar TTL se
  virar um.
- Do que está descrito na seção 12: o Dashboard (`/analytics`) foi
  implementado e testado sem rede (dublê de `bigquery.Client`), mas nunca
  rodou contra um BigQuery real — não há credencial de GCP disponível neste
  ambiente de desenvolvimento. Antes de considerar "pronto para produção":
  rodar uma execução real do Playground com `GOOGLE_APPLICATION_CREDENTIALS`
  configurada, confirmar que as 4 tabelas nascem certas no console do GCP, e
  abrir `/analytics` para validar os gráficos com dado de verdade.
- O prompt fixo de enriquecimento (`src/analytics/enrichment.py`) não foi
  ajustado/iterado contra um LLM real — o texto é uma primeira versão
  razoável, mas só foi validado estruturalmente (dublê determinístico nos
  testes). Vale rodar "Testar amostra" no Playground com um provider real
  cedo, e ajustar o texto do prompt (via a própria UI de Prompts, já que ele
  é um `Prompt` versionado normal) se a qualidade da extração não convencer.
- Sem paginação "carregar mais canais" em `/analytics/filtros` — o dropdown
  de canal trunca em 200 (`LIMIT 200` na query); não era um problema
  esperado para o volume atual, mas cresce com o tempo.

---

## 12. Warehouse analítico (BigQuery) + enriquecimento de comentários + Dashboard

Sessão seguinte à criação do subsistema (seções 1-11). Objetivo do usuário:
uma aba nova de analytics que agregue tudo que já é coletado (canais,
transcrições, comentários) com uma camada extra de enriquecimento por LLM
sobre cada comentário, filtrável por canal/sentimento — mais dois ajustes
de UI no Playground.

### 12.1 Decisões de arquitetura (mudaram durante a sessão — vale ler antes de mexer)

O desenho inicial previa "modos de análise" (Insight/Sentimento/Motivador)
selecionáveis pelo usuário. **Foi descartado.** Decisão final:

- **Enriquecimento é único, fixo e automático.** Todo comentário que entra
  na amostra passa pelo mesmo prompt, produzindo sempre o mesmo struct —
  `{sentimento, motivo, adjetivos, objeto}`. Não existe seleção de modo em
  lugar nenhum da UI. A execução roda sozinha, como um passo estrutural do
  `PipelineEngine` (`TipoStep.COMMENT_ENRICHMENT`), no mesmo nível de
  `RAW`/`REFINED`/`TRUST` — nunca configurável pelo usuário via `steps`
  (`resolver_passos` sempre injeta esse passo entre REFINED e o(s) passo(s)
  `agent`, mesmo que a pipeline declare uma lista customizada de steps —
  mesmo tratamento que TRUST já recebia).
- **Atualização (seção 12.9): a amostra é fixa em 3 comentários no total
  por execução**, não todos os coletados — decisão posterior do usuário
  pra controlar custo de LLM. O botão manual de "testar amostra" que existia
  aqui foi removido: a amostra automática já aparece no resultado normal
  (tabela de Comentários, modo Tabela do Playground).
- **BigQuery de verdade**, não SQLite. O usuário pediu explicitamente para
  seguir o padrão do notebook `tabela_marcas.ipynb` (schema explícito via
  `bigquery.SchemaField`, `client.get_table`/`NotFound`→`create_table`,
  `load_table_from_dataframe`) só que para um domínio de dado diferente —
  **aditivo** ao SQLite existente (`src/pipelines/db.py`), que continua
  intocado e continua servindo o modo "Tabela" por execução do Playground.
  O warehouse BigQuery serve especificamente o Dashboard cross-execução.

### 12.2 `src/analytics/` — o módulo novo

```
src/analytics/
├── warehouse.py   BigQueryWarehouse: dataset/tabela idempotentes, upsert (append-only), query
├── schemas.py     5 tabelas via bigquery.SchemaField — comentarios_analise tem output_struct como RECORD
├── ids.py         transcript_id(run_id, video_id) — hash determinístico, mesma técnica de refine.py
├── models.py      AnaliseComentarioStruct (Pydantic) — a saída da LLM É validada contra isto
└── enrichment.py  Prompt fixo (garantir_prompt) + enriquecer_comentarios() (chamada à LLM + correlação)
```

As 5 tabelas: `canais_buscados`, `transcricoes_coletadas` (com
`texto_bruto`/`texto_refinado` — seção 12.11), `comentarios_coletados`,
`comentarios_analise` (struct fixo do enriquecimento automático) e
`analises_ia` (seção 12.12 — qualquer prompt do usuário, schema livre).

- **`run_id` = `search_id`.** Não é um campo novo — cada execução/busca do
  Playground já é um `run_id` único. Documentado assim para não duplicar o
  conceito; toda tabela do warehouse tem uma coluna `run_id` com esse papel.
- **`transcript_id` é novo** (`src/analytics/ids.py`): antes não existia
  chave estável de transcrição, só o par composto `(run_id, video_id)` em
  `TranscriptRow`. É um hash sha1 curto de `f"{run_id}:{video_id}"`, no
  mesmo espírito de `refine.py::_hash_curto`/`refined_record_id`.
- **`output_struct` (só em `comentarios_analise`) é um STRUCT nativo do
  BigQuery** (`SchemaField(..., "RECORD", fields=[...])`), não um JSON
  serializado em texto — bate literalmente com o pedido original ("sempre
  traz a resposta do agente em uma coluna única, que será um struct"). Já
  `analises_ia.output_json` é texto livre de propósito — cobre prompts do
  usuário com schema arbitrário (Regra 8), não dá pra fixar um RECORD.
- **As 5 tabelas são todas append-only** (`WRITE_APPEND`) — nenhuma trunca;
  cada execução soma linhas novas. Escritas em `src/pipelines/executor.py::
  _persistir_tabelas`, na mesma função (e no mesmo padrão try/except
  isolado por tabela, log-only) que já chama `db.salvar_tabelas_execucao(run)`.
- **Nunca bloqueia o resto do app.** `settings.bigquery_configurado`
  (presença de `GOOGLE_APPLICATION_CREDENTIALS`, nunca o valor) decide se o
  warehouse roda; sem credencial, toda gravação/leitura é pulada (escrita)
  ou devolve 503 (leitura), sem afetar o Playground/pipelines.

### 12.3 Enriquecimento — como a correlação funciona

`enriquecer_comentarios(agente, comentarios, prompt=...)` manda **um lote
por vídeo** para a LLM (não um comentário por chamada — caro demais) e pede
de volta um array com um item por comentário, marcado por `indice` (posição
na lista enviada, começando em 0):

```json
{"comentarios": [{"indice": 0, "sentimento": "...", "motivo": "...", "adjetivos": ["..."], "objeto": "..."}, ...]}
```

A correlação de volta pro comentário original é feita pelo `indice`, nunca
por ordem assumida — se a LLM pular um índice ou devolver menos itens do
que o esperado, aquele comentário específico vira falha
(`error.type = "MissingOutput"`), sem derrubar os outros do mesmo lote. O
struct de cada item **é validado** contra `AnaliseComentarioStruct` (campo
vazio ou ausente → `OutputParseError`) — diferente do resto do app, onde
`output_schema` de um `Prompt` é só texto sugestivo colado no prompt da LLM
(`Agente._montar_system_prompt`), nunca verificado depois. Aqui, se a LLM
não seguir o formato, o comentário fica marcado como falha em vez de gravar
lixo no warehouse.

O prompt fixo em si **é um `Prompt` normal**, versionado como qualquer
outro (`garantir_prompt()` procura por nome — `"Enriquecimento de
comentários (fixo)"` — e cria na primeira vez que falta). Isso significa
que aparece na lista de Prompts do Playground e pode ser editado por lá
(Regra 8: prompt é configuração, não código) — só não deve ter o
`output_schema`/`input_type` alterados, ou o parse por `AnaliseComentarioStruct`
quebra.

### 12.4 Dashboard (`/analytics`)

Aba nova na sidebar (grupo "Automação", ao lado de Pipelines) — não é o
`/dashboard` genérico (que continua sendo só o hub de cards pros outros
modos, intocado). Estrutura:

- **Backend** (`src/api/analytics_api.py`, prefixo `/analytics`, só
  leitura): `GET /filtros` (dropdowns), `GET /resumo` (KPI + agregações por
  sentimento/objeto), `GET /comentarios` (tabela paginada). Todo filtro do
  usuário (`canal_id`, `sentimento`) entra como parâmetro nomeado do
  BigQuery (`@canal_id`), nunca por f-string direta — evita injeção; nomes
  de tabela (constantes internas) sim compõem a SQL por f-string.
- **Frontend** (`app/templates/analytics/`, `app/static/analytics/`):
  filtros (canal/sentimento) + 4 cards de KPI (comentários enriquecidos,
  vídeos analisados, canais monitorados, % sentimento positivo — ícone em
  badge circular pastel, padrão inspirado no template Sneat, seção 12.7) +
  2 gráficos Chart.js (doughnut de sentimento, barras de objeto mais
  comentado) + tabela paginada reaproveitando `.tabela`/`.tabela-wrap` (já
  rolável, com cabeçalho sticky — hoje em `shared/base.css`, não mais
  page-scoped) e as mesmas badges `.selo`/`.chip` do Playground (seção
  12.5) para sentimento/adjetivos. Chart.js carregado via CDN
  (`cdnjs.cloudflare.com`) no `extra_js` do template.

### 12.5 Ajustes de UI no Playground

- **Prompts recolhíveis/compactos**: um único botão-ícone
  (`data-prompts-toggle`, seta que gira 180°) no cabeçalho da seção de
  Prompts — mesmo padrão visual/comportamental de `.sidebar__toggle`
  (`shared/sidebar.css`), não um `<select>` (versão inicial, trocada depois
  do usuário achar o select "não limpo o bastante"). Modo compacto esconde
  o editor (`.prompts__editor`) e reduz os cards da lista a nome+badge;
  abrir "Novo prompt" ou "editar" força de volta pro modo expandido
  automaticamente (senão o editor fica inalcançável). Estado em
  `estado.visualizacaoPrompts` (script.js) — **não reaproveitar o nome
  `estado.prompts`**, que já é a lista de prompts carregados da API.
- **Tabelas roláveis**: `.tabela-wrap` (`max-height`/`overflow-y:auto`) e
  `.tabela th` (`position: sticky; top: 0` com fundo sólido) — hoje **em
  `shared/base.css`**, não mais duplicado por página, exatamente para valer
  também para as tabelas novas do Dashboard.
- **Badges de sentimento/adjetivos**: o app já tinha um sistema de badge de
  status/sentimento (`.selo`/`.selo--positivo/--negativo/--neutro`,
  `shared/base.css`, usado em `x/script.js` e `pipeline/script.js`) — em vez
  de criar um componente novo, ele ganhou fundo suave (`color-mix`) e uma
  variante `--misto`, e passou a ser reaproveitado também para o sentimento
  do enriquecimento de comentários (Playground e Dashboard). Adjetivos
  usam `.chip` (também movido pra `shared/base.css`, antes só existia em
  `youtube/style.css`). **Não recriar esses componentes** — se o Dashboard
  ou qualquer tela nova precisar de badge/chip, é isso que já existe.
- **Teste de amostra do enriquecimento**: virou um sub-card
  (`.teste-enriquecimento`) com ícone+título+controles, resultado com
  `.selo`/`.chip` em vez de texto cru, e um indicador de carregamento
  (`.carregando-inline`, reaproveita a mesma animação `pulsar` já usada no
  ponto do trilho de execução) em vez de um "Testando…" solto.

### 12.6 Se o próximo agente for mexer nisso

- O ciclo de import é sensível: `src/pipelines/engine.py` importa
  `src.analytics.enrichment` **dentro do método** (`_passo_enriquecimento_
  comentarios`), não no topo do arquivo — importar no nível de módulo cria
  ciclo (`engine.py` → `analytics.enrichment` → `pipelines.models`/`store`,
  que por sua vez pertencem ao pacote `src.pipelines` que está inicializando
  `engine.py`). Se for adicionar mais funções de `src.analytics` em
  `engine.py`, manter o import local.
- `usage` (tokens) do passo de enriquecimento é somado **uma vez por vídeo**
  (uma chamada à LLM = um lote de comentários), não uma vez por comentário —
  todo item de um mesmo lote carrega o mesmo `usage` (é a mesma resposta da
  LLM), somar por item duplicaria a contagem.
- Testado contra BigQuery real nesta sessão (ver 12.7) — funcionou até o
  ponto da autenticação; o resto (criação de tabela, upsert, query) segue
  validado só com dublê (`tests/test_analytics_warehouse.py`).

### 12.7 Bug real: tabelas nunca eram criadas, mesmo com `.env` preenchido

**Causa raiz**: o `.env` tinha `GOOGLE_APPLICATION_CREDENTIALS` definida
**duas vezes** — primeiro como `'.\config'` (uma pasta, não um arquivo —
erro de digitação), depois, corretamente, como `config\<arquivo>.json`.
`carregar_env()` (`src/config.py`) só grava uma variável se ela **ainda não
estiver** em `os.environ` — ou seja, a primeira ocorrência (errada) vencia
**silenciosamente**, e a segunda (certa) era descartada sem aviso nenhum.
`bigquery.Client()` falhava dentro de `warehouse._obter_client()`, e essa
exceção morria no `try/except Exception: logger.exception(...)` de
`executor._persistir_tabelas` — nada chegava até o usuário, só o log do
servidor (que ninguém checou). Ação imediata: apagar a linha errada do
`.env`. Correções de código pra essa classe de bug nunca mais ficar muda:

- `carregar_env()` agora **loga um aviso** quando a mesma chave aparece mais
  de uma vez no `.env`, dizendo que a primeira ocorrência venceu.
- `settings.bigquery_configurado` não checa mais só "a variável está
  setada" — confere que o valor **é um arquivo que existe**
  (`Path(...).is_file()`). Pasta ou caminho inexistente → não configurado,
  com um aviso específico logado uma única vez (`_avisar_uma_vez`).
- `src/analytics/warehouse.py` guarda o último erro de gravação
  (`_ultimo_erro`, `{"quando", "mensagem"}`, nunca a credencial/exceção
  crua) e expõe via `status()`.
- `GET /youtube/config` agora devolve um bloco `"warehouse": {"configurado":
  bool, "ultimo_erro": ... | None}` — mesmo endpoint que já reportava
  presença de credencial, sem vazar valor (Regra da seção 10).
- O Dashboard (`analytics/script.js`) checa isso no carregamento e mostra
  um aviso explicando o motivo em vez de só um painel vazio sem contexto.

**Validado ao vivo nesta sessão**, com a credencial já corrigida e
`bigquery_configurado=True`: uma escrita real de teste (`warehouse.upsert`)
foi tentada contra o projeto `ford-493401`. **Resultado**: falhou na troca
de token OAuth (`oauth2.googleapis.com`), com
`SSL: CERTIFICATE_VERIFY_FAILED` — **o mesmo problema de sandbox já
documentado na seção 8** (o `certifi` deste ambiente de desenvolvimento não
confia em certificados que o Windows/`curl` confiam; não é um bug do
código, não tentar "corrigir" desabilitando verificação de certificado).
O importante: o novo diagnóstico funcionou exatamente como desenhado —
`warehouse.status()` capturou a mensagem real do erro
(`RetryError: ... CERTIFICATE_VERIFY_FAILED ...`) em vez de ficar em
silêncio. **Isso ainda precisa ser validado numa máquina sem esse problema
de certificado** (a do usuário, fora do sandbox) antes de considerar o
warehouse "funcionando de ponta a ponta" — a mecânica (schema, upsert,
idempotência) está correta e testada, só a chamada de rede real a
`oauth2.googleapis.com`/`bigquery.googleapis.com` não completou aqui.

### 12.8 Segundo bug real: só `canais_buscados` gravava, as outras tabelas nunca

Depois de corrigir o `.env` (seção 12.7) e rodar de verdade, o usuário
reportou exatamente o padrão previsto ali: `canais_buscados` aparecia no
BigQuery, `transcricoes_coletadas`/`comentarios_coletados` não, mesmo com a
aba REFINED do Playground mostrando contagens > 0 pras duas.

**Causa raiz** (achada reproduzindo localmente, sem precisar de rede): o
schema declara `coletado_em` como `TIMESTAMP`, mas o valor gravado é uma
**string** ISO 8601 (`datetime.isoformat()`). A conversão pandas→arrow que
`load_table_from_dataframe` faz no cliente, **antes de qualquer chamada de
rede**, não sabe transformar uma coluna `object` de strings numa coluna
`TIMESTAMP` sozinha — falha com `ArrowTypeError` na hora. Isso valia pras
**quatro tabelas** (todas têm `coletado_em`), mas só derrubava a partir da
segunda: `registrar_execucao()` chamava os 4 `upsert()` em sequência sem
isolamento nenhum — a exceção da primeira falha (que vinha logo depois de
canais, já que canais é a primeira da lista) abortava a função inteira,
então as tabelas seguintes **nunca chegavam a ser tentadas**. `canais_
buscados` só "funcionava" porque é a primeira da lista — na prática a
gravação dela também falhava (a tabela existe, criada pelo DDL antes do
load, mas provavelmente vazia).

Correção em `src/analytics/warehouse.py`:

- `_normalizar_tipos(df, schema)` — nova função, roda dentro de `upsert()`
  antes do load: para todo campo do schema com `field_type == "TIMESTAMP"`,
  converte a coluna correspondente do DataFrame com
  `pd.to_datetime(..., utc=True)`. Genérico — vale pra qualquer tabela/
  campo TIMESTAMP futuro, não só `coletado_em`.
- `registrar_execucao()` — as 4 chamadas de `upsert()` agora rodam num loop
  com `try/except` **por tabela**: uma falhar loga e segue pra próxima, não
  aborta mais as demais.
- `_ultimo_erro` virou um mapa `{nome_da_tabela: {"quando", "mensagem"}}`
  em vez de uma mensagem única — `warehouse.status()`/`GET /youtube/config`
  agora mostram o erro específico de cada tabela, sem uma mascarar a outra.
- Regressão coberta em `tests/test_analytics_warehouse.py` (seções 5 e 6):
  reproduz o `ArrowTypeError` sem a normalização e confirma que some com
  ela; simula uma tabela falhando e confirma que as outras 3 ainda gravam.

### 12.9 Amostra fixa de 3 comentários + remoção do botão de teste manual

Ajuste de escopo pedido pelo usuário: o enriquecimento automático
(seção 12.3) **não classifica mais todos os comentários coletados** — só
uma amostra fixa de `MAX_COMENTARIOS_ENRIQUECIMENTO_AUTOMATICO = 3`
(`src/pipelines/engine.py`), **no total por execução**, não por vídeo.
Achata `comentarios_por_video` em `(video_id, comentário)` preservando a
ordem de coleta, corta nos 3 primeiros, reagrupa por vídeo só pra manter o
padrão de uma chamada de LLM por vídeo. É uma demonstração de que a
classificação funciona, não uma análise completa — controla custo de LLM e
volume de escrita no warehouse.

Consequência direta: o botão manual "Testar amostra" (endpoint
`POST /youtube/comentarios/analise/teste`, `TesteEnriquecimentoRequest`)
**foi removido** — ficou redundante, já que a amostra automática já aparece
no resultado normal da execução. A tabela "Comentários extraídos" (modo
Tabela, REFINED) ganhou 4 colunas — Sentimento/Motivo/Adjetivos/Objeto —
preenchidas via lookup por `comment_id` contra
`estado.run.trust.enriquecimento_comentarios` (já chega pelo polling
normal, sem endpoint novo); linhas fora da amostra mostram "—". Uma tabela
de **Canais** (antes ausente) também foi adicionada à mesma view, a partir
de `dados.channels` — que já vinha de `GET /pipelines/runs/{run_id}/tables`
mas não era desenhada.

`seloSentimento()`/`chipsAdjetivos()` (script.js) foram mantidas do botão
removido e reaproveitadas aqui — não recriar, são as mesmas classes
`.selo`/`.chip` usadas no Dashboard.

### 12.10 Cache local do Playground (localStorage)

O estado do Playground (vídeos carregados, seleção, prompts ativos, a
execução em andamento/concluída) vivia só em memória — trocar de aba e
voltar, ou dar F5, perdia tudo. `app/static/youtube/script.js` agora
persiste em `localStorage['ford_fiap_youtube_playground']`:
`{ runId, videos, selecionados, promptsAtivos }` (o array `videos`
completo, não só ids, pra redesenhar a galeria sem buscar de novo).

- `salvarCache()` é chamada nos pontos onde esse estado já mudava:
  `atualizarResumo()` (cobre seleção de vídeo e toda `renderVideos()`), o
  handler de checkbox de prompt, e o `.then()` de `POST /youtube/
  playground/run` (quando `estado.runId` é setado).
- `carregarCache()`/`restaurarRun()` rodam uma vez na inicialização: repõe
  vídeos/seleção antes de `renderVideos()`; prompts ativos só depois que
  `carregarPrompts()` resolve (precisa da lista carregada primeiro); e um
  `runId` salvo dispara um fetch de `GET /pipelines/runs/{id}` — se ainda
  estiver rodando, retoma o polling; se já terminou, só redesenha
  trilho+camadas; se o run não existe mais (histórico limita a 200 em
  `store.py`), limpa o cache em silêncio, sem mostrar erro.
- Sem TTL — é só "não perder ao trocar de página", não um histórico
  duradouro. `try/catch` em volta de toda leitura/escrita de `localStorage`
  (modo privado, quota) — a página funciona igual sem cache se ele falhar.

### 12.11 Terceiro bug real: Dashboard não carregava dado nenhum + transcrição sem texto

Depois dos bugs das seções 12.7/12.8 corrigidos, as 4 tabelas passaram a
gravar de verdade — mas surgiram mais dois problemas reais.

**1. `transcricoes_coletadas` nunca teve o texto da transcrição.** O schema
só guardava metadados (`idioma`, `palavras`, `caracteres`) — nem o texto
bruto extraído nem o texto tratado (REFINED) eram gravados. Corrigido:
`SCHEMA_TRANSCRICOES_COLETADAS` ganhou `texto_bruto` (os segmentos
originais da API, concatenados, **sem** limpeza — entidades HTML e tudo,
igual veio) e `texto_refinado` (o `clean_text` que `refine.
refinar_transcricao()` já produzia, mas não chegava ao warehouse).
`registrar_execucao()` agora lê `run.raw.get("transcripts")` (além de
`run.refined`, que já lia) pra montar `texto_bruto`.

**2. `GET /analytics/comentarios` devolvia 500, então o Dashboard não
mostrava nada** (mesmo com as 4 tabelas já tendo dado de verdade).
**Causa raiz**: campos `REPEATED` do BigQuery (`output_struct.adjetivos`)
voltam de `to_dataframe()` como `numpy.ndarray`, não `list` — e nem
`json.dumps` nem o `jsonable_encoder` do FastAPI sabem serializar um
`ndarray` cru (erro obscuro: `vars() argument must have __dict__
attribute`). Qualquer linha de `comentarios_analise` com `adjetivos`
preenchido (ou seja, qualquer linha bem-sucedida) quebrava o endpoint
inteiro. `/analytics/resumo`/`/analytics/filtros` não tocam nesse campo,
por isso só a tabela de comentários do Dashboard ficava muda.

Corrigido em `src/api/analytics_api.py`: nova função `_valor_serializavel()`
normaliza `numpy.ndarray`→`list`, `numpy.integer`/`numpy.floating`→`int`/
`float` nativos (e NaN→`None`), `pandas.Timestamp`→string ISO, `pandas.NaT`
→`None` — aplicada a cada valor de `_executar()` antes de devolver. Testes
de regressão em `tests/test_analytics_api.py` (novo arquivo) reproduzem o
`ndarray` exatamente como o BigQuery devolve e confirmam que
`json.dumps`/`jsonable_encoder` aceitam o resultado.

### 12.12 Quinta tabela: `analises_ia` — qualquer prompt do usuário, não só comentários

Gap identificado pelo usuário: `comentarios_analise` só cobre o
enriquecimento automático fixo de comentários (seção 12.3). A saída de
**qualquer outro prompt** (ex.: "Percepção sobre o carro" rodando sobre
transcrição, ou um prompt de escopo `dataset` sobre o conjunto inteiro de
vídeos) só existia em SQLite (`src/pipelines/db.py::InsightRow`) — nunca
chegava ao warehouse, então não tinha como aparecer num dashboard/consulta
cross-execução.

Nova tabela `analises_ia` (`src/analytics/schemas.py::SCHEMA_ANALISES_IA`),
uma linha por chamada de LLM feita pela etapa `agent` do engine (fonte:
`run.trust["analises"]` — lista diferente de `run.trust["enriquecimento_
comentarios"]`, que é só do struct fixo). Colunas: `run_id` (search_id),
`video_id` (nulo em escopo `dataset`), `transcript_id` (correlato, quando
`input_type` toca transcrição), `escopo`, `prompt_id`/`prompt_name`/
`prompt_version`, `input_type` (transcript/comments/video/dataset),
`provider`/`model`, `success`, **`output_json`** (STRING — a saída bruta da
LLM serializada; schema livre de propósito, já que quem define o formato é
o prompt do usuário, Regra 8, não dá pra fixar um `RECORD` como em
`comentarios_analise`) e `error_type`/`error_message` (preenchidos quando
`success=False` — falhas são gravadas, não descartadas, mesmo padrão de
`InsightRow`).

Gravada em `warehouse.registrar_execucao()`, mesmo loop com isolamento por
tabela das outras 4 (seção 12.8). Testada em
`tests/test_analytics_warehouse.py` (seção 8): cobre análise por vídeo
(com `transcript_id` correlato), análise de escopo `dataset` (sem
`video_id`) e uma análise que falhou (confirma que erro é preservado, não
descartado).

## 13. Gov Mode — catálogo FIPE (marca/modelo/ano) com pipelines próprias

Sessão seguinte à seção 12. O que existia antes (`app/templates/gov/`
ligado a `app/app.py::gov_consultar`) era uma casca vazia: formulário de
placa/renavam que sempre devolvia `{"dados": None}` — nunca teve backend
de verdade. Pedido do usuário: reaproveitar esse slot do menu para um
catálogo de veículos brasileiros (marca → modelo → ano → código FIPE),
seguindo o mesmo padrão do notebook `tabela_marcas.ipynb`, com um
mini-dashboard para disparar/agendar a extração e consultar o resultado.

### 13.1 Fontes de dado

Espelha o notebook exatamente:
- `parallelum.com.br/fipe/api/v1` — marcas, modelos, anos e código FIPE.
  As chamadas de código FIPE levam `X-Subscription-Token: FIPE_API_TOKEN`
  (`.env`, opcional — sem ele o passo `fipe` falha com um erro tipado,
  não trava o resto da pipeline).
- `brasilapi.com.br/api/fipe/tabelas/v1` — só a tabela de meses de
  referência, sem autenticação.

**Decisão que aumenta bastante o volume de chamadas**: ao contrário do
notebook (que resolve só o ano mais recente), o catálogo aqui grava **um
registro por `(modelo, ano)`** — todos os anos de cada modelo. Por isso a
extração roda em background com progresso consultável, não como uma
chamada síncrona.

### 13.2 Por que um sistema paralelo, não uma extensão do YouTube

`Pipeline.source` (`src/pipelines/models.py`) é tipado concretamente como
`FonteYouTube`, e `PipelineEngine` importa `src.youtube.*` direto — não é
um `Union`/`Any` genérico. Generalizar esses tipos já em produção pra
caber o domínio FIPE arriscaria os testes que já passam sem necessidade.
Em vez disso, `src/govmode/` é um sistema completo e paralelo, no mesmo
formato (pipeline nomeada e versionada, execução em background, DAG do
Airflow), mas com seus próprios arquivos de dado
(`data/gov_pipelines.json`, `data/gov_runs.json`) e sua própria engine.

O que É genérico e seguro reaproveitar, é reaproveitado por import direto,
não copiado:
- `ConfigSchedule`, `StatusRun`/`StatusStep`/`StatusPipeline`, `ErroStep`,
  `agora_iso`/`novo_id` — de `src.pipelines.models` (zero acoplamento a
  YouTube).
- `montar_cron`/`descrever_schedule`/`slug` — de
  `src.airflow_gen.generator` (matemática de cron e nomeação de arquivo,
  também genéricas).
- `_carregar`/`_salvar` (I/O de JSON versionado) — de `src.pipelines.store`.

O que não é genérico ganha equivalente próprio, mesmo formato: `TipoStepGov`
em vez de `TipoStep`, `StepResultGov` em vez de `StepResult`,
`GovPipeline`/`GovPipelineVersao`/`GovPipelineRun` em vez dos análogos do
YouTube (`src/govmode/models.py`).

### 13.3 Warehouse compartilhado — `src/bigquery_warehouse.py`

A lógica do warehouse do YouTube (`src/analytics/warehouse.py`) já tinha
corrigido, a duras penas, bugs reais documentados na seção 12: cliente
lazy, criação idempotente de dataset/tabela, conversão de TIMESTAMP de
string para datetime antes do `load_table_from_dataframe` (seção 12.8) e
isolamento de erro por tabela. Em vez de duplicar tudo isso para o Gov
Mode, essa lógica foi extraída para uma classe genérica nova,
`BigQueryWarehouse` (`src/bigquery_warehouse.py`), parametrizada por
`dataset`/`project_id` no construtor.

**Decisão de risco**: `src/analytics/warehouse.py` (já em produção, com
testes passando) só mudou no mínimo necessário — sua função de
normalização de TIMESTAMP agora é um import da versão compartilhada
(`normalizar_tipos_timestamp`), o resto do arquivo é intocado.
`src/govmode/warehouse.py` usa `BigQueryWarehouse` direto, sem herdar nada
do warehouse do YouTube. `_valor_serializavel`/`registros_serializaveis`
(a correção de `numpy.ndarray`/`pandas.Timestamp` não serializável em
JSON, seção 12.11) também moraram nesse módulo compartilhado — tanto
`src/api/analytics_api.py` quanto `src/api/gov_api.py` importam de lá.

Dataset do Gov Mode: `GOVMODE_BIGQUERY_DATASET` (`.env`, padrão
`analytics_ford` — mesmo projeto BigQuery do warehouse do YouTube,
dataset separado).

### 13.4 Schemas (`src/govmode/schemas.py`)

| Tabela | Chave / grão | `write_disposition` |
|---|---|---|
| `marcas_veiculos` | uma linha por marca | `WRITE_TRUNCATE` (catálogo recarregado por completo a cada execução do passo `marcas`) |
| `modelos_veiculos` | uma linha por `(marca_codigo, modelo_codigo)` | `WRITE_APPEND`, com anti-join antes de inserir |
| `modelos_com_fipe` | uma linha por `(marca_codigo, modelo_codigo, ano_codigo)` — **todos os anos**, não só o mais recente | `WRITE_APPEND`, com anti-join |
| `referencia_meses_fipe` | uma linha por mês de referência FIPE | `WRITE_TRUNCATE` |

### 13.5 Engine e resumabilidade (`src/govmode/engine.py`)

Quatro passos (`TipoStepGov`): `marcas` → `modelos` → `fipe` →
`referencia_meses` (o último é independente dos outros três). Cada passo
lê o resultado do anterior no BigQuery, não em memória entre execuções —
uma pipeline interrompida no meio retoma de onde parou na próxima
execução.

Resumabilidade em duas granularidades, verificada por execução dupla em
`tests/test_govmode_engine.py` (zero linhas duplicadas, zero chamadas de
API repetidas):
- **Por modelo** — `modelos_veiculos` faz anti-join em
  `(marca_codigo, modelo_codigo)` antes de buscar modelos de uma marca.
- **Por modelo E ano** — `modelos_com_fipe` faz anti-join em
  `(marca_codigo, modelo_codigo, ano_codigo)`, buscado numa única query
  por passo (não uma por modelo) e resolvido em memória via `set` — é
  essa granularidade dupla que permite retomar mesmo no meio da lista de
  anos de um modelo específico.

`marcas_alvo` (em `FonteGovFipe`) é **obrigatória e não-vazia** por
desenho: rodar "todas as marcas do Brasil" de uma vez multiplicaria o já
alto volume de chamadas do passo `fipe` (todos os anos × todo modelo) sem
necessidade — a UI sempre pede para escolher marca(s) ao criar a
pipeline. Resolução de marca por nome é case-insensitive
(`LOWER(marca_nome) = @nome`), e o passo `modelos` reaproveita o mapeamento
recém-obtido pelo passo `marcas` na mesma execução (`contexto["marcas_
codigo_por_nome"]`) antes de cair para uma consulta ao BigQuery.

### 13.6 API (`src/api/gov_api.py`, prefixo `/gov`)

Mesmo shape de `src/api/pipelines_api.py` (CRUD de pipelines,
`run`/`status`/`versions`/`runs`/`generate-dag`/`dag`), mais os endpoints
de leitura do catálogo, que toleram tabela ainda inexistente (nenhuma
pipeline rodou ainda) devolvendo lista vazia em vez de 502
(`_executar_catalogo`, distinto do `_executar` de `analytics_api.py`, que
propaga erro — aqui "sem dado ainda" é um estado normal do catálogo
recém-criado, não uma falha):

- `GET /gov/marcas?tipo_veiculo=` — marcas catalogadas + contagem de
  modelos.
- `GET /gov/modelos?marca_codigo=&tipo_veiculo=` — modelos de uma marca +
  contagem de anos resolvidos.
- `GET /gov/fipe?marca_codigo=&modelo_codigo=&tipo_veiculo=` — todos os
  anos/códigos FIPE de um modelo.
- `GET /gov/resumo?tipo_veiculo=` — KPIs do mini-dashboard (total de
  marcas/modelos/registros com FIPE, % de cobertura).

Atenção à ordem de declaração (mesmo cuidado de `pipelines_api.py`):
`/runs/{run_id}` e as rotas fixas de catálogo/preço (`/marcas`,
`/modelos`, `/fipe`, `/resumo`, `/preco/*`) precisam vir **antes** de
`/pipelines/{pipeline_id}` no arquivo, senão o FastAPI tenta casar essas
rotas fixas como um `pipeline_id`.

**Catálogo & busca de preço, unificados** (inspirado na seção
"#01/busca_de_preco.ts" de fipe.api.br — usada só como referência
visual/UX; a fonte de dado continua sendo `FipeAPI`/parallelum, não o
produto pago deles). Antes eram duas seções separadas (um browse do
catálogo do BigQuery e uma busca de preço ao vivo); foram unificadas numa
só pra não repetir marca/modelo em dois fluxos:

- `GET /gov/preco/marcas|modelos` — direto na `FipeAPI`
  (`src/govmode/client.py`), sem tocar no BigQuery. Sempre carros:
  `tipo_veiculo` não é parâmetro — está fixo em
  `TIPO_VEICULO_PRECO = "carros"` no código.
- `GET /gov/preco/modelo?marca_codigo=&modelo_codigo=` — visão unificada:
  todos os anos ao vivo (`buscar_anos`) já cruzados com o que o catálogo
  do BigQuery (`MODELOS_COM_FIPE`) tiver mapeado de execuções passadas da
  pipeline (`codigo_fipe`/`atualizado_em`, sem custo de rede — via
  `_executar_catalogo`, tolerante a BigQuery não configurado, devolvendo
  `null` em vez de erro), mais o valor ao vivo do ano mais recente
  (`atual`, única chamada com token nesta rota). Se só o `atual` falhar
  (`FipeAPIError`), a resposta inteira não cai — `atual` vem como
  `{erro, motivo}` e a tabela de anos mapeados continua útil.
- `GET /gov/preco/valor?marca_codigo=&modelo_codigo=&ano_codigo=` — busca
  o valor de um ano específico sob demanda (usado quando o usuário clica
  "Ver valor" numa linha da tabela que não veio pré-carregada). Chama
  `buscar_codigo_fipe` (único outro método com token) e devolve o
  envelope da parallelum (`Valor`, `Marca`, `Modelo`, `AnoModelo`,
  `Combustivel`, `CodigoFipe`, `MesReferencia`, ...) direto, sem gravar
  nada.
- Diferença chave para os endpoints de catálogo (`/gov/marcas` etc.,
  ainda existentes e usados só pelo datalist da pipeline — veja abaixo):
  aqui "sem dado" nunca é um estado normal fora do que já foi tratado
  acima — qualquer outra `FipeAPIError` vira erro (`_erro_fipe`):
  `missing_token` → 503, `timeout`/`network` → 502, senão repassa o
  status da API externa.

### 13.7 Frontend (`app/templates/gov/`, `app/static/gov/`)

Reescrita completa — o formulário de placa/renavam saiu, junto do stub
`app/app.py::gov_consultar` (removido; a leitura passa pelo proxy
genérico `/api/*`, que já cobre `/api/gov/*` sem código extra no Flask).

- **KPIs**: mesmo padrão visual do Dashboard do YouTube (ícone em badge
  circular pastel + valor/rótulo), classes `.analytics-kpi*` replicadas em
  `gov/style.css` (esta página não carrega `analytics/style.css`).
- **Catálogo & preço** (`#01`, topo da página, painel único — antigo
  "Catálogo FIPE" e antiga "Busca de preço" foram fundidos aqui): marca →
  modelo (sempre carros — sem seletor de tipo de veículo), cada campo um
  **combobox filtrável** (`criarCombobox` em `gov/script.js`): input de
  texto + lista suspensa, filtrada localmente (substring,
  case-insensitive) conforme o usuário digita, a partir da lista inteira
  já carregada de `/api/gov/preco/*` — sem round-trip por tecla, igual o
  site de referência. Só habilita o próximo campo depois do anterior ser
  escolhido. Ao escolher o **modelo** (sem precisar escolher o ano à
  parte), chama `/api/gov/preco/modelo` e já mostra tudo de uma vez:
  painel de destaque com preço/código FIPE/combustível/ano modelo/mês de
  referência do ano mais recente (`.busca-preco*`) e, logo abaixo, a
  tabela com o histórico completo de anos — código FIPE já preenchido
  pros anos que o catálogo tiver mapeado, e um botão "Ver valor" por
  linha pra buscar sob demanda o valor de um ano específico ainda não
  mapeado (`/api/gov/preco/valor`, preenche a célula da linha in-place).
  Estado (`estadoBusca`) próprio.
- **Pipelines**: lista de pipelines nomeadas + modal de criação (nome,
  tipo de veículo, marcas-alvo como texto livre separado por vírgula,
  resolvido pelo backend case-insensitive — seção 13.5 — e agenda) +
  execução com polling + histórico + código da DAG. Estrutura e classes
  (`.pipeline-card`, `.dobra`, `.codigo`/`.tk-*`) replicadas de
  `app/static/pipeline/script.js`/`style.css`, já que esta página não
  carrega os estáticos da aba Pipelines do YouTube. O campo "Marcas-alvo"
  do modal continua com autocomplete (`<datalist>`) alimentado por
  `GET /gov/marcas` (catálogo do BigQuery) — cosmético, carregado em
  segundo plano, sem select visível na página.
- **Contextualização** (pedido explícito do usuário): a página já recebe
  `marca`/`modelo`/`versao` da query string, como todas as outras
  (`veiculo_selecionado()`, `app/app.py`). Se a `marca` da query bater por
  nome (case-insensitive) com uma marca da busca ao vivo, o combobox de
  marca do painel unificado e o campo "Marcas-alvo" do modal de nova
  pipeline já vêm pré-preenchidos. É cosmético — a identidade FIPE
  (`marca_codigo` numérico) nunca é unificada com a identidade de
  `specs_api` (nomes de scraping), que são fontes diferentes e forçar
  equivalência geraria falso-positivo.

### 13.8 Testes

- `tests/test_govmode_warehouse.py` — `BigQueryWarehouse` genérico:
  dataset/tabela idempotentes, `write_disposition` por tabela, conversão
  de TIMESTAMP, conversão pyarrow real das 4 tabelas do catálogo.
- `tests/test_govmode_engine.py` — os 4 passos com dublê de `FipeAPI` (sem
  rede real), resumabilidade nas duas granularidades (execução dupla),
  falha graciosa sem `FIPE_API_TOKEN`, parsing de meses de referência.
- `tests/test_govmode_pipeline.py` — CRUD e versionamento do store,
  `executor.executar_agora`/`iniciar_em_background` fim a fim (com
  polling).
- `tests/test_govmode_dag.py` — geração da DAG, confirma que o template do
  Gov Mode não se confunde com o do YouTube (executor, tags e credencial
  verificada são diferentes) e que nenhum segredo vaza pro código gerado.

---

## 14. Scraper Mode — auto-discovery de sites de carro

Cadastra um site qualquer de carro, usa um LLM pra inferir os seletores CSS
uma vez por (site, objetivo), valida numa tela de teste (determinística, sem
LLM) e só então registra o site — virando uma fonte de dados própria,
coletada em background e disponível como API dentro do app. Inspirado num
repositório de referência (`WebScrapingAntigravity`, fora deste projeto:
Next.js+Prisma+Playwright+Gemini) — a ideia central de lá que valia a pena
trazer pra cá está documentada explicitamente no §14.2.

### 14.1 Os três objetivos

Cada site cadastrado escolhe manualmente (checkbox, não auto-detectado)
quais objetivos mapear, cada um com sua própria URL de amostra:

| Objetivo | O que extrai | Pré-requisito |
|---|---|---|
| `ficha_tecnica` | Tabela de atributos técnicos (motor, potência, câmbio...) | — |
| `anuncios` | Listagem de anúncios (preço, localização, vendedor...) | — |
| `comentarios` | Comentários/avaliações de um anúncio | exige `anuncios` também marcado (validado em `src/api/scraper_api.py::_validar_objetivos`) |

### 14.2 Por que um domínio paralelo, não uma extensão do Gov Mode/YouTube

Mesmo racional já documentado na seção 13.2 pro Gov Mode, estendido aqui a
um **segundo** domínio paralelo: `Pipeline.source`/`StepResult.type`
(YouTube) e `GovPipeline`/`StepResultGov` (FIPE) são tipados concretamente
pros seus próprios domínios — generalizar um deles pra caber "site
qualquer com seletores CSS" arriscaria os testes já passando desses dois
sistemas, pra zero ganho real. `src/scraper/` importa só o que é
genuinamente genérico (`ConfigLLM`, `ConfigSchedule`, `StatusRun`/
`StatusStep`/`StatusPipeline`, `ErroStep`, `agora_iso`/`novo_id` de
`src.pipelines.models`; `_carregar`/`_salvar` de `src.pipelines.store`;
`BigQueryWarehouse` de `src.bigquery_warehouse`; `ProxyPool` de
`src.youtube.proxy_pool`; `montar_cron`/`descrever_schedule`/`slug` de
`src.airflow_gen.generator`; `Agente` de `src.agents`) e define seus
próprios equivalentes pro que é específico (`SiteScraper`, `MapaSeletor`,
`ScraperRun`, `TipoStepScraper` — `src/scraper/models.py`).

O `/scraping` mode existente (`src/utils/CarrosWeb.py`, `src/api/
specs_api.py`) também não é tocado — é uma busca estática (curl_cffi +
BeautifulSoup, fontes fixas, sem persistência) que continua funcionando
exatamente como antes; o Scraper Mode é aditivo, um mecanismo de fetch novo
(Playwright) só usado por `src/scraper/`.

### 14.3 A ideia central trazida do repositório de referência

`gemini_logic.md` (do repositório de referência) chama essa estratégia de
**"Detecção de Estrutura sob Demanda"**: o LLM é chamado **uma vez** por
(site, objetivo) pra inferir a *forma* dos dados (os seletores CSS);
**toda extração seguinte é pura leitura determinística por seletor**
(BeautifulSoup, sem LLM). Isso dá três coisas ao mesmo tempo — economia
(LLM só custa na descoberta, não a cada coleta), velocidade (sem latência
de LLM nas execuções recorrentes) e confiabilidade (o LLM define a *forma*,
o código lê os dados *reais* — sem risco de alucinar um preço ou nome que
não está na página).

Três diferenças deliberadas do que o repositório de referência fazia:
1. **Tela de teste antes de registrar** — lá, o JSON do LLM era usado na
   mesma hora que chegava, sem revisão humana. Aqui, `POST .../discover`
   só grava um RASCUNHO (`site.seletores`, `registrado` continua `false`);
   só depois de `POST .../test` confirmar que os seletores produzem dados
   de verdade (`validado_em` preenchido) é que `POST .../register` libera
   o site pra rodar de verdade.
2. **Resumo de confirmação em linguagem natural, gerado pela IA** (pedido
   explícito do usuário — o diferencial sobre o repositório de referência,
   que só mostrava a tabela de dados crua): a cada `POST .../test`, além da
   tabela, `discovery.gerar_resumo_confirmacao()` chama o LLM de novo — não
   pra inferir seletor nenhum, só pra **descrever em 1-2 frases** o que a
   amostra extraída parece ser (ex.: "Encontrados 12 atributos técnicos,
   aparenta ser a ficha de um Ford Ka 2024"), pro usuário confirmar de
   leitura rápida "é exatamente isso que essa página deveria mostrar?"
   antes de aprovar. Roda sobre os **dados já extraídos** (determinístico),
   nunca sobre o HTML — não é descoberta de novo, é uma segunda opinião da
   IA sobre o resultado. Nunca bloqueia o teste: se essa chamada falhar,
   vira uma frase de aviso ("Não foi possível gerar o resumo automático...")
   e a tabela de dados continua sendo a fonte de verdade
   (`tests/test_scraper_discovery.py`, seção 4).
3. **Proxy fixo compartilhado, não por site** — lá, cada site guardava seu
   próprio proxy (host/porta/user/senha em colunas da tabela, sem rotação).
   Aqui, todos os sites compartilham o mesmo `ProxyPool` do §8/§14 (rotação
   round-robin, lista fechada configurada uma vez via env), consistente
   com o resto do projeto.

### 14.4 Motor de descoberta (`src/scraper/discovery.py`) — a única camada que chama o LLM

Fluxo por objetivo (`descobrir_objetivo`): busca HTML (`browser.py`,
Playwright + retry `2**attempt` até 3 tentativas, mesma constante de
`CarrosWeb._get`) → simplifica (`html_simplifier.py`: tira `script`/
`style`/`svg`/comentários/atributos inline, corta em
`settings.scraper_discovery_max_html_chars`) → `Agente(provider=site.llm.
provider, model=site.llm.model, ...)` (por site, cai pro padrão global
`LLM_PROVIDER`/`LLM_MODEL` se `None` — igual `Agente.__init__` já faz) →
`agente.run(html, prompt=PROMPT_POR_OBJETIVO[objetivo], output_format=
"json")` (nunca levanta — `AgentResult.success` indica falha) → valida o
JSON contra o Pydantic `Seletor*` certo (`SeletorFichaTecnica`/
`SeletorAnuncios`/`SeletorComentarios`, mesmo padrão de validação de
`AnaliseComentarioStruct` em `src/analytics/enrichment.py`) → devolve
`MapaSeletor` (rastreia `llm={"provider","model"}`) ou `ErroStep` isolado.

`descobrir_site` roda isso por objetivo com try/except independente —
`tests/test_scraper_discovery.py` cobre explicitamente que uma falha de
provider (`ProviderError`) num objetivo e uma saída fora do schema
(`OutputParseError`, de um `pydantic.ValidationError` capturado) noutro
objetivo não vazam um pro outro, nem derrubam o terceiro que teve sucesso.

### 14.5 Motor de extração (`src/scraper/engine.py`, `extractor.py`) — determinístico, sem LLM

`ScraperEngine.executar` — mesmo shape de `GovEngine.executar`
(§13.5): dispatch por objetivo, cada `_passo_*` num try/except próprio →
`resultado.falhar(...)` nunca propaga, agregação final `PARCIAL`/`FALHOU`/
`OK`. Cada passo busca o HTML de novo (Playwright), extrai via
`extractor.extrair_*` (BeautifulSoup, usando o `MapaSeletor.selectors`
já salvo — sem LLM nenhum aqui) e grava no BigQuery.

Tabelas (`schemas.py`) — todas append (logs históricos, não catálogo
recarregado):

| Tabela | Grão | Dedupe |
|---|---|---|
| `scraper_ficha_tecnica` | `(site_id, marca, modelo, versao, atributo, coletado_em)` | nenhum — histórico puro, valor de atributo pode mudar com o tempo |
| `scraper_anuncios` | `(site_id, marca, modelo, versao, ano, url_anuncio)` | anti-join antes do upsert — mesma página rodada 2x não duplica anúncio |
| `scraper_comentarios` | `(site_id, marca, modelo, versao, ano, url_origem, texto)` | anti-join antes do upsert |

`tests/test_scraper_engine.py` cobre as duas garantias centrais: rodar a
mesma configuração duas vezes faz `ficha_tecnica` crescer (histórico) mas
NÃO duplica `anuncios`/`comentarios`; e um objetivo com fetch quebrado
(`FetchError`) deixa a execução `partial`, sem impedir os outros dois de
concluir e gravar.

### 14.6 API (`src/api/scraper_api.py`, prefixo `/scraper`)

Mesmo shape de `gov_api.py` (CRUD + run/status/versions/runs/generate-dag/
dag), mais o que é específico deste módulo. Desde o §14.11 (cadeia de
etapas), a granularidade de mapeamento/teste é **por etapa**, não mais por
objetivo inteiro — o `POST .../test` plano de um objetivo foi removido:

- `GET /scraper/config` — providers disponíveis + lista curada de modelos
  por provider (`src/scraper/modelos_llm.py`), no mesmo formato de
  `GET /youtube/config` — alimenta os `<select>` de provider/modelo no
  cadastro (§14.11).
- CRUD de etapas por objetivo (`POST`/`PUT`/`DELETE .../etapas[/{ordem}]`,
  `POST .../etapas/reorder`) — ver §14.11.
- `POST .../etapas/{ordem}/discover` — roda a descoberta (LLM) só nesta
  etapa, mescla o resultado como rascunho (não valida, não registra).
- `POST .../etapas/{ordem}/test` — extração síncrona e determinística
  (sem LLM) contra os seletores informados (editáveis) ou os já
  descobertos, só desta etapa — mesmo papel de `POST /prompts/{id}/test`
  (`src/api/prompts_api.py`). Sucesso (`len(linhas) > 0`) marca
  `validado_em` **dessa etapa**; o objetivo só fica "pronto" quando toda
  etapa da cadeia estiver validada (`MapaSeletor.pronto()`). A resposta
  também inclui `resumo_ia` — o resumo em linguagem natural gerado por
  `discovery.gerar_resumo_confirmacao()` sobre a amostra extraída (§14.3,
  ponto 2) — nunca bloqueia a resposta, vira uma frase de aviso se essa
  chamada de IA falhar.
- `POST .../etapas/{ordem}/detectar-filtros` — parsing determinístico de
  query string + classificação por LLM contra o vocabulário fixo de
  filtro (§14.11); persiste `filtros`/`url_template` na etapa.
- `POST /scraper/sites/{id}/discover` — roda a descoberta em toda etapa de
  todo objetivo pedido (rascunho, isolamento de falha por etapa).
- `POST /scraper/sites/{id}/register` — exige `validado_em` preenchido em
  **toda etapa de todo** objetivo escolhido (`site.objetivos_prontos()`);
  versiona, `registrado=true`, `status=active`.
- `POST /scraper/sites/{id}/run` — aceita `filtros: dict[str,str]`
  opcional (ex.: `{"marca": "Fiat"}`), substituído no `url_template` da
  etapa 0 antes do fan-out (§14.11) — é o que fecha o loop de "consultar o
  site dinamicamente" depois de registrado.
- `GET /scraper/dados/ficha-tecnica|anuncios|comentarios` — leitura do
  BigQuery, filtros opcionais `site_id`/`marca`/`modelo`.

`/config`, `/discover` e `/test`/`/detectar-filtros` (site e etapa) não
dependem de BigQuery (só Playwright + LLM); `/register`/`/run`/`/dados/*`
dependem — 503 sem `GOOGLE_APPLICATION_CREDENTIALS`, mesmo contrato de
`gov_api.py::_exigir_configurado`.

### 14.7 Frontend (`app/templates/scraper/`, `app/static/scraper/`)

Fluxo **cadastrar → mapear → testar cada etapa → registrar** — não existia
um wizard multi-passo no projeto antes disso; reaproveita o vocabulário
visual já estabelecido (`.painel`/`.campo`/`.grade`/`.btn`/`.modal`/
`.tabela`/`.selo`/`.pipeline-card`→`.site-card`/`.dobra`) em vez de
inventar um novo. A visualização da cadeia de etapas (§14.11) é aditiva:
`.fluxo-etapas`/`.fluxo-etapa`/`.fluxo-seta`.

- **Cadastro** (modal): nome, URL base, checkboxes de objetivo
  (`comentarios` só habilita se `anuncios` estiver marcado — mesma regra
  do backend, checada nos dois lados), uma URL de amostra por objetivo,
  `<select>` de provider (alimentado por `GET /scraper/config`, com selo
  "configurado"/"sem credencial") + `<select>` de modelo curado por
  provider com opção final "Custom/outro…" que revela um input de texto
  livre (protege contra a lista curada ficar desatualizada).
- **Mapear**: `POST .../discover` (site inteiro), atualiza o rascunho de
  seletores de cada etapa no backend — o usuário abre a caixa da etapa
  pra conferir/testar (feedback por etapa, reflete o isolamento de falha
  do §14.4/§14.11).
- **Fluxo por objetivo**: cada objetivo desenha suas etapas como caixas
  ligadas por um conector (`→`, rotulado com o `campo_herdado`) — clicar
  numa caixa abre o modal de configuração daquela etapa; "+ etapa" anexa
  uma nova etapa no fim da cadeia; "×" remove (reindexa e invalida o
  teste das etapas restantes).
- **Modal de etapa**: URL de amostra; seletores fixos (última etapa do
  objetivo) ou uma lista livre de campos nomeados + seletor de linha
  (etapa intermediária de navegação) + campo de texto pra indicar qual
  desses campos é herdado pela próxima etapa; painel de filtros de URL
  com botão "Detectar filtros" (mostra `nome_param`/`valor_amostra`/
  `<select>` de papel); botões "Mapear com IA" e "Testar" — teste mostra
  **resumo da IA** (`.scraper-resumo-ia`, 1-2 frases perguntando "é
  exatamente isso que essa página deveria mostrar?") + tabela de prévia
  com a coluna-chave do objetivo (preço/valor do atributo/nota) destacada
  em negrito/cor de destaque (`.tabela__coluna-chave`).
- **Site registrado**: card com selo de status, "Executar agora" (polling
  em `GET /scraper/runs/{run_id}`, mesmo padrão do Gov Mode), histórico de
  execuções, código da DAG, e uma dobra "Dados coletados" (filtro de
  marca/modelo + uma tabela por objetivo, lendo `GET /scraper/dados/*`).

### 14.8 Config (`src/config.py`) e proxy

`# ── Scraper (auto-discovery) ──`: `scraper_bigquery_dataset` (default
`scraper_ford`), `scraper_proxy_list_url`/`scraper_proxy_list_path`
(mesma prioridade do §8), `scraper_playwright_headless`/
`_timeout_ms`/`scraper_discovery_max_html_chars`. Nenhuma variável nova de
LLM — `ConfigLLM` por site já cai pro `LLM_PROVIDER`/`LLM_MODEL` globais.

**Dependência nova**: `playwright` (`requirements.txt`) — depois do `pip
install`, é preciso rodar **uma vez** `playwright install chromium`
(baixa o binário do navegador; não é `pip install` normal). Nenhuma outra
dependência do projeto precisa desse passo extra.

### 14.9 Testes

- `tests/test_scraper_warehouse.py` — `BigQueryWarehouse` genérico, as 3
  tabelas gravam `WRITE_APPEND`, conversão pyarrow real.
- `tests/test_scraper_url_utils.py` — `extrair_query_params`/
  `montar_template` (`src/scraper/url_utils.py`, puro, sem LLM/rede):
  múltiplos parâmetros, sem query string, chave duplicada, papel `outro`
  não colide, `.format()` do template gerado é válido de verdade.
- `tests/test_scraper_discovery.py` — dublê de `Agente` (decide a resposta
  por um marcador no HTML recebido) + dublê de `browser.buscar_html`:
  sucesso valida contra o Pydantic certo; falha de provider e saída fora
  do schema ficam isoladas por objetivo; página fora do ar e falta de URL
  de amostra viram erro tipado, nunca exceção. Seção 2 cobre
  `descobrir_etapa(ultima=False)` contra `SeletorEtapaIntermediaria`.
  Seção 5 cobre `gerar_resumo_confirmacao`: não chama a IA à toa quando
  não há dado extraído, o resumo reflete a amostra recebida, e falha do
  provider vira frase de aviso, nunca exceção. Seção 6 cobre
  `classificar_filtros`/`detectar_filtros`: parâmetro conhecido
  classificado certo, parâmetro fora do vocabulário cai em `outro`, sem
  query string não chama a IA à toa.
- `tests/test_scraper_engine.py` — dublê de warehouse (anti-join em
  memória) + `browser.buscar_html`: um objetivo falhando não derruba os
  outros (`partial`); ficha técnica cresce (histórico) mas anúncios não
  duplicam ao rodar duas vezes; objetivo sem seletor mapeado é pulado, não
  falha. Seção 4 cobre a cadeia de 2 etapas (fan-out real): uma listagem
  com 3 linhas gera 3 fetches na etapa seguinte, uma delas quebrada —
  confirma 2/3 sobreviventes, linhas vindas dos DOIS detalhes diferentes
  (não é só reusar a amostra), e o passo ainda fecha `success`.
- `tests/test_scraper_pipeline.py` — CRUD/versionamento do store (mudar a
  URL-alvo invalida `registrado` e reseeda uma etapa nova, não validada,
  pra URL atual — não mais um `seletores == {}`), `executor.disparar_site`
  recusa site não registrado, `executar_agora`/`iniciar_em_background` fim
  a fim com polling.

**Não coberto por teste automatizado** (verificado manualmente, ver
§14.10): Playwright de verdade (fetch real + proxy), chamada de LLM real
ponta a ponta, geração de DAG (`dag_generator.py` — mesmo padrão de
`test_govmode_dag.py`, ainda não escrito).

### 14.10 Verificação manual de ponta a ponta

1. `pip install -r requirements.txt && playwright install chromium`.
2. `uvicorn main:app --port 8000` + `flask --app app/app.py run --port 5000`.
3. Em `/scraper`, cadastrar um site real com `ficha_tecnica`, dando como
   URL de amostra uma página de **listagem** (não a ficha em si) —
   confirma o cenário que motivou o §14.11: "antes da ficha técnica,
   precisa achar o carro".
4. Na caixa da etapa 0 (fluxo visual), "Mapear com IA" ou preencher à mão
   `linha`/`campos` (um deles deve ser a URL da ficha); marcar esse campo
   como herdado; "Testar" — confirma que a etapa fica "Testada" e a
   prévia mostra uma lista de URLs.
5. "+ etapa" adiciona a etapa 1 (final); usar como URL de amostra uma das
   URLs que a etapa 0 acabou de encontrar; mapear/testar com os seletores
   de `SeletorFichaTecnica` de sempre.
6. "Detectar filtros" na etapa 0 — confirmar que um parâmetro tipo
   `fabricante`/`marca` é classificado corretamente e um `url_template`
   aparece.
7. "Registrar", "Executar agora", `GET /scraper/runs/{id}` até concluir,
   conferir em `GET /scraper/dados/ficha-tecnica?site_id=...` que vieram
   linhas de **vários carros diferentes** (prova que o fan-out
   multiplicou, não só reusou a amostra da etapa final).
8. Quebrar uma URL da etapa 0 de propósito (editar pra uma rota 404) e
   rodar de novo — confirmar que o passo ainda fecha `success`/`partial`
   com "N-1/N" no resumo, não falha total.
9. Rodar `POST /scraper/sites/{id}/run` com `{"filtros": {"marca": "..."}}`
   e confirmar que muda quais carros são raspados.
10. Remover `GOOGLE_APPLICATION_CREDENTIALS` e confirmar que só
    `/dados/*`/`/register`/`/run` degradam (503) — `/discover`/`/test`/
    `/detectar-filtros`/`/config` continuam funcionando.

### 14.11 Cadeia de etapas dependentes, filtros de URL e fluxo visual

Motivado por um caso real que o modelo de "uma URL de amostra por
objetivo" (§14.1) não cobria: alguns sites exigem **navegar antes de
extrair** — uma busca/listagem que produz várias linhas (ex.: uma coluna
`url_anuncio`), cada uma levando a uma página de detalhe própria (ex.: a
ficha técnica daquele carro específico). É exatamente o padrão que
`src/utils/CarrosWeb.py` já resolve à mão, hardcoded, pra um site só
(fabricante → modelo → versões → ficha); esta extensão generaliza esse
encadeamento pra qualquer site cadastrado, como uma lista de **etapas**
por objetivo em vez de uma única URL/mapa de seletores.

**Modelo de dados** (`src/scraper/models.py`) — `MapaSeletor` virou um
envelope sobre `list[EtapaScraper]`:

```python
class EtapaScraper(BaseModel):
    id: str
    ordem: int
    url_amostra: str
    url_template: str          # preenchido por .../detectar-filtros
    filtros: list[FiltroParam]
    selectors: dict[str, Any]  # forma fixa (última etapa) OU {"linha","campos"} (navegação)
    campo_herdado: str | None  # None só na ÚLTIMA etapa
    llm: dict[str, str]
    descoberto_em: str | None
    validado_em: str | None

class MapaSeletor(BaseModel):
    objetivo: TipoObjetivoScraper
    etapas: list[EtapaScraper]

    def pronto(self) -> bool:
        return bool(self.etapas) and all(e.validado_em for e in self.etapas)
```

**Compatibilidade retroativa**: um `model_validator(mode="before")` em
`MapaSeletor` detecta o formato antigo (dict com `selectors`/`url_amostra`
no nível raiz, sem chave `etapas`) e embrulha numa lista de uma etapa só —
o site real já registrado em `data/scraper_sites.json` (`na_pista`,
descoberto antes desta extensão) carrega sem precisar reeditar o JSON à
mão.

**Descoberta por etapa** (`src/scraper/discovery.py`) — `descobrir_etapa
(site, objetivo, etapa, *, ultima)`: quando `ultima=True`, mesmo
`SELETOR_POR_OBJETIVO[objetivo]` de sempre (§14.4); quando `False`, um
prompt novo (`PROMPT_ETAPA_NAVEGACAO`) valida contra
`SeletorEtapaIntermediaria` (`{"linha": "...", "campos": {...}}`) — a IA
sugere qual campo parece ser o link pra próxima página, o usuário confirma
via `campo_herdado`.

**Fan-out no motor de execução** (`src/scraper/engine.py::_executar_cadeia`)
— generaliza o `f"{base_url}/{href}"` manual do `CarrosWeb.py`: busca
`etapas[0]` uma vez; a partir daí, **cada etapa seguinte é buscada uma vez
por linha** que a etapa anterior produziu, resolvendo a URL via
`urljoin(url_anterior, linha[campo_herdado])`. Tolerância parcial: uma
linha cuja etapa seguinte falhar (fetch quebrado, 404) é pulada e logada —
só quando **nenhuma** linha sobrevive é que o passo falha (`EmptyData`);
etapa 0 falhando por inteiro continua sendo falha total (`FetchError`,
como antes). `extractor.extrair_linhas_genericas(html, selectors, url)` é
a extração genérica usada nas etapas de navegação (campos link-like —
nome contendo `url`/`link`/`href` — são resolvidos pra absoluto via
`urljoin`); os 3 extratores finais (`extrair_ficha_tecnica`/`_anuncios`/
`_comentarios`) não mudam, continuam rodando só na última etapa.

**Filtros de URL como template reutilizável** — dividido em parte
determinística e parte LLM (Regra 11 preservada, só `discovery.py` chama
LLM):

- `src/scraper/url_utils.py` (puro, sem LLM) — `extrair_query_params(url)`
  (via `urlsplit`/`parse_qsl`) e `montar_template(url, filtros)` (troca
  cada valor classificado por `{papel}`, preservando os demais).
- `discovery.py::classificar_filtros(params, llm)` — manda só
  `{nome_param, valor_amostra}` (sem HTML) pro LLM classificar contra o
  vocabulário fixo `marca|modelo|versao|ano|id|outro`.
- `discovery.py::detectar_filtros(url, llm)` orquestra as duas partes;
  persiste `etapa.filtros`/`etapa.url_template`. Rodar
  `POST /scraper/sites/{id}/run` com `filtros={"marca": "Fiat"}` substitui
  o template da etapa 0 antes do fan-out — fecha o loop de "consultar o
  site dinamicamente" prometido no cadastro.

**Lista curada de modelos por provider** (`src/scraper/modelos_llm.py`,
novo, fora de `src/agents/providers/` de propósito — aquele módulo existe
pra ter "um modelo padrão, sem catálogo" por design, §9; generalizar ali
acoplaria um domínio de UI no genérico à toa): `GET /scraper/config`
expõe, por provider, `configurado`/`env_var`/`modelo_padrao`/`modelos` —
o frontend sempre soma uma opção final "Custom/outro…" que revela um
input de texto livre, porque a lista curada por natureza fica
desatualizada com o tempo (modelos novos saem depois).

**Fluxo visual sem dependência nova** — a ideia original cogitava um
editor node-based (Rete.js/React Flow, como um mini n8n); na prática, o
projeto não tem nenhuma infraestrutura de build (sem Node.js/npm/bundler,
100% Jinja + JS vanilla), e uma verificação da CDN do Rete.js v1 nesta
sessão mostrou que os pacotes de renderização vanilla (`rete-area-plugin`/
`rete-render-plugin`) não têm uma versão 1.x utilizável de verdade —
adotar isso sem poder verificar o carregamento/render num browser de
verdade seria um risco desnecessário. A decisão foi usar flexbox + CSS
puro (`.fluxo-etapas`/`.fluxo-etapa`/`.fluxo-seta` em
`app/static/scraper/style.css`): caixas numeradas em sequência, ligadas
por um conector rotulado com o `campo_herdado`, clicáveis (abre o modal de
configuração daquela etapa) — a mesma leitura visual do objetivo original
(fluxo de etapas dependentes, ponta a ponta visível), sem puxar uma lib
externa cujo comportamento não dava pra confirmar neste ambiente.
