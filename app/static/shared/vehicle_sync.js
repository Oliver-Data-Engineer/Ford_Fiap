/* ============================================================
   vehicle_sync.js — mantém a barra de veículo e os links do menu
   lateral sincronizados com o carro selecionado nesta página, mesmo
   quando a seleção acontece via JavaScript (combobox de marca/modelo
   do Gov Mode e da Home), sem recarregar a página.

   Por quê: marca/modelo/versao só existem no servidor no momento em
   que a página carregou (query string, ver `veiculo_selecionado()` em
   app.py) — os links do menu lateral (_sidebar.html, todos
   `url_for(..., marca=marca, modelo=modelo, versao=versao)`) e a URL
   do navegador são gerados uma única vez, nesse carregamento. Sem
   isto, escolher um carro no combobox do Gov Mode (ou da Home) nunca
   atualiza esses links — navegar pra outro modo depois (ex.: Gov Mode
   → Home) perde o carro escolhido e a próxima página abre vazia.

   Carregado globalmente em `_layout/dashboard_base.html`; cada página
   com seleção própria de carro (hoje: Gov Mode e Home) chama
   `window.sincronizarVeiculoSelecionado(marca, modelo, versao)` sempre
   que resolve uma seleção nova.
   ============================================================ */
(function () {
  'use strict';

  function atualizarQueryString(url, params) {
    var alvo = new URL(url, window.location.origin);
    Object.keys(params).forEach(function (chave) {
      var valor = params[chave];
      if (valor) alvo.searchParams.set(chave, valor);
      else alvo.searchParams.delete(chave);
    });
    return alvo;
  }

  window.sincronizarVeiculoSelecionado = function (marca, modelo, versao) {
    var params = { marca: marca || '', modelo: modelo || '', versao: versao || '' };

    // 1. URL da própria página — sobrevive a um refresh/copiar link.
    var novaUrl = atualizarQueryString(window.location.href, params);
    window.history.replaceState(null, '', novaUrl.pathname + novaUrl.search);

    // 2. Links do menu lateral — gerados pelo servidor com a marca/modelo
    // que a página tinha ao carregar; sem isto ficam presos nesse valor.
    document.querySelectorAll('.sidebar__item[href]').forEach(function (link) {
      var destino = atualizarQueryString(link.href, params);
      link.href = destino.pathname + destino.search;
    });

    // 3. Cabeçalho "vehicle-bar" desta própria página, se existir.
    var nomeEl = document.querySelector('.vehicle-bar__nome');
    if (nomeEl) nomeEl.textContent = [marca, modelo].filter(Boolean).join(' ');
    var versaoEl = document.querySelector('.vehicle-bar__versao');
    if (versaoEl) versaoEl.textContent = versao || '';
  };
})();
