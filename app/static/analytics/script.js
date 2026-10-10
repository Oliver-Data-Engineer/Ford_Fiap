/* ============================================================
   DASHBOARD ANALÍTICO
   Só leitura: consulta src/api/analytics_api.py, que por sua vez lê o
   warehouse BigQuery (src/analytics/warehouse.py). Sem regra de negócio
   aqui (Regra 2 do SPEC) — filtra, pagina e desenha o que o backend devolve.
   ============================================================ */
(function () {
  'use strict';

  var tabelaAlvo = document.querySelector('[data-tabela-comentarios]');
  if (!tabelaAlvo) return;

  var estado = {
    canalId: '',
    sentimento: '',
    offset: 0,
    limite: 25,
    graficoSentimento: null,
    graficoObjeto: null,
    graficoNpsObjeto: null,
    graficoLikertCanal: null,
    graficoTendencia: null,
    graficoEngajamento: null,
    graficoComparativo: null,
    dadosReais: false,
    ultimoResumo: null,      // último /analytics/resumo — alimenta o chat widget
    ultimasLinhas: [],       // últimos /analytics/comentarios — idem
    ultimaSatisfacao: null,  // último /analytics/satisfacao — idem
    ultimoComparativo: null, // última /analytics/comparativo-marcas — idem
  };

  // Cor fixa por sentimento (nunca por posição/ordem — um filtro que muda a
  // contagem não pode "repintar" as fatias que sobraram). Mesmas cores dos
  // selos já usados em todo o app (.selo--positivo/negativo/neutro/misto,
  // ver shared/base.css) — "neutro" ganha um tom próprio pros gráficos
  // porque --text-muted tem croma baixo demais pra distinguir de --ok num
  // gráfico colorido (validado com o script de paleta do skill de dataviz).
  var COR_SENTIMENTO = { positivo: '#1c7a4d', negativo: '#c1443a', neutro: '#54728f', misto: '#b7791e' };
  var ROTULO_SENTIMENTO = { positivo: 'Positivo', negativo: 'Negativo', neutro: 'Neutro', misto: 'Misto' };

  function corSentimento(sentimento) {
    return COR_SENTIMENTO[(sentimento || '').toLowerCase()] || '#8b93a1';
  }

  function rotuloSentimento(sentimento) {
    return ROTULO_SENTIMENTO[(sentimento || '').toLowerCase()] || sentimento || '—';
  }

  // Cor diverging do NPS (polaridade: promotor domina / equilibrado / detrator
  // domina) — reaproveita as mesmas 3 cores de sentimento, mesmo papel fixo.
  function corPorNps(nps) {
    if (nps === null || nps === undefined) return COR_SENTIMENTO.neutro;
    if (nps >= 10) return COR_SENTIMENTO.positivo;
    if (nps <= -10) return COR_SENTIMENTO.negativo;
    return COR_SENTIMENTO.neutro;
  }

  // Destrói pelo registro do próprio Chart.js (Chart.getChart), não só pela
  // referência que a gente guarda em `estado` — mais robusto: continua
  // funcionando mesmo se as duas ficarem dessincronizadas (ex.: depois de
  // uma restauração de cache/navegação). E cada desenho roda isolado num
  // try/catch: um gráfico com dado inesperado não pode derrubar os outros
  // que são desenhados na mesma leva (ex.: os 4 de satisfação).
  function desenharComSeguranca(nomeGrafico, fn) {
    try {
      fn();
    } catch (erro) {
      console.error('[analytics] falha ao desenhar "' + nomeGrafico + '":', erro);
    }
  }

  function destruirGraficoExistente(canvas) {
    var existente = window.Chart && Chart.getChart ? Chart.getChart(canvas) : null;
    if (existente) existente.destroy();
  }

  // ── Utilidades ─────────────────────────────────────────────────────────

  function $(seletor, raiz) { return (raiz || document).querySelector(seletor); }

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
    return isNaN(d) ? '—' : d.toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    });
  }

  function api(rota, params) {
    var url = '/api' + rota;
    var query = Object.keys(params || {})
      .filter(function (k) { return params[k] !== '' && params[k] !== null && params[k] !== undefined; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); })
      .join('&');
    if (query) url += '?' + query;

    return fetch(url).then(function (r) {
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
    var alvo = $('[data-aviso-analytics]');
    alvo.hidden = !texto;
    alvo.textContent = texto || '';
  }

  var SELO_POR_SENTIMENTO = {
    positivo: 'selo--positivo', negativo: 'selo--negativo',
    neutro: 'selo--neutro', misto: 'selo--misto'
  };

  function seloSentimento(sentimento) {
    if (!sentimento) return '<span class="texto-faint">—</span>';
    var classe = SELO_POR_SENTIMENTO[sentimento.toLowerCase()] || 'selo--neutro';
    return '<span class="selo ' + classe + '">' + esc(sentimento) + '</span>';
  }

  function chipsAdjetivos(adjetivos) {
    if (!adjetivos || !adjetivos.length) return '<span class="texto-faint">—</span>';
    return adjetivos.map(function (a) { return '<span class="chip">' + esc(a) + '</span>'; }).join(' ');
  }

  function tabela(colunas, linhas) {
    if (!linhas.length) {
      return '<div class="vazio vazio--compacto">Nenhum comentário enriquecido ainda para este filtro.</div>';
    }
    var cabecalho = colunas.map(function (c) { return '<th>' + esc(c[0]) + '</th>'; }).join('');
    var corpo = linhas.map(function (linha) {
      return '<tr>' + colunas.map(function (c) { return '<td>' + c[1](linha) + '</td>'; }).join('') + '</tr>';
    }).join('');
    return '<div class="tabela-wrap"><table class="tabela"><thead><tr>' + cabecalho +
      '</tr></thead><tbody>' + corpo + '</tbody></table></div>';
  }

  // ── Filtros ────────────────────────────────────────────────────────────

  function carregarFiltros() {
    return api('/analytics/filtros').then(function (resposta) {
      var selCanal = $('[data-filtro-canal]');
      (resposta.canais || []).forEach(function (c) {
        var opt = document.createElement('option');
        opt.value = c.canal_id;
        opt.textContent = c.canal_nome;
        selCanal.appendChild(opt);
      });

      var selSentimento = $('[data-filtro-sentimento]');
      (resposta.sentimentos || []).forEach(function (s) {
        var opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        selSentimento.appendChild(opt);
      });

      estado.dadosReais = !!resposta.dados_reais;
      // Reaplica canal/sentimento restaurados do cache — as <option> só
      // existem a partir daqui, então setar o .value antes disso não pega.
      selCanal.value = estado.canalId || '';
      selSentimento.value = estado.sentimento || '';
      return resposta;
    });
  }

  $('[data-filtro-canal]').addEventListener('change', function (e) {
    estado.canalId = e.target.value;
    estado.offset = 0;
    atualizarTudo();
  });

  $('[data-filtro-sentimento]').addEventListener('change', function (e) {
    estado.sentimento = e.target.value;
    estado.offset = 0;
    atualizarTudo();
  });

  $('[data-limpar-filtros]').addEventListener('click', function () {
    estado.canalId = '';
    estado.sentimento = '';
    estado.offset = 0;
    $('[data-filtro-canal]').value = '';
    $('[data-filtro-sentimento]').value = '';
    atualizarTudo();
  });

  // ── Resumo (KPIs + gráficos) ─────────────────────────────────────────────

  function filtrosAtuais() {
    return { canal_id: estado.canalId, sentimento: estado.sentimento };
  }

  function renderResumo(resposta) {
    var porSentimento = resposta.por_sentimento || [];
    var total = resposta.total_comentarios_analisados || 0;
    var positivos = porSentimento.filter(function (l) {
      return (l.sentimento || '').toLowerCase() === 'positivo';
    }).reduce(function (soma, l) { return soma + l.total; }, 0);
    var pctPositivo = total ? Math.round((positivos / total) * 100) : 0;

    $('[data-kpi-total]').textContent = numero(total);
    $('[data-kpi-videos]').textContent = numero(resposta.total_videos_analisados);
    $('[data-kpi-canais]').textContent = numero(resposta.total_canais);
    $('[data-kpi-positivo]').textContent = total ? (pctPositivo + '%') : '—';

    desenharComSeguranca('sentimento', function () { desenharGraficoSentimento(porSentimento); });
    desenharComSeguranca('objeto', function () { desenharGraficoObjeto(resposta.por_objeto || []); });
  }

  function carregarResumo() {
    return api('/analytics/resumo', filtrosAtuais()).then(function (resposta) {
      estado.ultimoResumo = resposta; // alimenta o chat widget e o cache (ver final do arquivo)
      renderResumo(resposta);
      salvarCache();
    });
  }

  function desenharGraficoSentimento(linhas) {
    var canvas = $('[data-grafico-sentimento]');
    if (!window.Chart || !canvas) return;

    // Cor fixa por sentimento (ver COR_SENTIMENTO) — nunca a paleta
    // categórica genérica: "positivo" tem que ser sempre verde, mesmo que
    // um filtro mude a ordem/quantidade das fatias.
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

  function desenharGraficoObjeto(linhas) {
    var canvas = $('[data-grafico-objeto]');
    if (!window.Chart || !canvas) return;

    var rotulos = linhas.map(function (l) { return l.objeto || '—'; });
    var valores = linhas.map(function (l) { return l.total; });

    destruirGraficoExistente(canvas);
    estado.graficoObjeto = new Chart(canvas, {
      type: 'bar',
      data: { labels: rotulos, datasets: [{ label: 'Comentários', data: valores, backgroundColor: '#b3672e' }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  // ── Tabela paginada ──────────────────────────────────────────────────────

  function renderTabelaResposta(resposta) {
    var linhas = resposta.comentarios || [];
    estado.ultimasLinhas = linhas; // alimenta o chat widget e o cache (ver final do arquivo)

    $('[data-resumo-tabela]').textContent =
      linhas.length + ' comentário(s) nesta página (offset ' + estado.offset + ').';

    tabelaAlvo.innerHTML = tabela([
      ['Canal', function (r) { return esc(r.canal_nome); }],
      ['Comentário', function (r) { return '<span class="texto-truncado">' + esc(r.texto) + '</span>'; }],
      ['Sentimento', function (r) { return seloSentimento(r.sentimento); }],
      ['Motivo', function (r) { return esc(r.motivo) || '<span class="texto-faint">—</span>'; }],
      ['Adjetivos', function (r) { return chipsAdjetivos(r.adjetivos); }],
      ['Objeto', function (r) { return esc(r.objeto) || '<span class="texto-faint">—</span>'; }],
      ['Coletado em', function (r) { return dataHora(r.coletado_em); }],
    ], linhas);

    $('[data-pagina-anterior]').disabled = estado.offset === 0;
    $('[data-pagina-seguinte]').disabled = linhas.length < estado.limite;
  }

  function carregarTabela() {
    var params = filtrosAtuais();
    params.limite = estado.limite;
    params.offset = estado.offset;

    return api('/analytics/comentarios', params).then(function (resposta) {
      renderTabelaResposta(resposta);
      salvarCache();
    });
  }

  $('[data-pagina-anterior]').addEventListener('click', function () {
    estado.offset = Math.max(0, estado.offset - estado.limite);
    carregarTabela();
  });

  $('[data-pagina-seguinte]').addEventListener('click', function () {
    estado.offset += estado.limite;
    carregarTabela();
  });

  // ── Panorama geral (todos os domínios de dado — FIPE/Gov, Scraper, YouTube) ─
  //
  // Cada domínio é um warehouse BigQuery independente (mesma restrição de
  // não-unificação de identidade do resto do app — ver `_filtro_comum` acima)
  // — em vez de o backend consultar 3 bancos numa SQL só, o frontend chama os
  // 3 endpoints de resumo já existentes (`/api/gov/resumo`, `/api/scraper/
  // resumo`, `/api/analytics/resumo`) e só compõe a exibição. Um domínio sem
  // BigQuery configurado (ou fora do ar) some da lista, os outros continuam.

  function fetchResumoOpcional(rota) {
    return fetch(rota).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  function tilePanorama(rotulo, valor) {
    return '<div class="analytics-kpi"><span>' +
      '<p class="analytics-kpi__valor">' + esc(valor) + '</p>' +
      '<p class="analytics-kpi__rotulo">' + esc(rotulo) + '</p></span></div>';
  }

  function carregarPanorama() {
    var alvo = $('[data-panorama-kpis]');

    Promise.all([
      fetchResumoOpcional('/api/gov/resumo'),
      fetchResumoOpcional('/api/scraper/resumo'),
    ]).then(function (resultados) {
      var gov = resultados[0], scraper = resultados[1];
      var tiles = [];

      if (gov) {
        tiles.push(tilePanorama('Marcas catalogadas (FIPE)', numero(gov.total_marcas)));
        tiles.push(tilePanorama('Modelos catalogados (FIPE)', numero(gov.total_modelos)));
        tiles.push(tilePanorama('Registros com código FIPE', numero(gov.total_registros_fipe)));
      }
      if (scraper) {
        tiles.push(tilePanorama('Ficha técnica coletada (Scraper Mode)', numero(scraper.total_ficha_tecnica)));
        tiles.push(tilePanorama('Anúncios coletados (Scraper Mode)', numero(scraper.total_anuncios)));
        tiles.push(tilePanorama('Comentários coletados (Scraper Mode)', numero(scraper.total_comentarios)));
      }

      alvo.innerHTML = tiles.length ? tiles.join('') :
        '<div class="vazio vazio--compacto">Nenhum outro domínio de dado disponível ainda ' +
        '(Gov Mode/Scraper Mode sem BigQuery configurado, ou sem nenhuma coleta ainda).</div>';
    });
  }

  // ── Satisfação: NPS & Likert derivados do sentimento ─────────────────────

  function renderSatisfacaoResposta(r) {
    renderStatNps(r.geral);
    renderStatLikert(r.geral);
    desenharComSeguranca('nps-objeto', function () { desenharGraficoNpsObjeto(r.por_objeto || []); });
    desenharComSeguranca('likert-canal', function () { desenharGraficoLikertCanal(r.por_canal || []); });
    desenharComSeguranca('tendencia', function () { desenharGraficoTendencia(r.tendencia || []); });
    desenharComSeguranca('engajamento', function () { desenharGraficoEngajamento(r.engajamento_por_sentimento || []); });
  }

  function carregarSatisfacao() {
    return api('/analytics/satisfacao', filtrosAtuais()).then(function (r) {
      estado.ultimaSatisfacao = r; // alimenta o chat widget e o cache (ver final do arquivo)
      renderSatisfacaoResposta(r);
      salvarCache();
    });
  }

  function renderStatNps(geral) {
    var elValor = $('[data-stat-nps-valor]');
    var elLegenda = $('[data-stat-nps-legenda]');

    if (geral.nps === null || geral.nps === undefined) {
      elValor.textContent = '—';
      elValor.style.color = '';
      elLegenda.textContent = 'Sem comentários suficientes para calcular.';
      return;
    }

    elValor.textContent = (geral.nps > 0 ? '+' : '') + geral.nps;
    elValor.style.color = corPorNps(geral.nps);
    elLegenda.textContent =
      geral.positivos + ' promotor(es) · ' + (geral.neutros + geral.mistos) + ' passivo(s) · ' +
      geral.negativos + ' detrator(es) · ' + geral.total + ' comentário(s) no total';
  }

  function renderStatLikert(geral) {
    $('[data-stat-likert-valor]').textContent =
      (geral.likert_medio === null || geral.likert_medio === undefined) ? '—' : geral.likert_medio.toFixed(2);

    var distEl = $('[data-likert-dist]');
    var porNota = { 1: geral.negativos || 0, 3: (geral.neutros || 0) + (geral.mistos || 0), 5: geral.positivos || 0 };
    var maior = Math.max(1, porNota[1], porNota[3], porNota[5]);

    distEl.innerHTML = [1, 3, 5].map(function (nota) {
      var qtd = porNota[nota];
      var pct = Math.round(qtd / maior * 100);
      return '<div class="likert-dist__linha">' +
        '<span class="likert-dist__nota">' + nota + '</span>' +
        '<div class="likert-dist__track"><div class="likert-dist__fill" style="width:' + pct + '%"></div></div>' +
        '<span class="likert-dist__qtd">' + qtd + '</span></div>';
    }).join('');
  }

  function desenharGraficoNpsObjeto(linhas) {
    var canvas = $('[data-grafico-nps-objeto]');
    if (!window.Chart || !canvas) return;

    var ordenadas = linhas.slice().sort(function (a, b) { return (b.nps || 0) - (a.nps || 0); });
    var rotulos = ordenadas.map(function (l) { return l.objeto || '—'; });
    var valores = ordenadas.map(function (l) { return l.nps || 0; });
    var cores = ordenadas.map(function (l) { return corPorNps(l.nps); });

    destruirGraficoExistente(canvas);
    estado.graficoNpsObjeto = new Chart(canvas, {
      type: 'bar',
      data: { labels: rotulos, datasets: [{ label: 'NPS', data: valores, backgroundColor: cores }] },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: function (ctx) { return 'NPS: ' + ctx.parsed.x; } } },
        },
        scales: { x: { min: -100, max: 100 } },
      },
    });
  }

  function desenharGraficoLikertCanal(linhas) {
    var canvas = $('[data-grafico-likert-canal]');
    if (!window.Chart || !canvas) return;

    // Série única (mesma métrica pra todo canal) — uma cor só, sequencial;
    // nunca uma cor por barra (isso seria identidade categórica, não é o
    // caso aqui — todas as barras são "Likert médio", só o canal muda).
    var ordenadas = linhas.slice().sort(function (a, b) { return (b.likert_medio || 0) - (a.likert_medio || 0); });
    var rotulos = ordenadas.map(function (l) { return l.canal_nome || '—'; });
    var valores = ordenadas.map(function (l) { return l.likert_medio || 0; });

    destruirGraficoExistente(canvas);
    estado.graficoLikertCanal = new Chart(canvas, {
      type: 'bar',
      data: { labels: rotulos, datasets: [{ label: 'Likert médio', data: valores, backgroundColor: '#b3672e' }] },
      options: {
        indexAxis: 'y',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { min: 1, max: 5 } },
      },
    });
  }

  function desenharGraficoTendencia(linhas) {
    var canvas = $('[data-grafico-tendencia]');
    if (!window.Chart || !canvas) return;

    var rotulos = linhas.map(function (l) { return l.dia; });
    var valores = linhas.map(function (l) { return l.nps; });

    destruirGraficoExistente(canvas);
    estado.graficoTendencia = new Chart(canvas, {
      type: 'line',
      data: {
        labels: rotulos,
        datasets: [{
          label: 'NPS', data: valores, borderColor: '#b3672e',
          backgroundColor: 'rgba(179,103,46,.15)', fill: true, tension: 0.25, spanGaps: true,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { min: -100, max: 100 } },
      },
    });
  }

  function desenharGraficoEngajamento(linhas) {
    var canvas = $('[data-grafico-engajamento]');
    if (!window.Chart || !canvas) return;

    var rotulos = linhas.map(function (l) { return rotuloSentimento(l.sentimento); });
    var valores = linhas.map(function (l) { return Math.round((l.likes_medio || 0) * 10) / 10; });
    var cores = linhas.map(function (l) { return corSentimento(l.sentimento); });

    destruirGraficoExistente(canvas);
    estado.graficoEngajamento = new Chart(canvas, {
      type: 'bar',
      data: { labels: rotulos, datasets: [{ label: 'Likes médios', data: valores, backgroundColor: cores }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
    });
  }

  // ── Comparação de marcas: Ford vs. concorrente escolhido ─────────────────
  // Ford sempre na cor de destaque do modo (--accent-analytics); concorrente
  // em cinza neutro — a mesma paleta fixa já usada no resto da página, sem
  // nenhuma cor nova. No modo ilustrativo (padrão) a Ford sempre lidera; no
  // modo real (`DASHBOARD_REAL=true`) o selo "líder" só aparece se o NPS da
  // Ford for de fato maior no dado coletado.

  function carregarComparativo() {
    var marca = $('[data-comparativo-marca]').value;
    return api('/analytics/comparativo-marcas', { marca_concorrente: marca }).then(function (r) {
      estado.ultimoComparativo = r; // alimenta o chat widget e o cache (ver final do arquivo)
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
          { label: 'Ford', data: valoresFord, backgroundColor: '#b3672e' },
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
  // Guarda o último resultado de cada seção em sessionStorage — ao reabrir a
  // aba Dashboard (trocar de página e voltar), a tela renderiza esse cache
  // na hora (sem "Carregando…" de novo) enquanto busca dado fresco por trás
  // e substitui quando chega (stale-while-revalidate). Nunca é a fonte da
  // verdade — só evita a tela em branco entre uma navegação e outra.

  var CHAVE_CACHE = 'analytics';

  function salvarCache() {
    if (!window.PageCache) return;
    PageCache.salvar(CHAVE_CACHE, {
      filtros: {
        canalId: estado.canalId, sentimento: estado.sentimento,
        offset: estado.offset, limite: estado.limite,
        marcaComparativo: $('[data-comparativo-marca]').value,
      },
      resumo: estado.ultimoResumo,
      tabela: { comentarios: estado.ultimasLinhas, limite: estado.limite, offset: estado.offset },
      satisfacao: estado.ultimaSatisfacao,
      comparativo: estado.ultimoComparativo,
    });
  }

  function restaurarCache() {
    if (!window.PageCache) return false;
    var cache = PageCache.carregar(CHAVE_CACHE);
    if (!cache) return false;

    if (cache.filtros) {
      estado.canalId = cache.filtros.canalId || '';
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
    avisar('');
    Promise.all([
      carregarResumo(), carregarTabela(), carregarSatisfacao(), carregarComparativo(),
    ]).catch(function (erro) {
      avisar(erro.message);
    });
  }

  // O warehouse (BigQuery) é um recurso opcional — sem credencial configurada
  // (ou configurada errada), os outros endpoints de /analytics devolvem 503.
  // Checar /youtube/config primeiro dá um aviso específico em vez de um erro
  // genérico "Erro 503" sem contexto nenhum. Só faz sentido no modo real —
  // no modo ilustrativo (padrão) nenhum endpoint toca o BigQuery.
  function verificarWarehouse() {
    return api('/youtube/config').then(function (resposta) {
      var wh = resposta.warehouse || {};
      if (!wh.configurado) {
        avisar('Warehouse do Dashboard não está configurado (GOOGLE_APPLICATION_CREDENTIALS ausente ' +
          'ou inválida no .env do servidor) — sem isso não há dado pra mostrar aqui.');
        return false;
      }
      // ultimo_erro agora é um mapa {nome_da_tabela: {quando, mensagem}} —
      // uma tabela falhar não mascara mais o diagnóstico de outra.
      if (wh.ultimo_erro) {
        var linhas = Object.keys(wh.ultimo_erro).map(function (tabela) {
          var erro = wh.ultimo_erro[tabela];
          return tabela + ' (' + dataHora(erro.quando) + '): ' + erro.mensagem;
        });
        avisar('Falha ao gravar no warehouse — ' + linhas.join(' | '));
      }
      return true;
    });
  }

  restaurarCache(); // pinta o que já tinha, na hora — antes de qualquer fetch

  carregarPanorama();

  carregarFiltros().then(function () {
    if (!estado.dadosReais) {
      atualizarTudo();
      return;
    }
    return verificarWarehouse().then(function (ok) {
      if (ok) atualizarTudo();
    });
  }).catch(function (erro) { avisar(erro.message); });

  // ── Chat widget — contexto = resumo + satisfação + comentários filtrados ─

  if (window.registrarContextoChat) {
    window.registrarContextoChat('Dashboard analítico (YouTube)', function () {
      return {
        dados_reais: estado.dadosReais,
        filtros: { canal_id: estado.canalId || null, sentimento: estado.sentimento || null },
        resumo: estado.ultimoResumo,
        satisfacao_nps_likert: estado.ultimaSatisfacao,
        comparativo_marcas: estado.ultimoComparativo,
        comentarios: (estado.ultimasLinhas || []).slice(0, 30)
      };
    }, [
      'Qual o sentimento geral dos comentários filtrados?',
      'Quais os principais assuntos comentados?',
      'Resuma os comentários mais negativos.',
      'Qual o NPS geral e o que mais está puxando ele pra baixo?',
      'Como o Likert médio varia entre os canais?',
      'Como a Ford se compara com o concorrente selecionado?'
    ]);
  }
})();
