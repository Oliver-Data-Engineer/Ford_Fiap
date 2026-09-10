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
7. **Suporte a proxy** para contornar esse bloqueio — duas estratégias
   (Webshare nativo + lista estática), testado ao vivo com credenciais reais
   do usuário. **Resultado do teste: a infraestrutura funciona: mas a conta
   Webshare do usuário tem o produto errado ("Proxy Server"/"Static
   Residential", não "Residential") — YouTube bloqueia datacenter IPs
   inteiros, rotação não ajuda dentro da mesma faixa.** Ver seção 8.

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
│   ├── engine.py                O motor: SOURCE → RAW → REFINED → AGENTE → TRUST
│   ├── executor.py              Execução async/background + entrada síncrona do Airflow
│   ├── store.py                 Persistência JSON (prompts/pipelines/runs) em data/
│   └── db.py                    Persistência SQLite (tabelas Vídeos/Canais/Transcrições/Comentários/Insights)
│
├── airflow_gen/
│   └── generator.py            Pipeline (config) → arquivo de DAG (.py) em airflow/dags/
│
└── api/                        CAMADA HTTP (FastAPI)
    ├── youtube_api.py           /youtube/*
    ├── prompts_api.py           /prompts/*
    ├── pipelines_api.py         /pipelines/*
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
│   └── dashboard/, scraping/, compare/, x/, gov/   Não tocados estruturalmente — só herdam o tema
└── static/
    ├── shared/base.css           Tokens de design (paleta, tipografia, radius, sombra) — fonte única do tema
    ├── shared/sidebar.css        Navegação lateral
    ├── youtube/{style.css,script.js}   O maior arquivo do projeto (script.js ~1300 linhas)
    └── pipeline/{style.css,script.js}
```

```
tests/
├── test_pipeline.py    Engine completo: RAW/REFINED/TRUST, versionamento, DAG, fonte_fixa, falhas por etapa
├── test_db.py           SQLAlchemy: as 5 tabelas, join de canal embutido, idempotência
├── test_agentes.py      Formato de requisição de cada adapter de LLM (Groq/OpenAI/Anthropic/Gemini) + erros HTTP
├── test_transcript.py   Classificação RequestBlocked→'blocked' vs TranscriptsDisabled→'unavailable' (biblioteca real)
├── test_proxy_pool.py   Rotação, retentativa, prioridade Webshare > lista estática
└── test_realce.js       Realce de sintaxe do visualizador de DAG (regressão de bug de markup)
```

Rodar tudo:
```bash
for f in tests/test_pipeline.py tests/test_db.py tests/test_agentes.py tests/test_transcript.py tests/test_proxy_pool.py; do python "$f"; done
node tests/test_realce.js
```
Todos rodam **sem rede e sem chave de API** (dublês reproduzem o formato exato de cada serviço). Total: ~150 verificações.

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

## 8. Proxies para transcrição — estado real, não teórico

**Contexto**: `youtube_transcript_api` é bloqueado pelo YouTube quando as
requisições vêm de IP de datacenter (`RequestBlocked`/`IpBlocked`) — comum
em ambientes de nuvem/CI. Isso **não é bug do projeto**, é o YouTube do lado
de fora recusando.

**O que existe** (`src/youtube/transcript.py`, `src/youtube/proxy_pool.py`):

1. **Webshare nativo** (`YOUTUBE_WEBSHARE_USERNAME`/`_PASSWORD`) — usa
   `WebshareProxyConfig` da própria biblioteca, que fala com o gateway
   rotativo `p.webshare.io` e tem retentativa embutida
   (`retries_when_blocked`). **Tem prioridade** sobre a opção 2 se ambas
   configuradas.
2. **Lista estática** (`YOUTUBE_PROXY_LIST=caminho/para/proxies.csv`,
   formato `host:porta:usuario:senha` por linha — exportação padrão
   Webshare) — rotação round-robin própria (`ProxyPool`), com retentativa
   automática (até 3 tentativas, um proxy diferente a cada vez) **somente**
   quando o erro é `RequestBlocked`/`IpBlocked` — qualquer outro motivo
   (vídeo sem legenda, indisponível) falha na hora, sem gastar tentativa.

**Resultado do teste ao vivo** (com as credenciais reais do usuário, arquivo
`config/Webshare 10 proxies.csv` — git-ignorado, nunca commitado):

- Os proxies conectam normalmente (confirmado via `ipinfo.io`) — ASN
  **Leaseweb UK Limited**, ou seja, são proxies de **datacenter**.
- Tanto a rotação manual (`GenericProxyConfig`, lista estática) quanto o
  gateway nativo (`WebshareProxyConfig`, mesmas credenciais de conta) foram
  bloqueados pelo YouTube.
- A própria biblioteca identificou e explicou a causa no corpo do erro: a
  conta Webshare do usuário tem o produto **"Proxy Server"/"Static
  Residential"** (ou tier gratuito) — **não** "Residential". Só
  "Residential" (pool de +30M IPs residenciais reais) contorna o bloqueio de
  forma confiável; datacenter/estático é blocklistado em faixa inteira,
  então trocar de IP dentro da mesma faixa não ajuda.

**Conclusão prática**: a implementação está correta e pronta — se o usuário
comprar o pacote certo na Webshare, funciona **sem nenhuma mudança de
código**, só trocando as duas variáveis de ambiente. Até lá, a pipeline
continua tolerando o bloqueio graciosamente (não trava; o resumo da etapa
agora diz *"YouTube bloqueou N requisição(ões) de transcrição (limite de
IP/volume...)"* em vez de confundir com "vídeo sem legenda").

**Se o próximo agente for revisitar isso**: não adianta tentar mais
estratégias de rotação sobre os mesmos 10 IPs de datacenter — já foi
tentado e a causa raiz está confirmada pela própria lib. O caminho é
produto Webshare "Residential" (ou trocar de provedor de proxy residencial).

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

GET    /specs/fabricantes  /modelos  /versoes     POST /specs/executar   (módulo anterior, intocado)
```

Ordem de rota importa em `pipelines_api.py`: `/runs/{run_id}...` precisa vir
**antes** de `/{pipeline_id}...` no arquivo, senão FastAPI casa `"runs"`
como se fosse um `pipeline_id`.

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

---

## 11. O que NÃO foi feito (próximos passos possíveis, não iniciados)

- Proxy residencial funcional (depende de compra externa, seção 8).
- Nenhuma mudança nas páginas `dashboard`, `compare`, `x`, `gov` além de
  herdar o tema — a interação/funcionalidade delas é anterior a esta sessão
  e não foi tocada.
- Sem browsing histórico de dados entre execuções na UI (o banco guarda tudo
  por `run_id`, mas não há uma tela "ver todas as execuções desta pipeline
  numa tabela consolidada" — hoje é run a run).
- `WebshareProxyConfig.filter_ip_locations` (restringir país) não está
  exposto em `TranscriptAPI` — daria pra adicionar se fizer sentido depois
  que o produto certo estiver ativo.
