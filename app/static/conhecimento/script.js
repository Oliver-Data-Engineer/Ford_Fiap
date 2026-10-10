/* ============================================================
   BASE DE CONHECIMENTO — visualização do banco vetorial de grafos
   (transcrições de vídeo, PDFs de ficha técnica e comentários
   coletados). Todo o dado desta página é gerado localmente, aqui
   neste arquivo — não há chamada de rede nem backend envolvido.
   ============================================================ */
(function () {
  'use strict';

  var raizGrafo = document.querySelector('[data-conh-grafo]');
  if (!raizGrafo) return;

  var DIMENSOES_EMBEDDING = 768;
  var N_VETORES = 42;

  var COLECOES = {
    transcricao: { rotulo: 'Transcrições de vídeo', cor: '#0c7488' },
    pdf:         { rotulo: 'PDFs de ficha técnica', cor: '#b3672e' },
    comentario:  { rotulo: 'Comentários coletados', cor: '#8a3b5e' }
  };

  // ── Gerador pseudo-aleatório determinístico (mesma seed = mesmo grafo a
  // cada carregamento — mais fácil de conferir/depurar que Math.random()) ──

  function criarGeradorSeed(seed) {
    var estado = seed >>> 0;
    return function () {
      estado = (estado * 1664525 + 1013904223) >>> 0;
      return estado / 4294967296;
    };
  }

  var aleatorio = criarGeradorSeed(20260915);

  function escolher(lista) { return lista[Math.floor(aleatorio() * lista.length)]; }
  function inteiroEntre(min, max) { return min + Math.floor(aleatorio() * (max - min + 1)); }

  // ── Conteúdo (temática automotiva, coerente com o resto do app) ──────────

  var MODELOS = ['Territory', 'Ranger', 'Ka', 'Bronco Sport', 'Maverick', 'EcoSport', 'Edge'];
  var TEMAS = ['motor', 'consumo de combustível', 'freios', 'suspensão', 'multimídia', 'porta-malas',
    'câmbio automático', 'tração 4x4', 'faróis de LED', 'piloto automático adaptativo', 'ar-condicionado', 'garantia'];
  var AUTORES = ['@carrosdodia', '@revisaoauto_br', '@testedrivebr', '@motorclube', '@garagemdocarro',
    'joao.silva', 'mariana_r', 'pedro.oliveira', 'anacosta', 'lucasm'];
  var CANAIS = ['Canal Auto Notícias', 'Garagem 22', 'Test Drive Semanal', 'Motor & Cia'];

  function fraseTranscricao(tema, modelo) {
    return 'Nesse trecho o apresentador comenta sobre ' + tema + ' do ' + modelo +
      ', comparando com a geração anterior e destacando os pontos que mais chamaram atenção durante o test drive.';
  }

  function fraseComentario(tema, modelo) {
    var opinioes = ['achei surpreendente', 'esperava mais', 'ficou muito bom', 'não gostei muito',
      'compensa o preço', 'é o principal motivo pra eu comprar', 'ainda tenho dúvida sobre isso'];
    return 'Sobre ' + tema + ' do ' + modelo + ', ' + escolher(opinioes) + '.';
  }

  function tituloPdf(modelo) {
    return 'Ficha técnica — Ford ' + modelo + ' ' + inteiroEntre(2022, 2026) + '.pdf';
  }

  // ── Geração dos vetores (nós) ──────────────────────────────────────────

  function gerarEmbedding() {
    var vetor = [];
    for (var i = 0; i < DIMENSOES_EMBEDDING; i++) vetor.push(aleatorio() * 2 - 1);
    return vetor;
  }

  function similaridadeCosseno(a, b) {
    var produto = 0, normaA = 0, normaB = 0;
    for (var i = 0; i < a.length; i++) {
      produto += a[i] * b[i];
      normaA += a[i] * a[i];
      normaB += b[i] * b[i];
    }
    return produto / (Math.sqrt(normaA) * Math.sqrt(normaB) || 1);
  }

  function gerarVetores() {
    var vetores = [];
    var tipos = Object.keys(COLECOES);

    for (var i = 0; i < N_VETORES; i++) {
      var tipo = tipos[i % tipos.length];
      var modelo = escolher(MODELOS);
      var tema = escolher(TEMAS);
      var item = {
        id: 'v' + i,
        tipo: tipo,
        embedding: gerarEmbedding()
      };

      if (tipo === 'transcricao') {
        item.titulo = 'Transcrição — "' + escolher(CANAIS) + '" sobre ' + modelo;
        item.trecho = fraseTranscricao(tema, modelo);
        item.fonte = escolher(CANAIS);
      } else if (tipo === 'pdf') {
        item.titulo = tituloPdf(modelo);
        item.trecho = 'Seção de especificações técnicas mencionando ' + tema + ' na versão ' + modelo + '.';
        item.fonte = 'PDF';
      } else {
        item.titulo = 'Comentário de ' + escolher(AUTORES);
        item.trecho = fraseComentario(tema, modelo);
        item.fonte = escolher(AUTORES);
      }

      vetores.push(item);
    }
    return vetores;
  }

  // ── Arestas (similaridade entre os K vizinhos mais próximos de cada nó) ──

  function gerarArestas(vetores, k) {
    var arestas = [];
    var vistos = {};

    vetores.forEach(function (origem) {
      var similares = vetores
        .filter(function (v) { return v.id !== origem.id; })
        .map(function (v) { return { id: v.id, score: similaridadeCosseno(origem.embedding, v.embedding) }; })
        .sort(function (a, b) { return b.score - a.score; })
        .slice(0, k);

      similares.forEach(function (s) {
        var chave = [origem.id, s.id].sort().join('::');
        if (vistos[chave]) return;
        vistos[chave] = true;
        arestas.push({ from: origem.id, to: s.id, score: s.score });
      });
    });

    return arestas;
  }

  // ── Dados ────────────────────────────────────────────────────────────

  var vetores = gerarVetores();
  var arestas = gerarArestas(vetores, 3);
  var vetoresPorId = {};
  vetores.forEach(function (v) { vetoresPorId[v.id] = v; });

  // ── KPIs ─────────────────────────────────────────────────────────────

  function $(seletor) { return document.querySelector(seletor); }

  $('[data-conh-kpi-vetores]').textContent = vetores.length.toLocaleString('pt-BR');
  $('[data-conh-kpi-dimensoes]').textContent = DIMENSOES_EMBEDDING.toLocaleString('pt-BR');
  $('[data-conh-kpi-colecoes]').textContent = Object.keys(COLECOES).length;

  // ── Legenda / filtro por coleção ─────────────────────────────────────

  var colecoesAtivas = {};
  Object.keys(COLECOES).forEach(function (tipo) { colecoesAtivas[tipo] = true; });

  var legendaEl = $('[data-conh-legenda]');
  legendaEl.innerHTML = Object.keys(COLECOES).map(function (tipo) {
    var c = COLECOES[tipo];
    var n = vetores.filter(function (v) { return v.tipo === tipo; }).length;
    return '<div class="conh-legenda__item" data-conh-legenda-item="' + tipo + '" data-ativo="true">' +
      '<span class="conh-legenda__ponto" style="background:' + c.cor + '"></span>' +
      c.rotulo + ' (' + n + ')</div>';
  }).join('');

  // ── Grafo (vis-network) ──────────────────────────────────────────────

  var nos = new vis.DataSet(vetores.map(function (v) {
    var c = COLECOES[v.tipo];
    return {
      id: v.id,
      label: v.titulo.length > 26 ? v.titulo.slice(0, 24) + '…' : v.titulo,
      title: v.titulo,
      color: { background: c.cor, border: c.cor, highlight: { background: c.cor, border: '#16233a' } },
      font: { color: '#16233a', size: 11 },
      shape: 'dot',
      size: 12
    };
  }));

  var arestasVis = new vis.DataSet(arestas.map(function (a, i) {
    return {
      id: 'a' + i,
      from: a.from,
      to: a.to,
      value: a.score,
      title: 'similaridade: ' + a.score.toFixed(3),
      color: { color: '#cdd3db', opacity: 0.6 }
    };
  }));

  var rede = new vis.Network(raizGrafo, { nodes: nos, edges: arestasVis }, {
    physics: { solver: 'forceAtlas2Based', stabilization: { iterations: 120 } },
    edges: { smooth: false, scaling: { min: 1, max: 4 } },
    nodes: { borderWidth: 1 },
    interaction: { hover: true, tooltipDelay: 120 }
  });

  // ── Painel lateral de detalhes ────────────────────────────────────────

  var painelEl = $('[data-conh-painel]');

  function embeddingPreview(vetor) {
    return vetor.slice(0, 8).map(function (n) { return n.toFixed(2); });
  }

  function renderDetalhe(vetor) {
    var c = COLECOES[vetor.tipo];
    var vizinhos = arestas
      .filter(function (a) { return a.from === vetor.id || a.to === vetor.id; })
      .map(function (a) {
        var outroId = a.from === vetor.id ? a.to : a.from;
        return { vetor: vetoresPorId[outroId], score: a.score };
      })
      .sort(function (a, b) { return b.score - a.score; });

    painelEl.innerHTML =
      '<span class="selo conh-detalhe__tipo" style="background:color-mix(in srgb, ' + c.cor + ' 16%, transparent); color:' + c.cor + '">' +
        c.rotulo + '</span>' +
      '<h3 class="conh-detalhe__titulo">' + escHtml(vetor.titulo) + '</h3>' +
      '<p class="conh-detalhe__trecho">' + escHtml(vetor.trecho) + '</p>' +
      '<p class="conh-detalhe__rotulo">Embedding — 8 de ' + DIMENSOES_EMBEDDING + ' dimensões</p>' +
      '<div class="conh-embedding">' + embeddingPreview(vetor.embedding).map(function (n) {
        return '<span class="conh-embedding__valor">' + n + '</span>';
      }).join('') + '</div>' +
      '<p class="conh-detalhe__rotulo">Vizinhos mais próximos</p>' +
      '<ul class="conh-vizinhos">' + vizinhos.map(function (v) {
        return '<li class="conh-vizinho" data-conh-ir-para="' + v.vetor.id + '">' +
          '<span class="conh-vizinho__nome">' + escHtml(v.vetor.titulo) + '</span>' +
          '<span class="conh-vizinho__score">' + v.score.toFixed(3) + '</span></li>';
      }).join('') + '</ul>';

    painelEl.querySelectorAll('[data-conh-ir-para]').forEach(function (el) {
      el.addEventListener('click', function () {
        var id = el.getAttribute('data-conh-ir-para');
        rede.selectNodes([id]);
        rede.focus(id, { scale: 1.1, animation: true });
        renderDetalhe(vetoresPorId[id]);
      });
    });
  }

  function escHtml(valor) {
    return String(valor)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  rede.on('click', function (params) {
    if (!params.nodes.length) return;
    renderDetalhe(vetoresPorId[params.nodes[0]]);
  });

  // ── Filtro por coleção (legenda funciona como toggle) ────────────────

  function aplicarFiltroColecoes() {
    nos.forEach(function (n) {
      var vetor = vetoresPorId[n.id];
      nos.update({ id: n.id, hidden: !colecoesAtivas[vetor.tipo] });
    });
  }

  legendaEl.querySelectorAll('[data-conh-legenda-item]').forEach(function (el) {
    el.addEventListener('click', function () {
      var tipo = el.getAttribute('data-conh-legenda-item');
      colecoesAtivas[tipo] = !colecoesAtivas[tipo];
      el.setAttribute('data-ativo', String(colecoesAtivas[tipo]));
      aplicarFiltroColecoes();
    });
  });

  // ── Busca por similaridade semântica ──────────────────────────────────
  //
  // O texto digitado é transformado num vetor determinístico (mesma frase
  // sempre gera o mesmo vetor) e comparado por similaridade de cosseno
  // contra os vetores indexados, igual à comparação feita entre eles.

  function hashTexto(texto) {
    var h = 0;
    for (var i = 0; i < texto.length; i++) {
      h = (h * 31 + texto.charCodeAt(i)) >>> 0;
    }
    return h;
  }

  function embeddingDaConsulta(texto) {
    var gerar = criarGeradorSeed(hashTexto(texto.trim().toLowerCase()) || 1);
    var vetor = [];
    for (var i = 0; i < DIMENSOES_EMBEDDING; i++) vetor.push(gerar() * 2 - 1);
    return vetor;
  }

  var campoBusca = $('[data-conh-busca-campo]');
  var botaoBusca = $('[data-conh-busca-botao]');
  var botaoLimparBusca = $('[data-conh-busca-limpar]');

  function executarBusca() {
    var termo = campoBusca.value.trim();
    if (!termo) return;

    var consulta = embeddingDaConsulta(termo);
    var ranking = vetores
      .map(function (v) { return { vetor: v, score: similaridadeCosseno(consulta, v.embedding) }; })
      .sort(function (a, b) { return b.score - a.score; })
      .slice(0, 6);

    var idsRanking = ranking.map(function (r) { return r.vetor.id; });
    rede.selectNodes(idsRanking);
    rede.fit({ nodes: idsRanking, animation: true });

    painelEl.innerHTML =
      '<p class="conh-detalhe__rotulo">Resultados para "' + escHtml(termo) + '"</p>' +
      '<div class="conh-resultados-busca">' + ranking.map(function (r) {
        var c = COLECOES[r.vetor.tipo];
        return '<div class="conh-vizinho" data-conh-ir-para="' + r.vetor.id + '">' +
          '<span class="conh-vizinho__nome"><span class="conh-legenda__ponto" style="background:' + c.cor +
            ';display:inline-block;margin-right:6px"></span>' + escHtml(r.vetor.titulo) + '</span>' +
          '<span class="conh-vizinho__score">' + r.score.toFixed(3) + '</span></div>';
      }).join('') + '</div>';

    painelEl.querySelectorAll('[data-conh-ir-para]').forEach(function (el) {
      el.addEventListener('click', function () {
        var id = el.getAttribute('data-conh-ir-para');
        rede.selectNodes([id]);
        rede.focus(id, { scale: 1.1, animation: true });
        renderDetalhe(vetoresPorId[id]);
      });
    });
  }

  botaoBusca.addEventListener('click', executarBusca);
  campoBusca.addEventListener('keydown', function (evento) {
    if (evento.key === 'Enter') executarBusca();
  });
  botaoLimparBusca.addEventListener('click', function () {
    campoBusca.value = '';
    rede.unselectAll();
    rede.fit({ animation: true });
    painelEl.innerHTML = '<div class="vazio vazio--compacto">Clique num vetor do grafo (ou faça uma busca) pra ver os detalhes aqui.</div>';
  });
})();
