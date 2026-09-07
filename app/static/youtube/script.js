(function () {
  var form = document.querySelector('[data-form-youtube]');
  var resultado = document.querySelector('[data-resultado-youtube]');
  var botao = document.querySelector('[data-botao-buscar-yt]');

  if (!form) return;

  var SELO_POR_SENTIMENTO = {
    positivo: 'selo--positivo',
    neutro: 'selo--neutro',
    negativo: 'selo--negativo'
  };

  function renderVideos(videos) {
    if (!videos.length) {
      resultado.innerHTML =
        '<div class="vazio"><strong>Nenhum vídeo encontrado</strong>Tente outra palavra-chave ou cole o link de um vídeo específico.</div>';
      return;
    }

    var itens = videos.map(function (v) {
      var classeSelo = SELO_POR_SENTIMENTO[v.sentimento] || 'selo--pendente';
      return (
        '<div class="video-item">' +
          '<div>' +
            '<p class="video-item__titulo">' + v.titulo + '</p>' +
            '<p class="video-item__canal">' + v.canal + '</p>' +
            '<div class="video-item__acoes">' +
              '<a class="btn btn--linha" href="' + v.link + '" target="_blank" rel="noopener">Ver vídeo</a>' +
              '<button type="button" class="btn btn--linha">Ver transcrição</button>' +
            '</div>' +
          '</div>' +
          '<div class="video-item__lado"><span class="selo ' + classeSelo + '">' + v.sentimento_rotulo + '</span></div>' +
        '</div>'
      );
    }).join('');

    resultado.innerHTML = '<div class="lista-videos">' + itens + '</div>';
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();

    var dados = new FormData(form);
    botao.disabled = true;
    resultado.innerHTML = '<div class="vazio">Buscando vídeos e transcrevendo o áudio…</div>';

    // TODO: substituir pela chamada real ao backend, por exemplo:
    // fetch('/api/youtube/run', { method: 'POST', body: dados })
    //   .then(function (r) { return r.json(); })
    //   .then(function (json) { renderVideos(json.videos); })
    //   .finally(function () { botao.disabled = false; });

    setTimeout(function () {
      botao.disabled = false;
      renderVideos([]); // placeholder: troque por dados reais do backend
    }, 900);
  });
})();
