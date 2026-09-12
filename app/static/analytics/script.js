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
  };

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

  function carregarResumo() {
    return api('/analytics/resumo', filtrosAtuais()).then(function (resposta) {
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

      desenharGraficoSentimento(porSentimento);
      desenharGraficoObjeto(resposta.por_objeto || []);
    });
  }

  function paletaCores(n) {
    var base = ['#b3672e', '#1d4ed8', '#0f6e6e', '#8a3b5e', '#b3423a', '#4b4f8a', '#2f6b46'];
    var cores = [];
    for (var i = 0; i < n; i++) cores.push(base[i % base.length]);
    return cores;
  }

  function desenharGraficoSentimento(linhas) {
    var canvas = $('[data-grafico-sentimento]');
    if (!window.Chart || !canvas) return;

    var rotulos = linhas.map(function (l) { return l.sentimento || '—'; });
    var valores = linhas.map(function (l) { return l.total; });

    if (estado.graficoSentimento) estado.graficoSentimento.destroy();
    estado.graficoSentimento = new Chart(canvas, {
      type: 'doughnut',
      data: { labels: rotulos, datasets: [{ data: valores, backgroundColor: paletaCores(rotulos.length) }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
    });
  }

  function desenharGraficoObjeto(linhas) {
    var canvas = $('[data-grafico-objeto]');
    if (!window.Chart || !canvas) return;

    var rotulos = linhas.map(function (l) { return l.objeto || '—'; });
    var valores = linhas.map(function (l) { return l.total; });

    if (estado.graficoObjeto) estado.graficoObjeto.destroy();
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

  function carregarTabela() {
    var params = filtrosAtuais();
    params.limite = estado.limite;
    params.offset = estado.offset;

    return api('/analytics/comentarios', params).then(function (resposta) {
      var linhas = resposta.comentarios || [];

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

  // ── Bootstrap ────────────────────────────────────────────────────────────

  function atualizarTudo() {
    avisar('');
    Promise.all([carregarResumo(), carregarTabela()]).catch(function (erro) {
      avisar(erro.message);
    });
  }

  // O warehouse (BigQuery) é um recurso opcional — sem credencial configurada
  // (ou configurada errada), os outros endpoints de /analytics devolvem 503.
  // Checar /youtube/config primeiro dá um aviso específico em vez de um erro
  // genérico "Erro 503" sem contexto nenhum.
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

  verificarWarehouse().then(function (ok) {
    carregarFiltros().catch(function (erro) { avisar(erro.message); });
    if (ok) atualizarTudo();
  });
})();
