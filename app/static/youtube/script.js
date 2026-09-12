/* ============================================================
   YOUTUBE PLAYGROUND
   O JavaScript não executa regra de negócio (Regra 2): só coleta
   entrada, chama /api/* e desenha o que o backend devolve.
   ============================================================ */
(function () {
  'use strict';

  var heroForm = document.querySelector('[data-form-hero]');
  if (!heroForm) return;

  // ── Estado da página ───────────────────────────────────────────────────
  var estado = {
    modoOrigem: 'busca',      // 'busca' | 'url'
    videos: [],                // cards carregados (busca + URL), ordem original de chegada
    selecionados: {},          // video_id → true
    ordemGaleria: 'relevancia',
    prompts: [],
    promptsAtivos: {},         // prompt_id → true
    editando: null,            // prompt_id em edição
    runId: null,
    run: null,
    timer: null,
    verJson: false,
    camada: 'raw',
    modoVisual: 'cartoes',     // 'cartoes' | 'tabela'
    tabelas: null,               // cache de GET /pipelines/runs/{id}/tables
    visualizacaoPrompts: 'expandido'   // 'expandido' | 'compacto'
  };

  // ── Persistência local (sobrevive a trocar de aba/página) ───────────────
  // Guarda só o suficiente pra reconstruir a tela sem perder o que o
  // usuário já tinha feito: vídeos/seleção/prompts marcados e a execução em
  // andamento ou concluída. Sem TTL — o pedido é só "não perder ao navegar
  // pra outra aba", não um histórico duradouro.
  var CHAVE_CACHE = 'ford_fiap_youtube_playground';

  function lerCache() {
    try {
      var bruto = localStorage.getItem(CHAVE_CACHE);
      return bruto ? JSON.parse(bruto) : null;
    } catch (e) {
      return null; // localStorage indisponível (modo privado, quota, etc.)
    }
  }

  function salvarCache() {
    try {
      localStorage.setItem(CHAVE_CACHE, JSON.stringify({
        runId: estado.runId,
        videos: estado.videos,
        selecionados: estado.selecionados,
        promptsAtivos: estado.promptsAtivos
      }));
    } catch (e) {
      // localStorage indisponível — a página continua funcionando sem cache
    }
  }

  var INTERVALO_POLLING = 1500;

  var ROTULO_ENTRADA = {
    transcript: { icone: '📝', rotulo: 'Transcrição',
      ajuda: 'Roda uma vez por vídeo, recebendo apenas a Tabela de Transcrições.' },
    comments: { icone: '💬', rotulo: 'Comentários',
      ajuda: 'Roda uma vez por vídeo, recebendo a Tabela de Comentários extraídos.' },
    video: { icone: '🎬', rotulo: 'Vídeo completo',
      ajuda: 'Roda uma vez por vídeo, recebendo transcrição e comentários juntos.' },
    dataset: { icone: '📦', rotulo: 'Conjunto inteiro',
      ajuda: 'Roda uma única vez, recebendo a Tabela de Vídeos completa selecionada.' }
  };

  // ── Utilidades ─────────────────────────────────────────────────────────

  function $(seletor, raiz) { return (raiz || document).querySelector(seletor); }
  function $$(seletor, raiz) { return Array.prototype.slice.call((raiz || document).querySelectorAll(seletor)); }

  /** Escapa texto vindo da API antes de interpolar em innerHTML. */
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

  function data(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleDateString('pt-BR');
  }

  function duracao(segundos) {
    if (!segundos && segundos !== 0) return '—';
    var m = Math.floor(segundos / 60);
    var s = segundos % 60;
    return m + ':' + String(s).padStart(2, '0');
  }

  /** Chamada ao backend. Erros da API viram Error com a mensagem do servidor. */
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

  function avisar(elemento, texto, tipo) {
    if (!elemento) return;
    elemento.textContent = texto || '';
    elemento.className = 'aviso' + (tipo ? ' aviso--' + tipo : '');
  }

  function numeroOuNulo(campo) {
    var valor = campo && campo.value.trim();
    return valor === '' || valor === null || valor === undefined ? null : Number(valor);
  }

  function iconeMetrica(tipo) {
    var icones = {
      views: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-6.5 10-6.5S22 12 22 12s-3.5 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/></svg>',
      likes: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M7 10v10H4V10h3zm0 0l4.2-6.6a1.4 1.4 0 012.6 1l-1 5.6h5.5a1.9 1.9 0 011.8 2.5l-1.9 6.6a1.9 1.9 0 01-1.8 1.4H7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      comentarios: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5.5h16v11H8.5l-3.8 3.3v-3.3H4z" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    };
    return icones[tipo] || '';
  }

  // ── 0. Configuração do backend ─────────────────────────────────────────

  function carregarConfig() {
    return api('GET', '/youtube/config').then(function (cfg) {
      pintarStatus($('[data-status-youtube]'), 'YouTube API', cfg.youtube.configurado,
        cfg.youtube.env_var + ' ausente no .env');

      var opcoes = cfg.llm.providers.map(function (p) {
        return '<option value="' + esc(p.provider) + '"' +
          (p.provider === cfg.llm.provider_padrao ? ' selected' : '') + '>' +
          esc(p.provider) + (p.configurado ? '' : ' (sem chave)') + '</option>';
      }).join('');

      $$('[data-llm-provider]').forEach(function (sel) { sel.innerHTML = opcoes; });
      $$('[data-llm-provider]')[0].dataset.info = JSON.stringify(cfg.llm.providers);
      $$('[data-llm-provider]')[0].dataset.padrao = JSON.stringify({
        provider: cfg.llm.provider_padrao, model: cfg.llm.model_padrao
      });

      atualizarStatusLLM();
      $('[data-llm-provider]').addEventListener('change', atualizarStatusLLM);

      // Modelo do .env (LLM_MODEL) pré-preenchido no campo — só na carga
      // inicial e só quando o provider selecionado é o mesmo do .env;
      // trocar de provider limpa o campo e volta a usar o padrão dele.
      if (cfg.llm.model_padrao && $('[data-llm-provider]').value === cfg.llm.provider_padrao) {
        $$('[data-llm-model]').forEach(function (el) { if (!el.value) el.value = cfg.llm.model_padrao; });
      }

      return cfg;
    }).catch(function (erro) {
      pintarStatus($('[data-status-youtube]'), 'Backend', false, erro.message);
    });
  }

  function atualizarStatusLLM() {
    var seletor = $('[data-llm-provider]');
    var lista = JSON.parse(seletor.dataset.info || '[]');
    var atual = lista.filter(function (p) { return p.provider === seletor.value; })[0];
    if (!atual) return;

    $$('[data-llm-model]').forEach(function (el) { el.placeholder = atual.modelo_padrao || 'padrão do provider'; });
    pintarStatus($('[data-status-llm]'), 'LLM: ' + atual.provider, atual.configurado,
      atual.env_var + ' ausente no .env');
  }

  function pintarStatus(alvo, rotulo, ok, dicaFalha) {
    if (!alvo) return;
    alvo.innerHTML = '<span class="selo ' + (ok ? 'selo--positivo' : 'selo--negativo') +
      '" title="' + esc(ok ? 'configurado' : dicaFalha) + '">' + esc(rotulo) + '</span>';
  }

  // ── 0.1 Modal de configurações (engrenagem) ─────────────────────────────

  var modalConfig = $('[data-modal-config]');

  $('[data-abrir-config]').addEventListener('click', function () { modalConfig.hidden = false; });
  $$('[data-fechar-config]').forEach(function (b) {
    b.addEventListener('click', function () { modalConfig.hidden = true; });
  });
  modalConfig.addEventListener('click', function (evento) {
    if (evento.target === modalConfig) modalConfig.hidden = true;
  });

  // ── 1. Modo de origem (busca / url) embutido na busca hero ─────────────

  function isoDe(valorData, hora) {
    return valorData ? valorData + 'T' + hora + 'Z' : null;
  }

  function minutosEmSegundos(campo) {
    var minutos = numeroOuNulo(campo);
    return minutos === null ? null : minutos * 60;
  }

  var heroInput = $('[data-hero-input]');
  var heroLimpar = $('[data-hero-limpar]');
  var heroBotao = $('[data-botao-buscar]');
  var filtrosDetails = $('[data-filtros]');

  var PLACEHOLDER_BUSCA = heroInput.placeholder;
  var PLACEHOLDER_URL = 'Cole a URL ou o ID do vídeo';

  $$('[data-modo]').forEach(function (botao) {
    botao.addEventListener('click', function () {
      estado.modoOrigem = botao.dataset.modo;

      $$('[data-modo]').forEach(function (b) {
        var ativo = b === botao;
        b.classList.toggle('is-ativa', ativo);
        b.setAttribute('aria-checked', String(ativo));
      });

      if (estado.modoOrigem === 'url') {
        heroInput.placeholder = PLACEHOLDER_URL;
        heroBotao.textContent = 'Adicionar vídeo';
        filtrosDetails.hidden = true;
      } else {
        heroInput.placeholder = PLACEHOLDER_BUSCA;
        heroBotao.textContent = 'Pesquisar';
        filtrosDetails.hidden = false;
      }

      heroInput.focus();
    });
  });

  heroInput.addEventListener('input', function () {
    heroLimpar.hidden = !heroInput.value;
  });

  heroLimpar.addEventListener('click', function () {
    heroInput.value = '';
    heroLimpar.hidden = true;
    heroInput.focus();
  });

  function filtrosDaTela() {
    return {
      max_results: numeroOuNulo($('[data-f-max]')) || 10,
      order: $('[data-f-ordem]').value,
      meses_atras: numeroOuNulo($('[data-f-meses]')),
      published_after: isoDe($('[data-f-inicio]').value, '00:00:00'),
      published_before: isoDe($('[data-f-fim]').value, '23:59:59'),
      channel_id: $('[data-f-canal]').value.trim() || null,
      relevance_language: $('[data-f-idioma]').value || null,
      video_duration: $('[data-f-duracao]').value || null,
      min_views: numeroOuNulo($('[data-f-views]')),
      min_likes: numeroOuNulo($('[data-f-likes]')),
      min_comments: numeroOuNulo($('[data-f-coments]')),
      duracao_min_segundos: minutosEmSegundos($('[data-f-dur-min]'))
    };
  }

  heroForm.addEventListener('submit', function (evento) {
    evento.preventDefault();
    if (estado.modoOrigem === 'url') {
      adicionarPorUrl(heroInput.value.trim());
    } else {
      buscarVideos(heroInput.value.trim());
    }
  });

  function dedup(novos, existentes) {
    var vistos = {};
    existentes.forEach(function (v) { vistos[v.video_id] = true; });
    return novos.filter(function (v) {
      if (vistos[v.video_id]) return false;
      vistos[v.video_id] = true;
      return true;
    });
  }

  function buscarVideos(query) {
    if (!query) return;

    heroBotao.disabled = true;
    $('[data-lista-videos]').innerHTML = '<div class="vazio vazio--compacto">Buscando vídeos…</div>';

    var corpo = filtrosDaTela();
    corpo.query = query;

    api('POST', '/youtube/search', corpo).then(function (resposta) {
      var manuais = estado.videos.filter(function (v) { return v._origem === 'url'; });
      var novos = resposta.videos.map(function (v) { v._origem = 'busca'; return v; });

      estado.videos = manuais.concat(dedup(novos, manuais));
      novos.forEach(function (v) { estado.selecionados[v.video_id] = true; });

      renderVideos();
      renderDescartados(resposta.descartados);
    }).catch(function (erro) {
      $('[data-lista-videos]').innerHTML =
        '<div class="vazio vazio--compacto"><strong>Falha na busca</strong>' + esc(erro.message) + '</div>';
    }).finally(function () {
      heroBotao.disabled = false;
    });
  }

  function adicionarPorUrl(url) {
    if (!url) return;

    heroBotao.disabled = true;

    api('POST', '/youtube/video', { url: url }).then(function (resposta) {
      var video = resposta.video;
      video._origem = 'url';

      var jaExiste = estado.videos.some(function (v) { return v.video_id === video.video_id; });
      if (!jaExiste) estado.videos.unshift(video);

      estado.selecionados[video.video_id] = true;
      heroInput.value = '';
      heroLimpar.hidden = true;
      renderVideos();
    }).catch(function (erro) {
      alert('Não foi possível adicionar o vídeo: ' + erro.message);
    }).finally(function () {
      heroBotao.disabled = false;
    });
  }

  // ── 2. Galeria de vídeos ────────────────────────────────────────────────

  var ORDENADORES = {
    relevancia: null,
    views: function (a, b) { return (b.views || 0) - (a.views || 0); },
    likes: function (a, b) { return (b.likes || 0) - (a.likes || 0); },
    comentarios: function (a, b) { return (b.comentarios || 0) - (a.comentarios || 0); },
    recentes: function (a, b) { return new Date(b.publicado_em || 0) - new Date(a.publicado_em || 0); },
    duracao: function (a, b) { return (b.duracao_segundos || 0) - (a.duracao_segundos || 0); }
  };

  $('[data-ordenar-galeria]').addEventListener('change', function (evento) {
    estado.ordemGaleria = evento.target.value;
    renderVideos();
  });

  function renderVideos() {
    var alvo = $('[data-lista-videos]');

    if (!estado.videos.length) {
      alvo.innerHTML = '<div class="vazio"><strong>Nenhum vídeo carregado</strong>' +
        'Pesquise acima ou cole a URL de um vídeo para começar.</div>';
      atualizarResumo();
      return;
    }

    var ordenados = estado.videos.slice();
    var comparador = ORDENADORES[estado.ordemGaleria];
    if (comparador) ordenados.sort(comparador);

    alvo.innerHTML = '<div class="galeria-videos">' + ordenados.map(cardVideo).join('') + '</div>';

    alvo.querySelectorAll('.video-card__check').forEach(function (check) {
      check.addEventListener('change', function () {
        estado.selecionados[check.value] = check.checked;
        check.closest('.video-card').classList.toggle('is-selecionado', check.checked);
        atualizarResumo();
      });
    });

    // clicar no card (fora do checkbox/link) também alterna a seleção
    alvo.querySelectorAll('.video-card').forEach(function (card) {
      card.addEventListener('click', function (evento) {
        if (evento.target.closest('.video-card__check, .link-video')) return;
        var check = card.querySelector('.video-card__check');
        check.checked = !check.checked;
        check.dispatchEvent(new Event('change'));
      });
    });

    atualizarResumo();
  }

  function cardVideo(v) {
    var marcado = !!estado.selecionados[v.video_id];

    var meta = [esc(v.canal_nome), data(v.publicado_em)]
      .filter(Boolean).map(function (p) { return '<span>' + p + '</span>'; }).join(' · ');

    return '' +
      '<article class="video-card' + (marcado ? ' is-selecionado' : '') + '" data-video-id="' + esc(v.video_id) + '">' +
        '<input type="checkbox" class="video-card__check" value="' + esc(v.video_id) + '"' +
          (marcado ? ' checked' : '') + ' aria-label="Selecionar vídeo">' +
        (v.thumbnail
          ? '<img class="video-card__thumb" src="' + esc(v.thumbnail) + '" alt="" loading="lazy">'
          : '<span class="video-card__thumb"></span>') +
        '<div class="video-card__corpo">' +
          '<p class="video-card__titulo">' + esc(v.titulo) + '</p>' +
          '<p class="video-card__meta">' + meta + (v.duracao_segundos ? ' · ' + duracao(v.duracao_segundos) : '') + '</p>' +
          '<div class="video-card__metricas">' +
            '<span class="metrica">' + iconeMetrica('views') + '<strong>' + numero(v.views) + '</strong></span>' +
            '<span class="metrica">' + iconeMetrica('likes') + numero(v.likes) + '</span>' +
            '<span class="metrica">' + iconeMetrica('comentarios') + numero(v.comentarios) + '</span>' +
          '</div>' +
          '<div class="video-card__rodape">' +
            (v._origem === 'url' ? '<span class="marca-origem">URL</span>' : '<span></span>') +
            '<a class="link-video" href="' + esc(v.url) + '" target="_blank" rel="noopener">Abrir ↗</a>' +
          '</div>' +
        '</div>' +
      '</article>';
  }

  function renderDescartados(lista) {
    var alvo = $('[data-descartados]');
    if (!lista || !lista.length) { alvo.hidden = true; return; }

    alvo.hidden = false;
    alvo.innerHTML = '<strong>' + lista.length + ' vídeo(s) fora dos filtros:</strong><ul>' +
      lista.map(function (v) {
        return '<li>' + esc(v.titulo) + ' — ' + esc(v.motivo_descarte) + '</li>';
      }).join('') + '</ul>';
  }

  function videosSelecionados() {
    return estado.videos.filter(function (v) { return estado.selecionados[v.video_id]; });
  }

  function atualizarResumo() {
    var total = estado.videos.length;
    var marcados = videosSelecionados().length;

    $('[data-resumo-selecao]').textContent = total
      ? marcados + ' de ' + total + ' vídeo(s) selecionado(s) para a pipeline.'
      : 'Nenhum vídeo carregado ainda.';

    salvarCache();
  }

  $('[data-selecionar-todos]').addEventListener('click', function () {
    estado.videos.forEach(function (v) { estado.selecionados[v.video_id] = true; });
    renderVideos();
  });

  $('[data-limpar-selecao]').addEventListener('click', function () {
    estado.selecionados = {};
    renderVideos();
  });

  // ── 3. Prompts ─────────────────────────────────────────────────────────

  function aplicarVisualizacaoPrompts() {
    var container = $('[data-prompts-container]');
    var compacto = estado.visualizacaoPrompts === 'compacto';
    if (container) container.classList.toggle('is-compacto', compacto);

    var botao = $('[data-prompts-toggle]');
    if (botao) {
      botao.setAttribute('aria-expanded', String(!compacto));
      botao.title = compacto ? 'Expandir esta seção' : 'Recolher esta seção';
    }
  }

  function expandirPrompts() {
    if (estado.visualizacaoPrompts === 'compacto') {
      estado.visualizacaoPrompts = 'expandido';
      aplicarVisualizacaoPrompts();
    }
  }

  $('[data-prompts-toggle]').addEventListener('click', function () {
    estado.visualizacaoPrompts = estado.visualizacaoPrompts === 'compacto' ? 'expandido' : 'compacto';
    aplicarVisualizacaoPrompts();
  });

  function atualizarAjudaEntrada() {
    var tipo = $('[data-p-entrada]').value;
    var info = ROTULO_ENTRADA[tipo];
    $('[data-p-entrada-ajuda]').textContent = info ? info.ajuda : '';
  }

  $('[data-p-entrada]').addEventListener('change', atualizarAjudaEntrada);

  function carregarPrompts() {
    return api('GET', '/prompts').then(function (resposta) {
      estado.prompts = resposta.prompts || [];
      renderPrompts();
    }).catch(function (erro) {
      $('[data-lista-prompts]').innerHTML =
        '<div class="vazio vazio--compacto">' + esc(erro.message) + '</div>';
    });
  }

  function badgeFonte(tipo) {
    var info = ROTULO_ENTRADA[tipo] || { icone: '•', rotulo: tipo };
    return '<span class="badge-fonte">' + info.icone + ' ' + esc(info.rotulo) + '</span>';
  }

  function renderPrompts() {
    var alvo = $('[data-lista-prompts]');

    if (!estado.prompts.length) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">Nenhum prompt salvo.</div>';
      return;
    }

    alvo.innerHTML = estado.prompts.map(function (p) {
      var ativo = !!estado.promptsAtivos[p.id];
      return '' +
        '<div class="prompt-item' + (ativo ? ' is-selecionado' : '') + '">' +
          '<input type="checkbox" class="prompt-item__check" data-prompt-check="' + esc(p.id) + '"' +
            (ativo ? ' checked' : '') + '>' +
          '<div class="prompt-item__corpo">' +
            '<p class="prompt-item__nome">' + esc(p.name) + '</p>' +
            badgeFonte(p.input_type) +
            '<p class="prompt-item__meta">v' + p.version + ' · saída ' + esc(p.output_format) + '</p>' +
            '<div class="prompt-item__acoes">' +
              '<button type="button" class="link-acao" data-editar-prompt="' + esc(p.id) + '">editar</button>' +
              '<button type="button" class="link-acao link-acao--perigo" data-remover-prompt="' +
                esc(p.id) + '">remover</button>' +
            '</div>' +
          '</div>' +
        '</div>';
    }).join('');

    alvo.querySelectorAll('[data-prompt-check]').forEach(function (check) {
      check.addEventListener('change', function () {
        estado.promptsAtivos[check.dataset.promptCheck] = check.checked;
        renderPrompts();
        salvarCache();
      });
    });

    alvo.querySelectorAll('[data-editar-prompt]').forEach(function (botao) {
      botao.addEventListener('click', function () { editarPrompt(botao.dataset.editarPrompt); });
    });

    alvo.querySelectorAll('[data-remover-prompt]').forEach(function (botao) {
      botao.addEventListener('click', function () { removerPrompt(botao.dataset.removerPrompt); });
    });
  }

  function editarPrompt(id) {
    var prompt = estado.prompts.filter(function (p) { return p.id === id; })[0];
    if (!prompt) return;

    expandirPrompts();
    estado.editando = id;
    $('[data-titulo-editor]').textContent = 'Editando: ' + prompt.name + ' (v' + prompt.version + ')';
    $('[data-p-nome]').value = prompt.name;
    $('[data-p-desc]').value = prompt.description || '';
    $('[data-p-texto]').value = prompt.prompt;
    $('[data-p-entrada]').value = prompt.input_type;
    $('[data-p-formato]').value = prompt.output_format;
    $('[data-p-schema]').value = prompt.output_schema
      ? JSON.stringify(prompt.output_schema, null, 2) : '';

    atualizarAjudaEntrada();
    $('[data-cancelar-prompt]').hidden = false;
    avisar($('[data-aviso-prompt]'), 'Alterar o texto cria uma versão nova.');
    $('[data-p-nome]').scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function limparEditor() {
    estado.editando = null;
    $('[data-titulo-editor]').textContent = 'Novo prompt';
    $('[data-form-prompt]').reset();
    atualizarAjudaEntrada();
    $('[data-cancelar-prompt]').hidden = true;
    avisar($('[data-aviso-prompt]'), '');
  }

  $('[data-novo-prompt]').addEventListener('click', function () {
    expandirPrompts();
    limparEditor();
    $('[data-p-nome]').focus();
  });

  $('[data-cancelar-prompt]').addEventListener('click', limparEditor);

  function removerPrompt(id) {
    if (!confirm('Remover este prompt? Pipelines que o referenciam deixarão de funcionar.')) return;

    api('DELETE', '/prompts/' + id).then(function () {
      delete estado.promptsAtivos[id];
      if (estado.editando === id) limparEditor();
      carregarPrompts();
    }).catch(function (erro) {
      alert('Não foi possível remover: ' + erro.message);
    });
  }

  $('[data-form-prompt]').addEventListener('submit', function (evento) {
    evento.preventDefault();

    var schemaTexto = $('[data-p-schema]').value.trim();
    var schema = null;

    if (schemaTexto) {
      try {
        schema = JSON.parse(schemaTexto);
      } catch (erro) {
        avisar($('[data-aviso-prompt]'), 'Output schema não é um JSON válido.', 'erro');
        return;
      }
    }

    var corpo = {
      name: $('[data-p-nome]').value.trim(),
      description: $('[data-p-desc]').value.trim(),
      prompt: $('[data-p-texto]').value.trim(),
      input_type: $('[data-p-entrada]').value,
      output_format: $('[data-p-formato]').value,
      output_schema: schema
    };

    var botao = $('[data-salvar-prompt]');
    botao.disabled = true;
    avisar($('[data-aviso-prompt]'), 'Salvando…');

    var requisicao = estado.editando
      ? api('PUT', '/prompts/' + estado.editando, corpo)
      : api('POST', '/prompts', corpo);

    requisicao.then(function (prompt) {
      estado.promptsAtivos[prompt.id] = true;
      limparEditor();
      return carregarPrompts();
    }).then(function () {
      avisar($('[data-aviso-prompt]'), 'Prompt salvo.', 'ok');
    }).catch(function (erro) {
      avisar($('[data-aviso-prompt]'), erro.message, 'erro');
    }).finally(function () {
      botao.disabled = false;
    });
  });

  function promptsSelecionados() {
    return estado.prompts.filter(function (p) { return estado.promptsAtivos[p.id]; });
  }

  // ── 4. Execução ────────────────────────────────────────────────────────

  function configuracaoAtual() {
    var filtros = filtrosDaTela();
    var selecionados = videosSelecionados().map(function (v) { return v.video_id; });
    var fixa = selecionados.length > 0;
    // Com vídeos marcados, a execução ad-hoc reprocessa exatamente esse
    // conjunto — não refaz a busca por trás (mesmo que o campo ainda tenha texto).
    var query = fixa ? '' : heroInput.value.trim();

    var source = {
      type: 'youtube',
      query: query,
      video_ids: selecionados,
      fonte_fixa: fixa,
      max_results: filtros.max_results,
      order: filtros.order,
      meses_atras: filtros.meses_atras,
      published_after: filtros.published_after,
      published_before: filtros.published_before,
      channel_id: filtros.channel_id,
      relevance_language: filtros.relevance_language,
      video_duration: filtros.video_duration,
      min_views: filtros.min_views,
      min_likes: filtros.min_likes,
      min_comments: filtros.min_comments,
      duracao_min_segundos: filtros.duracao_min_segundos,
      max_comentarios_por_video: 100
    };

    var steps = [
      { type: 'youtube_search' },
      { type: 'transcript' },
      { type: 'comments' },
      { type: 'raw' },
      { type: 'refined' }
    ];

    promptsSelecionados().forEach(function (p) {
      steps.push({ type: 'agent', prompt_id: p.id, prompt_version: p.version, name: p.name });
    });

    return {
      source: source,
      steps: steps,
      llm: {
        provider: $('[data-llm-provider]').value,
        model: $('[data-llm-model]').value.trim() || null,
        temperature: Number($('[data-llm-temp]').value) || 0
      }
    };
  }

  $('[data-executar]').addEventListener('click', function () {
    var config = configuracaoAtual();

    if (!config.source.query && !config.source.video_ids.length) {
      avisarExecucao('Pesquise vídeos ou selecione ao menos um antes de executar.');
      return;
    }

    var botao = $('[data-executar]');
    botao.disabled = true;
    avisarExecucao('');
    estado.tabelas = null;

    api('POST', '/youtube/playground/run', config).then(function (resposta) {
      estado.runId = resposta.run_id;
      estado.run = resposta;
      renderTrilho(resposta.steps || []);
      iniciarPolling();
      salvarCache();
    }).catch(function (erro) {
      avisarExecucao(erro.message);
      botao.disabled = false;
    });
  });

  function avisarExecucao(texto) {
    var alvo = $('[data-aviso-execucao]');
    alvo.hidden = !texto;
    alvo.textContent = texto || '';
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

    api('GET', '/pipelines/runs/' + estado.runId).then(function (run) {
      estado.run = run;
      renderTrilho(run.steps || []);

      var terminou = ['success', 'partial', 'failed'].indexOf(run.status) !== -1;
      if (terminou) {
        pararPolling();
        $('[data-executar]').disabled = false;
        $('[data-gerar-pipeline]').disabled = false;
        renderCamadas();

        if (run.status === 'failed') {
          avisarExecucao('A execução falhou. Veja o detalhe na etapa marcada em vermelho.');
        } else if (run.status === 'partial') {
          avisarExecucao('Execução concluída com falhas parciais — os dados das etapas ' +
            'anteriores foram preservados.');
        }
      }
    }).catch(function (erro) {
      pararPolling();
      $('[data-executar]').disabled = false;
      avisarExecucao('Falha ao consultar o status: ' + erro.message);
    });
  }

  // Restaura a execução guardada no cache local ao carregar a página — como
  // se ela nunca tivesse saído da tela. Um run_id que não existe mais
  // (histórico limitado a 200 execuções em store.py) é ignorado em
  // silêncio, sem o aviso de erro que consultarRun() mostraria.
  function restaurarRun(runId) {
    estado.runId = runId;

    api('GET', '/pipelines/runs/' + runId).then(function (run) {
      estado.run = run;
      renderTrilho(run.steps || []);

      var terminou = ['success', 'partial', 'failed'].indexOf(run.status) !== -1;
      if (terminou) {
        renderCamadas();
      } else {
        iniciarPolling();
      }
    }).catch(function () {
      estado.runId = null;
      estado.run = null;
      salvarCache();
    });
  }

  var MARCA_STATUS = { pending: '·', running: '•', success: '✓', failed: '✕', skipped: '–' };

  function renderTrilho(steps) {
    var alvo = $('[data-trilho]');

    if (!steps.length) {
      alvo.innerHTML = '<li class="vazio vazio--compacto">Nenhuma execução iniciada.</li>';
      return;
    }

    alvo.innerHTML = steps.map(function (s, indice) {
      var erro = s.error
        ? '<p class="trilho-passo__erro"><code>' + esc(s.error.error_type) + '</code> ' +
          esc(s.error.message) + (s.error.retryable ? ' <em>(pode ser reexecutada)</em>' : '') + '</p>'
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

  function decorrido(step) {
    if (!step.started_at) return '';
    var fim = step.finished_at ? new Date(step.finished_at) : new Date();
    var segundos = (fim - new Date(step.started_at)) / 1000;
    if (isNaN(segundos) || segundos < 0) return '';
    return segundos < 60 ? segundos.toFixed(1) + 's' : Math.round(segundos / 60) + 'min';
  }

  // ── 5. Camadas RAW / REFINED / TRUST ────────────────────────────────────

  $$('[data-camada]').forEach(function (botao) {
    botao.addEventListener('click', function () {
      estado.camada = botao.dataset.camada;
      $$('[data-camada]').forEach(function (b) { b.classList.toggle('is-ativa', b === botao); });
      $$('[data-painel-camada]').forEach(function (p) {
        p.hidden = p.dataset.painelCamada !== estado.camada;
      });

      // RAW não tem representação de "Tabela" — some com a opção quando ativa
      var opcaoTabela = $('[data-visual="tabela"]');
      opcaoTabela.hidden = estado.camada === 'raw';
      if (estado.camada === 'raw' && estado.modoVisual === 'tabela') {
        estado.modoVisual = 'cartoes';
        atualizarBotoesVisual();
      }

      renderCamadas();
    });
  });

  $$('[data-visual]').forEach(function (botao) {
    botao.addEventListener('click', function () {
      estado.modoVisual = botao.dataset.visual;
      atualizarBotoesVisual();
      renderCamadas();
    });
  });

  function atualizarBotoesVisual() {
    $$('[data-visual]').forEach(function (b) { b.classList.toggle('is-ativa', b.dataset.visual === estado.modoVisual); });
  }

  $('[data-ver-json]').addEventListener('change', function (evento) {
    estado.verJson = evento.target.checked;
    renderCamadas();
  });

  function json(valor) {
    return '<pre class="bloco-json">' + esc(JSON.stringify(valor, null, 2)) + '</pre>';
  }

  function grupo(titulo, contagem, conteudo, explicacao) {
    return '<div class="camada-grupo">' +
      '<h3 class="camada-grupo__titulo">' + esc(titulo) +
        (contagem !== null && contagem !== undefined
          ? '<span class="camada-grupo__contagem">' + contagem + '</span>' : '') +
      '</h3>' +
      (explicacao ? '<p class="camada-grupo__explicacao">' + esc(explicacao) + '</p>' : '') +
      conteudo + '</div>';
  }

  function cartao(rotulo, valor, texto) {
    return '<div class="cartao">' +
      '<p class="cartao__rotulo">' + esc(rotulo) + '</p>' +
      '<p class="cartao__valor">' + esc(valor) + '</p>' +
      (texto ? '<p class="cartao__texto">' + esc(texto) + '</p>' : '') +
      '</div>';
  }

  function tabela(colunas, linhas) {
    if (!linhas.length) return '<div class="vazio vazio--compacto">Sem registros.</div>';

    var cabecalho = '<tr>' + colunas.map(function (c) { return '<th>' + esc(c[0]) + '</th>'; }).join('') + '</tr>';
    var corpo = linhas.map(function (linha) {
      return '<tr>' + colunas.map(function (c) {
        var valor = c[1](linha);
        return '<td>' + (valor === undefined ? '—' : valor) + '</td>';
      }).join('') + '</tr>';
    }).join('');

    return '<div class="tabela-wrap"><table class="tabela"><thead>' + cabecalho +
      '</thead><tbody>' + corpo + '</tbody></table></div>';
  }

  function truncar(texto, tamanho) {
    if (!texto) return '';
    return texto.length > tamanho ? texto.slice(0, tamanho) + '…' : texto;
  }

  /** Busca (com cache) as tabelas persistidas da execução atual. */
  function obterTabelas() {
    if (estado.tabelas) return Promise.resolve(estado.tabelas);
    if (!estado.runId) return Promise.resolve({ videos: [], channels: [], transcripts: [], comments: [], insights: [] });

    return api('GET', '/pipelines/runs/' + estado.runId + '/tables').then(function (dados) {
      estado.tabelas = dados;
      return dados;
    });
  }

  function renderCamadas() {
    if (!estado.run) return;

    if (estado.camada === 'raw') { renderRaw(estado.run.raw || {}); return; }

    if (estado.modoVisual === 'tabela') {
      var alvo = $('[data-painel-camada="' + estado.camada + '"]');
      alvo.innerHTML = '<div class="vazio vazio--compacto">Carregando tabelas…</div>';

      obterTabelas().then(function (dados) {
        if (estado.camada === 'refined') renderRefinedTabela(dados);
        if (estado.camada === 'trust') renderTrustTabela(dados);
      }).catch(function (erro) {
        alvo.innerHTML = '<div class="vazio vazio--compacto">' + esc(erro.message) + '</div>';
      });
      return;
    }

    if (estado.camada === 'refined') renderRefined(estado.run.refined || {});
    if (estado.camada === 'trust') renderTrust(estado.run.trust || {});
  }

  function renderRaw(raw) {
    var alvo = $('[data-painel-camada="raw"]');
    var videos = raw.videos || [];

    if (!videos.length) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">Nenhum dado RAW coletado.</div>';
      return;
    }

    var transcricoes = Object.keys(raw.transcripts || {}).length;
    var comentarios = Object.keys(raw.comments || {}).reduce(function (soma, k) {
      return soma + raw.comments[k].length;
    }, 0);

    var resumo = '<div class="cartoes">' +
      cartao('Vídeos', videos.length, 'resposta original de videos.list') +
      cartao('Canais', (raw.channels || []).length, 'channels.list') +
      cartao('Transcrições', transcricoes, 'segmentos com timestamp') +
      cartao('Comentários', comentarios, 'commentThreads.list, sem tratamento') +
      '</div>';

    var detalhe = estado.verJson
      ? grupo('Payload original', null, json(raw))
      : grupo('Amostra — primeiro vídeo', null, json(videos[0]));

    alvo.innerHTML = grupo('Volume coletado', null, resumo) + detalhe +
      '<p class="cartao__texto">RAW é imutável: nenhuma etapa posterior o altera.</p>';
  }

  function par(chave, valorHtml) {
    return '<div class="par"><span class="par__chave">' + esc(chave) +
      '</span><span class="par__valor">' + valorHtml + '</span></div>';
  }

  function renderRefined(refined) {
    var alvo = $('[data-painel-camada="refined"]');
    var videos = refined.videos || [];

    if (!videos.length) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">Nenhum dado REFINED disponível.</div>';
      return;
    }

    var stats = refined.estatisticas || {};

    var resumo = '<div class="cartoes">' +
      cartao('Vídeos', stats.total_videos || videos.length) +
      cartao('Transcrições', stats.total_transcricoes || 0) +
      cartao('Comentários únicos', stats.total_comentarios || 0,
        (stats.comentarios_descartados || 0) + ' duplicados/vazios removidos') +
      cartao('Palavras transcritas', numero(stats.total_palavras_transcricao || 0)) +
      '</div>';

    if (estado.verJson) {
      alvo.innerHTML = grupo('Estatísticas', null, resumo) +
        grupo('REFINED completo', null, json(refined));
      return;
    }

    var porVideo = videos.map(function (v) {
      var t = (refined.transcripts || {})[v.video_id];
      var c = (refined.comments || {})[v.video_id] || [];

      var linhas = [
        par('Canal', esc(v.canal_nome) + ' · ' + numero(v.canal_inscritos) + ' inscritos'),
        par('Publicado', data(v.publicado_em)),
        par('Duração', duracao(v.duracao_segundos)),
        par('Métricas', numero(v.views) + ' views · ' + numero(v.likes) + ' likes'),
        par('Transcrição', t
          ? numero(t.palavras) + ' palavras (' + esc(t.language || '—') + ')'
          : '<em>indisponível</em>'),
        par('Comentários', c.length + ' após limpeza e deduplicação')
      ].join('');

      var amostra = '';
      if (t && t.clean_text) {
        amostra += '<p class="cartao__rotulo" style="margin-top:12px">Texto normalizado</p>' +
          '<p class="texto-longo">' + esc(t.clean_text.slice(0, 1200)) +
          (t.clean_text.length > 1200 ? '…' : '') + '</p>';
      }
      if (c.length) {
        amostra += '<p class="cartao__rotulo" style="margin-top:12px">Comentários (amostra)</p>' +
          '<div class="texto-longo">' + c.slice(0, 8).map(function (item) {
            return '<div style="padding:4px 0">"' + esc(item.texto) + '" — ' +
              esc(item.autor) + ' (' + item.likes + ' likes)</div>';
          }).join('') + '</div>';
      }

      return grupo(v.titulo, v.video_id, linhas + amostra);
    }).join('');

    alvo.innerHTML = grupo('Estatísticas', null, resumo) + porVideo;
  }

  function renderRefinedTabela(dados) {
    var alvo = $('[data-painel-camada="refined"]');
    var videos = dados.videos || [];

    if (!videos.length) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">' +
        'Nenhuma tabela persistida ainda para esta execução.</div>';
      return;
    }

    var canais = dados.channels || [];
    var tabelaCanais = tabela([
      ['Canal', function (c) { return esc(c.nome); }],
      ['ID do canal', function (c) { return '<span class="texto-truncado">' + esc(c.canal_id) + '</span>'; }],
      ['Inscritos', function (c) { return numero(c.inscritos); }],
      ['Views totais', function (c) { return numero(c.views_totais); }],
      ['Vídeos totais', function (c) { return numero(c.videos_totais); }],
      ['Criado em', function (c) { return data(c.criado_em); }]
    ], canais);

    var tabelaVideos = tabela([
      ['Vídeo', function (v) { return '<a class="link-video" href="' + esc(v.url || '') + '" target="_blank" rel="noopener">' + esc(truncar(v.titulo, 48)) + '</a>'; }],
      ['Canal', function (v) { return esc(v.canal_nome); }],
      ['Publicado', function (v) { return data(v.publicado_em); }],
      ['Duração', function (v) { return duracao(v.duracao_segundos); }],
      ['Views', function (v) { return numero(v.views); }],
      ['Likes', function (v) { return numero(v.likes); }],
      ['Comentários', function (v) { return numero(v.comentarios); }],
      ['Inscritos do canal', function (v) { return numero(v.canal_inscritos); }]
    ], videos);

    var transcricoes = dados.transcripts || [];
    var tabelaTranscricoes = tabela([
      ['Vídeo', function (t) { return esc(t.video_id); }],
      ['Idioma', function (t) { return esc(t.language || '—'); }],
      ['Palavras', function (t) { return numero(t.palavras); }],
      ['Caracteres', function (t) { return numero(t.caracteres); }],
      ['Prévia do texto', function (t) { return '<span class="texto-truncado">' + esc(truncar(t.clean_text, 90)) + '</span>'; }]
    ], transcricoes);

    var comentarios = dados.comments || [];
    var enriquecimentoPorComentario = mapaEnriquecimento();
    var tabelaComentarios = tabela([
      ['Vídeo', function (c) { return esc(c.video_id); }],
      ['Autor', function (c) { return esc(c.autor); }],
      ['Comentário', function (c) { return '<span class="texto-truncado">' + esc(truncar(c.texto, 90)) + '</span>'; }],
      ['Likes', function (c) { return numero(c.likes); }],
      ['Respostas', function (c) { return numero(c.respostas); }],
      ['Sentimento', function (c) { return celulaEnriquecimento(enriquecimentoPorComentario[c.comment_id], 'sentimento'); }],
      ['Motivo', function (c) { return celulaEnriquecimento(enriquecimentoPorComentario[c.comment_id], 'motivo'); }],
      ['Adjetivos', function (c) { return celulaEnriquecimento(enriquecimentoPorComentario[c.comment_id], 'adjetivos'); }],
      ['Objeto', function (c) { return celulaEnriquecimento(enriquecimentoPorComentario[c.comment_id], 'objeto'); }]
    ], comentarios);

    alvo.innerHTML =
      grupo('Tabela: Canais', canais.length, tabelaCanais,
        'Um canal por linha — dado agregado dos vídeos coletados nesta execução.') +
      grupo('Tabela: Vídeos', videos.length, tabelaVideos,
        'Um vídeo por linha, com os dados do canal (inscritos, views totais) já embutidos na criação.') +
      grupo('Tabela: Transcrições', transcricoes.length, tabelaTranscricoes,
        'Dado usado por prompts do tipo Transcrição e Vídeo completo.') +
      grupo('Tabela: Comentários extraídos', comentarios.length, tabelaComentarios,
        'Dado usado por prompts do tipo Comentários e Vídeo completo — as colunas de ' +
        'sentimento/motivo/adjetivos/objeto só vêm preenchidas para a amostra fixa (3) ' +
        'classificada automaticamente pelo enriquecimento de comentários.');
  }

  // Enriquecimento automático de comentários (sentimento/motivo/adjetivos/
  // objeto) roda sozinho junto da pipeline, numa amostra fixa — não precisa
  // de um botão de teste separado. `estado.run.trust.enriquecimento_
  // comentarios` já chega pronto no polling normal da execução
  // (ver src/analytics/enrichment.py / engine.py::_passo_enriquecimento_comentarios).
  var SELO_POR_SENTIMENTO = {
    positivo: 'selo--positivo', negativo: 'selo--negativo',
    neutro: 'selo--neutro', misto: 'selo--misto'
  };

  function seloSentimento(sentimento) {
    var classe = SELO_POR_SENTIMENTO[(sentimento || '').toLowerCase()] || 'selo--neutro';
    return '<span class="selo ' + classe + '">' + esc(sentimento || '—') + '</span>';
  }

  function chipsAdjetivos(adjetivos) {
    if (!adjetivos || !adjetivos.length) return '<span class="texto-faint">—</span>';
    return adjetivos.map(function (a) { return '<span class="chip">' + esc(a) + '</span>'; }).join(' ');
  }

  function mapaEnriquecimento() {
    var mapa = {};
    var lista = (estado.run && estado.run.trust && estado.run.trust.enriquecimento_comentarios) || [];
    lista.forEach(function (r) { if (r.comment_id) mapa[r.comment_id] = r; });
    return mapa;
  }

  function celulaEnriquecimento(resultado, campo) {
    if (!resultado) return '<span class="texto-faint">—</span>';

    if (!resultado.struct) {
      if (campo === 'sentimento') {
        var motivoFalha = resultado.error && resultado.error.message ? esc(resultado.error.message) : 'motivo desconhecido';
        return '<span class="selo selo--pendente" title="' + motivoFalha + '">falhou</span>';
      }
      return '<span class="texto-faint">—</span>';
    }

    if (campo === 'sentimento') return seloSentimento(resultado.struct.sentimento);
    if (campo === 'adjetivos') return chipsAdjetivos(resultado.struct.adjetivos);
    return esc(resultado.struct[campo]) || '<span class="texto-faint">—</span>';
  }

  function renderTrust(trust) {
    var alvo = $('[data-painel-camada="trust"]');
    var analises = trust.analises || [];

    if (!analises.length) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">' +
        'Nenhuma análise executada. Selecione ao menos um prompt na etapa 2.</div>';
      return;
    }

    if (estado.verJson) {
      alvo.innerHTML = grupo('TRUST completo', null, json(trust));
      return;
    }

    var stats = trust.estatisticas || {};
    var resumo = '<div class="cartoes">' +
      cartao('Análises', stats.total_analises || analises.length) +
      cartao('Concluídas', stats.analises_ok || 0) +
      cartao('Falhas', stats.analises_falhas || 0) +
      '</div>';

    var blocos = analises.map(function (a) {
      var origem = a.source.video_id
        ? '<a href="https://www.youtube.com/watch?v=' + esc(a.source.video_id) + '"' +
          ' target="_blank" rel="noopener">' + esc(a.source.video_id) + '</a>'
        : esc(a.escopo);

      var cabecalho = '<div class="insight__cabecalho">' +
        '<p class="insight__titulo">' + esc(a.prompt_name) + ' · v' + a.prompt_version +
          ' ' + badgeFonte(a.input_type) + '</p>' +
        '<span class="insight__origem">' + origem + ' · ' + esc(a.agent.provider) + '/' +
          esc(a.agent.model) + '</span></div>';

      if (!a.success) {
        return '<div class="insight">' + cabecalho +
          '<p class="trilho-passo__erro"><code>' + esc(a.error && a.error.type) + '</code> ' +
          esc(a.error && a.error.message) + '</p></div>';
      }

      return '<div class="insight">' + cabecalho + renderSaida(a.output) + '</div>';
    }).join('');

    alvo.innerHTML = grupo('Resumo', null, resumo) +
      grupo('Insights e rastreabilidade', analises.length, blocos);
  }

  function renderTrustTabela(dados) {
    var alvo = $('[data-painel-camada="trust"]');
    var insights = dados.insights || [];

    if (!insights.length) {
      alvo.innerHTML = '<div class="vazio vazio--compacto">' +
        'Nenhuma tabela de insights persistida ainda para esta execução.</div>';
      return;
    }

    var tabelaInsights = tabela([
      ['Prompt', function (i) { return esc(i.prompt_name); }],
      ['Versão', function (i) { return 'v' + i.prompt_version; }],
      ['Escopo', function (i) { return i.video_id ? esc(i.video_id) : '<em>conjunto</em>'; }],
      ['Provider/Modelo', function (i) { return esc(i.provider) + ' / ' + esc(i.model); }],
      ['Resultado', function (i) {
        if (!i.success) return '<span style="color:var(--danger)">' + esc(i.error_type || 'falhou') + '</span>';
        var resumo = i.output_json ? truncar(i.output_json.replace(/[{}"]/g, ''), 70) : '—';
        return '<span class="texto-truncado">' + esc(resumo) + '</span>';
      }],
      ['Status', function (i) { return i.success
        ? '<span class="selo selo--positivo">ok</span>'
        : '<span class="selo selo--negativa">falhou</span>'; }]
    ], insights);

    alvo.innerHTML = grupo('Tabela: Insights', insights.length, tabelaInsights,
      'Gerada pelo agente a partir do prompt escolhido, uma linha por chamada.');
  }

  /** Desenha a saída do agente de forma legível; JSON aninhado vira bloco. */
  function renderSaida(saida) {
    if (saida === null || saida === undefined) return '<p class="cartao__texto">—</p>';

    if (typeof saida === 'string') {
      return '<p class="texto-longo">' + esc(saida) + '</p>';
    }

    if (Array.isArray(saida)) {
      return '<ul class="par__valor">' + saida.map(function (item) {
        return '<li>' + (typeof item === 'object' ? json(item) : esc(item)) + '</li>';
      }).join('') + '</ul>';
    }

    return Object.keys(saida).map(function (chave) {
      var valor = saida[chave];
      var html;

      if (valor === null || valor === undefined) {
        html = '—';
      } else if (Array.isArray(valor)) {
        html = valor.length
          ? '<ul>' + valor.map(function (i) {
              return '<li>' + (typeof i === 'object' ? esc(JSON.stringify(i)) : esc(i)) + '</li>';
            }).join('') + '</ul>'
          : '—';
      } else if (typeof valor === 'object') {
        html = Object.keys(valor).map(function (k) {
          var v = valor[k];
          return '<strong>' + esc(k) + ':</strong> ' +
            (Array.isArray(v) ? esc(v.join('; ')) : esc(v));
        }).join('<br>');
      } else {
        html = esc(valor);
      }

      return par(chave, html);
    }).join('');
  }

  // ── 6. Gerar pipeline ──────────────────────────────────────────────────

  var modal = $('[data-modal-pipeline]');

  $('[data-gerar-pipeline]').addEventListener('click', function () {
    $('[data-pl-nome]').value = heroInput.value.trim() || 'Nova pipeline';
    avisar($('[data-aviso-modal]'), '');
    prepararModalPipeline();
    modal.hidden = false;
  });

  $('[data-fechar-modal]').addEventListener('click', function () { modal.hidden = true; });

  modal.addEventListener('click', function (evento) {
    if (evento.target === modal) modal.hidden = true;
  });

  $('[data-pl-agenda]').addEventListener('change', function (evento) {
    var modo = evento.target.value;
    $('[data-campo-cron]').hidden = modo !== 'cron';
    $('[data-campo-hora]').hidden = ['daily', 'weekly', 'monthly'].indexOf(modo) === -1;
  });

  $$('[data-pl-fonte]').forEach(function (radio) {
    radio.addEventListener('change', atualizarSecaoFonteModal);
  });

  function atualizarSecaoFonteModal() {
    var fixa = $('input[data-pl-fonte]:checked').value === 'fixa';
    $('[data-pl-filtros]').hidden = fixa;
    $('[data-pl-fonte-fixa-resumo]').hidden = !fixa;
  }

  /** Preenche o modal com o estado atual do Playground antes de abrir. */
  function prepararModalPipeline() {
    var filtros = filtrosDaTela();
    var selecionados = videosSelecionados();
    var temQuery = Boolean(heroInput.value.trim());

    // Fonte: sugere "fixa" quando há vídeos marcados e nenhuma busca ativa
    var sugerirFixa = selecionados.length > 0 && !temQuery;
    $$('[data-pl-fonte]').forEach(function (r) { r.checked = (r.value === 'fixa') === sugerirFixa; });

    $('[data-pl-fonte-fixa-lista]').innerHTML = selecionados.map(function (v) {
      return '<li>' + esc(v.titulo) + '</li>';
    }).join('') || '<li><em>nenhum vídeo selecionado</em></li>';

    $('[data-pl-query]').value = heroInput.value.trim();
    $('[data-pl-f-meses]').value = filtros.meses_atras || '';
    $('[data-pl-f-max]').value = filtros.max_results || '';
    $('[data-pl-f-ordem]').value = filtros.order;
    $('[data-pl-f-idioma]').value = filtros.relevance_language || '';
    $('[data-pl-f-duracao]').value = filtros.video_duration || '';
    $('[data-pl-f-canal]').value = filtros.channel_id || '';
    $('[data-pl-f-views]').value = filtros.min_views ?? '';
    $('[data-pl-f-likes]').value = filtros.min_likes ?? '';
    $('[data-pl-f-coments]').value = filtros.min_comments ?? '';
    $('[data-pl-f-dur-min]').value = filtros.duracao_min_segundos ? filtros.duracao_min_segundos / 60 : '';

    var prompts = promptsSelecionados();
    $('[data-pl-prompts-resumo]').innerHTML = prompts.length
      ? prompts.map(function (p) { return '<span class="chip">' + badgeFonte(p.input_type) + ' ' + esc(p.name) + '</span>'; }).join('')
      : '<span class="vazio vazio--compacto">Nenhum prompt selecionado na tela.</span>';

    var infoProviders = JSON.parse(($('[data-llm-provider]').dataset.info) || '[]');
    $('[data-pl-llm-provider]').innerHTML = infoProviders.map(function (p) {
      return '<option value="' + esc(p.provider) + '">' + esc(p.provider) + '</option>';
    }).join('');
    $('[data-pl-llm-provider]').value = $('[data-llm-provider]').value;
    $('[data-pl-llm-model]').value = $('[data-llm-model]').value;
    $('[data-pl-llm-model]').placeholder = $('[data-llm-model]').placeholder;
    $('[data-pl-llm-temp]').value = $('[data-llm-temp]').value;

    atualizarSecaoFonteModal();
  }

  $('[data-form-pipeline-nova]').addEventListener('submit', function (evento) {
    evento.preventDefault();

    var fixa = $('input[data-pl-fonte]:checked').value === 'fixa';
    var selecionados = videosSelecionados().map(function (v) { return v.video_id; });

    if (fixa && !selecionados.length) {
      avisar($('[data-aviso-modal]'), 'Fonte fixa requer ao menos um vídeo selecionado no Playground.', 'erro');
      return;
    }
    if (!fixa && !$('[data-pl-query]').value.trim()) {
      avisar($('[data-aviso-modal]'), 'Fonte dinâmica requer um termo de busca.', 'erro');
      return;
    }

    var source = {
      type: 'youtube',
      query: fixa ? '' : $('[data-pl-query]').value.trim(),
      video_ids: fixa ? selecionados : [],
      fonte_fixa: fixa,
      max_results: numeroOuNulo($('[data-pl-f-max]')) || 10,
      order: $('[data-pl-f-ordem]').value,
      meses_atras: numeroOuNulo($('[data-pl-f-meses]')),
      relevance_language: $('[data-pl-f-idioma]').value || null,
      video_duration: $('[data-pl-f-duracao]').value || null,
      channel_id: $('[data-pl-f-canal]').value.trim() || null,
      min_views: numeroOuNulo($('[data-pl-f-views]')),
      min_likes: numeroOuNulo($('[data-pl-f-likes]')),
      min_comments: numeroOuNulo($('[data-pl-f-coments]')),
      duracao_min_segundos: minutosEmSegundos($('[data-pl-f-dur-min]')),
      max_comentarios_por_video: 100
    };

    var steps = [
      { type: 'youtube_search' },
      { type: 'transcript' },
      { type: 'comments' },
      { type: 'raw' },
      { type: 'refined' }
    ];
    promptsSelecionados().forEach(function (p) {
      steps.push({ type: 'agent', prompt_id: p.id, prompt_version: p.version, name: p.name });
    });

    var modo = $('[data-pl-agenda]').value;
    var corpo = {
      name: $('[data-pl-nome]').value.trim(),
      description: $('[data-pl-desc]').value.trim(),
      source: source,
      steps: steps,
      llm: {
        provider: $('[data-pl-llm-provider]').value,
        model: $('[data-pl-llm-model]').value.trim() || null,
        temperature: Number($('[data-pl-llm-temp]').value) || 0
      },
      schedule: {
        mode: modo,
        hora: $('[data-pl-hora]').value || '08:00',
        cron: $('[data-pl-cron]').value.trim() || null
      },
      status: 'active'
    };

    var botao = $('[data-confirmar-pipeline]');
    botao.disabled = true;
    avisar($('[data-aviso-modal]'), 'Criando pipeline…');

    api('POST', '/pipelines', corpo).then(function (pipeline) {
      return api('POST', '/pipelines/' + pipeline.id + '/generate-dag').then(function (dag) {
        avisar($('[data-aviso-modal]'),
          'Pipeline criada e DAG "' + dag.dag_id + '" gerada.', 'ok');
        setTimeout(function () { window.location.href = '/pipeline'; }, 900);
      });
    }).catch(function (erro) {
      avisar($('[data-aviso-modal]'), erro.message, 'erro');
    }).finally(function () {
      botao.disabled = false;
    });
  });

  // ── Inicialização ──────────────────────────────────────────────────────

  var cacheInicial = lerCache();
  if (cacheInicial) {
    if (Array.isArray(cacheInicial.videos) && cacheInicial.videos.length) {
      estado.videos = cacheInicial.videos;
    }
    if (cacheInicial.selecionados && typeof cacheInicial.selecionados === 'object') {
      estado.selecionados = cacheInicial.selecionados;
    }
    renderVideos();

    if (cacheInicial.runId) restaurarRun(cacheInicial.runId);
  }

  atualizarAjudaEntrada();
  carregarConfig();
  carregarPrompts().then(function () {
    if (cacheInicial && cacheInicial.promptsAtivos && typeof cacheInicial.promptsAtivos === 'object') {
      estado.promptsAtivos = cacheInicial.promptsAtivos;
      renderPrompts();
    }
  });
})();
