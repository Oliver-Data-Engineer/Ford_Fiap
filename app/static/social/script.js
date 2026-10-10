/* ============================================================
   REDES SOCIAIS
   Duas partes: (1) Playground — gera posts sintéticos e roda a pipeline
   (RAW → REFINED → Enriquecimento de IA → TRUST) via /api/social/*; (2)
   Análise consolidada — lê o warehouse (`social_ford`) com o mesmo padrão
   de app/static/analytics/script.js. Sem regra de negócio aqui — só chama a
   API e desenha o que ela devolve.
   ============================================================ */
(function () {
  'use strict';

  var raiz = document.querySelector('[data-form-social]');
  if (!raiz) return;

  var INTERVALO_POLLING = 1500;

  var estado = {
    runId: null, run: null, timer: null, camadaAtual: 'raw',
    redeAtual: '', sentimento: '', offset: 0, limite: 25,
    graficoSentimento: null, graficoRede: null,
    graficoComparativo: null,
    ultimoResumo: null, ultimasLinhas: [], ultimaSatisfacao: null, ultimoComparativo: null,
  };

  var COR_SENTIMENTO = { positivo: '#1c7a4d', negativo: '#c1443a', neutro: '#54728f', misto: '#b7791e' };
  var ROTULO_SENTIMENTO = { positivo: 'Positivo', negativo: 'Negativo', neutro: 'Neutro', misto: 'Misto' };

  function corSentimento(s) { return COR_SENTIMENTO[(s || '').toLowerCase()] || '#8b93a1'; }
  function rotuloSentimento(s) { return ROTULO_SENTIMENTO[(s || '').toLowerCase()] || s || '—'; }

  // Mesma robustez de analytics/script.js: destrói pelo registro do próprio
  // Chart.js (não só pela referência em `estado`) e isola cada desenho num
  // try/catch, pra um gráfico com dado inesperado não derrubar os outros.
  function desenharComSeguranca(nomeGrafico, fn) {
    try {
      fn();
    } catch (erro) {
      console.error('[social] falha ao desenhar "' + nomeGrafico + '":', erro);
    }
  }

  function destruirGraficoExistente(canvas) {
    var existente = window.Chart && Chart.getChart ? Chart.getChart(canvas) : null;
    if (existente) existente.destroy();
  }

  // ── Utilidades ─────────────────────────────────────────────────────────

  function $(seletor, raiz2) { return (raiz2 || document).querySelector(seletor); }
  function $$(seletor, raiz2) { return Array.prototype.slice.call((raiz2 || document).querySelectorAll(seletor)); }

  function esc(valor) {
    if (valor === null || valor === undefined) return '';
    return String(valor)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function numero(valor) {
    if (valor === null || valor === undefined) return '—';
    return Number(valor).toLocaleString('pt-BR');
  }

  function dataHora(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function api(metodo, rota, dados) {
    var url = '/api' + rota;
    var opcoes = { method: metodo, headers: { 'Content-Type': 'application/json' } };

    if (metodo === 'GET') {
      var query = Object.keys(dados || {})
        .filter(function (k) { return dados[k] !== '' && dados[k] !== null && dados[k] !== undefined; })
        .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(dados[k]); })
        .join('&');
      if (query) url += '?' + query;
    } else if (dados !== undefined) {
      opcoes.body = JSON.stringify(dados);
    }

    return fetch(url, opcoes).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (json) {
        if (!r.ok) {
          var detalhe = json.detail || json;
          throw new Error(detalhe.erro || detalhe.message || json.erro || ('Erro ' + r.status));
        }
        return json;
      });
    });
  }

  function avisar(texto) {
    var alvo = $('[data-aviso-social]');
    alvo.hidden = !texto;
    alvo.textContent = texto || '';
  }

  var SELO_POR_SENTIMENTO = { positivo: 'selo--positivo', negativo: 'selo--negativo', neutro: 'selo--neutro', misto: 'selo--misto' };

  function seloSentimento(sentimento) {
    if (!sentimento) return '<span class="texto-faint">—</span>';
    var classe = SELO_POR_SENTIMENTO[sentimento.toLowerCase()] || 'selo--neutro';
    return '<span class="selo ' + classe + '">' + esc(sentimento) + '</span>';
  }

  function chipsAdjetivos(adjetivos) {
    if (!adjetivos || !adjetivos.length) return '<span class="texto-faint">—</span>';
    return adjetivos.map(function (a) { return '<span class="chip">' + esc(a) + '</span>'; }).join(' ');
  }

  function tabela(colunas, linhas, mensagemVazia) {
    if (!linhas.length) {
      return '<div class="vazio vazio--compacto">' + esc(mensagemVazia) + '</div>';
    }
    var cabecalho = colunas.map(function (c) { return '<th>' + esc(c[0]) + '</th>'; }).join('');
    var corpo = linhas.map(function (linha) {
      return '<tr>' + colunas.map(function (c) { return '<td>' + c[1](linha) + '</td>'; }).join('') + '</tr>';
    }).join('');
    return '<div class="tabela-wrap"><table class="tabela"><thead><tr>' + cabecalho + '</tr></thead><tbody>' + corpo + '</tbody></table></div>';
  }

  // ── 1. Playground ────────────────────────────────────────────────────────

  raiz.addEventListener('submit', function (e) {
    e.preventDefault();

    var redes = $$('input[name="redes"]:checked', raiz).map(function (i) { return i.value; });
    var body = {
      termo_busca: $('#termo-busca-social').value.trim(),
      redes: redes.length ? redes : ['reddit', 'x', 'instagram'],
      quantidade: parseInt($('#quantidade-social').value, 10) || 40,
    };

    $('[data-botao-executar-social]').disabled = true;
    avisar('');
    $('[data-bloco-execucao]').hidden = false;

    api('POST', '/social/playground/run', body).then(function (resposta) {
      estado.runId = resposta.run_id;
      estado.run = resposta;
      renderTrilho(resposta.steps || []);
      iniciarPolling();
    }).catch(function (erro) {
      avisar(erro.message);
      $('[data-botao-executar-social]').disabled = false;
    });
  });

  var MARCA_STATUS = { pending: '·', running: '•', success: '✓', failed: '✕', skipped: '–' };

  function decorrido(step) {
    if (!step.started_at) return '';
    var fim = step.finished_at ? new Date(step.finished_at) : new Date();
    var segundos = (fim - new Date(step.started_at)) / 1000;
    if (isNaN(segundos) || segundos < 0) return '';
    return segundos < 60 ? segundos.toFixed(1) + 's' : Math.round(segundos / 60) + 'min';
  }

  function renderTrilho(steps) {
    var alvo = $('[data-trilho]');
    alvo.innerHTML = steps.map(function (s, indice) {
      var erro = s.error
        ? '<p class="trilho-passo__erro"><code>' + esc(s.error.error_type) + '</code> ' +
          esc(s.error.message) + '</p>'
        : '';
      return '' +
        '<li class="trilho-passo trilho-passo--' + esc(s.status) + '">' +
          '<span class="trilho-passo__marca">' + (MARCA_STATUS[s.status] || (indice + 1)) + '</span>' +
          '<div>' +
            '<p class="trilho-passo__nome">' + esc(s.name) + '</p>' +
            (s.resumo ? '<p class="trilho-passo__resumo">' + esc(s.resumo) + '</p>' : '') +
            erro +
          '</div>' +
          '<span class="trilho-passo__tempo">' + esc(decorrido(s)) + '</span>' +
        '</li>';
    }).join('');
  }

  function iniciarPolling() {
    pararPolling();
    estado.timer = setInterval(consultarRun, INTERVALO_POLLING);
    consultarRun();
  }

  function pararPolling() {
    if (estado.timer) { clearInterval(estado.timer); estado.timer = null; }
  }

  function consultarRun() {
    if (!estado.runId) return;
    api('GET', '/social/runs/' + estado.runId).then(function (run) {
      estado.run = run;
      renderTrilho(run.steps || []);

      var terminou = ['success', 'partial', 'failed'].indexOf(run.status) !== -1;
      if (terminou) {
        pararPolling();
        $('[data-botao-executar-social]').disabled = false;
        renderCamada();
        atualizarTudo(); // o run acabou de gravar no warehouse — refresca a análise consolidada
      }
    }).catch(function (erro) {
      pararPolling();
      avisar(erro.message);
      $('[data-botao-executar-social]').disabled = false;
    });
  }

  $$('[data-camada]').forEach(function (botao) {
    botao.addEventListener('click', function () {
      $$('[data-camada]').forEach(function (b) { b.classList.remove('is-ativa'); });
      botao.classList.add('is-ativa');
      estado.camadaAtual = botao.getAttribute('data-camada');
      renderCamada();
    });
  });

  function cardPost(post, analise) {
    var struct = analise && analise.struct;
    return '' +
      '<div class="post-card">' +
        '<div class="post-card__cabecalho">' +
          '<span class="post-card__rede">' + esc(post.rede) + '</span>' +
          (struct ? seloSentimento(struct.sentimento) : '') +
        '</div>' +
        '<p class="post-card__autor">' + esc(post.autor) + '</p>' +
        '<p class="post-card__texto">' + esc(post.texto) + '</p>' +
        (struct ? '<p class="post-card__rodape">' + esc(struct.objeto || '') + ' · ' + chipsAdjetivos(struct.adjetivos) + '</p>' : '') +
        '<p class="post-card__rodape"><span>' + numero(post.curtidas) + ' curtida(s)</span></p>' +
      '</div>';
  }

  function renderCamada() {
    var run = estado.run;
    var alvo = $('[data-painel-camada]');
    if (!run) { alvo.innerHTML = ''; return; }

    var posts;
    if (estado.camadaAtual === 'raw') {
      posts = (run.raw && run.raw.posts) || [];
      alvo.innerHTML = posts.length
        ? '<div class="posts-grade">' + posts.map(function (p) { return cardPost(p); }).join('') + '</div>'
        : '<div class="vazio vazio--compacto">Nenhum post em RAW ainda.</div>';
    } else if (estado.camadaAtual === 'refined') {
      posts = (run.refined && run.refined.posts) || [];
      alvo.innerHTML = posts.length
        ? '<div class="posts-grade">' + posts.map(function (p) { return cardPost(p); }).join('') + '</div>'
        : '<div class="vazio vazio--compacto">Nenhum post em REFINED ainda.</div>';
    } else {
      var analises = (run.trust && run.trust.analise) || [];
      var refinedPosts = (run.refined && run.refined.posts) || [];
      var porId = {};
      refinedPosts.forEach(function (p) { porId[p.post_id] = p; });

      var insights = run.trust && run.trust.insights;
      var resumoInsights = insights
        ? '<p class="campo__ajuda">' + insights.total_sucesso + '/' + insights.total_analisado +
          ' post(s) analisado(s) com sucesso — ' +
          Object.keys(insights.por_sentimento || {}).map(function (s) {
            return rotuloSentimento(s) + ': ' + insights.por_sentimento[s];
          }).join(', ') + '</p>'
        : '';

      alvo.innerHTML = resumoInsights + (analises.length
        ? '<div class="posts-grade">' + analises.map(function (a) {
            var post = porId[a.post_id] || { rede: '', autor: '', texto: a.texto, curtidas: 0 };
            return cardPost(post, a);
          }).join('') + '</div>'
        : '<div class="vazio vazio--compacto">Nenhuma análise em TRUST ainda.</div>');
    }
  }

  // ── 2. Filtros ───────────────────────────────────────────────────────────

  function carregarFiltros() {
    return api('GET', '/social/filtros').then(function (resposta) {
      var selRede = $('[data-filtro-rede]');
      selRede.querySelectorAll('option:not(:first-child)').forEach(function (o) { o.remove(); });
      (resposta.redes || []).forEach(function (r) {
        var opt = document.createElement('option');
        opt.value = r; opt.textContent = r;
        selRede.appendChild(opt);
      });

      var selSentimento = $('[data-filtro-sentimento]');
      selSentimento.querySelectorAll('option:not(:first-child)').forEach(function (o) { o.remove(); });
      (resposta.sentimentos || []).forEach(function (s) {
        var opt = document.createElement('option');
        opt.value = s; opt.textContent = s;
        selSentimento.appendChild(opt);
      });

      // Reaplica rede/sentimento restaurados do cache — as <option> só
      // existem a partir daqui.
      selRede.value = estado.redeAtual || '';
      selSentimento.value = estado.sentimento || '';
    });
  }

  $('[data-filtro-rede]').addEventListener('change', function (e) {
    estado.redeAtual = e.target.value; estado.offset = 0; atualizarTudo();
  });
  $('[data-filtro-sentimento]').addEventListener('change', function (e) {
    estado.sentimento = e.target.value; estado.offset = 0; atualizarTudo();
  });
  $('[data-limpar-filtros-social]').addEventListener('click', function () {
    estado.redeAtual = ''; estado.sentimento = ''; estado.offset = 0;
    $('[data-filtro-rede]').value = ''; $('[data-filtro-sentimento]').value = '';
    atualizarTudo();
  });

  function filtrosAtuais() {
    return { rede: estado.redeAtual, sentimento: estado.sentimento };
  }

  // ── 3. Resumo / gráficos ─────────────────────────────────────────────────

  function renderResumo(resposta) {
    var porSentimento = resposta.por_sentimento || [];
    var total = resposta.total_posts_analisados || 0;
    var positivos = porSentimento.filter(function (l) { return (l.sentimento || '').toLowerCase() === 'positivo'; })
      .reduce(function (soma, l) { return soma + l.total; }, 0);
    var pct = total ? Math.round((positivos / total) * 100) : 0;

    $('[data-kpi-total]').textContent = numero(total);
    $('[data-kpi-positivo]').textContent = total ? (pct + '%') : '—';

    desenharComSeguranca('sentimento', function () { desenharGraficoSentimento(porSentimento); });
    desenharComSeguranca('rede', function () { desenharGraficoRede(resposta.por_rede || []); });
  }

  function carregarResumo() {
    return api('GET', '/social/resumo', filtrosAtuais()).then(function (resposta) {
      estado.ultimoResumo = resposta;
      renderResumo(resposta);
      salvarCache();
    });
  }

  function desenharGraficoSentimento(linhas) {
    var canvas = $('[data-grafico-sentimento]');
    if (!window.Chart || !canvas) return;
    var rotulos = linhas.map(function (l) { return rotuloSentimento(l.sentimento); });
    var valores = linhas.map(function (l) { return l.total; });
    var cores = linhas.map(function (l) { return corSentimento(l.sentimento); });

    destruirGraficoExistente(canvas);
    estado.graficoSentimento = new Chart(canvas, {
      type: 'doughnut',
      data: { labels: rotulos, datasets: [{ data: valores, backgroundColor: cores }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
    });
  }

  function desenharGraficoRede(linhas) {
    var canvas = $('[data-grafico-rede]');
    if (!window.Chart || !canvas) return;
    var rotulos = linhas.map(function (l) { return l.rede || '—'; });
    var valores = linhas.map(function (l) { return l.total; });

    destruirGraficoExistente(canvas);
    estado.graficoRede = new Chart(canvas, {
      type: 'bar',
      data: { labels: rotulos, datasets: [{ label: 'Posts', data: valores, backgroundColor: '#4b4f8a' }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  // ── 4. Tabela paginada ───────────────────────────────────────────────────

  function carregarTabela() {
    var params = filtrosAtuais();
    params.limite = estado.limite;
    params.offset = estado.offset;

    return api('GET', '/social/posts', params).then(function (resposta) {
      renderTabelaResposta(resposta);
      salvarCache();
    });
  }

  function renderTabelaResposta(resposta) {
    var linhas = resposta.posts || [];
    estado.ultimasLinhas = linhas;

    $('[data-resumo-tabela]').textContent = linhas.length + ' post(s) nesta página (offset ' + estado.offset + ').';

    $('[data-tabela-posts]').innerHTML = tabela([
      ['Rede', function (r) { return esc(r.rede); }],
      ['Post', function (r) { return '<span class="texto-truncado">' + esc(r.texto) + '</span>'; }],
      ['Sentimento', function (r) { return seloSentimento(r.sentimento); }],
      ['Motivo', function (r) { return esc(r.motivo) || '<span class="texto-faint">—</span>'; }],
      ['Objeto', function (r) { return esc(r.objeto) || '<span class="texto-faint">—</span>'; }],
      ['Coletado em', function (r) { return dataHora(r.coletado_em); }],
    ], linhas, 'Nenhum post analisado ainda para este filtro — rode o Playground acima.');

    $('[data-pagina-anterior]').disabled = estado.offset === 0;
    $('[data-pagina-seguinte]').disabled = linhas.length < estado.limite;
  }

  $('[data-pagina-anterior]').addEventListener('click', function () {
    estado.offset = Math.max(0, estado.offset - estado.limite);
    carregarTabela();
  });
  $('[data-pagina-seguinte]').addEventListener('click', function () {
    estado.offset += estado.limite;
    carregarTabela();
  });

  // ── 5. Satisfação NPS/Likert ─────────────────────────────────────────────

  function corPorNps(nps) {
    if (nps === null || nps === undefined) return COR_SENTIMENTO.neutro;
    if (nps >= 10) return COR_SENTIMENTO.positivo;
    if (nps <= -10) return COR_SENTIMENTO.negativo;
    return COR_SENTIMENTO.neutro;
  }

  function renderSatisfacaoResposta(r) {
    renderStatNps(r.geral);
    renderStatLikert(r.geral);
  }

  function carregarSatisfacao() {
    return api('GET', '/social/satisfacao', filtrosAtuais()).then(function (r) {
      estado.ultimaSatisfacao = r;
      renderSatisfacaoResposta(r);
      salvarCache();
    });
  }

  function renderStatNps(geral) {
    var elValor = $('[data-stat-nps-valor]');
    var elLegenda = $('[data-stat-nps-legenda]');
    if (geral.nps === null || geral.nps === undefined) {
      elValor.textContent = '—'; elValor.style.color = '';
      elLegenda.textContent = 'Sem posts suficientes para calcular.';
      return;
    }
    elValor.textContent = (geral.nps > 0 ? '+' : '') + geral.nps;
    elValor.style.color = corPorNps(geral.nps);
    elLegenda.textContent = geral.positivos + ' promotor(es) · ' + (geral.neutros + geral.mistos) +
      ' passivo(s) · ' + geral.negativos + ' detrator(es) · ' + geral.total + ' post(s) no total';
  }

  function renderStatLikert(geral) {
    $('[data-stat-likert-valor]').textContent = (geral.likert_medio === null || geral.likert_medio === undefined)
      ? '—' : geral.likert_medio.toFixed(2);

    var porNota = { 1: geral.negativos || 0, 3: (geral.neutros || 0) + (geral.mistos || 0), 5: geral.positivos || 0 };
    var maior = Math.max(1, porNota[1], porNota[3], porNota[5]);
    $('[data-likert-dist]').innerHTML = [1, 3, 5].map(function (nota) {
      var qtd = porNota[nota];
      var pct = Math.round(qtd / maior * 100);
      return '<div class="likert-dist__linha">' +
        '<span class="likert-dist__nota">' + nota + '</span>' +
        '<div class="likert-dist__track"><div class="likert-dist__fill" style="width:' + pct + '%"></div></div>' +
        '<span class="likert-dist__qtd">' + qtd + '</span></div>';
    }).join('');
  }

  // ── 6. Comparação de marcas ──────────────────────────────────────────────

  function carregarComparativo() {
    var marca = $('[data-comparativo-marca]').value;
    return api('GET', '/social/comparativo-marcas', { marca_concorrente: marca }).then(function (r) {
      estado.ultimoComparativo = r;
      renderComparativo(r);
      salvarCache();
    });
  }

  function statComparativo(prefixo, dados) {
    $('[data-comparativo-' + prefixo + '-nps]').textContent =
      (dados.nps === null || dados.nps === undefined) ? '—' : ((dados.nps > 0 ? '+' : '') + dados.nps);
    $('[data-comparativo-' + prefixo + '-positivo]').textContent =
      (dados.pct_positivo === null || dados.pct_positivo === undefined) ? '—' : (dados.pct_positivo + '%');
    $('[data-comparativo-' + prefixo + '-engajamento]').textContent =
      (dados.engajamento_medio === null || dados.engajamento_medio === undefined) ? '—' : dados.engajamento_medio;
    $('[data-comparativo-' + prefixo + '-mencoes]').textContent = numero(dados.mencoes);
  }

  function renderComparativo(r) {
    var ford = r.ford, concorrente = r.concorrente;
    statComparativo('ford', ford);
    statComparativo('concorrente', concorrente);
    $('[data-comparativo-concorrente-nome]').textContent = concorrente.marca;

    var seloFord = $('[data-comparativo-selo-ford]');
    if (seloFord) seloFord.hidden = !((ford.nps || 0) > (concorrente.nps || 0));

    desenharComSeguranca('comparativo', function () { desenharGraficoComparativo(ford, concorrente); });
  }

  function desenharGraficoComparativo(ford, concorrente) {
    var canvas = $('[data-grafico-comparativo]');
    if (!window.Chart || !canvas) return;
    var rotulos = ['NPS', '% Positivo', 'Engajamento médio'];
    var valoresFord = [ford.nps || 0, ford.pct_positivo || 0, ford.engajamento_medio || 0];
    var valoresConcorrente = [concorrente.nps || 0, concorrente.pct_positivo || 0, concorrente.engajamento_medio || 0];

    destruirGraficoExistente(canvas);
    estado.graficoComparativo = new Chart(canvas, {
      type: 'bar',
      data: {
        labels: rotulos,
        datasets: [
          { label: 'Ford', data: valoresFord, backgroundColor: '#4b4f8a' },
          { label: concorrente.marca, data: valoresConcorrente, backgroundColor: '#8b93a1' },
        ],
      },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
    });
  }

  $('[data-comparativo-marca]').addEventListener('change', function () {
    carregarComparativo().catch(function (erro) { avisar(erro.message); });
  });

  // ── Cache entre páginas (navegação fluida) ────────────────────────────────
  // Mesmo padrão de analytics/script.js — guarda o último resultado em
  // sessionStorage e pinta na hora ao reabrir a aba, antes do fetch fresco
  // chegar (stale-while-revalidate). O Playground (trilho/run em andamento)
  // não entra no cache — só a análise consolidada, que é o que trava/aparece
  // em branco ao trocar de página.

  var CHAVE_CACHE = 'social';

  function salvarCache() {
    if (!window.PageCache) return;
    PageCache.salvar(CHAVE_CACHE, {
      filtros: {
        redeAtual: estado.redeAtual, sentimento: estado.sentimento,
        offset: estado.offset, limite: estado.limite,
        marcaComparativo: $('[data-comparativo-marca]').value,
      },
      resumo: estado.ultimoResumo,
      tabela: { posts: estado.ultimasLinhas, limite: estado.limite, offset: estado.offset },
      satisfacao: estado.ultimaSatisfacao,
      comparativo: estado.ultimoComparativo,
    });
  }

  function restaurarCache() {
    if (!window.PageCache) return false;
    var cache = PageCache.carregar(CHAVE_CACHE);
    if (!cache) return false;

    if (cache.filtros) {
      estado.redeAtual = cache.filtros.redeAtual || '';
      estado.sentimento = cache.filtros.sentimento || '';
      estado.offset = cache.filtros.offset || 0;
      estado.limite = cache.filtros.limite || estado.limite;
      if (cache.filtros.marcaComparativo) $('[data-comparativo-marca]').value = cache.filtros.marcaComparativo;
    }

    if (cache.resumo) { estado.ultimoResumo = cache.resumo; renderResumo(cache.resumo); }
    if (cache.tabela) { renderTabelaResposta(cache.tabela); }
    if (cache.satisfacao) { estado.ultimaSatisfacao = cache.satisfacao; renderSatisfacaoResposta(cache.satisfacao); }
    if (cache.comparativo) { estado.ultimoComparativo = cache.comparativo; renderComparativo(cache.comparativo); }

    return true;
  }

  // ── Bootstrap ────────────────────────────────────────────────────────────

  function atualizarTudo() {
    Promise.all([carregarResumo(), carregarTabela(), carregarSatisfacao(), carregarComparativo()])
      .catch(function (erro) { avisar(erro.message); });
  }

  restaurarCache(); // pinta o que já tinha, na hora — antes de qualquer fetch

  carregarFiltros().then(atualizarTudo).catch(function (erro) { avisar(erro.message); });

  // ── Chat widget ──────────────────────────────────────────────────────────

  if (window.registrarContextoChat) {
    window.registrarContextoChat('Redes Sociais', function () {
      return {
        filtros: { rede: estado.redeAtual || null, sentimento: estado.sentimento || null },
        resumo: estado.ultimoResumo,
        satisfacao_nps_likert: estado.ultimaSatisfacao,
        comparativo_marcas: estado.ultimoComparativo,
        posts: (estado.ultimasLinhas || []).slice(0, 30),
      };
    }, [
      'Qual o sentimento geral dos posts filtrados?',
      'Como a Ford se compara com o concorrente selecionado?',
      'Qual rede social tem o engajamento mais alto?'
    ]);
  }
})();
