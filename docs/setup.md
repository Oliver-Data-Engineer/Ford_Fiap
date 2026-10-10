# Guia de Instalação e Execução Local

Este guia cobre todos os passos para rodar o projeto do zero: ambiente virtual, instalação de dependências, configuração e inicialização dos dois servidores (FastAPI e Flask).

---

## Pré-requisitos

| Requisito | Versão mínima | Verificar |
|---|---|---|
| Python | 3.11 | `python --version` |
| pip | qualquer | `pip --version` |
| Git | qualquer | `git --version` |

> **Windows:** use sempre o **PowerShell** ou **Prompt de Comando** — não o Git Bash para passos de `venv` e `pip`.

---

## 1. Clonar o repositório

```bash
git clone <url-do-repositorio>
cd Ford_Fiap
```

---

## 2. Criar e ativar o ambiente virtual

O projeto usa **um único `venv`** na raiz para os dois servidores.

**Criar o venv:**
```bash
python -m venv venv
```

**Ativar — Windows (PowerShell):**
```powershell
.\venv\Scripts\Activate.ps1
```

**Ativar — Windows (CMD):**
```cmd
venv\Scripts\activate.bat
```

**Ativar — macOS / Linux:**
```bash
source venv/bin/activate
```

Após ativar, o prompt exibirá `(venv)` no início.

---

## 3. Instalar as dependências

O projeto tem **dois arquivos de requisitos**:

| Arquivo | Para quê |
|---|---|
| `requirements.txt` | FastAPI, scrapers, curl_cffi, uvicorn |
| `app/requirements.txt` | Flask, httpx |

**Instalar tudo de uma vez (recomendado):**
```bash
pip install -r requirements.txt
pip install -r app/requirements.txt
```

**Dependências principais instaladas:**

| Pacote | Versão | Função |
|---|---|---|
| `fastapi` | 0.141.1 | Framework da API |
| `uvicorn` | 0.52.4 | Servidor ASGI para a FastAPI |
| `flask` | 3.1.3 | Servidor web do frontend |
| `httpx` | 0.28.1 | HTTP client (proxy Flask → FastAPI) |
| `curl_cffi` | 0.16.3 | HTTP com TLS impersonation (scrapers) |
| `beautifulsoup4` | 4.15.0 | Parse de HTML |
| `pydantic` | 2.13.5 | Validação de dados da API |
| `requests` | 2.34.2 | HTTP client de fallback |
| `youtube-transcript-api` | 1.2.2+ | Transcrições de vídeos (YouTube Playground) |

> **Nota sobre `curl_cffi`:** este pacote instala binários pré-compilados. Se a instalação falhar, verifique se o pip está atualizado: `pip install --upgrade pip` e tente novamente.

---

## 4. Configurar as credenciais (`.env`)

O módulo de análise de conteúdo (YouTube Playground e pipelines) precisa de
chaves de API. Elas são lidas **exclusivamente** de variáveis de ambiente — nunca
ficam no código, no HTML ou nas DAGs geradas.

**Copie o modelo e preencha:**

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux
cp .env.example .env
```

Depois edite o `.env`:

```ini
# Obrigatória para buscar vídeos, metadados e comentários
YOUTUBE_API_KEY=sua-chave-aqui

# Provider de LLM padrão do agente
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-20b

# Preencha só a chave do provider que você vai usar
GROQ_API_KEY=sua-chave-aqui
```

**Onde obter as chaves:**

| Chave | Onde |
|---|---|
| `YOUTUBE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → APIs e serviços → habilite "YouTube Data API v3" → Credenciais → Chave de API |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) |
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

> O `.env` está no `.gitignore`. **Nunca comite credenciais.**
>
> A busca de especificações técnicas (`/scraping`) **não** precisa de chave —
> funciona sem configurar nada.

---

## 5. Verificar a instalação

```bash
python -c "import fastapi, flask, curl_cffi, bs4, httpx; print('OK — dependências disponíveis')"
```

**Verificar a arquitetura completa (roda sem rede e sem chave de API):**

```bash
python tests/test_pipeline.py    # coleta, RAW/REFINED/TRUST, versionamento, DAG
python tests/test_agentes.py     # adapters de LLM
node   tests/test_realce.js      # realce de sintaxe (opcional, precisa de Node)
```

Todos devem terminar com `Todas as verificações passaram.`

**Verificar o que o backend reconheceu do seu `.env`** (com a FastAPI rodando):

```bash
curl http://localhost:8000/youtube/config
```

A resposta diz quais credenciais estão presentes — mas nunca mostra o valor delas.

---

## 6. Iniciar a FastAPI (porta 8000)

A FastAPI é o motor de dados — deve ser iniciada **antes** do Flask.

**Terminal 1:**
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Saída esperada:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

**Verificar que está rodando:**
```bash
curl http://localhost:8000/docs
# ou abra http://localhost:8000/docs no browser
```

A flag `--reload` reinicia automaticamente a API quando você salva um arquivo Python. Remova em produção.

---

## 7. Iniciar o Flask (porta 5000)

O Flask serve o frontend e atua como proxy para a FastAPI.

**Terminal 2** (com o venv ativado):
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

**Alternativa (equivalente):**
```bash
python app/app.py
```

Saída esperada:
```
 * Serving Flask app 'app/app.py'
 * Debug mode: on
 * Running on http://0.0.0.0:5000
 * Restarting with stat
 * Debugger is active!
```

**Verificar que está rodando:**

Abra `http://localhost:5000` no browser — você deve ver a tela de seleção de veículo.

---

## 8. Fluxo de uso após inicialização

**Especificações técnicas:**
```
1. Abra http://localhost:5000
2. Selecione a Marca (ex: Ford)
3. Selecione o Modelo (ex: Ranger)
4. Selecione a Versão (ex: Ranger Raptor 3.0 V6 2026)
5. Clique em "Buscar" → redireciona para /scraping
6. Opcionalmente, preencha os atributos desejados
7. Clique em "Buscar especificações"
8. Aguarde o resultado (até 30 s)
9. Exporte em CSV se necessário
```

**YouTube Playground** (requer `YOUTUBE_API_KEY` e a chave de um provider de LLM):
```
1. Abra http://localhost:5000/youtube
2. Confira os selos no topo — YouTube API e LLM devem estar verdes
3. Pesquise ("review Ford Territory 2026") ou cole a URL de um vídeo
4. Marque os vídeos que entram na análise
5. Escreva um prompt e salve — ele fica reutilizável e versionado
6. Marque o prompt e clique em "Executar pipeline"
7. Acompanhe as etapas e inspecione RAW / REFINED / TRUST
8. Ajuste o prompt e reexecute quantas vezes quiser
9. Satisfeito: "Gerar pipeline" → salva, agenda e gera a DAG
10. Gerencie tudo em http://localhost:5000/pipeline
```

→ Detalhes da arquitetura: [youtube_pipeline.md](./youtube_pipeline.md)

---

## 9. Testar a API diretamente

Com a FastAPI rodando, você pode testar os endpoints via Swagger UI ou `curl`:

**Listar fabricantes:**
```bash
curl http://localhost:8000/specs/fabricantes
```

**Listar modelos da Ford:**
```bash
curl "http://localhost:8000/specs/modelos?fabricante=Ford"
```

**Listar versões da Ranger:**
```bash
curl "http://localhost:8000/specs/versoes?fabricante=Ford&modelo=Ranger"
```

**Buscar specs da Ranger Raptor:**
```bash
curl -X POST http://localhost:8000/specs/executar \
  -H "Content-Type: application/json" \
  -d '{"fabricante":"Ford","modelo":"Ranger","versao":"Raptor"}'
```

**Via Swagger UI:** acesse `http://localhost:8000/docs` e use a interface interativa.

---

## 10. Testar o scraper standalone (CLI)

O `scraper.py` pode ser usado independentemente dos servidores:

```bash
# Busca básica
python scraper.py --marca Ford --modelo "Ranger Raptor" --versao "3.0 V6"

# Com exportação
python scraper.py --marca Ford --modelo "Ranger Raptor" --json saida.json

# Com debug detalhado
python scraper.py --marca Ford --modelo "Ranger Raptor" --debug
```

---

## Resumo rápido

```bash
# 1. Clonar e entrar na pasta
git clone <url> && cd Ford_Fiap

# 2. Ambiente virtual
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell

# 3. Dependências
pip install -r requirements.txt
pip install -r app/requirements.txt

# 4. Credenciais (só para o YouTube Playground)
Copy-Item .env.example .env   # depois edite e preencha as chaves

# 5. Terminal 1 — FastAPI
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 6. Terminal 2 — Flask
flask --app app/app.py run --port 5000 --debug

# 7. Abrir no browser
# http://localhost:5000           → especificações técnicas
# http://localhost:5000/youtube   → YouTube Playground
# http://localhost:5000/pipeline  → pipelines e DAGs
```

---

## Solução de problemas comuns

**`ModuleNotFoundError: No module named 'curl_cffi'`**
```bash
pip install --upgrade pip
pip install curl_cffi
```

**`YOUTUBE_API_KEY não configurada no ambiente`**

O `.env` não foi criado, está fora da raiz do projeto ou a chave está vazia.
Confira com `curl http://localhost:8000/youtube/config` e **reinicie a FastAPI**
depois de editar o `.env` — as variáveis são lidas na inicialização.

**O selo do LLM aparece vermelho no Playground**

Falta a chave do provider selecionado. Cada provider tem a sua própria variável
(`GROQ_API_KEY`, `OPENAI_API_KEY`, …) — troque de provider no painel
"Configuração" ou preencha a chave correspondente no `.env`.

**"Transcrição indisponível" em alguns vídeos**

Normal: nem todo vídeo tem legenda no idioma pedido. A execução continua e
termina como `partial` — os comentários e metadados daquele vídeo são
preservados. Amplie os idiomas aceitos se precisar.

**`quotaExceeded` na YouTube API**

A cota diária padrão é de 10.000 unidades. Uma busca custa 100 unidades, e cada
página de comentários custa 1. Reduza o número máximo de vídeos e de comentários
por vídeo nos filtros avançados.

**As DAGs geradas não aparecem no Airflow**

O Airflow não é dependência do projeto — a aplicação só escreve o arquivo em
`airflow/dags/`. Aponte o `dags_folder` da sua instalação para essa pasta e
defina `FORD_FIAP_HOME` no worker, para que `import src...` funcione.

**`Address already in use` na porta 8000 ou 5000**

Windows PowerShell:
```powershell
# Verificar o que está usando a porta
netstat -ano | findstr :8000

# Matar o processo pelo PID encontrado
taskkill /PID <PID> /F
```

**`ImportError` no `src/utils/`**

Certifique-se de executar os comandos a partir da **raiz do projeto** (`Ford_Fiap/`), não de dentro de `src/` ou `app/`.

**Scrapers retornam lista vazia**

Os sites externos (carrosnaweb, icarros) podem estar temporariamente indisponíveis ou aplicando bloqueios. Tente novamente após alguns minutos. Use `--debug` no `scraper.py` para ver os detalhes da requisição.
