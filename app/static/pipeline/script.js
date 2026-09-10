/* ============================================================
   ABA PIPELINES
   Lista as pipelines salvas, dispara execuções, mostra o histórico
   e o código da DAG gerada. Toda a lógica fica no backend.
   ============================================================ */
(function () {
  'use strict';

  var lista = document.querySelector('[data-lista-pipelines]');
  if (!lista) return;

  var estado = { pipelines: [], timers: {} };
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

  function api(metodo, rota, corpo) {
    var opcoes = { method: metodo, headers: { 'Content-Type': 'application/json' } };
    if (corpo !== undefined) opcoes.body = JSON.stringify(corpo);

    return fetch('/api' + rota, opcoes).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (json) {
        if (!r.ok) {
          var detalhe = json.detail || json;
          throw new Error(detalhe.erro || detalhe.message || json.erro || ('Erro ' + r.status));
        }
        return json;
      });
    });
  }

  function dataHora(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString('pt-BR', {
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'
    });
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

  function avisar(elemento, texto, tipo) {
    if (!elemento) return;
    elemento.textContent = texto || '';
    elemento.className = 'aviso aviso--linha' + (tipo ? ' aviso--' + tipo : '');
  }

  // ── Carga ──────────────────────────────────────────────────────────────

  function carregar() {
    return api('GET', '/pipelines').then(function (resposta) {
      estado.pipelines = resposta.pipelines || [];
      render();
    }).catch(function (erro) {
      lista.innerHTML = '<div class="vazio"><strong>Não foi possível carregar</strong>' +
        esc(erro.message) + '</div>';
      $('[data-resumo-pipelines]').textContent = '';
    });
  }

  function render() {
    var ativas = estado.pipelines.filter(function (p) { return p.status === 'active'; }).length;

    $('[data-resumo-pipelines]').textContent = estado.pipelines.length
      ? estado.pipelines.length + ' pipeline(s) — ' + ativas + ' ativa(s).'
      : 'Nenhuma pipeline criada ainda.';

    if (!estado.pipelines.length) {
      lista.innerHTML = '<div class="vazio"><strong>Nenhuma pipeline criada</strong>' +
        'Configure uma análise no Playground e clique em “Gerar pipeline”.</div>';
      return;
    }

    lista.innerHTML = '<div class="lista-pipelines">' +
      estado.pipelines.map(cardPipeline).join('') + '</div>';

    ligarEventos();
  }

  function cardPipeline(p) {
    var ativa = p.status === 'active';
    var agentes = (p.steps || []).filter(function (s) { return s.type === 'agent'; });

    var dagAviso = '';
    if (!p.dag_id) {
      dagAviso = '<span class="versao">DAG não gerada</span>';
    } else if (p.dag_version !== p.version) {
      dagAviso = '<span class="versao" title="A DAG foi gerada de uma versão anterior">' +
        'DAG v' + p.dag_version + ' desatualizada</span>';
    }

    var fonteFixa = !!(p.source && p.source.fonte_fixa);
    var badgeFonte = '<span class="selo selo--neutro" title="' +
      (fonteFixa
        ? 'Sempre reprocessa os mesmos vídeos fixados'
        : 'Faz uma busca nova a cada execução') + '">' +
      (fonteFixa ? 'Fonte fixa' : 'Fonte dinâmica') + '</span>';

    return '' +
    '<article class="pipeline-card" data-pipeline="' + esc(p.id) + '">' +
      '<div class="pipeline-card__topo">' +
        '<div>' +
          '<h3 class="pipeline-card__nome">' + esc(p.name) +
            '<span class="versao">v' + p.version + '</span>' +
            '<span class="selo ' + (ativa ? 'selo--positivo' : 'selo--neutro') + '">' +
              (ativa ? 'Ativa' : 'Pausada') + '</span>' +
            badgeFonte +
            dagAviso +
          '</h3>' +
          (p.description
            ? '<p class="pipeline-card__descricao">' + esc(p.description) + '</p>' : '') +
          '<div class="pipeline-card__meta">' +
            dado('Agenda', p.schedule_legivel) +
            dado('Cron', p.cron || '—') +
            dado('Última execução', dataHora(p.last_run_at)) +
            dado('Resultado', ROTULO_STATUS[p.last_run_status] || '—') +
            dado('Agente', p.llm.provider + (p.llm.model ? ' / ' + p.llm.model : '')) +
            dado('Análises', agentes.length + ' prompt(s)') +
          '</div>' +
        '</div>' +
        '<div></div>' +
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

  // ── Eventos ────────────────────────────────────────────────────────────

  function ligarEventos() {
    $$('[data-pipeline]').forEach(function (card) {
      var id = card.dataset.pipeline;
      var aviso = $('[data-aviso]', card);

      $('[data-executar]', card).addEventListener('click', function () {
        executar(id, card, aviso);
      });

      $('[data-alternar]', card).addEventListener('click', function () {
        var atual = estado.pipelines.filter(function (p) { return p.id === id; })[0];
        var novo = atual.status === 'active' ? 'paused' : 'active';

        api('POST', '/pipelines/' + id + '/status', { status: novo })
          .then(carregar)
          .catch(function (erro) { avisar(aviso, erro.message, 'erro'); });
      });

      $('[data-gerar-dag]', card).addEventListener('click', function () {
        avisar(aviso, 'Gerando DAG…');
        api('POST', '/pipelines/' + id + '/generate-dag').then(function (dag) {
          avisar(aviso, 'DAG "' + dag.dag_id + '" gravada em ' + dag.caminho + '.', 'ok');
          return carregar();
        }).catch(function (erro) {
          avisar(aviso, erro.message, 'erro');
        });
      });

      $('[data-remover]', card).addEventListener('click', function () {
        if (!confirm('Remover esta pipeline? O arquivo da DAG continua em airflow/dags/.')) return;

        api('DELETE', '/pipelines/' + id)
          .then(carregar)
          .catch(function (erro) { avisar(aviso, erro.message, 'erro'); });
      });

      // Carrega sob demanda: só ao abrir a dobra
      $('[data-dobra-runs]', card).addEventListener('toggle', function (evento) {
        if (evento.target.open) carregarRuns(id, card);
      });

      $('[data-dobra-dag]', card).addEventListener('toggle', function (evento) {
        if (evento.target.open) carregarDag(id, card);
      });
    });
  }

  // ── Execução manual ────────────────────────────────────────────────────

  function executar(id, card, aviso) {
    var botao = $('[data-executar]', card);
    botao.disabled = true;
    avisar(aviso, 'Execução iniciada…');

    api('POST', '/pipelines/' + id + '/run', {}).then(function (resposta) {
      acompanhar(resposta.run_id, id, card, aviso);
    }).catch(function (erro) {
      avisar(aviso, erro.message, 'erro');
      botao.disabled = false;
    });
  }

  function acompanhar(runId, pipelineId, card, aviso) {
    if (estado.timers[runId]) clearInterval(estado.timers[runId]);

    function consultar() {
      api('GET', '/pipelines/runs/' + runId + '?incluir_dados=false').then(function (run) {
        var atual = (run.steps || []).filter(function (s) { return s.status === 'running'; })[0];
        var feitas = (run.steps || []).filter(function (s) {
          return s.status !== 'pending';
        }).length;

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

          // Reabre o histórico já atualizado, se estiver visível
          var dobra = $('[data-dobra-runs]', card);
          if (dobra && dobra.open) carregarRuns(pipelineId, card);
          carregar();
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

  // ── Histórico ──────────────────────────────────────────────────────────

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
            '<th>Início</th><th>Status</th><th>Origem</th><th>Versão</th>' +
            '<th>Vídeos</th><th>Insights</th><th>Tokens</th><th>Etapas</th>' +
          '</tr></thead><tbody>' +
          runs.map(function (r) {
            return '<tr>' +
              '<td class="mono">' + esc(dataHora(r.started_at)) + '</td>' +
              '<td><span class="selo ' + (SELO_STATUS[r.status] || 'selo--neutro') + '">' +
                esc(ROTULO_STATUS[r.status] || r.status) + '</span></td>' +
              '<td class="mono">' + esc(r.origem) + '</td>' +
              '<td class="mono">v' + esc(r.pipeline_version) + '</td>' +
              '<td class="mono">' + esc(r.total_videos) + '</td>' +
              '<td class="mono">' + esc(r.total_insights) + '</td>' +
              '<td class="mono">' + esc((r.usage && r.usage.total_tokens) || '—') + '</td>' +
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

  // ── Código da DAG ──────────────────────────────────────────────────────

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
            '<button type="button" class="btn btn--linha btn--pequeno" data-baixar>Baixar</button>' +
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

      $('[data-baixar]', alvo).addEventListener('click', function () {
        var blob = new Blob([dag.codigo], { type: 'text/x-python' });
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = dag.dag_id + '.py';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(link.href);
      });
    }).catch(function (erro) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">' + esc(erro.message) + '</div>';
    });
  }

  /**
   * Realce de sintaxe Python mínimo.
   *
   * O código é escapado primeiro e depois percorrido em UMA passada só. Isso
   * é essencial: encadear vários .replace() faria as regras seguintes casarem
   * dentro do markup já inserido — `class` é palavra reservada em Python e
   * também o atributo dos spans, então a segunda passada quebraria as tags.
   *
   * A ordem das alternativas é a precedência dos tokens. Entidades HTML
   * (`&quot;`, `&#39;`) entram como passagem livre para que a regra de número
   * não parta um `&#39;` ao meio.
   */
  var PALAVRAS = 'False|None|True|and|as|assert|async|await|break|class|continue|def|del|' +
    'elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|' +
    'raise|return|try|while|with|yield';

  var TOKENS = new RegExp(
    '(&quot;&quot;&quot;[\\s\\S]*?&quot;&quot;&quot;)' +          // 1 docstring
    '|(&quot;[^&\\n]*?&quot;|&#39;[^&\\n]*?&#39;)' +              // 2 string
    '|(^[ \\t]*#[^\\n]*)' +                                       // 3 comentário
    '|(&[a-zA-Z]+;|&#\\d+;)' +                                    // 4 entidade (passa direto)
    '|\\b(' + PALAVRAS + ')\\b' +                                 // 5 palavra-chave
    '|\\b(\\d+)\\b',                                              // 6 número
    'gm'
  );

  function realcar(codigo) {
    return esc(codigo).replace(
      TOKENS,
      function (trecho, doc, str, comentario, entidade, palavra, numero) {
        if (entidade) return trecho;               // preserva a entidade intacta

        var classe = (doc || comentario) ? 'tk-com'
                   : str ? 'tk-str'
                   : palavra ? 'tk-kw'
                   : 'tk-num';

        return '<span class="' + classe + '">' + trecho + '</span>';
      }
    );
  }

  // ── Inicialização ──────────────────────────────────────────────────────

  document.querySelector('[data-recarregar]').addEventListener('click', carregar);
  carregar();
})();
