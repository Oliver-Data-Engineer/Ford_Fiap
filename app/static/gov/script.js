/* ============================================================
   GOV MODE — catálogo FIPE (marca/modelo/ano) e pipelines de extração.
   O JavaScript não executa regra de negócio: só coleta entrada, chama
   /api/gov/* e desenha o que o backend devolve.
   ============================================================ */
(function () {
  'use strict';

  var pagina = document.querySelector('[data-tipo-veiculo]');
  if (!pagina) return;

  var estado = {
    tipoVeiculo: 'carros',
    marcaCodigo: '',
    modeloCodigo: '',
    marcasCatalogadas: [],   // [{marca_codigo, marca_nome, total_modelos}]
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
          throw new Error(detalhe.erro || detalhe.message || json.erro || ('Erro ' + r.status));
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

  // ── Catálogo ───────────────────────────────────────────────────────────

  function carregarMarcas() {
    var select = $('[data-cat-marca]');
    select.innerHTML = '<option value="">Carregando…</option>';
    select.disabled = true;
    resetarModelos('Selecione a marca primeiro');
    resetarFipe('Escolha marca e modelo para listar os anos e códigos FIPE.');

    api('GET', '/marcas?tipo_veiculo=' + encodeURIComponent(estado.tipoVeiculo)).then(function (r) {
      estado.marcasCatalogadas = r.marcas || [];
      select.disabled = false;

      if (!estado.marcasCatalogadas.length) {
        select.innerHTML = '<option value="">Nenhuma marca catalogada ainda</option>';
        atualizarDatalistMarcas();
        return;
      }

      select.innerHTML = '<option value="">Selecione…</option>' +
        estado.marcasCatalogadas.map(function (m) {
          return '<option value="' + esc(m.marca_codigo) + '">' + esc(m.marca_nome) +
            ' (' + numero(m.total_modelos) + ' modelo(s))</option>';
        }).join('');

      atualizarDatalistMarcas();
      preSelecionarMarcaDaQuery();
    }).catch(function (erro) {
      select.innerHTML = '<option value="">Falha ao carregar</option>';
      avisarPagina('Não foi possível carregar as marcas: ' + erro.message);
    });
  }

  function atualizarDatalistMarcas() {
    var datalist = $('#pl-marcas-conhecidas');
    if (!datalist) return;
    datalist.innerHTML = estado.marcasCatalogadas.map(function (m) {
      return '<option value="' + esc(m.marca_nome) + '">';
    }).join('');
  }

  // Contextualização: se a página abriu com ?marca=X e X bate (case-insensitive)
  // com uma marca_nome já catalogada, pré-seleciona o filtro — cosmético, sem
  // acoplar a identidade FIPE (numérica) com a do specs_api (nomes de scraping).
  function preSelecionarMarcaDaQuery() {
    var alvo = (window.GOV_MARCA_QUERY || '').trim().toLowerCase();
    if (!alvo || estado.marcaCodigo) return;

    var achada = estado.marcasCatalogadas.filter(function (m) {
      return (m.marca_nome || '').trim().toLowerCase() === alvo;
    })[0];

    if (achada) {
      $('[data-cat-marca]').value = achada.marca_codigo;
      selecionarMarca(achada.marca_codigo);
    }
  }

  function resetarModelos(mensagem) {
    var select = $('[data-cat-modelo]');
    select.innerHTML = '<option value="">' + esc(mensagem) + '</option>';
    select.disabled = true;
    estado.modeloCodigo = '';
  }

  function resetarFipe(mensagem) {
    $('[data-tabela-fipe]').innerHTML = '<div class="vazio vazio--compacto">' + esc(mensagem) + '</div>';
    $('[data-resumo-catalogo]').textContent = 'Selecione marca e modelo para ver os anos disponíveis.';
  }

  function selecionarMarca(marcaCodigo) {
    estado.marcaCodigo = marcaCodigo;
    resetarFipe('Escolha um modelo para listar os anos e códigos FIPE.');

    if (!marcaCodigo) {
      resetarModelos('Selecione a marca primeiro');
      return;
    }

    var select = $('[data-cat-modelo]');
    select.innerHTML = '<option value="">Carregando…</option>';
    select.disabled = true;

    api('GET', '/modelos?marca_codigo=' + encodeURIComponent(marcaCodigo) +
      '&tipo_veiculo=' + encodeURIComponent(estado.tipoVeiculo)).then(function (r) {
      var modelos = r.modelos || [];
      select.disabled = false;

      if (!modelos.length) {
        select.innerHTML = '<option value="">Nenhum modelo catalogado ainda</option>';
        return;
      }

      select.innerHTML = '<option value="">Selecione…</option>' +
        modelos.map(function (m) {
          return '<option value="' + esc(m.modelo_codigo) + '">' + esc(m.modelo_nome) +
            ' (' + numero(m.total_anos) + ' ano(s))</option>';
        }).join('');
    }).catch(function (erro) {
      select.innerHTML = '<option value="">Falha ao carregar</option>';
      avisarPagina('Não foi possível carregar os modelos: ' + erro.message);
    });
  }

  function selecionarModelo(modeloCodigo) {
    estado.modeloCodigo = modeloCodigo;

    if (!modeloCodigo) {
      resetarFipe('Escolha um modelo para listar os anos e códigos FIPE.');
      return;
    }

    $('[data-tabela-fipe]').innerHTML = '<div class="vazio vazio--compacto">Carregando…</div>';

    api('GET', '/fipe?marca_codigo=' + encodeURIComponent(estado.marcaCodigo) +
      '&modelo_codigo=' + encodeURIComponent(modeloCodigo) +
      '&tipo_veiculo=' + encodeURIComponent(estado.tipoVeiculo)).then(function (r) {
      var anos = r.anos || [];

      if (!anos.length) {
        $('[data-tabela-fipe]').innerHTML = '<div class="vazio vazio--compacto">' +
          'Nenhum ano com código FIPE resolvido para este modelo ainda.</div>';
        $('[data-resumo-catalogo]').textContent = 'Nenhum ano catalogado.';
        return;
      }

      $('[data-resumo-catalogo]').textContent = anos.length + ' ano(s) com código FIPE.';
      $('[data-tabela-fipe]').innerHTML = '' +
        '<table class="tabela">' +
          '<thead><tr><th>Ano/combustível</th><th>Código FIPE</th><th>Atualizado em</th></tr></thead>' +
          '<tbody>' + anos.map(function (a) {
            return '<tr>' +
              '<td>' + esc(a.ano_modelo || a.ano_codigo) + '</td>' +
              '<td class="dado-mono">' + esc(a.codigo_fipe) + '</td>' +
              '<td class="dado-mono">' + esc(dataHora(a.atualizado_em)) + '</td>' +
            '</tr>';
          }).join('') + '</tbody>' +
        '</table>';
    }).catch(function (erro) {
      $('[data-tabela-fipe]').innerHTML = '<div class="vazio vazio--compacto">' + esc(erro.message) + '</div>';
    });
  }

  $('[data-tipo-veiculo]').addEventListener('change', function (evento) {
    estado.tipoVeiculo = evento.target.value;
    $('[data-pl-tipo]').value = estado.tipoVeiculo;
    carregarResumo();
    carregarMarcas();
  });

  $('[data-cat-marca]').addEventListener('change', function (evento) {
    selecionarMarca(evento.target.value);
  });

  $('[data-cat-modelo]').addEventListener('change', function (evento) {
    selecionarModelo(evento.target.value);
  });

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
  carregarMarcas();
  carregarPipelines();
})();
