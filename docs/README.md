# Ford FIAP — Plataforma de Inteligência Competitiva Automotiva

Plataforma web para coleta, normalização e análise de dados técnicos e de mercado sobre veículos da concorrência. O projeto foi desenvolvido como parte de um desafio acadêmico FIAP em parceria com a Ford.

---

## Objetivo

Permitir que analistas de inteligência competitiva pesquisem especificações técnicas de qualquer veículo do mercado a partir de uma entrada simples (marca, modelo, versão), consolidando dados de múltiplas fontes externas em uma lista padronizada e comparável.

---

## Arquitetura geral

```
┌───────────────────────────────────────────────┐
│              Browser (usuário)                │
└──────────────────┬────────────────────────────┘
                   │ HTTP (porta 5000)
┌──────────────────▼────────────────────────────┐
│            Flask — app/app.py                 │  Interface web + proxy reverso
│  Renderiza templates Jinja2                   │
│  Roteia /specs/* e /api/* → FastAPI           │
└──────────────────┬────────────────────────────┘
                   │ HTTP interno (porta 8000)
┌──────────────────▼────────────────────────────┐
│             FastAPI — main.py                 │  API RESTful assíncrona
│  /specs  /youtube  /prompts  /pipelines       │
└────┬─────────────────────────────┬────────────┘
     │                             │
┌────▼─────────────────┐   ┌───────▼─────────────────────┐
│  ESPECIFICAÇÕES      │   │  ANÁLISE DE CONTEÚDO        │
│                      │   │                             │
│  CarrosWeb           │   │  src/youtube/    (API)      │
│  iCarros             │   │  src/pipelines/  (RAW→TRUST)│
│  ↓                   │   │  src/agents/     (LLM)      │
│  normalizador        │   │  src/airflow_gen/ (DAG)     │
└──────────────────────┘   └───────┬─────────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  airflow/dags/*.py  │  Execução agendada
                        └─────────────────────┘
```

**Dois servidores rodam em paralelo:**
| Servidor | Porta | Responsabilidade |
|---|---|---|
| Flask | 5000 | Interface web, templates, proxy reverso |
| FastAPI | 8000 | API RESTful, scraping, normalização |

---

## Módulos do projeto

```
Ford_Fiap/
├── main.py                      # Ponto de entrada da FastAPI
├── scraper.py                   # Script CLI standalone (uso independente)
├── requirements.txt             # Dependências da FastAPI + scrapers
├── .env.example                 # Modelo de configuração — copie para .env
│
├── src/
│   ├── config.py                # Credenciais e diretórios (única porta de acesso)
│   │
│   ├── api/                     # Routers FastAPI
│   │   ├── specs_api.py         # /specs/*     — especificações técnicas
│   │   ├── youtube_api.py       # /youtube/*   — busca, coleta e Playground
│   │   ├── prompts_api.py       # /prompts/*   — prompts de IA versionados
│   │   ├── pipelines_api.py     # /pipelines/* — pipelines, execuções e DAGs
│   │   └── mock_api.py          # API mock para desenvolvimento/testes
│   │
│   ├── utils/                   # Scraping de especificações
│   │   ├── CarrosWeb.py         # Scraper carrosnaweb.com.br
│   │   ├── iCarros.py           # Scraper icarros.com.br
│   │   └── normalizador.py      # Normalização e consolidação de specs
│   │
│   ├── youtube/                 # Camada API — só comunicação externa
│   │   ├── client.py            # YouTube Data API v3
│   │   └── transcript.py        # Transcrições
│   │
│   ├── agents/                  # Camada de inteligência (LLM)
│   │   ├── agent.py             # A classe genérica `Agente`
│   │   ├── base.py              # Contratos: LLMProvider, AgentResult
│   │   └── providers/           # Adapters: groq, openai, anthropic, gemini, ollama
│   │
│   ├── pipelines/               # Camada de processamento
│   │   ├── engine.py            # RAW → REFINED → AGENTE → TRUST
│   │   ├── refine.py            # Tratamento determinístico
│   │   ├── executor.py          # Execução assíncrona e entrada do Airflow
│   │   ├── models.py            # Prompt, Pipeline, PipelineRun
│   │   └── store.py             # Persistência
│   │
│   └── airflow_gen/
│       └── generator.py         # Pipeline → arquivo de DAG
│
├── app/
│   ├── app.py                   # Servidor Flask + proxy reverso
│   ├── requirements.txt         # Dependências do Flask
│   ├── templates/               # Templates Jinja2 por página
│   │   ├── busca/               # Seleção de veículo (cascata marca→modelo→versão)
│   │   ├── scraping/            # Pesquisa de especificações técnicas
│   │   ├── dashboard/           # Painel principal
│   │   ├── compare/             # Comparativo entre veículos
│   │   ├── youtube/             # YouTube Playground
│   │   ├── x/                   # Análise de menções no X (Twitter)
│   │   ├── gov/                 # Consulta de dados governamentais
│   │   ├── pipeline/            # Gestão de pipelines e DAGs
│   │   └── _layout/             # Base layout e sidebar
│   └── static/                  # CSS e JS por módulo
│
├── airflow/dags/                # DAGs geradas pela aplicação
├── data/                        # prompts.json, pipelines.json, runs.json (git-ignored)
├── tests/                       # Testes de arquitetura (sem rede)
│
└── docs/                        # Documentação do projeto
    ├── README.md                # Este arquivo
    ├── api.md                   # Documentação completa da API de specs
    ├── youtube_pipeline.md      # Playground, agente LLM, pipelines e DAGs
    └── setup.md                 # Guia de instalação e execução local
```

---

## Funcionalidades implementadas

### Busca de Especificações Técnicas (`/scraping`)
Ferramenta principal do projeto. O usuário seleciona um veículo e define os atributos que deseja pesquisar. O sistema consulta múltiplas fontes em paralelo, normaliza os dados e apresenta uma tabela padronizada com exportação CSV.

→ Documentação detalhada: [api.md](./api.md)

### Seleção de Veículo (`/`)
Dropdowns em cascata (Marca → Modelo → Versão) alimentados pela API em tempo real. Ao confirmar, redireciona para a página de scraping com o veículo selecionado.

### Dashboard (`/dashboard`)
Painel central do veículo selecionado, com acesso rápido a todas as ferramentas de análise.

### Comparativo (`/compare`)
Compara especificações técnicas entre múltiplos veículos lado a lado.

### YouTube Playground (`/youtube`)
Laboratório de pipelines de análise de conteúdo. Busca vídeos (por palavra-chave
com filtros avançados ou por URL direta), coleta transcrições e comentários, e
apresenta o dado em três camadas comparáveis: **RAW** (resposta original das
APIs), **REFINED** (limpeza determinística) e **TRUST** (interpretação do LLM).

O usuário escreve os próprios prompts, testa, ajusta e reexecuta — e quando o
resultado convence, transforma a configuração numa pipeline versionada com DAG
do Airflow gerada automaticamente.

→ Documentação detalhada: [youtube_pipeline.md](./youtube_pipeline.md)

### Pipelines (`/pipeline`)
Gestão das pipelines criadas no Playground: agenda, ativação/pausa, execução
manual, histórico de execuções e visualização do código da DAG gerada (com
realce de sintaxe, cópia e download).

### X, Gov
Módulos em desenvolvimento para análise de menções em redes sociais e consulta
de dados oficiais.

---

## Documentação complementar

| Documento | Conteúdo |
|---|---|
| [api.md](./api.md) | Endpoints da API de especificações, parâmetros e exemplos |
| [youtube_pipeline.md](./youtube_pipeline.md) | Playground, camadas RAW/REFINED/TRUST, agente LLM, pipelines e DAGs |
| [setup.md](./setup.md) | Instalação do ambiente, dependências e execução local passo a passo |

---

## Tecnologias utilizadas

| Camada | Tecnologia |
|---|---|
| API backend | Python 3.11+, FastAPI, Uvicorn |
| Web frontend server | Flask, Jinja2 |
| Frontend | HTML, CSS e JavaScript sem framework |
| Scraping HTTP | curl_cffi (TLS impersonation Chrome) |
| Parsing HTML | BeautifulSoup4 |
| Proxy interno e clientes de API | httpx |
| Validação de dados | Pydantic v2 |
| Coleta do YouTube | YouTube Data API v3, youtube-transcript-api |
| LLM | Groq, OpenAI, Anthropic, Gemini ou Ollama (intercambiáveis) |
| Orquestração | Apache Airflow (DAGs geradas pela aplicação) |

---

## Validação do sistema

O critério de validação definido no projeto é a busca pela **Ford Ranger Raptor**, que deve retornar corretamente ao menos os seguintes atributos:

- Motor, Potência, Torque
- Tração, Câmbio
- Capacidade do tanque
- Comprimento, Largura, Altura

Para executar a validação via CLI:
```bash
python scraper.py --marca Ford --modelo "Ranger Raptor" --versao "3.0 V6" --debug
```
