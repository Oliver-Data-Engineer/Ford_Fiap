(function () {
  var form = document.querySelector('[data-form-scraping]');
  var status = document.querySelector('[data-status-varredura]');
  var resultado = document.querySelector('[data-resultado-scraping]');
  var botao = document.querySelector('[data-botao-iniciar]');

  if (!form) return;

  function renderTabela(pecas) {
    if (!pecas.length) {
      resultado.innerHTML =
        '<div class="vazio"><strong>Nada encontrado</strong>Nenhuma peça correspondeu aos filtros usados nesta varredura.</div>';
      return;
    }

    var linhas = pecas.map(function (p) {
      return (
        '<tr>' +
          '<td>' + p.nome + '</td>' +
          '<td class="dado-mono">' + p.referencia + '</td>' +
          '<td>' + p.site + '</td>' +
          '<td>' + p.preco + '</td>' +
          '<td><a class="btn btn--linha" href="' + p.link + '" target="_blank" rel="noopener">Ver anúncio</a></td>' +
        '</tr>'
      );
    }).join('');

    resultado.innerHTML =
      '<table class="tabela">' +
        '<thead><tr><th>Peça</th><th>Referência</th><th>Site</th><th>Preço</th><th></th></tr></thead>' +
        '<tbody>' + linhas + '</tbody>' +
      '</table>';
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();

    var dados = new FormData(form);
    botao.disabled = true;
    status.textContent = 'Executando varredura…';
    status.className = 'status-varredura esta-rodando';
    resultado.innerHTML = '<div class="vazio">Varrendo sites em busca de peças…</div>';

    // TODO: substituir pela chamada real ao backend, por exemplo:
    // fetch('/api/scraping/run', { method: 'POST', body: dados })
    //   .then(function (r) { return r.json(); })
    //   .then(function (json) { renderTabela(json.pecas); })
    //   .catch(function () { status.textContent = 'Falha na varredura'; });

    setTimeout(function () {
      status.textContent = 'Última varredura concluída';
      status.className = 'status-varredura esta-concluida';
      botao.disabled = false;
      renderTabela([]); // placeholder: troque por dados reais do backend
    }, 900);
  });
})();
