# Requirements Document

## Introduction

Esta feature adiciona ao aplicativo de inteligência competitiva automotiva uma ferramenta de busca de especificações técnicas de veículos da concorrência. A partir de uma entrada simples (marca, modelo, versão e lista de atributos desejados), o sistema realiza scraping em múltiplas fontes, normaliza os dados coletados e devolve uma lista padronizada de especificações técnicas — sempre no mesmo formato, independentemente do veículo pesquisado. Campos sem informação disponível nas fontes são explicitamente marcados como "não disponível".

---

## Glossary

- **Sistema**: o aplicativo Flask de inteligência competitiva automotiva descrito neste documento.
- **Buscador**: o componente de busca de especificações técnicas responsável por orquestrar as fontes de dados e devolver o resultado normalizado.
- **Scraper**: módulo Python que acessa uma fonte externa (ex.: carrosnaweb.com.br, icarros.com.br) e extrai dados técnicos estruturados.
- **Normalizador**: componente que consolida dados provenientes de múltiplas fontes num formato padronizado de especificações.
- **Ficha técnica**: página de uma fonte externa que contém os atributos técnicos de uma versão específica de veículo.
- **Especificação**: par atributo/valor resultante da extração, ex.: `{"atributo": "Potência", "valor": "272 cv", "fonte": "carrosnaweb"}`.
- **Atributo desejado**: nome de característica técnica ou equipamento que o usuário quer pesquisar (ex.: "potência", "airbags", "suspensão").
- **Versão**: variante específica de um modelo de veículo, identificada por código único nas fontes externas.
- **Fonte**: site externo de onde as especificações são extraídas (carrosnaweb.com.br ou icarros.com.br).

---

## Requirements

### Requisito 1 — Seleção de veículo

**User Story:** Como analista de inteligência competitiva, eu quero selecionar marca, modelo, versão e ano do veículo a pesquisar, para que a busca de especificações seja direcionada ao veículo correto.

#### Critérios de Aceitação

1. WHEN o usuário acessa a página de busca de especificações, THE Sistema SHALL exibir o campo de seleção de marca habilitado e os campos de modelo, versão e ano desabilitados.
2. WHEN o usuário seleciona uma marca, THE Sistema SHALL requisitar a lista de modelos disponíveis via endpoint de API e habilitar o campo de modelo ao receber a resposta, no prazo máximo de 10 segundos.
3. WHEN o usuário seleciona um modelo, THE Sistema SHALL requisitar a lista de versões disponíveis via endpoint de API e habilitar o campo de versão ao receber a resposta, no prazo máximo de 10 segundos.
4. WHEN o usuário seleciona uma versão, THE Sistema SHALL requisitar os anos disponíveis via endpoint de API e habilitar o campo de ano ao receber a resposta, no prazo máximo de 10 segundos.
5. WHEN o usuário altera a seleção de um campo, THE Sistema SHALL limpar e desabilitar todos os campos posicionados abaixo dele na sequência marca → modelo → versão → ano.
6. IF o endpoint de listagem retornar erro HTTP 5xx ou não responder em até 10 segundos, THEN THE Sistema SHALL exibir mensagem de erro indicando falha no carregamento e orientando nova tentativa no campo que falhou ao carregar.
7. IF o endpoint de listagem retornar erro HTTP 5xx ou não responder em até 10 segundos, THEN THE Sistema SHALL manter o campo pai com a seleção atual e deixar o campo dependente desabilitado.
8. IF o endpoint de listagem retornar lista vazia, THEN THE Sistema SHALL exibir mensagem indicando ausência de resultados no campo dependente que estava sendo carregado e deixá-lo desabilitado.

---

### Requisito 2 — Listagem de fabricantes

**User Story:** Como analista de inteligência competitiva, eu quero obter a lista de fabricantes disponíveis nas fontes externas, para que eu possa selecionar a marca correta do veículo a pesquisar.

#### Critérios de Aceitação

1. WHEN o endpoint `GET /specs/fabricantes` é chamado, THE Buscador SHALL retornar HTTP 200 com a lista de fabricantes, onde cada entrada contém os campos `id_busca` (string) e `nome_exibicao` (string).
2. THE Buscador SHALL consultar pelo menos as fontes carrosnaweb.com.br e icarros.com.br ao construir a lista de fabricantes, aguardando no máximo 10 segundos por resposta de cada fonte antes de considerá-la indisponível.
3. IF um Scraper retornar erro de conexão ao buscar fabricantes, THEN THE Buscador SHALL registrar o erro no log com a identificação da fonte e retornar a lista parcial dos scrapers que responderam com sucesso.
4. IF todos os Scrapers retornarem erro de conexão, THEN THE Buscador SHALL retornar HTTP 503 com corpo `{"erro": "Nenhuma fonte disponível no momento."}`.
5. THE Buscador SHALL desduplicar fabricantes com o mesmo `nome_exibicao` (comparação insensível a maiúsculas/minúsculas) antes de retornar a lista, mantendo apenas uma entrada por nome.
6. IF a lista de fabricantes retornada estiver vazia após deduplicação, THEN THE Buscador SHALL retornar HTTP 200 com corpo `{"fabricantes": []}`.

---

### Requisito 3 — Listagem de modelos por fabricante

**User Story:** Como analista de inteligência competitiva, eu quero obter a lista de modelos de um fabricante, para que eu possa selecionar o modelo correto do veículo a pesquisar.

#### Critérios de Aceitação

1. WHEN o endpoint `GET /specs/modelos?fabricante={fabricante}` é chamado, THE Buscador SHALL retornar HTTP 200 com a lista de modelos, onde cada entrada contém os campos `fabricante` (string), `id_busca` (string) e `nome_exibicao` (string), com no máximo 500 itens na lista.
2. IF o parâmetro `fabricante` não for informado na requisição, THEN THE Buscador SHALL retornar HTTP 400 com corpo contendo campo `erro` indicando o parâmetro obrigatório ausente.
3. IF o parâmetro `fabricante` for informado mas não corresponder a nenhum fabricante conhecido, THEN THE Buscador SHALL retornar HTTP 200 com lista vazia.
4. IF um Scraper retornar erro de conexão ao buscar modelos, THEN THE Buscador SHALL registrar o erro no log e retornar HTTP 200 com a lista parcial dos scrapers que responderam com sucesso, incluindo campo `fontes_com_erro` listando os identificadores das fontes que falharam.
5. IF todos os Scrapers retornarem erro de conexão, THEN THE Buscador SHALL retornar HTTP 503 com corpo `{"erro": "Nenhuma fonte disponível no momento."}`.
6. WHEN o endpoint `GET /specs/modelos` é chamado, THE Buscador SHALL retornar a resposta em até 10 segundos a partir do recebimento da requisição.

---

### Requisito 4 — Listagem de versões por modelo

**User Story:** Como analista de inteligência competitiva, eu quero obter a lista de versões de um modelo, para que eu possa selecionar a versão exata do veículo a pesquisar.

#### Critérios de Aceitação

1. WHEN o endpoint `GET /specs/versoes?fabricante={fabricante}&modelo={modelo}` é chamado, THE Buscador SHALL retornar HTTP 200 com corpo `{"versoes": [...]}`, onde cada entrada contém os campos `id_veiculo`, `fabricante`, `versao`, `ano` e `url_ficha`, com no máximo 200 itens na lista.
2. IF o parâmetro `fabricante` ou `modelo` não for informado na requisição ou contiver apenas espaços em branco, THEN THE Buscador SHALL retornar HTTP 400 com corpo contendo campo `erro` indicando os parâmetros obrigatórios ausentes.
3. IF um Scraper retornar erro de conexão ao buscar versões, THEN THE Buscador SHALL registrar o erro no log e retornar HTTP 200 com a lista parcial dos scrapers que responderam com sucesso, incluindo campo `fontes_com_erro` listando os identificadores das fontes que falharam.
4. IF todos os Scrapers retornarem erro de conexão, THEN THE Buscador SHALL retornar HTTP 503 com corpo `{"erro": "Nenhuma fonte disponível no momento."}`.
5. IF os parâmetros `fabricante` e `modelo` forem válidos mas nenhuma versão for encontrada nas fontes disponíveis, THEN THE Buscador SHALL retornar HTTP 200 com corpo `{"versoes": []}`.

---

### Requisito 5 — Definição de atributos desejados

**User Story:** Como analista de inteligência competitiva, eu quero informar uma lista livre de atributos ou equipamentos que desejo pesquisar, para que o resultado seja filtrado apenas pelos campos que me interessam.

#### Critérios de Aceitação

1. THE Sistema SHALL exibir na interface um campo de entrada de texto livre para que o usuário informe os atributos desejados, um por linha ou separados por vírgula, aceitando no máximo 2000 caracteres no total.
2. IF o campo de atributos desejados estiver vazio ou contiver apenas espaços em branco, THEN THE Buscador SHALL retornar todas as especificações técnicas disponíveis para o veículo, sem filtragem.
3. WHEN o usuário informa atributos desejados, THE Buscador SHALL dividir a entrada pelos delimitadores vírgula e quebra de linha, descartar os segmentos resultantes que sejam vazios ou compostos apenas por espaços em branco, e retornar somente as especificações cujos nomes de atributo contêm pelo menos um dos termos restantes, comparados sem diferenciação de maiúsculas e minúsculas.
4. IF nenhum atributo do resultado corresponder aos termos informados, THEN THE Buscador SHALL retornar uma lista de especificações vazia com indicação de sucesso, sem mensagem de erro.

---

### Requisito 6 — Extração de especificações técnicas da ficha do veículo

**User Story:** Como analista de inteligência competitiva, eu quero que o sistema extraia as especificações técnicas da ficha de um veículo a partir de uma URL de ficha, para que os dados técnicos detalhados fiquem disponíveis para normalização.

#### Critérios de Aceitação

1. WHEN o Scraper recebe uma `url_ficha` válida, THE Scraper SHALL extrair todos os pares atributo/valor presentes na seção de ficha técnica da página e retorná-los como lista de objetos `{"atributo": str, "valor": str}`.
2. IF a `url_ficha` retornar HTTP 404, THEN THE Scraper SHALL retornar lista vazia e registrar aviso no log.
3. IF a `url_ficha` retornar status diferente de 200 após 3 tentativas com backoff exponencial, ou retornar resposta com indicação de verificação humana (captcha), THEN THE Scraper SHALL retornar lista vazia e registrar o erro no log.
4. THE Scraper SHALL executar as requisições à `url_ficha` respeitando o timeout de 15 segundos por tentativa.
5. IF a `url_ficha` retornar HTTP 200 mas a página não contiver seção de ficha técnica identificável, THEN THE Scraper SHALL retornar lista vazia e registrar aviso no log indicando ausência de dados técnicos.

---

### Requisito 7 — Normalização das especificações

**User Story:** Como analista de inteligência competitiva, eu quero que as especificações técnicas sejam apresentadas sempre no mesmo formato, independentemente da fonte ou do veículo, para que eu possa comparar dados entre modelos diferentes.

#### Critérios de Aceitação

1. THE Normalizador SHALL produzir cada especificação no formato `{"atributo": str, "valor": str, "fonte": str}`, onde `atributo` contém no máximo 200 caracteres, `valor` contém no máximo 500 caracteres, e `fonte` identifica o site de origem com no máximo 100 caracteres.
2. WHEN o mesmo atributo estiver presente em mais de uma fonte, THE Normalizador SHALL incluir uma entrada separada por fonte, preservando todas as ocorrências no resultado sem deduplicação.
3. IF um atributo esperado não for encontrado em nenhuma fonte, THEN THE Normalizador SHALL incluir a entrada com `"valor": "não disponível"` e `"fonte": "—"`, sem lançar exceção.
4. THE Normalizador SHALL ordenar as especificações resultantes em ordem alfabética crescente (A→Z) pelo campo `atributo`, utilizando comparação insensível a maiúsculas e minúsculas.
5. THE Normalizador SHALL aplicar normalização Unicode NFC aos valores de `atributo` e `valor` antes de inserir no resultado, removendo caracteres de controle (pontos de código U+0000–U+001F e U+007F–U+009F).
6. IF a entrada bruta de um atributo ou valor exceder o limite de caracteres após normalização Unicode, THEN THE Normalizador SHALL truncar o campo ao limite máximo e registrar um aviso indicando o campo truncado e o tamanho original.
7. IF o conjunto de atributos esperados não for definido para um determinado veículo, THEN THE Normalizador SHALL processar apenas os atributos presentes nas fontes disponíveis, sem inferir atributos ausentes.

---

### Requisito 8 — Execução da busca de especificações

**User Story:** Como analista de inteligência competitiva, eu quero executar a busca de especificações técnicas informando marca, modelo, versão e atributos desejados, para que eu receba rapidamente uma lista padronizada com os dados técnicos do veículo.

#### Critérios de Aceitação

1. WHEN o endpoint `POST /scraping/executar` recebe um corpo JSON válido contendo `fabricante`, `modelo` e `versao`, THE Buscador SHALL retornar HTTP 200 com corpo `{"especificacoes": [...]}`, onde cada elemento contém exatamente os campos `atributo`, `valor` e `fonte`; se `atributos` for fornecido, a lista SHALL conter apenas os atributos solicitados; se `atributos` for omitido ou vazio, a lista SHALL conter todos os atributos disponíveis para o veículo.
2. IF o corpo da requisição não contiver ao menos um dos campos `fabricante`, `modelo` ou `versao`, THEN THE Buscador SHALL retornar HTTP 400 com corpo contendo campo `erro` indicando quais campos obrigatórios estão ausentes.
3. WHEN o usuário clica no botão de busca na interface, THE Sistema SHALL exibir um indicador visual de carregamento (spinner ou barra de progresso visível na tela) e, IF a resposta do endpoint `/scraping/executar` não for recebida em até 30 segundos, THEN THE Sistema SHALL encerrar o indicador de carregamento e exibir mensagem de erro indicando tempo limite excedido, sem recarregar a página.
4. WHEN a resposta do endpoint `/scraping/executar` é recebida com HTTP 200, THE Sistema SHALL exibir a lista de especificações em tabela com colunas "Atributo", "Valor" e "Fonte"; IF `especificacoes` for uma lista vazia, THEN THE Sistema SHALL exibir mensagem informando que nenhuma especificação foi encontrada para o veículo informado.
5. IF a requisição ao endpoint `/scraping/executar` retornar HTTP 4xx ou 5xx, THEN THE Sistema SHALL exibir mensagem de erro indicando a causa da falha ao usuário, sem recarregar a página.
6. WHEN o endpoint `POST /scraping/executar` recebe uma requisição com campo `atributos` contendo uma lista não vazia de nomes de atributos, THE Buscador SHALL retornar em `especificacoes` somente os atributos cujos nomes coincidam com os valores informados em `atributos`, omitindo da resposta qualquer atributo não solicitado.

---

### Requisito 9 — Exibição do resultado na interface

**User Story:** Como analista de inteligência competitiva, eu quero visualizar as especificações encontradas em formato claro e organizado, para que eu possa analisar os dados sem ambiguidade.

#### Critérios de Aceitação

1. THE Sistema SHALL exibir cada especificação em uma linha da tabela de resultados contendo exatamente três colunas: "Atributo", "Valor" e "Fonte", onde "Atributo" contém o nome do campo (máximo 100 caracteres), "Valor" contém o dado retornado (máximo 255 caracteres) e "Fonte" contém a origem do dado (máximo 100 caracteres).
2. WHEN o valor de uma especificação for "não disponível", THE Sistema SHALL exibir a célula da coluna "Valor" com estilo visual distinto das células com valor preenchido, de forma que a diferença seja perceptível sem depender exclusivamente de cor.
3. THE Sistema SHALL exibir o total de especificações encontradas como um número inteiro não negativo imediatamente acima da tabela de resultados, no formato "X especificações encontradas".
4. WHEN a lista de especificações estiver vazia, THE Sistema SHALL exibir a mensagem "Nenhuma especificação encontrada para os critérios informados." no lugar da tabela, ocultando o contador e a tabela.
5. WHEN o usuário acionar a exportação, THE Sistema SHALL gerar e disponibilizar para download um arquivo CSV contendo todas as linhas exibidas na tabela, com cabeçalho "Atributo,Valor,Fonte", sem realizar requisição ao servidor, em até 3 segundos após o acionamento.
6. IF o número de especificações exibidas na tabela exceder 500 linhas, THEN THE Sistema SHALL exibir uma mensagem de aviso informando o limite atingido e truncar a exibição nas primeiras 500 linhas.
