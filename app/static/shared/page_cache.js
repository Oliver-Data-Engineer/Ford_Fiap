/* ============================================================
   page_cache.js
   Cache simples em sessionStorage pra dar navegação fluida entre páginas —
   cada página que adotar isso salva o último resultado carregado com sucesso
   e, ao ser reaberta (troca de aba do menu, "voltar" do navegador), renderiza
   esse cache na hora enquanto busca dado fresco em paralelo (stale-while-
   revalidate): a tela nunca fica em branco/"Carregando…" de novo só porque o
   usuário trocou de página e voltou.

   Escopo: sessionStorage — sobrevive à navegação entre páginas na mesma aba,
   nunca entre sessões diferentes do navegador (fecha a aba, some), e nunca é
   enviado a lugar nenhum (fica só no navegador do usuário).
   ============================================================ */
(function () {
  'use strict';

  var PREFIXO = 'ford_fiap_cache::';

  function salvar(pagina, dados) {
    try {
      sessionStorage.setItem(PREFIXO + pagina, JSON.stringify({ dados: dados, quando: Date.now() }));
    } catch (e) {
      // sessionStorage indisponível (modo privado, quota cheia etc.) — a
      // página continua funcionando normalmente, só sem cache.
    }
  }

  function carregar(pagina) {
    try {
      var bruto = sessionStorage.getItem(PREFIXO + pagina);
      if (!bruto) return null;
      var registro = JSON.parse(bruto);
      return registro && registro.dados !== undefined ? registro.dados : null;
    } catch (e) {
      return null;
    }
  }

  function limpar(pagina) {
    try { sessionStorage.removeItem(PREFIXO + pagina); } catch (e) { /* ignora */ }
  }

  window.PageCache = { salvar: salvar, carregar: carregar, limpar: limpar };
})();
