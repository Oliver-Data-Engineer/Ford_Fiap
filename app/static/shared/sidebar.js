(function () {
  var CHAVE = 'painelcarro:sidebar-recolhida';
  var shell = document.querySelector('[data-shell]');
  var botao = document.querySelector('[data-sidebar-toggle]');

  if (!shell || !botao) return;

  function aplicar(recolhida) {
    shell.classList.toggle('is-collapsed', recolhida);
    botao.setAttribute('aria-expanded', String(!recolhida));
  }

  var salvo = localStorage.getItem(CHAVE) === '1';
  aplicar(salvo);

  botao.addEventListener('click', function () {
    var recolhida = !shell.classList.contains('is-collapsed');
    aplicar(recolhida);
    localStorage.setItem(CHAVE, recolhida ? '1' : '0');
  });
})();
