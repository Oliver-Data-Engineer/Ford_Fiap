# Ford FIAP — Plataforma de Inteligência Competitiva Automotiva

Plataforma web para coleta, normalização e análise de dados técnicos e de mercado sobre veículos da concorrência. O projeto foi desenvolvido como parte de um desafio acadêmico FIAP em parceria com a Ford.

---

## Objetivo

Permitir que analistas de inteligência competitiva pesquisem especificações técnicas de qualquer veículo do mercado a partir de uma entrada simples (marca, modelo, versão), consolidando dados de múltiplas fontes externas em uma lista padronizada e comparável.

---

## Arquitetura geral

```
┌─────────────────────────────────────┐
│          Browser (usuário)          │
└──────────────┬──────────────────────┘
               │ HTTP (porta 5000)
┌──────────────▼──────────────────────┐
│         Flask — app/app.py          │  Interface web + proxy reverso
│  Renderiza templates Jinja2         │
│  Roteia /specs/* → FastAPI          │
└──────────────┬──────────────────────┘
               │ HTTP interno (porta 8000)
┌──────────────▼──────────────────────┐
│      FastAPI — main.py              │  API RESTful assíncrona
│  src/api/specs_api.py               │
└──────┬───────────────────┬──────────┘
       │                   │
┌──────▼──────┐   ┌────────▼────────┐
│ CarrosWeb   │   │    iCarros      │  Scrapers (src/utils/)
│ .com.br     │   │  .com.br        │
└─────────────┘   └─────────────────┘
       │                   │
       └─────────┬─────────┘
       ┌─────────▼─────────┐
       │   normalizador    │  src/utils/normalizador.py
       └───────────────────┘
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
│
├── src/
│   ├── api/
│   │   ├── specs_api.py         # Router FastAPI — todos os endpoints /specs/*
│   │   └── mock_api.py          # API mock para desenvolvimento/testes
│   └── utils/
│       ├── CarrosWeb.py         # Scraper carrosnaweb.com.br
│       ├── iCarros.py           # Scraper icarros.com.br
│       └── normalizador.py      # Normalização e consolidação de specs
│
├── app/
│   ├── app.py                   # Servidor Flask + proxy reverso
│   ├── requirements.txt         # Dependências do Flask
│   ├── templates/               # Templates Jinja2 por página
│   │   ├── busca/               # Seleção de veículo (cascata marca→modelo→versão)
│   │   ├── scraping/            # Pesquisa de especificações técnicas
│   │   ├── dashboard/           # Painel principal
│   │   ├── compare/             # Comparativo entre veículos
│   │   ├── youtube/             # Análise de vídeos
│   │   ├── x/                   # Análise de menções no X (Twitter)
│   │   ├── gov/                 # Consulta de dados governamentais
│   │   ├── pipeline/            # Agendamento de pipelines
│   │   └── _layout/             # Base layout e sidebar
│   └── static/                  # CSS e JS por módulo
│
└── docs/                        # Documentação do projeto
    ├── README.md                # Este arquivo
    ├── api.md                   # Documentação completa da API
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

### YouTube, X, Gov, Pipeline
Módulos em desenvolvimento para análise de sentimento em vídeos, menções em redes sociais, consulta de dados oficiais e agendamento de coletas.

---

## Documentação complementar

| Documento | Conteúdo |
|---|---|
| [api.md](./api.md) | Todos os endpoints da API, parâmetros, respostas e exemplos |
| [setup.md](./setup.md) | Instalação do ambiente, dependências e execução local passo a passo |

---

## Tecnologias utilizadas

| Camada | Tecnologia |
|---|---|
| API backend | Python 3.11+, FastAPI, Uvicorn |
| Web frontend server | Flask, Jinja2 |
| Scraping HTTP | curl_cffi (TLS impersonation Chrome) |
| Parsing HTML | BeautifulSoup4 |
| Proxy interno | httpx |
| Validação de dados | Pydantic v2 |

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
