(function () {
  var form = document.querySelector('[data-form-pipeline]');
  var lista = document.querySelector('[data-lista-pipelines]');
  var botao = document.querySelector('[data-botao-agendar]');

  if (!form) return;

  var ROTULO_MODO = {
    scraping: 'Web Scraping',
    youtube: 'YouTube Search',
    x: 'X Search',
    gov: 'Gov Mode'
  };

  var ROTULO_FREQUENCIA = {
    diaria: 'Todos os dias',
    semanal: 'Toda semana',
    mensal: 'Todo mês'
  };

  function renderLista(pipelines) {
    if (!pipelines.length) {
      lista.innerHTML =
        '<div class="vazio"><strong>Nenhum agendamento criado ainda</strong>Crie um agendamento acima para automatizar a coleta de dados deste veículo.</div>';
      return;
    }

    var itens = pipelines.map(function (p) {
      return (
        '<div class="pipeline-item">' +
          '<div>' +
            '<p class="pipeline-item__modo">' + ROTULO_MODO[p.modo] + '</p>' +
            '<p class="pipeline-item__frequencia">' + ROTULO_FREQUENCIA[p.frequencia] + '</p>' +
          '</div>' +
          '<div class="pipeline-item__acoes">' +
            '<span class="pipeline-item__proxima">próx.: ' + p.proxima_execucao + '</span>' +
            '<button type="button" class="btn btn--linha">Pausar</button>' +
          '</div>' +
        '</div>'
      );
    }).join('');

    lista.innerHTML = itens;
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();

    var dados = new FormData(form);
    botao.disabled = true;

    // TODO: substituir pela chamada real ao backend, por exemplo:
    // fetch('/api/pipeline/criar', { method: 'POST', body: dados })
    //   .then(function (r) { return r.json(); })
    //   .then(function (json) { renderLista(json.pipelines); })
    //   .finally(function () { botao.disabled = false; });

    setTimeout(function () {
      botao.disabled = false;
      renderLista([]); // placeholder: troque pela lista real vinda do backend
    }, 500);
  });
})();
