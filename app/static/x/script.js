(function () {
  var form = document.querySelector('[data-form-x]');
  var resultado = document.querySelector('[data-resultado-x]');
  var botao = document.querySelector('[data-botao-buscar-x]');

  if (!form) return;

  var SELO_POR_SENTIMENTO = {
    positivo: 'selo--positivo',
    neutro: 'selo--neutro',
    negativo: 'selo--negativo'
  };

  function renderMencoes(mencoes) {
    if (!mencoes.length) {
      resultado.innerHTML =
        '<div class="vazio"><strong>Nenhuma menção encontrada</strong>Tente ampliar os termos da busca.</div>';
      return;
    }

    var itens = mencoes.map(function (m) {
      var classeSelo = SELO_POR_SENTIMENTO[m.sentimento] || 'selo--pendente';
      return (
        '<div class="mencao">' +
          '<div>' +
            '<p class="mencao__autor">' + m.autor + '</p>' +
            '<p class="mencao__texto">' + m.texto + '</p>' +
          '</div>' +
          '<span class="selo ' + classeSelo + '">' + m.sentimento_rotulo + '</span>' +
        '</div>'
      );
    }).join('');

    resultado.innerHTML = '<div class="lista-mencoes">' + itens + '</div>';
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();

    var dados = new FormData(form);
    botao.disabled = true;
    resultado.innerHTML = '<div class="vazio">Coletando menções na plataforma X…</div>';

    // TODO: substituir pela chamada real ao backend, por exemplo:
    // fetch('/api/x/run', { method: 'POST', body: dados })
    //   .then(function (r) { return r.json(); })
    //   .then(function (json) { renderMencoes(json.mencoes); })
    //   .finally(function () { botao.disabled = false; });

    setTimeout(function () {
      botao.disabled = false;
      renderMencoes([]); // placeholder: troque por dados reais do backend
    }, 900);
  });
})();
