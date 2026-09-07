(function () {
  var form = document.querySelector('[data-form-compare]');
  var resultado = document.querySelector('[data-resultado-compare]');
  var botao = document.querySelector('[data-botao-comparar]');

  if (!form) return;

  function renderComparativo(carros, linhas) {
    if (!carros.length || !linhas.length) {
      resultado.innerHTML =
        '<div class="vazio"><strong>Sem dados suficientes</strong>Colete dados desses carros nos outros modos antes de comparar.</div>';
      return;
    }

    var cabecalho = '<th></th>' + carros.map(function (c) { return '<th>' + c + '</th>'; }).join('');

    var corpo = linhas.map(function (linha) {
      return '<tr><td>' + linha.rotulo + '</td>' +
        linha.valores.map(function (v) { return '<td>' + v + '</td>'; }).join('') +
        '</tr>';
    }).join('');

    resultado.innerHTML =
      '<table class="tabela tabela-compare">' +
        '<thead><tr>' + cabecalho + '</tr></thead>' +
        '<tbody>' + corpo + '</tbody>' +
      '</table>';
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();

    var dados = new FormData(form);
    var carros = [dados.get('carro_1'), dados.get('carro_2'), dados.get('carro_3')].filter(Boolean);

    botao.disabled = true;
    resultado.innerHTML = '<div class="vazio">Reunindo os dados coletados de cada carro…</div>';

    // TODO: substituir pela chamada real ao backend, por exemplo:
    // fetch('/api/compare/rodar', { method: 'POST', body: dados })
    //   .then(function (r) { return r.json(); })
    //   .then(function (json) { renderComparativo(json.carros, json.linhas); })
    //   .finally(function () { botao.disabled = false; });

    setTimeout(function () {
      botao.disabled = false;
      renderComparativo(carros, []); // placeholder: troque por dados reais do backend
    }, 700);
  });
})();
