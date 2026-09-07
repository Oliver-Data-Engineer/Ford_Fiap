(function () {
  var form = document.querySelector('[data-form-gov]');
  var resultado = document.querySelector('[data-resultado-gov]');
  var botao = document.querySelector('[data-botao-consultar-gov]');

  if (!form) return;

  function renderFicha(dados) {
    if (!dados) {
      resultado.innerHTML =
        '<div class="vazio"><strong>Nada encontrado</strong>Confira a placa ou o Renavam informado e tente novamente.</div>';
      return;
    }

    var campos = [
      ['Situação', dados.situacao],
      ['Município/UF', dados.municipio],
      ['Ano/Modelo', dados.ano_modelo],
      ['Restrições', dados.restricoes],
      ['Débitos', dados.debitos],
      ['Última atualização', dados.atualizado_em]
    ];

    var itens = campos.map(function (c) {
      return (
        '<div class="ficha-gov__item">' +
          '<span class="ficha-gov__rotulo">' + c[0] + '</span>' +
          '<span class="ficha-gov__valor">' + c[1] + '</span>' +
        '</div>'
      );
    }).join('');

    resultado.innerHTML = '<div class="ficha-gov">' + itens + '</div>';
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();

    var dados = new FormData(form);
    botao.disabled = true;
    resultado.innerHTML = '<div class="vazio">Consultando a base oficial…</div>';

    // TODO: substituir pela chamada real ao backend, por exemplo:
    // fetch('/api/gov/consultar', { method: 'POST', body: dados })
    //   .then(function (r) { return r.json(); })
    //   .then(function (json) { renderFicha(json.dados); })
    //   .finally(function () { botao.disabled = false; });

    setTimeout(function () {
      botao.disabled = false;
      renderFicha(null); // placeholder: troque por dados reais do backend
    }, 900);
  });
})();
