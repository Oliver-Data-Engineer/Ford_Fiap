/**
 * scraping/script.js
 *
 * Gerencia o fluxo de busca de especificações técnicas:
 *   1. Lê veículo selecionado dos inputs hidden (marca/modelo/versão)
 *   2. Envia POST /scraping/executar (proxy Flask → FastAPI)
 *   3. Exibe spinner → tabela de resultados
 *   4. Destaca células "não disponível"
 *   5. Exporta CSV client-side
 *   6. Trata erros, timeout (30 s) e limite de 500 linhas
 */

(function () {
  "use strict";

  // ── Constantes ──────────────────────────────────────────────────────────────
  const TIMEOUT_MS   = 30_000;  // 30 s (Req 8.3)
  const MAX_LINHAS   = 500;     // Req 9.6
  const VALOR_VAZIO  = "não disponível";

  // ── Referências ao DOM ──────────────────────────────────────────────────────
  const form           = document.querySelector("[data-form-specs]");
  const botaoBuscar    = document.querySelector("[data-botao-buscar]");
  const statusBusca    = document.querySelector("[data-status-busca]");
  const painelResult   = document.querySelector("[data-painel-resultado]");
  const spinner        = document.querySelector("[data-spinner]");
  const estadoVazio    = document.querySelector("[data-estado-vazio]");
  const estadoErro     = document.querySelector("[data-estado-erro]");
  const tabelaWrap     = document.querySelector("[data-tabela-wrap]");
  const tabelaCorpo    = document.querySelector("[data-tabela-corpo]");
  const contadorSpecs  = document.querySelector("[data-contador-specs]");
  const botaoCSV       = document.querySelector("[data-botao-csv]");
  const avisoLimite    = document.querySelector("[data-aviso-limite]");

  // Dados do veículo injetados pelo Flask
  const marca  = document.getElementById("veiculo-marca")?.value  || "";
  const modelo = document.getElementById("veiculo-modelo")?.value || "";
  const versao = document.getElementById("veiculo-versao")?.value || "";

  if (!form) return; // guard — página sem o formulário

  // ── Helpers de UI ───────────────────────────────────────────────────────────

  function mostrarSpinner() {
    spinner.hidden     = false;
    estadoVazio.hidden = true;
    estadoErro.hidden  = true;
    tabelaWrap.hidden  = true;
    avisoLimite.hidden = true;
    painelResult.hidden = false;
  }

  function ocultarSpinner() {
    spinner.hidden = true;
  }

  function mostrarErro(msg) {
    ocultarSpinner();
    estadoErro.textContent = msg;
    estadoErro.hidden = false;
    contadorSpecs.textContent = "";
    botaoCSV.hidden = true;
  }

  function setStatus(texto, cls) {
    statusBusca.textContent = texto;
    statusBusca.className   = "status-busca" + (cls ? " " + cls : "");
  }

  // ── Renderização da tabela ──────────────────────────────────────────────────

  /**
   * Escapa caracteres HTML para evitar injeção.
   */
  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /**
   * Constrói as linhas da tabela a partir da lista de especificações.
   * Limita a MAX_LINHAS e destaca células com valor "não disponível".
   */
  function renderizarTabela(specs) {
    const total    = specs.length;
    const exibidas = specs.slice(0, MAX_LINHAS);

    // Aviso de limite
    avisoLimite.hidden = total <= MAX_LINHAS;

    if (!exibidas.length) {
      ocultarSpinner();
      estadoVazio.hidden = false;
      contadorSpecs.textContent = "";
      botaoCSV.hidden = true;
      return;
    }

    // Monta HTML das linhas de uma vez (performance)
    tabelaCorpo.innerHTML = exibidas
      .map((s) => {
        const isVazio  = s.valor === VALOR_VAZIO;
        const classeVl = isVazio ? ' class="valor--indisponivel"' : "";
        const ariaVl   = isVazio ? ` aria-label="Não disponível"` : "";
        return (
          `<tr>` +
          `<td>${esc(s.atributo)}</td>` +
          `<td${classeVl}${ariaVl}>${esc(s.valor)}</td>` +
          `<td>${esc(s.fonte)}</td>` +
          `</tr>`
        );
      })
      .join("");

    ocultarSpinner();
    tabelaWrap.hidden = false;

    // Contador — "X especificações encontradas" (Req 9.3)
    contadorSpecs.textContent =
      total === 1
        ? "1 especificação encontrada"
        : `${total} especificações encontradas`;

    // Botão CSV visível
    botaoCSV.hidden = false;

    // Guarda specs para exportação
    botaoCSV._specs = specs.slice(0, MAX_LINHAS);
  }

  // ── Exportação CSV client-side ──────────────────────────────────────────────

  function exportarCSV(specs) {
    const cabecalho = "Atributo,Valor,Fonte";
    const linhas    = specs.map((s) => {
      // Envolve campos com vírgula ou aspas em aspas duplas
      const campo = (v) => {
        const str = String(v).replace(/"/g, '""');
        return /[,"\n]/.test(str) ? `"${str}"` : str;
      };
      return `${campo(s.atributo)},${campo(s.valor)},${campo(s.fonte)}`;
    });

    const csv  = [cabecalho, ...linhas].join("\n");
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    const url  = URL.createObjectURL(blob);

    const veiculo = [marca, modelo, versao].filter(Boolean).join("_").replace(/\s+/g, "-");
    const nome    = `specs_${veiculo || "veiculo"}.csv`;

    const link = document.createElement("a");
    link.href     = url;
    link.download = nome;
    link.click();
    URL.revokeObjectURL(url);
  }

  botaoCSV.addEventListener("click", () => {
    if (botaoCSV._specs?.length) exportarCSV(botaoCSV._specs);
  });

  // ── Submissão do formulário ─────────────────────────────────────────────────

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    // Lê atributos desejados — divide por vírgula e quebra de linha, filtra vazios
    const textoAtributos = document.getElementById("atributos").value || "";
    const atributos = textoAtributos
      .split(/[,\n]/)
      .map((t) => t.trim())
      .filter(Boolean);

    // Monta payload
    const payload = { fabricante: marca, modelo, versao };
    if (atributos.length) payload.atributos = atributos;

    // Estados iniciais
    botaoBuscar.disabled = true;
    setStatus("Consultando fontes…", "esta-rodando");
    mostrarSpinner();

    // AbortController para timeout de 30 s (Req 8.3)
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

    try {
      const resp = await fetch("/scraping/executar", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(payload),
        signal:  controller.signal,
      });
      clearTimeout(timer);

      if (!resp.ok) {
        // Tenta extrair mensagem de erro do body (Req 8.5)
        let detalhe = `Erro ${resp.status}`;
        try {
          const json = await resp.json();
          detalhe = json.erro || json.detail?.erro || JSON.stringify(json.detail) || detalhe;
        } catch (_) { /* ignora parse error */ }
        setStatus("Falha na busca", "esta-erro");
        mostrarErro(`Falha ao buscar especificações: ${detalhe}`);
        return;
      }

      const json = await resp.json();
      const specs = json.especificacoes || [];

      setStatus("Concluído", "esta-concluida");
      renderizarTabela(specs);

    } catch (erro) {
      clearTimeout(timer);

      const isTimeout = erro.name === "AbortError";
      const msg = isTimeout
        ? "Tempo limite excedido (30 s). Tente novamente."
        : `Erro na requisição: ${erro.message}`;

      setStatus("Falha na busca", "esta-erro");
      mostrarErro(msg);

    } finally {
      botaoBuscar.disabled = false;
    }
  });
})();
