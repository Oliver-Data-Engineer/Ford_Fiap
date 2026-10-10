/* ============================================================
   SCRAPER MODE — cadastro de sites, cadeias de etapas dependentes
   (fluxo visual), auto-discovery de seletores (IA), detecção de
   filtros de URL, tela de teste (extração determinística, sem IA),
   painel de dados coletados e registro/execução.
   O JavaScript não executa regra de negócio: só coleta entrada, chama
   /api/scraper/* e desenha o que o backend devolve.
   ============================================================ */
(function () {
  'use strict';

  var pagina = document.querySelector('[data-lista-sites]');
  if (!pagina) return;

  var estado = {
    sites: [], config: [], timers: {}, modalEtapa: null, modalSite: null,
    ultimosDados: {}  // { site_id: { objetivo: {marca, modelo, linhas} } } — alimenta o chat widget
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

    return fetch('/api/scraper' + rota, opcoes).then(function (r) {
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

  function mensagemErro(erro) {
    if (erro && erro.motivo === 'network') {
      return 'Falha de rede ao carregar a página (site fora do ar, proxy ou timeout).';
    }
    return (erro && erro.message) || 'Erro desconhecido.';
  }

  function avisar(elemento, texto, tipo) {
    if (!elemento) return;
    elemento.textContent = texto || '';
    elemento.className = 'aviso' + (tipo ? ' aviso--' + tipo : '');
  }

  function avisarPagina(texto) {
    var alvo = $('[data-aviso-scraper]');
    alvo.hidden = !texto;
    alvo.textContent = texto || '';
  }

  function dado(rotulo, valor) {
    return '<span class="dado-rotulado">' + esc(rotulo) + '<strong>' + esc(valor) + '</strong></span>';
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
    dag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><circle cx="5" cy="6" r="2"/><circle cx="19" cy="12" r="2"/><circle cx="5" cy="18" r="2"/><path d="M7 6h6a4 4 0 014 4M7 18h6a4 4 0 004-4" stroke-linecap="round"/></svg>',
    remover: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M5 7h14M9 7V5a1 1 0 011-1h4a1 1 0 011 1v2m2 0v13a1 1 0 01-1 1H8a1 1 0 01-1-1V7h10z" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    editar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="14" height="14"><path d="M4 20l.9-4L16 4.9a1.5 1.5 0 012.1 0l1 1a1.5 1.5 0 010 2.1L8 19l-4 1z" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 6.9L17.1 10" stroke-linecap="round"/></svg>'
  };

  var ROTULOS_OBJETIVO = {
    ficha_tecnica: 'Ficha técnica', anuncios: 'Anúncios/preços', comentarios: 'Comentários'
  };

  var CAMPOS_POR_OBJETIVO = {
    ficha_tecnica: [
      { chave: 'linha_atributo', rotulo: 'Linha do atributo (repete)' },
      { chave: 'nome_atributo', rotulo: 'Nome do atributo' },
      { chave: 'valor_atributo', rotulo: 'Valor do atributo' },
      { chave: 'marca', rotulo: 'Marca' }, { chave: 'modelo', rotulo: 'Modelo' },
      { chave: 'versao', rotulo: 'Versão' }, { chave: 'ano', rotulo: 'Ano' }
    ],
    anuncios: [
      { chave: 'item_listagem', rotulo: 'Item da listagem (repete)' },
      { chave: 'preco', rotulo: 'Preço' },
      { chave: 'marca', rotulo: 'Marca' }, { chave: 'modelo', rotulo: 'Modelo' },
      { chave: 'versao', rotulo: 'Versão' }, { chave: 'ano', rotulo: 'Ano' },
      { chave: 'url_anuncio', rotulo: 'URL do anúncio' }, { chave: 'localizacao', rotulo: 'Localização' },
      { chave: 'vendedor', rotulo: 'Vendedor' }, { chave: 'publicado_em', rotulo: 'Publicado em' }
    ],
    comentarios: [
      { chave: 'item_comentario', rotulo: 'Item do comentário (repete)' },
      { chave: 'texto', rotulo: 'Texto' },
      { chave: 'autor', rotulo: 'Autor' }, { chave: 'nota', rotulo: 'Nota' },
      { chave: 'publicado_em', rotulo: 'Publicado em' },
      { chave: 'marca', rotulo: 'Marca' }, { chave: 'modelo', rotulo: 'Modelo' },
      { chave: 'versao', rotulo: 'Versão' }, { chave: 'ano', rotulo: 'Ano' }
    ]
  };

  var COLUNA_CHAVE_POR_OBJETIVO = { ficha_tecnica: 'valor_atributo', anuncios: 'preco', comentarios: 'nota' };
  var ROTA_DADOS = { ficha_tecnica: '/dados/ficha-tecnica', anuncios: '/dados/anuncios', comentarios: '/dados/comentarios' };
  var PAPEIS_FILTRO = ['marca', 'modelo', 'versao', 'ano', 'id', 'outro'];

  // ── KPIs ───────────────────────────────────────────────────────────────

  function carregarResumo() {
    api('GET', '/resumo').then(function (r) {
      $('[data-kpi-sites]').textContent = numero(r.total_sites);
      $('[data-kpi-registrados]').textContent = numero(r.total_registrados);
      $('[data-kpi-ficha]').textContent = numero(r.total_ficha_tecnica);
      $('[data-kpi-anuncios]').textContent = numero(r.total_anuncios);
      $('[data-kpi-comentarios]').textContent = numero(r.total_comentarios);
    }).catch(function (erro) {
      avisarPagina('Não foi possível carregar os indicadores: ' + erro.message);
    });
  }

  // ── Config — providers/modelos disponíveis ──────────────────────────────

  function carregarConfig() {
    return api('GET', '/config').then(function (r) {
      estado.config = r.providers || [];
      popularProvidersSite();
    }).catch(function () { /* dropdown fica só com "padrão do sistema" */ });
  }

  function popularProvidersSite() {
    var select = $('[data-site-provider]');
    if (!select) return;
    var atual = select.value;
    select.innerHTML = '<option value="">padrão do sistema</option>' +
      estado.config.map(function (p) {
        return '<option value="' + esc(p.provider) + '">' + esc(p.provider) +
          (p.configurado ? ' (configurado)' : ' (sem credencial)') + '</option>';
      }).join('');
    select.value = atual;
  }

  function popularModelosSite(provider) {
    var select = $('[data-site-model]');
    var custom = $('[data-site-model-custom]');
    custom.hidden = true;
    custom.value = '';

    if (!provider) {
      select.innerHTML = '<option value="">padrão do provider</option>';
      select.disabled = true;
      return;
    }

    var info = estado.config.filter(function (p) { return p.provider === provider; })[0];
    var modelos = (info && info.modelos) || [];

    select.innerHTML = '<option value="">padrão (' + esc((info && info.modelo_padrao) || '—') + ')</option>' +
      modelos.map(function (m) { return '<option value="' + esc(m) + '">' + esc(m) + '</option>'; }).join('') +
      '<option value="__custom__">Custom / outro…</option>';
    select.disabled = false;
  }

  // ── Sites — listagem e renderização ─────────────────────────────────────

  function carregarSites() {
    return api('GET', '/sites').then(function (r) {
      estado.sites = r.sites || [];
      renderSites();
    }).catch(function (erro) {
      $('[data-lista-sites]').innerHTML = '<div class="vazio"><strong>Não foi possível carregar</strong>' +
        esc(erro.message) + '</div>';
      $('[data-resumo-sites]').textContent = '';
    });
  }

  function renderSites() {
    var alvo = $('[data-lista-sites]');
    var registrados = estado.sites.filter(function (s) { return s.registrado; }).length;

    $('[data-resumo-sites]').textContent = estado.sites.length
      ? estado.sites.length + ' site(s) — ' + registrados + ' registrado(s).'
      : 'Nenhum site cadastrado ainda.';

    if (!estado.sites.length) {
      alvo.innerHTML = '<div class="vazio"><strong>Nenhum site cadastrado</strong>' +
        'Clique em "Novo site" pra cadastrar seu primeiro scraper.</div>';
      return;
    }

    alvo.innerHTML = '<div class="lista-sites">' + estado.sites.map(cardSite).join('') + '</div>';
    ligarEventosSites();
  }

  function cardSite(s) {
    var registrado = !!s.registrado;
    var ativo = s.status === 'active';
    var objetivos = s.objetivos || [];
    var prontos = s.objetivos_prontos || [];
    var pendentes = objetivos.filter(function (o) { return prontos.indexOf(o) === -1; });

    return '' +
    '<article class="site-card" data-site="' + esc(s.id) + '">' +
      '<div class="site-card__topo">' +
        '<div>' +
          '<h3 class="site-card__nome">' + esc(s.name) +
            '<span class="versao">v' + s.version + '</span>' +
            '<span class="selo ' + (registrado ? 'selo--positivo' : 'selo--neutro') + '">' +
              (registrado ? 'Registrado' : 'Rascunho') + '</span>' +
            (registrado ? '<span class="selo ' + (ativo ? 'selo--positivo' : 'selo--neutro') + '">' +
              (ativo ? 'Ativo' : 'Pausado') + '</span>' : '') +
          '</h3>' +
          (s.description ? '<p class="site-card__descricao">' + esc(s.description) + '</p>' : '') +
          '<div class="site-card__meta">' +
            dado('URL base', s.url_base) +
            dado('Objetivos', objetivos.map(function (o) { return ROTULOS_OBJETIVO[o] || o; }).join(', ') || '—') +
            dado('Agenda', s.schedule_legivel) +
            dado('Última execução', dataHora(s.last_run_at)) +
            dado('Resultado', ROTULO_STATUS[s.last_run_status] || '—') +
          '</div>' +
        '</div>' +
        '<div class="site-card__acoes">' +
          '<button type="button" class="btn btn--linha btn--pequeno" data-editar>' +
            ICONES.editar + 'Editar</button>' +
          '<button type="button" class="btn btn--linha btn--pequeno" data-mapear>Mapear</button>' +
          (registrado
            ? '<button type="button" class="btn btn--primario btn--pequeno" data-executar>' + ICONES.play + 'Executar</button>'
            : '<button type="button" class="btn btn--primario btn--pequeno" data-registrar' +
              (pendentes.length ? ' disabled' : '') + '>Registrar</button>') +
          (registrado ? '<button type="button" class="btn btn--linha btn--pequeno" data-gerar-dag>' +
            ICONES.dag + (s.dag_id ? 'Regerar DAG' : 'Gerar DAG') + '</button>' : '') +
          '<button type="button" class="btn btn--linha btn--pequeno" data-remover>' +
            ICONES.remover + 'Remover</button>' +
        '</div>' +
      '</div>' +

      '<span class="aviso aviso--linha" data-aviso style="padding:0 16px 10px"></span>' +

      '<details class="dobra" data-dobra-objetivos open>' +
        '<summary class="dobra__resumo">Fluxo de coleta por objetivo' +
          (registrado ? '' : ' — mapeie e teste cada etapa antes de registrar') + '</summary>' +
        '<div class="dobra__corpo">' + objetivos.map(function (o) { return painelObjetivo(s, o); }).join('') + '</div>' +
      '</details>' +

      (registrado ? '' +
      '<details class="dobra" data-dobra-runs>' +
        '<summary class="dobra__resumo">Histórico de execuções</summary>' +
        '<div class="dobra__corpo" data-runs><div class="vazio vazio--compacto">Abrindo…</div></div>' +
      '</details>' +
      '<details class="dobra" data-dobra-dag>' +
        '<summary class="dobra__resumo">Código da DAG</summary>' +
        '<div class="dobra__corpo" data-dag><div class="vazio vazio--compacto">Abrindo…</div></div>' +
      '</details>' +
      '<details class="dobra" data-dobra-dados>' +
        '<summary class="dobra__resumo">Dados coletados</summary>' +
        '<div class="dobra__corpo" data-dados><div class="vazio vazio--compacto">Abrindo…</div></div>' +
      '</details>' : '') +
    '</article>';
  }

  // ── Fluxo de etapas (visual — caixas ligadas por conector) ──────────────

  function painelObjetivo(site, objetivo) {
    var mapa = (site.seletores || {})[objetivo] || {};
    var etapas = (mapa.etapas || []).slice().sort(function (a, b) { return a.ordem - b.ordem; });
    var pronto = (site.objetivos_prontos || []).indexOf(objetivo) !== -1;
    var maxOrdem = etapas.length ? etapas[etapas.length - 1].ordem : 0;

    var caixas = etapas.map(function (etapa, indice) {
      var ultima = etapa.ordem === maxOrdem;
      var classes = 'fluxo-etapa' + (etapa.validado_em ? ' is-testada' : '') + (ultima ? ' fluxo-etapa--final' : '');

      var caixa = '' +
        '<div class="' + classes + '" data-etapa-box data-objetivo="' + esc(objetivo) + '" data-ordem="' + etapa.ordem + '">' +
          '<div class="fluxo-etapa__acoes">' +
            (etapas.length > 1 ? '<button type="button" class="fluxo-etapa__acao" data-etapa-remover title="Remover etapa">×</button>' : '') +
          '</div>' +
          '<span class="fluxo-etapa__numero">' + (indice + 1) + '</span>' +
          '<span class="fluxo-etapa__rotulo">' + (ultima ? 'Extração final' : 'Navegação') + '</span>' +
          '<span class="selo ' + (etapa.validado_em ? 'selo--positivo' : 'selo--neutro') + ' fluxo-etapa__selo">' +
            (etapa.validado_em ? 'Testada' : 'Não testada') + '</span>' +
        '</div>';

      if (indice < etapas.length - 1) {
        caixa += '<div class="fluxo-seta">→<span class="fluxo-seta__campo">' + esc(etapa.campo_herdado || '?') + '</span></div>';
      }
      return caixa;
    }).join('');

    return '' +
      '<div class="scraper-objetivo-painel" data-objetivo-painel="' + esc(objetivo) + '">' +
        '<div class="scraper-objetivo-painel__titulo">' +
          esc(ROTULOS_OBJETIVO[objetivo] || objetivo) +
          '<span class="selo ' + (pronto ? 'selo--positivo' : 'selo--neutro') + '">' +
            (pronto ? 'Pronto' : 'Pendente') + '</span>' +
        '</div>' +
        '<div class="fluxo-etapas">' + caixas +
          '<button type="button" class="fluxo-add" data-add-etapa="' + esc(objetivo) + '">+ etapa</button>' +
        '</div>' +
      '</div>';
  }

  function montarTabela(linhas, colunaChave) {
    if (!linhas.length) return '<div class="scraper-preview-vazio">Nenhum registro na prévia.</div>';

    var colunas = [];
    linhas.forEach(function (linha) {
      Object.keys(linha).forEach(function (chave) {
        if (colunas.indexOf(chave) === -1) colunas.push(chave);
      });
    });

    return '<div class="tabela-wrap"><table class="tabela">' +
      '<thead><tr>' + colunas.map(function (c) {
        return '<th' + (c === colunaChave ? ' class="tabela__coluna-chave"' : '') + '>' + esc(c) + '</th>';
      }).join('') + '</tr></thead>' +
      '<tbody>' + linhas.map(function (linha) {
        return '<tr>' + colunas.map(function (c) {
          var valor = linha[c];
          var classe = 'dado-mono' + (c === colunaChave ? ' tabela__coluna-chave' : '');
          return '<td class="' + classe + '">' + (valor === null || valor === undefined ? '—' : esc(String(valor))) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
  }

  // ── Modal: etapa (URL, seletores, campo herdado, filtros, teste) ───────

  function camposFixosHtml(objetivo, selectors) {
    var campos = CAMPOS_POR_OBJETIVO[objetivo] || [];
    return '<div class="scraper-campos-seletor">' +
      campos.map(function (c) {
        return '<div class="scraper-campo-seletor"><label>' + esc(c.rotulo) + '</label>' +
          '<input type="text" data-campo-seletor="' + esc(c.chave) + '" value="' +
          esc((selectors || {})[c.chave] || '') + '" placeholder="seletor CSS"></div>';
      }).join('') +
    '</div>';
  }

  function linhaCampoLivre(nome, seletor) {
    return '<div class="scraper-campo-livre" data-campo-livre-linha>' +
      '<input type="text" data-campo-livre-nome value="' + esc(nome || '') + '" placeholder="nome do campo (ex.: url_anuncio)">' +
      '<input type="text" data-campo-livre-seletor value="' + esc(seletor || '') + '" placeholder="seletor CSS">' +
      '<button type="button" class="fluxo-etapa__acao" data-campo-livre-remover title="Remover campo">×</button>' +
    '</div>';
  }

  function linhaFiltro(f) {
    return '<div class="scraper-filtro-linha" data-filtro-linha>' +
      '<span class="mono">' + esc(f.nome_param) + '</span>' +
      '<span class="mono">' + esc(f.valor_amostra || '') + '</span>' +
      '<select data-filtro-papel>' + PAPEIS_FILTRO.map(function (p) {
        return '<option value="' + p + '"' + (p === f.papel ? ' selected' : '') + '>' + p + '</option>';
      }).join('') + '</select>' +
      '<button type="button" class="fluxo-etapa__acao" data-filtro-remover title="Remover filtro">×</button>' +
    '</div>';
  }

  function corpoModalEtapa(etapa, ultima, objetivo) {
    var selectors = etapa.selectors || {};
    var filtros = etapa.filtros || [];
    var html = '' +
      '<div class="campo" style="margin-bottom:10px">' +
        '<label>URL de amostra</label>' +
        '<input type="url" data-etapa-url value="' + esc(etapa.url_amostra || '') + '">' +
      '</div>';

    if (ultima) {
      html += camposFixosHtml(objetivo, selectors);
    } else {
      var camposLivres = selectors.campos || {};
      var nomes = Object.keys(camposLivres);
      html += '' +
        '<div class="campo" style="margin-bottom:10px">' +
          '<label>Seletor da linha (repete)</label>' +
          '<input type="text" data-etapa-linha value="' + esc(selectors.linha || '') + '" placeholder="seletor CSS do item repetido">' +
        '</div>' +
        '<p class="rotulo-secao">Campos desta linha</p>' +
        '<div data-campos-livres-lista>' +
          (nomes.length ? nomes.map(function (n) { return linhaCampoLivre(n, camposLivres[n]); }).join('') : linhaCampoLivre('', '')) +
        '</div>' +
        '<button type="button" class="btn btn--linha btn--pequeno" data-add-campo-livre style="margin:6px 0 12px">+ campo</button>' +
        '<div class="campo" style="margin-bottom:10px">' +
          '<label>Campo herdado pela próxima etapa</label>' +
          '<input type="text" list="datalist-campos-livres" data-etapa-herdado value="' + esc(etapa.campo_herdado || '') + '" placeholder="ex.: url_anuncio">' +
          '<datalist id="datalist-campos-livres">' + nomes.map(function (n) { return '<option value="' + esc(n) + '">'; }).join('') + '</datalist>' +
        '</div>';
    }

    html += '' +
      '<p class="rotulo-secao rotulo-secao--espacada">Filtros de URL ' +
        '<button type="button" class="btn btn--linha btn--pequeno" data-detectar-filtros style="margin-left:8px">Detectar filtros</button>' +
      '</p>' +
      '<div data-filtros-lista>' +
        (filtros.length ? filtros.map(linhaFiltro).join('') : '<div class="vazio vazio--compacto">Nenhum filtro detectado ainda.</div>') +
      '</div>' +
      '<div class="scraper-filtro-add">' +
        '<input type="text" data-filtro-manual-valor placeholder="valor exato na URL (ex.: ford)">' +
        '<select data-filtro-manual-papel>' + PAPEIS_FILTRO.map(function (p) {
          return '<option value="' + p + '">' + p + '</option>';
        }).join('') + '</select>' +
        '<button type="button" class="btn btn--linha btn--pequeno" data-add-filtro-manual>+ filtro</button>' +
      '</div>' +
      '<p class="campo__ajuda">Adicione um valor que aparece literalmente na URL de amostra (num parâmetro' +
        ' ou num trecho do caminho, ex.: ".../busca/ford/2026") e o papel dele — "Salvar" recalcula o' +
        ' template a partir da URL de amostra e dos filtros atuais.</p>' +
      '<p class="campo__ajuda" data-etapa-template>' + (etapa.url_template ? 'Template: ' + esc(etapa.url_template) : '') + '</p>' +

      '<div class="modal__acoes" style="justify-content:flex-start;gap:8px;margin-top:14px">' +
        '<button type="button" class="btn btn--linha btn--pequeno" data-btn-descobrir>Mapear com IA</button>' +
        '<button type="button" class="btn btn--primario btn--pequeno" data-btn-testar>Testar</button>' +
      '</div>' +
      '<span class="aviso" data-aviso-etapa></span>' +
      '<div class="scraper-resumo-ia" data-resumo-ia-etapa hidden></div>' +
      '<div data-preview-etapa></div>';

    return html;
  }

  function abrirModalEtapa(siteId, objetivo, ordem) {
    var site = estado.sites.filter(function (s) { return s.id === siteId; })[0];
    if (!site) return;
    var mapa = (site.seletores || {})[objetivo];
    var etapas = (mapa && mapa.etapas) || [];
    var etapa = etapas.filter(function (e) { return e.ordem === ordem; })[0];
    if (!etapa) return;

    var maxOrdem = etapas.length ? Math.max.apply(null, etapas.map(function (e) { return e.ordem; })) : 0;
    var ultima = ordem === maxOrdem;

    estado.modalEtapa = { siteId: siteId, objetivo: objetivo, ordem: ordem, ultima: ultima };

    $('[data-modal-etapa-titulo]').textContent = 'Etapa ' + (ordem + 1) + ' — ' + (ROTULOS_OBJETIVO[objetivo] || objetivo);
    $('[data-modal-etapa-legenda]').textContent = ultima
      ? 'Última etapa da cadeia — extrai os dados finais desta página.'
      : 'Etapa de navegação — encontra as linhas que levam à(s) próxima(s) página(s). Diga qual campo é a URL herdada pela etapa seguinte.';

    $('[data-modal-etapa-corpo]').innerHTML = corpoModalEtapa(etapa, ultima, objetivo);
    if (etapa.validado_em) {
      avisar($('[data-aviso-etapa]', $('[data-modal-etapa-corpo]')), 'Testada em ' + dataHora(etapa.validado_em) + '.', 'ok');
    }
    $('[data-modal-etapa]').hidden = false;
  }

  function coletarSelectorsModal() {
    var ctx = estado.modalEtapa;
    var corpo = $('[data-modal-etapa-corpo]');
    if (ctx.ultima) {
      var selectors = {};
      $$('[data-campo-seletor]', corpo).forEach(function (input) {
        var v = input.value.trim();
        if (v) selectors[input.dataset.campoSeletor] = v;
      });
      return selectors;
    }

    var campos = {};
    $$('[data-campo-livre-linha]', corpo).forEach(function (linha) {
      var nome = $('[data-campo-livre-nome]', linha).value.trim();
      var seletor = $('[data-campo-livre-seletor]', linha).value.trim();
      if (nome && seletor) campos[nome] = seletor;
    });
    var linhaInput = $('[data-etapa-linha]', corpo);
    return { linha: linhaInput ? linhaInput.value.trim() : '', campos: campos };
  }

  function atualizarDatalistCampos(corpo) {
    var datalist = $('#datalist-campos-livres', corpo);
    if (!datalist) return;
    var nomes = $$('[data-campo-livre-nome]', corpo).map(function (i) { return i.value.trim(); }).filter(Boolean);
    datalist.innerHTML = nomes.map(function (n) { return '<option value="' + esc(n) + '">'; }).join('');
  }

  function adicionarFiltroManualModal(corpo) {
    var valorInput = $('[data-filtro-manual-valor]', corpo);
    var papelSelect = $('[data-filtro-manual-papel]', corpo);
    var valor = valorInput.value.trim();
    if (!valor) return;

    var lista = $('[data-filtros-lista]', corpo);
    var vazio = $('.vazio', lista);
    if (vazio) vazio.remove();
    lista.insertAdjacentHTML('beforeend', linhaFiltro({ nome_param: valor, valor_amostra: valor, papel: papelSelect.value }));

    valorInput.value = '';
    avisar($('[data-aviso-etapa]', corpo), 'Filtro adicionado — clique em "Salvar" pra gerar o template.', 'ok');
  }

  function coletarFiltrosModal(corpo) {
    return $$('[data-filtro-linha]', corpo).map(function (linha) {
      var monos = $$('.mono', linha);
      var select = $('[data-filtro-papel]', linha);
      return {
        nome_param: monos[0] ? monos[0].textContent.trim() : '',
        valor_amostra: monos[1] ? monos[1].textContent.trim() : '',
        papel: select ? select.value : 'outro'
      };
    });
  }

  function salvarEtapaModal() {
    var ctx = estado.modalEtapa;
    if (!ctx) return;
    var corpo = $('[data-modal-etapa-corpo]');
    var botao = $('[data-salvar-modal-etapa]');
    var aviso = $('[data-aviso-etapa]', corpo);

    var url = $('[data-etapa-url]', corpo).value.trim();
    var payload = { url_amostra: url, selectors: coletarSelectorsModal(), filtros: coletarFiltrosModal(corpo) };
    if (!ctx.ultima) {
      var herdado = $('[data-etapa-herdado]', corpo);
      payload.campo_herdado = herdado ? herdado.value.trim() : '';
    }

    botao.disabled = true;
    api('PUT', '/sites/' + ctx.siteId + '/objetivos/' + ctx.objetivo + '/etapas/' + ctx.ordem, payload)
      .then(function () {
        avisar(aviso, 'Configuração salva.', 'ok');
        return carregarSites();
      })
      .catch(function (erro) {
        avisar(aviso, mensagemErro(erro), 'erro');
      })
      .finally(function () {
        botao.disabled = false;
      });
  }

  function testarEtapaModal() {
    var ctx = estado.modalEtapa;
    if (!ctx) return;
    var corpo = $('[data-modal-etapa-corpo]');
    var botao = $('[data-btn-testar]', corpo);
    var aviso = $('[data-aviso-etapa]', corpo);
    var resumoIa = $('[data-resumo-ia-etapa]', corpo);
    var previewWrap = $('[data-preview-etapa]', corpo);

    var url = $('[data-etapa-url]', corpo).value.trim();
    var corpoReq = { selectors: coletarSelectorsModal(), url: url || undefined };
    if (!ctx.ultima) {
      var herdado = $('[data-etapa-herdado]', corpo);
      corpoReq.campo_herdado = herdado ? herdado.value.trim() : '';
    }

    botao.disabled = true;
    avisar(aviso, 'Testando…');
    resumoIa.hidden = true;
    previewWrap.innerHTML = '';

    api('POST', '/sites/' + ctx.siteId + '/objetivos/' + ctx.objetivo + '/etapas/' + ctx.ordem + '/test', corpoReq).then(function (r) {
      avisar(aviso, r.sucesso ? (r.total + ' registro(s) encontrado(s).') : 'Nenhum registro encontrado — ajuste os seletores.',
        r.sucesso ? 'ok' : 'erro');

      if (r.resumo_ia) {
        resumoIa.innerHTML = '<strong>Resumo da IA:</strong> ' + esc(r.resumo_ia) +
          '<span class="scraper-resumo-ia__pergunta">É exatamente isso que essa página deveria mostrar? Confira a tabela abaixo.</span>';
        resumoIa.hidden = false;
      }

      previewWrap.innerHTML = montarTabela(r.preview || [], COLUNA_CHAVE_POR_OBJETIVO[ctx.objetivo]);
      return carregarSites();
    }).catch(function (erro) {
      avisar(aviso, mensagemErro(erro), 'erro');
    }).finally(function () {
      botao.disabled = false;
    });
  }

  function descobrirEtapaModal() {
    var ctx = estado.modalEtapa;
    if (!ctx) return;
    var corpo = $('[data-modal-etapa-corpo]');
    var url = $('[data-etapa-url]', corpo).value.trim();
    var aviso = $('[data-aviso-etapa]', corpo);
    if (!url) { avisar(aviso, 'Informe a URL de amostra antes de mapear.', 'erro'); return; }

    var botao = $('[data-btn-descobrir]', corpo);
    botao.disabled = true;
    avisar(aviso, 'Mapeando com IA…');

    api('PUT', '/sites/' + ctx.siteId + '/objetivos/' + ctx.objetivo + '/etapas/' + ctx.ordem, { url_amostra: url })
      .then(function () {
        return api('POST', '/sites/' + ctx.siteId + '/objetivos/' + ctx.objetivo + '/etapas/' + ctx.ordem + '/discover', {});
      })
      .then(function (r) {
        if (!r.sucesso) {
          avisar(aviso, (r.erro && r.erro.message) || 'Falha no mapeamento.', 'erro');
          return;
        }
        return carregarSites().then(function () {
          abrirModalEtapa(ctx.siteId, ctx.objetivo, ctx.ordem);
          avisar($('[data-aviso-etapa]', $('[data-modal-etapa-corpo]')), 'Seletores sugeridos — confira e clique em "Testar".', 'ok');
        });
      })
      .catch(function (erro) {
        avisar(aviso, mensagemErro(erro), 'erro');
      });
  }

  function detectarFiltrosModal() {
    var ctx = estado.modalEtapa;
    if (!ctx) return;
    var corpo = $('[data-modal-etapa-corpo]');
    var url = $('[data-etapa-url]', corpo).value.trim();
    var botao = $('[data-detectar-filtros]', corpo);
    var aviso = $('[data-aviso-etapa]', corpo);
    botao.disabled = true;
    avisar(aviso, 'Detectando filtros…');

    api('POST', '/sites/' + ctx.siteId + '/objetivos/' + ctx.objetivo + '/etapas/' + ctx.ordem + '/detectar-filtros', { url: url || undefined })
      .then(function (r) {
        $('[data-filtros-lista]', corpo).innerHTML = (r.filtros || []).map(linhaFiltro).join('') ||
          '<div class="vazio vazio--compacto">Nenhum parâmetro de URL encontrado.</div>';
        $('[data-etapa-template]', corpo).textContent = r.url_template ? ('Template: ' + r.url_template) : '';
        avisar(aviso, 'Filtros detectados.', 'ok');
      })
      .catch(function (erro) {
        avisar(aviso, mensagemErro(erro), 'erro');
      })
      .finally(function () {
        botao.disabled = false;
      });
  }

  function adicionarEtapa(siteId, objetivo) {
    api('POST', '/sites/' + siteId + '/objetivos/' + objetivo + '/etapas', {}).then(function (r) {
      return carregarSites().then(function () {
        abrirModalEtapa(siteId, objetivo, r.etapa.ordem);
      });
    }).catch(function (erro) {
      avisarPagina('Não foi possível adicionar a etapa: ' + mensagemErro(erro));
    });
  }

  function removerEtapa(siteId, objetivo, ordem) {
    if (!confirm('Remover esta etapa da cadeia?')) return;
    api('DELETE', '/sites/' + siteId + '/objetivos/' + objetivo + '/etapas/' + ordem).then(function () {
      carregarSites();
    }).catch(function (erro) {
      avisarPagina('Não foi possível remover a etapa: ' + mensagemErro(erro));
    });
  }

  // ── Dados coletados (BigQuery) por site ─────────────────────────────────

  function corpoDadosSite(site) {
    return '' +
      '<div class="grade grade--2" style="margin-bottom:10px">' +
        '<div class="campo"><label>Marca</label><input type="text" data-dados-marca placeholder="ex.: Ford"></div>' +
        '<div class="campo"><label>Modelo</label><input type="text" data-dados-modelo placeholder="ex.: Ranger"></div>' +
      '</div>' +
      '<button type="button" class="btn btn--linha btn--pequeno" data-dados-filtrar style="margin-bottom:14px">Filtrar</button>' +
      site.objetivos.map(function (o) {
        return '<div class="scraper-dados-secao" data-dados-secao="' + esc(o) + '">' +
          '<p class="scraper-dados-secao__titulo">' + esc(ROTULOS_OBJETIVO[o] || o) + '</p>' +
          '<div data-dados-tabela="' + esc(o) + '"><div class="vazio vazio--compacto">Carregando…</div></div>' +
        '</div>';
      }).join('');
  }

  function carregarDadosSite(id, card) {
    var alvo = $('[data-dados]', card);
    var site = estado.sites.filter(function (s) { return s.id === id; })[0];
    if (!site) return;

    if (!$('[data-dados-secao]', alvo)) {
      alvo.innerHTML = corpoDadosSite(site);
      $('[data-dados-filtrar]', alvo).addEventListener('click', function () {
        buscarDadosSite(id, alvo, site);
      });
    }
    buscarDadosSite(id, alvo, site);
  }

  function buscarDadosSite(id, alvo, site) {
    var marca = ($('[data-dados-marca]', alvo) || {}).value || '';
    var modelo = ($('[data-dados-modelo]', alvo) || {}).value || '';

    site.objetivos.forEach(function (o) {
      var wrap = $('[data-dados-tabela="' + o + '"]', alvo);
      var q = '?site_id=' + encodeURIComponent(id) +
        (marca.trim() ? '&marca=' + encodeURIComponent(marca.trim()) : '') +
        (modelo.trim() ? '&modelo=' + encodeURIComponent(modelo.trim()) : '');

      api('GET', ROTA_DADOS[o] + q).then(function (r) {
        wrap.innerHTML = montarTabela(r.linhas || [], COLUNA_CHAVE_POR_OBJETIVO[o]);
        // Guarda o último resultado — alimenta o chat widget (ver final do
        // arquivo), já que a resposta hoje só é jogada direto na tela.
        estado.ultimosDados[id] = estado.ultimosDados[id] || {};
        estado.ultimosDados[id][o] = { marca: marca, modelo: modelo, linhas: r.linhas || [] };
      }).catch(function (erro) {
        wrap.innerHTML = '<div class="vazio vazio--compacto">' + esc(mensagemErro(erro)) + '</div>';
      });
    });
  }

  // ── Ações por site ───────────────────────────────────────────────────────

  function ligarEventosSites() {
    $$('[data-site]').forEach(function (card) {
      var id = card.dataset.site;
      var aviso = $('[data-aviso]', card);

      $('[data-editar]', card).addEventListener('click', function () {
        var site = estado.sites.filter(function (s) { return s.id === id; })[0];
        if (site) abrirModalSite(site);
      });

      $('[data-mapear]', card).addEventListener('click', function () {
        mapearSite(id, card, aviso);
      });

      var botaoRegistrar = $('[data-registrar]', card);
      if (botaoRegistrar) {
        botaoRegistrar.addEventListener('click', function () {
          registrarSite(id, card, aviso);
        });
      }

      var botaoExecutar = $('[data-executar]', card);
      if (botaoExecutar) {
        botaoExecutar.addEventListener('click', function () {
          executarSite(id, card, aviso);
        });
      }

      var botaoDag = $('[data-gerar-dag]', card);
      if (botaoDag) {
        botaoDag.addEventListener('click', function () {
          gerarDagSite(id, card, aviso);
        });
      }

      $('[data-remover]', card).addEventListener('click', function () {
        if (!confirm('Remover este site? Os dados já coletados no BigQuery não são apagados.')) return;
        api('DELETE', '/sites/' + id).then(carregarSites).catch(function (erro) {
          avisar(aviso, mensagemErro(erro), 'erro');
        });
      });

      $$('[data-etapa-box]', card).forEach(function (caixa) {
        caixa.addEventListener('click', function (evento) {
          if (evento.target.closest('[data-etapa-remover]')) return;
          abrirModalEtapa(id, caixa.dataset.objetivo, Number(caixa.dataset.ordem));
        });
        var botaoRemoverEtapa = $('[data-etapa-remover]', caixa);
        if (botaoRemoverEtapa) {
          botaoRemoverEtapa.addEventListener('click', function (evento) {
            evento.stopPropagation();
            removerEtapa(id, caixa.dataset.objetivo, Number(caixa.dataset.ordem));
          });
        }
      });

      $$('[data-add-etapa]', card).forEach(function (botaoAdd) {
        botaoAdd.addEventListener('click', function () {
          adicionarEtapa(id, botaoAdd.dataset.addEtapa);
        });
      });

      var dobraRuns = $('[data-dobra-runs]', card);
      if (dobraRuns) {
        dobraRuns.addEventListener('toggle', function (evento) {
          if (evento.target.open) carregarRuns(id, card);
        });
      }

      var dobraDag = $('[data-dobra-dag]', card);
      if (dobraDag) {
        dobraDag.addEventListener('toggle', function (evento) {
          if (evento.target.open) carregarDag(id, card);
        });
      }

      var dobraDados = $('[data-dobra-dados]', card);
      if (dobraDados) {
        dobraDados.addEventListener('toggle', function (evento) {
          if (evento.target.open) carregarDadosSite(id, card);
        });
      }
    });
  }

  function mapearSite(id, card, aviso) {
    var botao = $('[data-mapear]', card);
    botao.disabled = true;
    avisar(aviso, 'Mapeando (chama a IA — pode levar alguns segundos por etapa)…');

    api('POST', '/sites/' + id + '/discover', {}).then(function (resposta) {
      var falhas = [];
      Object.keys(resposta.resultados || {}).forEach(function (objetivo) {
        var porEtapa = resposta.resultados[objetivo] || {};
        Object.keys(porEtapa).forEach(function (ordem) {
          if (!porEtapa[ordem].sucesso) falhas.push((ROTULOS_OBJETIVO[objetivo] || objetivo) + ' (etapa ' + (Number(ordem) + 1) + ')');
        });
      });

      avisar(aviso, falhas.length ? ('Mapeamento parcial — falhou: ' + falhas.join(', ')) : 'Mapeamento concluído em todas as etapas.',
        falhas.length ? 'erro' : 'ok');
      return carregarSites();
    }).catch(function (erro) {
      avisar(aviso, mensagemErro(erro), 'erro');
    }).finally(function () {
      botao.disabled = false;
    });
  }

  function registrarSite(id, card, aviso) {
    var botao = $('[data-registrar]', card);
    botao.disabled = true;
    avisar(aviso, 'Registrando…');

    api('POST', '/sites/' + id + '/register', {}).then(function () {
      avisar(aviso, 'Site registrado!', 'ok');
      return carregarSites();
    }).catch(function (erro) {
      avisar(aviso, mensagemErro(erro), 'erro');
      botao.disabled = false;
    });
  }

  function executarSite(id, card, aviso) {
    var botao = $('[data-executar]', card);
    botao.disabled = true;
    avisar(aviso, 'Execução iniciada…');

    api('POST', '/sites/' + id + '/run', {}).then(function (resposta) {
      acompanharExecucao(resposta.run_id, id, card, aviso);
    }).catch(function (erro) {
      avisar(aviso, mensagemErro(erro), 'erro');
      botao.disabled = false;
    });
  }

  function acompanharExecucao(runId, siteId, card, aviso) {
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

          var botaoExecutar = $('[data-executar]', card);
          if (botaoExecutar) botaoExecutar.disabled = false;

          var dobra = $('[data-dobra-runs]', card);
          if (dobra && dobra.open) carregarRuns(siteId, card);

          carregarSites();
          carregarResumo();
        }
      }).catch(function (erro) {
        clearInterval(estado.timers[runId]);
        delete estado.timers[runId];
        avisar(aviso, 'Falha ao acompanhar a execução: ' + erro.message, 'erro');
        var botaoExecutar = $('[data-executar]', card);
        if (botaoExecutar) botaoExecutar.disabled = false;
      });
    }

    estado.timers[runId] = setInterval(consultar, INTERVALO_POLLING);
    consultar();
  }

  function gerarDagSite(id, card, aviso) {
    avisar(aviso, 'Gerando DAG…');
    api('POST', '/sites/' + id + '/generate-dag').then(function (dag) {
      avisar(aviso, 'DAG "' + dag.dag_id + '" gravada em ' + dag.caminho + '.', 'ok');
      return carregarSites();
    }).catch(function (erro) {
      avisar(aviso, mensagemErro(erro), 'erro');
    });
  }

  function carregarRuns(id, card) {
    var alvo = $('[data-runs]', card);

    api('GET', '/sites/' + id + '/runs').then(function (resposta) {
      var runs = resposta.runs || [];

      if (!runs.length) {
        alvo.innerHTML = '<div class="vazio vazio--compacto">Nenhuma execução registrada para este site.</div>';
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
              '<td class="mono">v' + esc(r.site_version) + '</td>' +
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

    api('GET', '/sites/' + id + '/dag').then(function (dag) {
      var situacao = dag.gravada
        ? (dag.desatualizada
            ? 'gravada da versão v' + dag.dag_version + ' — regere para v' + dag.site_version
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

  /** Realce de sintaxe Python mínimo — mesma implementação do Gov Mode/Pipelines. */
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

  // ── Modal: novo site / editar site ──────────────────────────────────────

  var modal = $('[data-modal-site]');

  function abrirModalSite(site) {
    $('[data-form-site]').reset();
    $$('[data-amostra-campo]').forEach(function (campo) { campo.hidden = true; });

    var checkAnuncios = $('[data-objetivo-check="anuncios"]');
    var checkComentarios = $('[data-objetivo-check="comentarios"]');
    checkComentarios.disabled = true;
    checkComentarios.checked = false;

    estado.modalSite = site ? { id: site.id } : null;
    $('#modal-site-titulo').textContent = site ? 'Editar site' : 'Novo site';
    $('[data-confirmar-site]').textContent = site ? 'Salvar alterações' : 'Criar site';

    if (site) {
      $('[data-site-nome]').value = site.name || '';
      $('[data-site-url]').value = site.url_base || '';

      (site.objetivos || []).forEach(function (objetivo) {
        var checkbox = $('[data-objetivo-check="' + objetivo + '"]');
        var campoAmostra = $('[data-amostra-campo="' + objetivo + '"]');
        if (checkbox) checkbox.checked = true;
        if (campoAmostra) campoAmostra.hidden = false;
        var campoUrl = $('[data-amostra-url="' + objetivo + '"]');
        if (campoUrl) campoUrl.value = (site.amostra_urls || {})[objetivo] || '';
      });

      if (checkAnuncios.checked) checkComentarios.disabled = false;

      var provider = (site.llm && site.llm.provider) || '';
      $('[data-site-provider]').value = provider;
      popularModelosSite(provider);
      if (provider) {
        var modelo = (site.llm && site.llm.model) || '';
        var modeloSelect = $('[data-site-model]');
        var opcaoExiste = modelo && $$('option', modeloSelect).some(function (o) { return o.value === modelo; });
        if (modelo && !opcaoExiste) {
          modeloSelect.value = '__custom__';
          var custom = $('[data-site-model-custom]');
          custom.hidden = false;
          custom.value = modelo;
        } else {
          modeloSelect.value = modelo;
        }
      }
    } else {
      popularModelosSite('');
    }

    avisar($('[data-aviso-modal-site]'), '');
    modal.hidden = false;
  }

  $('[data-novo-site]').addEventListener('click', function () {
    abrirModalSite(null);
  });

  $('[data-fechar-modal-site]').addEventListener('click', function () { modal.hidden = true; });

  modal.addEventListener('click', function (evento) {
    if (evento.target === modal) modal.hidden = true;
  });

  $$('[data-objetivo-check]').forEach(function (checkbox) {
    checkbox.addEventListener('change', function () {
      var objetivo = checkbox.dataset.objetivoCheck;
      var campoAmostra = $('[data-amostra-campo="' + objetivo + '"]');
      if (campoAmostra) campoAmostra.hidden = !checkbox.checked;

      if (objetivo === 'anuncios') {
        var checkComentarios = $('[data-objetivo-check="comentarios"]');
        checkComentarios.disabled = !checkbox.checked;
        if (!checkbox.checked) {
          checkComentarios.checked = false;
          $('[data-amostra-campo="comentarios"]').hidden = true;
        }
      }
    });
  });

  $('[data-site-provider]').addEventListener('change', function () {
    popularModelosSite(this.value);
  });

  $('[data-site-model]').addEventListener('change', function () {
    var custom = $('[data-site-model-custom]');
    custom.hidden = this.value !== '__custom__';
    if (!custom.hidden) custom.focus();
  });

  $('[data-form-site]').addEventListener('submit', function (evento) {
    evento.preventDefault();

    var objetivos = [];
    var amostraUrls = {};
    $$('[data-objetivo-check]').forEach(function (checkbox) {
      if (!checkbox.checked) return;
      var objetivo = checkbox.dataset.objetivoCheck;
      objetivos.push(objetivo);
      var campoUrl = $('[data-amostra-url="' + objetivo + '"]');
      if (campoUrl) amostraUrls[objetivo] = campoUrl.value.trim();
    });

    if (!objetivos.length) {
      avisar($('[data-aviso-modal-site]'), 'Selecione ao menos um objetivo.', 'erro');
      return;
    }

    var corpo = {
      name: $('[data-site-nome]').value.trim(),
      url_base: $('[data-site-url]').value.trim(),
      objetivos: objetivos,
      amostra_urls: amostraUrls
    };

    var provider = $('[data-site-provider]').value.trim();
    if (provider) {
      var modeloSelect = $('[data-site-model]').value;
      var modelo = modeloSelect === '__custom__' ? $('[data-site-model-custom]').value.trim() : modeloSelect.trim();
      corpo.llm = { provider: provider, model: modelo || null };
    }

    var editando = estado.modalSite;
    var botao = $('[data-confirmar-site]');
    botao.disabled = true;
    avisar($('[data-aviso-modal-site]'), editando ? 'Salvando alterações…' : 'Criando site…');

    var chamada = editando ? api('PUT', '/sites/' + editando.id, corpo) : api('POST', '/sites', corpo);

    chamada.then(function () {
      avisar($('[data-aviso-modal-site]'), editando ? 'Site atualizado!' : 'Site criado!', 'ok');
      setTimeout(function () {
        modal.hidden = true;
        carregarSites();
        carregarResumo();
      }, 600);
    }).catch(function (erro) {
      avisar($('[data-aviso-modal-site]'), mensagemErro(erro), 'erro');
    }).finally(function () {
      botao.disabled = false;
    });
  });

  // ── Modal: etapa — delegação de eventos (bindada uma única vez) ─────────

  var modalEtapa = $('[data-modal-etapa]');
  var modalEtapaCorpo = $('[data-modal-etapa-corpo]');

  $('[data-fechar-modal-etapa]').addEventListener('click', function () {
    modalEtapa.hidden = true;
    estado.modalEtapa = null;
  });

  $('[data-salvar-modal-etapa]').addEventListener('click', salvarEtapaModal);

  modalEtapa.addEventListener('click', function (evento) {
    if (evento.target === modalEtapa) {
      modalEtapa.hidden = true;
      estado.modalEtapa = null;
    }
  });

  modalEtapaCorpo.addEventListener('click', function (evento) {
    if (evento.target.closest('[data-campo-livre-remover]')) {
      evento.target.closest('[data-campo-livre-linha]').remove();
      atualizarDatalistCampos(modalEtapaCorpo);
      return;
    }
    if (evento.target.closest('[data-add-campo-livre]')) {
      $('[data-campos-livres-lista]', modalEtapaCorpo).insertAdjacentHTML('beforeend', linhaCampoLivre('', ''));
      atualizarDatalistCampos(modalEtapaCorpo);
      return;
    }
    if (evento.target.closest('[data-filtro-remover]')) {
      evento.target.closest('[data-filtro-linha]').remove();
      if (!$('[data-filtro-linha]', modalEtapaCorpo)) {
        $('[data-filtros-lista]', modalEtapaCorpo).innerHTML = '<div class="vazio vazio--compacto">Nenhum filtro detectado ainda.</div>';
      }
      return;
    }
    if (evento.target.closest('[data-add-filtro-manual]')) { adicionarFiltroManualModal(modalEtapaCorpo); return; }
    if (evento.target.closest('[data-btn-descobrir]')) { descobrirEtapaModal(); return; }
    if (evento.target.closest('[data-detectar-filtros]')) { detectarFiltrosModal(); return; }
    if (evento.target.closest('[data-btn-testar]')) { testarEtapaModal(); return; }
  });

  modalEtapaCorpo.addEventListener('input', function (evento) {
    if (evento.target.matches('[data-campo-livre-nome]')) atualizarDatalistCampos(modalEtapaCorpo);
  });

  // ── Inicialização ──────────────────────────────────────────────────────

  $('[data-recarregar-sites]').addEventListener('click', carregarSites);

  carregarResumo();
  carregarSites();
  carregarConfig();

  // ── Chat widget — contexto = últimos dados coletados vistos na tela ──────

  if (window.registrarContextoChat) {
    window.registrarContextoChat('Scraper Mode — dados coletados', function () {
      var sitesComDados = Object.keys(estado.ultimosDados);
      if (!sitesComDados.length) return null;
      return sitesComDados.map(function (siteId) {
        var porObjetivo = estado.ultimosDados[siteId];
        var site = (estado.sites || []).filter(function (s) { return s.id === siteId; })[0];
        return {
          site: (site && site.name) || siteId,
          dados: Object.keys(porObjetivo).map(function (objetivo) {
            var d = porObjetivo[objetivo];
            return {
              objetivo: objetivo, marca: d.marca || null, modelo: d.modelo || null,
              linhas: (d.linhas || []).slice(0, 30)
            };
          })
        };
      });
    }, [
      'Quais atributos de ficha técnica foram coletados?',
      'Resuma os comentários coletados deste site.'
    ]);
  }
})();
