/* ============================================================
   GOV MODE — catálogo & busca de preço FIPE (marca/modelo/ano,
   unificados numa única seção) e pipelines de extração.
   O JavaScript não executa regra de negócio: só coleta entrada, chama
   /api/gov/* e desenha o que o backend devolve.
   ============================================================ */
(function () {
  'use strict';

  var pagina = document.querySelector('[data-combobox-marca]');
  if (!pagina) return;

  var estado = {
    tipoVeiculo: 'carros',
    marcasCatalogadas: [],   // [{marca_codigo, marca_nome, total_modelos}] — datalist da pipeline
    pipelines: [],
    timers: {}
  };

  var INTERVALO_POLLING = 2000;

  // ── Utilidades ─────────────────────────────────────────────────────────

  function $(seletor, raiz) { return (raiz || document).querySelector(seletor); }
  function $$(seletor, raiz) {
    return Array.prototype.slice.call((raiz || document).querySelectorAll(seletor));
  }

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

  function api(metodo, rota, corpo) {
    var opcoes = { method: metodo, headers: { 'Content-Type': 'application/json' } };
    if (corpo !== undefined) opcoes.body = JSON.stringify(corpo);

    return fetch('/api/gov' + rota, opcoes).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (json) {
        if (!r.ok) {
          var detalhe = json.detail || json;
          var erro = new Error(detalhe.erro || detalhe.message || json.erro || ('Erro ' + r.status));
          erro.motivo = detalhe.motivo;
          throw erro;
        }
        return json;
      });
    });
  }

  function avisar(elemento, texto, tipo) {
    if (!elemento) return;
    elemento.textContent = texto || '';
    elemento.className = 'aviso' + (tipo ? ' aviso--' + tipo : '');
  }

  function avisarPagina(texto) {
    var alvo = $('[data-aviso-gov]');
    alvo.hidden = !texto;
    alvo.textContent = texto || '';
  }

  var SELO_STATUS = {
    success: 'selo--positivo', partial: 'selo--pendente',
    failed: 'selo--negativo', running: 'selo--pendente', pending: 'selo--neutro'
  };

  var ROTULO_STATUS = {
    success: 'Concluída', partial: 'Parcial', failed: 'Falhou',
    running: 'Executando', pending: 'Na fila'
  };

  var ICONES = {
    play: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M7 5l12 7-12 7V5z" stroke-linejoin="round"/></svg>',
    pausa: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M8 5v14M16 5v14" stroke-linecap="round"/></svg>',
    dag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><circle cx="5" cy="6" r="2"/><circle cx="19" cy="12" r="2"/><circle cx="5" cy="18" r="2"/><path d="M7 6h6a4 4 0 014 4M7 18h6a4 4 0 004-4" stroke-linecap="round"/></svg>',
    remover: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M5 7h14M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2m2 0v13a1 1 0 01-1 1H8a1 1 0 01-1-1V7h10z" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  };

  // ── KPIs ───────────────────────────────────────────────────────────────

  function carregarResumo() {
    api('GET', '/resumo?tipo_veiculo=' + encodeURIComponent(estado.tipoVeiculo)).then(function (r) {
      $('[data-kpi-marcas]').textContent = numero(r.total_marcas);
      $('[data-kpi-modelos]').textContent = numero(r.total_modelos);
      $('[data-kpi-fipe]').textContent = numero(r.total_registros_fipe);
      $('[data-kpi-cobertura]').textContent = (r.cobertura_fipe_pct || 0) + '%';
    }).catch(function (erro) {
      avisarPagina('Não foi possível carregar os indicadores: ' + erro.message);
    });
  }

  // ── Datalist de marcas conhecidas ────────────────────────────────────────
  // Só alimenta o <datalist> do campo "Marcas-alvo" da pipeline (autocomplete)
  // — não é mais um select navegável, isso virou o combobox unificado abaixo.

  function carregarMarcasCatalogo() {
    api('GET', '/marcas?tipo_veiculo=carros').then(function (r) {
      estado.marcasCatalogadas = r.marcas || [];
      atualizarDatalistMarcas();
    }).catch(function () {
      // cosmético (autocomplete) — falha aqui não deve impedir o resto da página.
    });
  }

  function atualizarDatalistMarcas() {
    var datalist = $('#pl-marcas-conhecidas');
    if (!datalist) return;
    datalist.innerHTML = estado.marcasCatalogadas.map(function (m) {
      return '<option value="' + esc(m.marca_nome) + '">';
    }).join('');
  }

  // ── Catálogo & busca de preço, unificados (mold: fipe.api.br #01/busca_de_preco.ts) ──
  // Marca/Modelo vêm ao vivo da FipeAPI (/api/gov/preco/*), sempre carros,
  // como combobox filtrável (lista inteira carregada de uma vez, filtrada
  // localmente conforme o usuário digita — sem round-trip por tecla). Ao
  // escolher o modelo, carrega tudo de uma vez (/preco/modelo): destaque do
  // ano mais recente (valor ao vivo) + tabela com todos os anos, já trazendo
  // o código FIPE dos anos que o catálogo (BigQuery) já tiver mapeado — sem
  // precisar de um passo extra pra "selecionar o ano".

  var estadoBusca = { marcaCodigo: '', modeloCodigo: '' };

  function formatarMoeda(valor) {
    if (valor === null || valor === undefined || valor === '') return '—';
    var numero = typeof valor === 'number' ? valor : parseFloat(
      String(valor).replace(/[^\d,.-]/g, '').replace(/\./g, '').replace(',', '.')
    );
    return isNaN(numero) ? String(valor) : numero.toLocaleString('pt-BR', {
      style: 'currency', currency: 'BRL'
    });
  }

  function avisarBusca(texto, tipo) {
    avisar($('[data-busca-aviso]'), texto, tipo);
  }

  function resetarResultadoBusca(mensagem) {
    $('[data-busca-resultado]').innerHTML = mensagem
      ? '<div class="vazio vazio--compacto">' + esc(mensagem) + '</div>' : '';
  }

  function mensagemErroBusca(erro) {
    if (erro && erro.motivo === 'missing_token') {
      return 'FIPE_API_TOKEN não configurado no servidor — só a busca de preço exige esse token.';
    }
    return erro.message;
  }

  /** Combobox filtrável: input de texto + lista suspensa filtrada localmente
   * a partir de uma lista já carregada (sem round-trip por tecla). */
  function criarCombobox(seletorRaiz, opcoes) {
    var raiz = $(seletorRaiz);
    var input = $('.combobox__input', raiz);
    var valorOculto = $('[data-combobox-valor]', raiz);
    var lista = $('[data-combobox-lista]', raiz);

    var itens = [];
    var filtrados = [];
    var ativo = -1;
    var selecionado = null;

    function render() {
      lista.innerHTML = filtrados.length
        ? filtrados.map(function (item, i) {
            return '<div class="combobox__opcao' + (i === ativo ? ' combobox__opcao--ativa' : '') +
              '" data-indice="' + i + '">' + esc(opcoes.getTexto(item)) + '</div>';
          }).join('')
        : '<div class="combobox__vazio">Nenhum resultado</div>';
      lista.hidden = false;
    }

    function filtrar(termo) {
      var alvo = (termo || '').trim().toLowerCase();
      filtrados = !alvo ? itens.slice() : itens.filter(function (item) {
        return opcoes.getTexto(item).toLowerCase().indexOf(alvo) !== -1;
      });
      ativo = filtrados.length ? 0 : -1;
    }

    function abrir() {
      if (input.disabled) return;
      filtrar(input.value);
      render();
    }

    function fechar() {
      lista.hidden = true;
      ativo = -1;
    }

    function selecionarItem(item) {
      selecionado = item;
      input.value = opcoes.getTexto(item);
      valorOculto.value = opcoes.getCodigo(item);
      fechar();
      if (opcoes.aoSelecionar) opcoes.aoSelecionar(item);
    }

    input.addEventListener('focus', abrir);
    input.addEventListener('click', abrir);

    input.addEventListener('input', function () {
      if (selecionado && input.value !== opcoes.getTexto(selecionado)) {
        selecionado = null;
        valorOculto.value = '';
      }
      filtrar(input.value);
      render();
    });

    input.addEventListener('keydown', function (evento) {
      if (lista.hidden && (evento.key === 'ArrowDown' || evento.key === 'ArrowUp')) {
        abrir();
        return;
      }
      if (evento.key === 'ArrowDown') {
        evento.preventDefault();
        ativo = Math.min(ativo + 1, filtrados.length - 1);
        render();
      } else if (evento.key === 'ArrowUp') {
        evento.preventDefault();
        ativo = Math.max(ativo - 1, 0);
        render();
      } else if (evento.key === 'Enter') {
        evento.preventDefault();
        if (ativo >= 0 && filtrados[ativo]) selecionarItem(filtrados[ativo]);
      } else if (evento.key === 'Escape') {
        fechar();
      }
    });

    lista.addEventListener('mousedown', function (evento) {
      var alvo = evento.target.closest('[data-indice]');
      if (!alvo) return;
      evento.preventDefault();
      var indice = Number(alvo.dataset.indice);
      if (filtrados[indice]) selecionarItem(filtrados[indice]);
    });

    input.addEventListener('blur', function () {
      setTimeout(function () {
        fechar();
        input.value = selecionado ? opcoes.getTexto(selecionado) : '';
        if (!selecionado) valorOculto.value = '';
      }, 120);
    });

    return {
      definirOpcoes: function (novosItens, mensagemVazia) {
        itens = novosItens || [];
        selecionado = null;
        input.value = '';
        valorOculto.value = '';
        input.disabled = !itens.length;
        input.placeholder = itens.length ? opcoes.placeholder : (mensagemVazia || 'Nenhuma opção');
        fechar();
      },
      desabilitar: function (mensagem) {
        itens = [];
        selecionado = null;
        input.value = '';
        valorOculto.value = '';
        input.disabled = true;
        input.placeholder = mensagem || '';
        fechar();
      },
      selecionarPorPredicado: function (predicado) {
        var achado = itens.filter(predicado)[0];
        if (achado) selecionarItem(achado);
      }
    };
  }

  var comboboxMarca = criarCombobox('[data-combobox-marca]', {
    getTexto: function (m) { return m.nome; },
    getCodigo: function (m) { return m.codigo; },
    placeholder: 'Selecione a marca…',
    aoSelecionar: function (m) { selecionarMarcaBusca(m.codigo); }
  });

  var comboboxModelo = criarCombobox('[data-combobox-modelo]', {
    getTexto: function (m) { return m.nome; },
    getCodigo: function (m) { return m.codigo; },
    placeholder: 'Selecione o modelo…',
    aoSelecionar: function (m) { selecionarModeloBusca(m.codigo); }
  });

  function resetarTabelaBusca(mensagem) {
    $('[data-busca-tabela]').innerHTML = '<div class="vazio vazio--compacto">' + esc(mensagem) + '</div>';
  }

  function carregarMarcasBusca() {
    comboboxMarca.desabilitar('Carregando…');
    comboboxModelo.desabilitar('Selecione a marca primeiro');
    resetarResultadoBusca('');
    resetarTabelaBusca('Escolha marca e modelo para ver o histórico de anos.');
    avisarBusca('');

    api('GET', '/preco/marcas').then(function (r) {
      comboboxMarca.definirOpcoes(r.marcas || [], 'Nenhuma marca encontrada');
      preSelecionarMarcaDaQuery();
    }).catch(function (erro) {
      comboboxMarca.desabilitar('Falha ao carregar');
      avisarBusca(mensagemErroBusca(erro), 'erro');
    });
  }

  // Contextualização: se a página abriu com ?marca=X, seleciona no combobox
  // (mesmo comportamento do antigo select do catálogo).
  function preSelecionarMarcaDaQuery() {
    var alvo = (window.GOV_MARCA_QUERY || '').trim().toLowerCase();
    if (!alvo || estadoBusca.marcaCodigo) return;

    comboboxMarca.selecionarPorPredicado(function (m) {
      return (m.nome || '').trim().toLowerCase() === alvo;
    });
  }

  function selecionarMarcaBusca(marcaCodigo) {
    estadoBusca.marcaCodigo = marcaCodigo;
    estadoBusca.modeloCodigo = '';
    resetarResultadoBusca('');
    resetarTabelaBusca('Escolha um modelo para ver o histórico de anos.');

    comboboxModelo.desabilitar('Carregando…');
    avisarBusca('');

    api('GET', '/preco/modelos?marca_codigo=' + encodeURIComponent(marcaCodigo)).then(function (r) {
      comboboxModelo.definirOpcoes(r.modelos || [], 'Nenhum modelo encontrado');
    }).catch(function (erro) {
      comboboxModelo.desabilitar('Falha ao carregar');
      avisarBusca(mensagemErroBusca(erro), 'erro');
    });
  }

  function selecionarModeloBusca(modeloCodigo) {
    estadoBusca.modeloCodigo = modeloCodigo;

    $('[data-busca-resultado]').innerHTML = '<div class="vazio vazio--compacto">Consultando…</div>';
    resetarTabelaBusca('Consultando…');
    avisarBusca('');

    api('GET', '/preco/modelo?marca_codigo=' + encodeURIComponent(estadoBusca.marcaCodigo) +
      '&modelo_codigo=' + encodeURIComponent(modeloCodigo)).then(function (r) {
      renderDestaqueBusca(r.atual);
      renderTabelaBusca(r.anos || [], r.atual);
    }).catch(function (erro) {
      resetarResultadoBusca(mensagemErroBusca(erro));
      resetarTabelaBusca(mensagemErroBusca(erro));
    });
  }

  function renderDestaqueBusca(atual) {
    if (!atual || atual.erro) {
      resetarResultadoBusca(atual ? mensagemErroBusca(atual)
        : 'Nenhum ano encontrado para este modelo.');
      return;
    }

    $('[data-busca-resultado]').innerHTML = '' +
      '<div class="busca-preco__resultado">' +
        '<div class="busca-preco__rotulo">valor de referência' +
          (atual.MesReferencia ? ' · ' + esc(atual.MesReferencia) : '') + '</div>' +
        '<div class="busca-preco__preco">' + esc(formatarMoeda(atual.Valor)) + '</div>' +
        '<p class="busca-preco__descricao">› ' + esc(atual.Marca) + ' ' + esc(atual.Modelo) +
          (atual.AnoModelo ? ' <span class="busca-preco__ano">(' + esc(atual.AnoModelo) + ')</span>' : '') +
        '</p>' +
        '<div class="busca-preco__meta">' +
          dado('código fipe', atual.CodigoFipe) +
          dado('combustível', atual.Combustivel) +
          dado('ano modelo', atual.AnoModelo) +
        '</div>' +
      '</div>';
  }

  function renderTabelaBusca(anos, atual) {
    if (!anos.length) {
      resetarTabelaBusca('Nenhum ano encontrado para este modelo.');
      return;
    }

    $('[data-busca-tabela]').innerHTML = '' +
      '<table class="tabela">' +
        '<thead><tr><th>Ano/combustível</th><th>Código FIPE</th><th>Valor</th><th>Catálogo atualizado em</th></tr></thead>' +
        '<tbody>' + anos.map(function (a, i) {
          var atualOk = i === 0 && atual && !atual.erro;
          var codigoFipe = atualOk ? atual.CodigoFipe : a.codigo_fipe;
          var valorCelula = atualOk
            ? esc(formatarMoeda(atual.Valor))
            : '<button type="button" class="btn btn--linha btn--pequeno" data-ver-valor>Ver valor</button>';

          return '<tr data-ano-linha data-ano-codigo="' + esc(a.codigo) + '">' +
            '<td>' + esc(a.nome) + '</td>' +
            '<td class="dado-mono" data-codigo-celula>' + (codigoFipe ? esc(codigoFipe) : '—') + '</td>' +
            '<td class="dado-mono" data-valor-celula>' + valorCelula + '</td>' +
            '<td class="dado-mono">' + (a.atualizado_em ? esc(dataHora(a.atualizado_em)) : '—') + '</td>' +
          '</tr>';
        }).join('') + '</tbody>' +
      '</table>';

    ligarEventosTabelaBusca();
  }

  function ligarEventosTabelaBusca() {
    $$('[data-ver-valor]').forEach(function (botao) {
      botao.addEventListener('click', function () {
        var linha = botao.closest('[data-ano-linha]');
        var anoCodigo = linha.dataset.anoCodigo;
        botao.disabled = true;
        botao.textContent = 'Consultando…';

        api('GET', '/preco/valor?marca_codigo=' + encodeURIComponent(estadoBusca.marcaCodigo) +
          '&modelo_codigo=' + encodeURIComponent(estadoBusca.modeloCodigo) +
          '&ano_codigo=' + encodeURIComponent(anoCodigo)).then(function (r) {
          $('[data-valor-celula]', linha).textContent = formatarMoeda(r.Valor);
          if (r.CodigoFipe) $('[data-codigo-celula]', linha).textContent = r.CodigoFipe;
        }).catch(function (erro) {
          botao.disabled = false;
          botao.textContent = 'Falhou — tentar de novo';
          avisarBusca(mensagemErroBusca(erro), 'erro');
        });
      });
    });
  }

  // ── Pipelines ──────────────────────────────────────────────────────────

  function carregarPipelines() {
    return api('GET', '/pipelines').then(function (r) {
      estado.pipelines = r.pipelines || [];
      renderPipelines();
    }).catch(function (erro) {
      $('[data-lista-pipelines]').innerHTML = '<div class="vazio"><strong>Não foi possível carregar</strong>' +
        esc(erro.message) + '</div>';
      $('[data-resumo-pipelines]').textContent = '';
    });
  }

  function renderPipelines() {
    var alvo = $('[data-lista-pipelines]');
    var ativas = estado.pipelines.filter(function (p) { return p.status === 'active'; }).length;

    $('[data-resumo-pipelines]').textContent = estado.pipelines.length
      ? estado.pipelines.length + ' pipeline(s) — ' + ativas + ' ativa(s).'
      : 'Nenhuma pipeline criada ainda.';

    if (!estado.pipelines.length) {
      alvo.innerHTML = '<div class="vazio"><strong>Nenhuma pipeline criada</strong>' +
        'Clique em "Nova pipeline" para agendar a extração do catálogo FIPE.</div>';
      return;
    }

    alvo.innerHTML = '<div class="lista-pipelines">' +
      estado.pipelines.map(cardPipeline).join('') + '</div>';

    ligarEventosPipelines();
  }

  function cardPipeline(p) {
    var ativa = p.status === 'active';

    var dagAviso = '';
    if (!p.dag_id) {
      dagAviso = '<span class="versao">DAG não gerada</span>';
    } else if (p.dag_version !== p.version) {
      dagAviso = '<span class="versao" title="A DAG foi gerada de uma versão anterior">' +
        'DAG v' + p.dag_version + ' desatualizada</span>';
    }

    var marcas = (p.source && p.source.marcas_alvo) || [];

    return '' +
    '<article class="pipeline-card" data-pipeline="' + esc(p.id) + '">' +
      '<div class="pipeline-card__topo">' +
        '<div>' +
          '<h3 class="pipeline-card__nome">' + esc(p.name) +
            '<span class="versao">v' + p.version + '</span>' +
            '<span class="selo ' + (ativa ? 'selo--positivo' : 'selo--neutro') + '">' +
              (ativa ? 'Ativa' : 'Pausada') + '</span>' +
            dagAviso +
          '</h3>' +
          (p.description
            ? '<p class="pipeline-card__descricao">' + esc(p.description) + '</p>' : '') +
          '<div class="pipeline-card__meta">' +
            dado('Veículo', (p.source && p.source.tipo_veiculo) || '—') +
            dado('Marcas-alvo', marcas.length ? marcas.join(', ') : '—') +
            dado('Agenda', p.schedule_legivel) +
            dado('Última execução', dataHora(p.last_run_at)) +
            dado('Resultado', ROTULO_STATUS[p.last_run_status] || '—') +
          '</div>' +
        '</div>' +
        '<div class="pipeline-card__acoes">' +
          '<button type="button" class="btn btn--primario btn--pequeno" data-executar>' +
            ICONES.play + 'Executar</button>' +
          '<button type="button" class="btn btn--linha btn--pequeno" data-alternar>' +
            (ativa ? ICONES.pausa + 'Pausar' : ICONES.play + 'Ativar') + '</button>' +
          '<button type="button" class="btn btn--linha btn--pequeno" data-gerar-dag>' +
            ICONES.dag + (p.dag_id ? 'Regerar DAG' : 'Gerar DAG') + '</button>' +
          '<button type="button" class="btn btn--linha btn--pequeno" data-remover>' +
            ICONES.remover + 'Remover</button>' +
        '</div>' +
      '</div>' +

      '<span class="aviso aviso--linha" data-aviso style="padding:0 16px 10px"></span>' +

      '<details class="dobra" data-dobra-runs>' +
        '<summary class="dobra__resumo">Histórico de execuções</summary>' +
        '<div class="dobra__corpo" data-runs>' +
          '<div class="vazio vazio--compacto">Abrindo…</div>' +
        '</div>' +
      '</details>' +

      '<details class="dobra" data-dobra-dag>' +
        '<summary class="dobra__resumo">Código da DAG</summary>' +
        '<div class="dobra__corpo" data-dag>' +
          '<div class="vazio vazio--compacto">Abrindo…</div>' +
        '</div>' +
      '</details>' +
    '</article>';
  }

  function dado(rotulo, valor) {
    return '<span class="dado-rotulado">' + esc(rotulo) +
      '<strong>' + esc(valor) + '</strong></span>';
  }

  function ligarEventosPipelines() {
    $$('[data-pipeline]').forEach(function (card) {
      var id = card.dataset.pipeline;
      var aviso = $('[data-aviso]', card);

      $('[data-executar]', card).addEventListener('click', function () {
        executarPipeline(id, card, aviso);
      });

      $('[data-alternar]', card).addEventListener('click', function () {
        var atual = estado.pipelines.filter(function (p) { return p.id === id; })[0];
        var novo = atual.status === 'active' ? 'paused' : 'active';

        api('POST', '/pipelines/' + id + '/status', { status: novo })
          .then(carregarPipelines)
          .catch(function (erro) { avisar(aviso, erro.message, 'erro'); });
      });

      $('[data-gerar-dag]', card).addEventListener('click', function () {
        avisar(aviso, 'Gerando DAG…');
        api('POST', '/pipelines/' + id + '/generate-dag').then(function (dag) {
          avisar(aviso, 'DAG "' + dag.dag_id + '" gravada em ' + dag.caminho + '.', 'ok');
          return carregarPipelines();
        }).catch(function (erro) {
          avisar(aviso, erro.message, 'erro');
        });
      });

      $('[data-remover]', card).addEventListener('click', function () {
        if (!confirm('Remover esta pipeline? O arquivo da DAG continua em airflow/dags/.')) return;

        api('DELETE', '/pipelines/' + id)
          .then(carregarPipelines)
          .catch(function (erro) { avisar(aviso, erro.message, 'erro'); });
      });

      $('[data-dobra-runs]', card).addEventListener('toggle', function (evento) {
        if (evento.target.open) carregarRuns(id, card);
      });

      $('[data-dobra-dag]', card).addEventListener('toggle', function (evento) {
        if (evento.target.open) carregarDag(id, card);
      });
    });
  }

  function executarPipeline(id, card, aviso) {
    var botao = $('[data-executar]', card);
    botao.disabled = true;
    avisar(aviso, 'Execução iniciada…');

    api('POST', '/pipelines/' + id + '/run', {}).then(function (resposta) {
      acompanharExecucao(resposta.run_id, id, card, aviso);
    }).catch(function (erro) {
      avisar(aviso, erro.message, 'erro');
      botao.disabled = false;
    });
  }

  function acompanharExecucao(runId, pipelineId, card, aviso) {
    if (estado.timers[runId]) clearInterval(estado.timers[runId]);

    function consultar() {
      api('GET', '/runs/' + runId).then(function (run) {
        var atual = (run.steps || []).filter(function (s) { return s.status === 'running'; })[0];
        var feitas = (run.steps || []).filter(function (s) { return s.status !== 'pending'; }).length;

        avisar(aviso, 'Executando (' + feitas + '/' + (run.steps || []).length + ')' +
          (atual ? ' — ' + atual.name : '') + '…');

        if (['success', 'partial', 'failed'].indexOf(run.status) !== -1) {
          clearInterval(estado.timers[runId]);
          delete estado.timers[runId];

          var falha = (run.steps || []).filter(function (s) { return s.error; })[0];
          avisar(aviso,
            'Execução ' + (ROTULO_STATUS[run.status] || run.status).toLowerCase() +
            (falha ? ' — ' + falha.name + ': ' + falha.error.message : '.'),
            run.status === 'success' ? 'ok' : (run.status === 'failed' ? 'erro' : ''));

          $('[data-executar]', card).disabled = false;

          var dobra = $('[data-dobra-runs]', card);
          if (dobra && dobra.open) carregarRuns(pipelineId, card);

          carregarPipelines();
          carregarResumo();
        }
      }).catch(function (erro) {
        clearInterval(estado.timers[runId]);
        delete estado.timers[runId];
        avisar(aviso, 'Falha ao acompanhar a execução: ' + erro.message, 'erro');
        $('[data-executar]', card).disabled = false;
      });
    }

    estado.timers[runId] = setInterval(consultar, INTERVALO_POLLING);
    consultar();
  }

  function carregarRuns(id, card) {
    var alvo = $('[data-runs]', card);

    api('GET', '/pipelines/' + id + '/runs').then(function (resposta) {
      var runs = resposta.runs || [];

      if (!runs.length) {
        alvo.innerHTML = '<div class="vazio vazio--compacto">' +
          'Nenhuma execução registrada para esta pipeline.</div>';
        return;
      }

      alvo.innerHTML = '' +
        '<table class="tabela-runs">' +
          '<thead><tr>' +
            '<th>Início</th><th>Status</th><th>Origem</th><th>Versão</th><th>Etapas</th>' +
          '</tr></thead><tbody>' +
          runs.map(function (r) {
            return '<tr>' +
              '<td class="mono">' + esc(dataHora(r.started_at)) + '</td>' +
              '<td><span class="selo ' + (SELO_STATUS[r.status] || 'selo--neutro') + '">' +
                esc(ROTULO_STATUS[r.status] || r.status) + '</span></td>' +
              '<td class="mono">' + esc(r.origem) + '</td>' +
              '<td class="mono">v' + esc(r.pipeline_version) + '</td>' +
              '<td><div class="etapas-inline">' + (r.steps || []).map(function (s) {
                return '<span class="etapa-ponto etapa-ponto--' + esc(s.status) +
                  '" title="' + esc(s.type + ': ' + (s.resumo || s.status)) + '"></span>';
              }).join('') + '</div></td>' +
            '</tr>';
          }).join('') +
          '</tbody></table>';
    }).catch(function (erro) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">' + esc(erro.message) + '</div>';
    });
  }

  function carregarDag(id, card) {
    var alvo = $('[data-dag]', card);

    api('GET', '/pipelines/' + id + '/dag').then(function (dag) {
      var situacao = dag.gravada
        ? (dag.desatualizada
            ? 'gravada da versão v' + dag.dag_version + ' — regere para v' + dag.pipeline_version
            : 'gravada · v' + dag.dag_version)
        : 'prévia (arquivo ainda não gravado)';

      alvo.innerHTML = '' +
        '<div class="dag-barra">' +
          '<span class="dag-caminho">' + esc(dag.caminho) + ' — ' + esc(situacao) + '</span>' +
          '<span class="dag-acoes">' +
            '<button type="button" class="btn btn--linha btn--pequeno" data-copiar>Copiar</button>' +
          '</span>' +
        '</div>' +
        '<pre class="codigo" data-codigo>' + realcar(dag.codigo) + '</pre>';

      $('[data-copiar]', alvo).addEventListener('click', function () {
        var botao = this;
        navigator.clipboard.writeText(dag.codigo).then(function () {
          botao.textContent = 'Copiado';
          setTimeout(function () { botao.textContent = 'Copiar'; }, 1600);
        }).catch(function () {
          botao.textContent = 'Falhou';
        });
      });
    }).catch(function (erro) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">' + esc(erro.message) + '</div>';
    });
  }

  /** Realce de sintaxe Python mínimo — mesma implementação da aba Pipelines. */
  var PALAVRAS = 'False|None|True|and|as|assert|async|await|break|class|continue|def|del|' +
    'elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|' +
    'raise|return|try|while|with|yield';

  var TOKENS = new RegExp(
    '(&quot;&quot;&quot;[\\s\\S]*?&quot;&quot;&quot;)' +
    '|(&quot;[^&\\n]*?&quot;|&#39;[^&\\n]*?&#39;)' +
    '|(^[ \\t]*#[^\\n]*)' +
    '|(&[a-zA-Z]+;|&#\\d+;)' +
    '|\\b(' + PALAVRAS + ')\\b' +
    '|\\b(\\d+)\\b',
    'gm'
  );

  function realcar(codigo) {
    return esc(codigo).replace(
      TOKENS,
      function (trecho, doc, str, comentario, entidade, palavra, numero) {
        if (entidade) return trecho;
        var classe = (doc || comentario) ? 'tk-com' : str ? 'tk-str' : palavra ? 'tk-kw' : 'tk-num';
        return '<span class="' + classe + '">' + trecho + '</span>';
      }
    );
  }

  // ── Modal: nova pipeline ───────────────────────────────────────────────

  var modal = $('[data-modal-pipeline]');

  $('[data-nova-pipeline]').addEventListener('click', function () {
    $('[data-modal-titulo]').textContent = 'Nova pipeline';
    $('[data-pl-nome]').value = '';
    $('[data-pl-desc]').value = '';
    $('[data-pl-tipo]').value = estado.tipoVeiculo;
    $('[data-pl-marcas]').value = window.GOV_MARCA_QUERY || '';
    $('[data-pl-agenda]').value = 'daily';
    $('[data-pl-hora]').value = '08:00';
    $('[data-pl-cron]').value = '';
    $('[data-campo-cron]').hidden = true;
    $('[data-campo-hora]').hidden = false;
    avisar($('[data-aviso-modal-pipeline]'), '');
    modal.hidden = false;
  });

  $('[data-fechar-modal-pipeline]').addEventListener('click', function () { modal.hidden = true; });

  modal.addEventListener('click', function (evento) {
    if (evento.target === modal) modal.hidden = true;
  });

  $('[data-pl-agenda]').addEventListener('change', function (evento) {
    var modo = evento.target.value;
    $('[data-campo-cron]').hidden = modo !== 'cron';
    $('[data-campo-hora]').hidden = ['daily', 'weekly', 'monthly'].indexOf(modo) === -1;
  });

  $('[data-form-pipeline]').addEventListener('submit', function (evento) {
    evento.preventDefault();

    var marcas = $('[data-pl-marcas]').value.split(',')
      .map(function (m) { return m.trim(); })
      .filter(Boolean);

    if (!marcas.length) {
      avisar($('[data-aviso-modal-pipeline]'), 'Informe ao menos uma marca-alvo.', 'erro');
      return;
    }

    var corpo = {
      name: $('[data-pl-nome]').value.trim(),
      description: $('[data-pl-desc]').value.trim(),
      source: {
        tipo_veiculo: $('[data-pl-tipo]').value,
        marcas_alvo: marcas
      },
      steps: [
        { type: 'marcas' },
        { type: 'modelos' },
        { type: 'fipe' },
        { type: 'referencia_meses' }
      ],
      schedule: {
        mode: $('[data-pl-agenda]').value,
        hora: $('[data-pl-hora]').value || '08:00',
        cron: $('[data-pl-cron]').value.trim() || null
      },
      status: 'active'
    };

    var botao = $('[data-confirmar-pipeline]');
    botao.disabled = true;
    avisar($('[data-aviso-modal-pipeline]'), 'Criando pipeline…');

    api('POST', '/pipelines', corpo).then(function (pipeline) {
      return api('POST', '/pipelines/' + pipeline.id + '/generate-dag').then(function (dag) {
        avisar($('[data-aviso-modal-pipeline]'),
          'Pipeline criada e DAG "' + dag.dag_id + '" gerada.', 'ok');
        setTimeout(function () {
          modal.hidden = true;
          carregarPipelines();
        }, 900);
      });
    }).catch(function (erro) {
      avisar($('[data-aviso-modal-pipeline]'), erro.message, 'erro');
    }).finally(function () {
      botao.disabled = false;
    });
  });

  // ── Inicialização ──────────────────────────────────────────────────────

  $('[data-recarregar-pipelines]').addEventListener('click', carregarPipelines);

  carregarResumo();
  carregarMarcasCatalogo();
  carregarMarcasBusca();
  carregarPipelines();
})();
