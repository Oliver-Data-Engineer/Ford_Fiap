/* ============================================================
   CHAT WIDGET — bolha flutuante global (toda página), responde
   perguntas com base SOMENTE no que a página atual registrou como
   "dado carregado/filtrado agora" via `window.registrarContextoChat`.

   Não sabe nada de regra de negócio: monta a própria UI, manda
   {pergunta, pagina, contexto, historico} pra POST /api/chat/perguntar
   e mostra a resposta. Toda a montagem do contexto é responsabilidade
   de cada página (dashboard/script.js, analytics/script.js, etc.).

   Histórico da conversa só em memória — reseta ao recarregar a página
   (sem persistência, sem sessão de servidor: "chat simples").
   ============================================================ */
(function () {
  'use strict';

  // Sempre presente (shell compartilhado, ver _layout/dashboard_base.html) —
  // guarda igual aos outros scripts globais (sidebar.js/vehicle_sync.js).
  if (!document.querySelector('[data-shell]')) return;

  var rotuloPaginaAtual = '';
  var obterContextoAtual = null;
  var mensagens = []; // {papel: 'usuario'|'assistente', texto}
  var enviando = false;

  // Sugestões fixas de análise, disponíveis em toda página independente do
  // que ela registrar — atalhos pra pedir uma leitura geral dos dados sem
  // precisar formular a pergunta.
  var SUGESTOES_ANALISE = [
    'Faça uma análise resumida dos dados carregados nesta página, destacando os pontos mais relevantes.',
    'Quais os principais pontos de atenção ou alertas nos dados desta página?',
    'Existe algum dado que se destaca ou foge do padrão aqui?'
  ];
  var sugestoesAtuais = SUGESTOES_ANALISE.slice();

  // ── Registro de contexto (chamado pelas páginas) ──────────────────────────

  window.registrarContextoChat = function (rotuloPagina, obterContexto, sugestoes) {
    rotuloPaginaAtual = rotuloPagina || '';
    obterContextoAtual = typeof obterContexto === 'function' ? obterContexto : null;
    sugestoesAtuais = SUGESTOES_ANALISE.concat(Array.isArray(sugestoes) ? sugestoes : []);
    var elPagina = document.querySelector('[data-chatw-pagina]');
    if (elPagina) elPagina.textContent = rotuloPaginaAtual;
    renderSugestoes();
  };

  // ── Montagem da UI ─────────────────────────────────────────────────────

  var bolha = document.createElement('button');
  bolha.type = 'button';
  bolha.className = 'chatw-bolha';
  bolha.setAttribute('aria-label', 'Abrir assistente');
  bolha.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">' +
    '<path d="M4 5h16v11H8l-4 4V5z" stroke-linejoin="round"/></svg>';

  var painel = document.createElement('div');
  painel.className = 'chatw-painel';
  painel.hidden = true;
  painel.innerHTML =
    '<div class="chatw-cabecalho">' +
      '<div><div class="chatw-cabecalho__titulo">Assistente</div>' +
        '<div class="chatw-cabecalho__pagina" data-chatw-pagina></div></div>' +
      '<button type="button" class="chatw-fechar" data-chatw-fechar aria-label="Fechar">&times;</button>' +
    '</div>' +
    '<div class="chatw-mensagens" data-chatw-mensagens>' +
      '<div class="chatw-msg chatw-msg--sistema">Pergunte algo sobre os dados carregados/filtrados nesta página.</div>' +
      '<div class="chatw-sugestoes" data-chatw-sugestoes></div>' +
    '</div>' +
    '<form class="chatw-rodape" data-chatw-form>' +
      '<input class="chatw-campo" type="text" placeholder="Pergunte sobre esta página…" data-chatw-campo autocomplete="off">' +
      '<button type="submit" class="chatw-enviar" data-chatw-enviar>Enviar</button>' +
    '</form>';

  document.body.appendChild(painel);
  document.body.appendChild(bolha);

  var listaMensagens = painel.querySelector('[data-chatw-mensagens]');
  var sugestoesEl = painel.querySelector('[data-chatw-sugestoes]');
  var campo = painel.querySelector('[data-chatw-campo]');
  var botaoEnviar = painel.querySelector('[data-chatw-enviar]');
  var elPaginaCab = painel.querySelector('[data-chatw-pagina]');
  if (elPaginaCab) elPaginaCab.textContent = rotuloPaginaAtual;

  // ── Sugestões de pergunta (chips clicáveis) ───────────────────────────
  //
  // Só aparecem antes da primeira mensagem — depois que a conversa começa,
  // o campo de texto livre já basta, sem poluir a tela com chips.

  function renderSugestoes() {
    if (!sugestoesEl) return;
    if (mensagens.length > 0 || !sugestoesAtuais.length) {
      sugestoesEl.innerHTML = '';
      return;
    }
    sugestoesEl.innerHTML = sugestoesAtuais.map(function (texto, i) {
      return '<button type="button" class="chatw-chip" data-chatw-sugestao="' + i + '">' + texto + '</button>';
    }).join('');
    sugestoesEl.querySelectorAll('[data-chatw-sugestao]').forEach(function (botao) {
      botao.addEventListener('click', function () {
        enviarPergunta(sugestoesAtuais[Number(botao.getAttribute('data-chatw-sugestao'))]);
      });
    });
  }

  renderSugestoes();

  function abrir() {
    painel.hidden = false;
    campo.focus();
  }

  function fechar() {
    painel.hidden = true;
  }

  bolha.addEventListener('click', function () {
    if (painel.hidden) abrir(); else fechar();
  });
  painel.querySelector('[data-chatw-fechar]').addEventListener('click', fechar);

  function renderMensagem(papel, texto) {
    var classe = 'chatw-msg--' + papel;
    var div = document.createElement('div');
    div.className = 'chatw-msg ' + classe;
    div.textContent = texto;
    listaMensagens.appendChild(div);
    listaMensagens.scrollTop = listaMensagens.scrollHeight;
  }

  function definirEnviando(valor) {
    enviando = valor;
    campo.disabled = valor;
    botaoEnviar.disabled = valor;
  }

  function enviarPergunta(pergunta) {
    pergunta = (pergunta || '').trim();
    if (!pergunta || enviando) return;

    campo.value = '';
    renderMensagem('usuario', pergunta);
    mensagens.push({ papel: 'usuario', texto: pergunta });
    renderSugestoes(); // esconde os chips — a conversa já começou
    definirEnviando(true);

    var contexto = obterContextoAtual ? obterContextoAtual() : null;

    fetch('/api/chat/perguntar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pergunta: pergunta,
        pagina: rotuloPaginaAtual || null,
        contexto: contexto,
        historico: mensagens.slice(0, -1) // sem a pergunta que acabou de entrar (já vai como `pergunta`)
      })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (json) {
        if (!r.ok) {
          var detalhe = json.detail || json;
          var erro = new Error(detalhe.erro || detalhe.message || json.erro || ('Erro ' + r.status));
          throw erro;
        }
        return json;
      });
    }).then(function (resposta) {
      renderMensagem('assistente', resposta.resposta || '(sem resposta)');
      mensagens.push({ papel: 'assistente', texto: resposta.resposta || '' });
    }).catch(function (erro) {
      renderMensagem('erro', erro.message);
    }).finally(function () {
      definirEnviando(false);
      campo.focus();
    });
  }

  painel.querySelector('[data-chatw-form]').addEventListener('submit', function (evento) {
    evento.preventDefault();
    enviarPergunta(campo.value);
  });
})();
