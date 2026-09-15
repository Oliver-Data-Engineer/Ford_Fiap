/**
 * scraping/script.js
 *
 * Gerencia o fluxo completo da sessão de Web Scraping, tudo numa página só:
 *   1. Seletor de veículo em cascata (marca → modelo → versão), consumindo
 *      a API via proxy same-origin do Flask (GET /specs/fabricantes|modelos|versoes)
 *   2. Envia POST /scraping/executar (proxy Flask → FastAPI) com o veículo escolhido
 *   3. Exibe spinner → resultado em seções retráteis por categoria
 *   4. Destaca especificações "não disponível"
 *   5. Exporta CSV client-side (com a coluna categoria)
 *   6. Trata erros, timeout (30 s) e limite de 500 linhas
 */

(function () {
  "use strict";

  // ── Constantes ──────────────────────────────────────────────────────────────
  const TIMEOUT_MS        = 30_000;  // 30 s (Req 8.3)
  const TIMEOUT_VEICULO_MS = 10_000; // 10 s por chamada de listagem (Req 1.2–1.4)
  const MAX_LINHAS        = 500;     // Req 9.6
  const VALOR_VAZIO       = "não disponível";

  // ── Referências ao DOM — veículo ────────────────────────────────────────────
  const selectMarca      = document.querySelector("[data-select-marca]");
  const selectModelo     = document.querySelector("[data-select-modelo]");
  const selectVersao     = document.querySelector("[data-select-versao]");
  const mensagemVeiculo  = document.querySelector("[data-mensagem-veiculo]");
  const inputMarca       = document.getElementById("veiculo-marca");
  const inputModelo      = document.getElementById("veiculo-modelo");
  const inputVersao      = document.getElementById("veiculo-versao");

  // ── Referências ao DOM — busca/resultado ────────────────────────────────────
  const form           = document.querySelector("[data-form-specs]");
  const botaoBuscar     = document.querySelector("[data-botao-buscar]");
  const statusBusca     = document.querySelector("[data-status-busca]");
  const painelResult    = document.querySelector("[data-painel-resultado]");
  const spinner         = document.querySelector("[data-spinner]");
  const estadoVazio     = document.querySelector("[data-estado-vazio]");
  const estadoErro      = document.querySelector("[data-estado-erro]");
  const secoesWrap      = document.querySelector("[data-secoes-wrap]");
  const secoesLista     = document.querySelector("[data-secoes-lista]");
  const contadorSpecs   = document.querySelector("[data-contador-specs]");
  const botaoCSV        = document.querySelector("[data-botao-csv]");
  const botaoExpandir   = document.querySelector("[data-botao-expandir]");
  const avisoLimite     = document.querySelector("[data-aviso-limite]");

  if (!form || !selectMarca) return; // guard — página sem os elementos esperados

  // ── Utilidades genéricas ─────────────────────────────────────────────────────

  function esc(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function fetchComTimeout(url, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(url, { signal: controller.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } finally {
      clearTimeout(timer);
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Seletor de veículo em cascata (marca → modelo → versão)
  // ══════════════════════════════════════════════════════════════════════════

  function preencherSelect(select, itens, placeholder) {
    select.innerHTML = "";
    const padrao = document.createElement("option");
    padrao.value = "";
    padrao.textContent = placeholder;
    select.appendChild(padrao);

    itens.forEach((item) => {
      const opt = document.createElement("option");
      opt.value = item.value;
      opt.textContent = item.label;
      select.appendChild(opt);
    });
  }

  function resetarSelect(select, placeholder) {
    select.innerHTML = `<option value="">${placeholder}</option>`;
    select.disabled = true;
  }

  function mostrarMensagemVeiculo(texto) {
    mensagemVeiculo.textContent = texto;
    mensagemVeiculo.hidden = !texto;
  }

  function optionExiste(select, valor) {
    return Array.from(select.options).some((o) => o.value === valor);
  }

  function sincronizarHiddenEBotao() {
    inputMarca.value  = selectMarca.value;
    inputModelo.value = selectModelo.value;
    inputVersao.value = selectVersao.value;
    botaoBuscar.disabled = !(selectMarca.value && selectModelo.value && selectVersao.value);
  }

  async function carregarFabricantes() {
    mostrarMensagemVeiculo("");
    try {
      const json = await fetchComTimeout("/specs/fabricantes", TIMEOUT_VEICULO_MS);
      const fabricantes = (json.fabricantes || []).map((f) => ({
        value: f.nome_exibicao,
        label: f.nome_exibicao,
      }));
      if (!fabricantes.length) {
        mostrarMensagemVeiculo("Nenhum fabricante encontrado.");
        return;
      }
      preencherSelect(selectMarca, fabricantes, "Selecione a marca");
    } catch (erro) {
      console.error("[scraping] fabricantes:", erro);
      mostrarMensagemVeiculo("Falha ao carregar as marcas. Tente novamente.");
    }
  }

  async function carregarModelos(fabricante) {
    resetarSelect(selectModelo, "Carregando modelos…");
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarMensagemVeiculo("");
    sincronizarHiddenEBotao();

    try {
      const json = await fetchComTimeout(
        `/specs/modelos?fabricante=${encodeURIComponent(fabricante)}`,
        TIMEOUT_VEICULO_MS
      );
      const modelos = (json.modelos || []).map((m) => ({
        value: m.nome_exibicao,
        label: m.nome_exibicao,
      }));
      if (!modelos.length) {
        resetarSelect(selectModelo, "Nenhum modelo encontrado");
        mostrarMensagemVeiculo("Nenhum resultado encontrado para a seleção atual.");
        return;
      }
      preencherSelect(selectModelo, modelos, "Selecione o modelo");
      selectModelo.disabled = false;
    } catch (erro) {
      console.error("[scraping] modelos:", erro);
      resetarSelect(selectModelo, "Erro ao carregar modelos");
      mostrarMensagemVeiculo("Falha ao carregar os modelos. Tente novamente.");
    }
  }

  async function carregarVersoes(fabricante, modelo) {
    resetarSelect(selectVersao, "Carregando versões…");
    mostrarMensagemVeiculo("");
    sincronizarHiddenEBotao();

    try {
      const json = await fetchComTimeout(
        `/specs/versoes?fabricante=${encodeURIComponent(fabricante)}&modelo=${encodeURIComponent(modelo)}`,
        TIMEOUT_VEICULO_MS
      );
      const versoes = (json.versoes || []).map((v) => ({
        value: v.versao,
        label: `${v.versao}${v.ano ? " — " + v.ano : ""}`,
      }));
      if (!versoes.length) {
        resetarSelect(selectVersao, "Nenhuma versão encontrada");
        mostrarMensagemVeiculo("Nenhum resultado encontrado para a seleção atual.");
        return;
      }
      preencherSelect(selectVersao, versoes, "Selecione a versão");
      selectVersao.disabled = false;
    } catch (erro) {
      console.error("[scraping] versoes:", erro);
      resetarSelect(selectVersao, "Erro ao carregar versões");
      mostrarMensagemVeiculo("Falha ao carregar as versões. Tente novamente.");
    }
  }

  selectMarca.addEventListener("change", () => {
    const marca = selectMarca.value;
    resetarSelect(selectModelo, "Selecione a marca primeiro");
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarMensagemVeiculo("");
    sincronizarHiddenEBotao();
    if (marca) carregarModelos(marca);
  });

  selectModelo.addEventListener("change", () => {
    const marca  = selectMarca.value;
    const modelo = selectModelo.value;
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarMensagemVeiculo("");
    sincronizarHiddenEBotao();
    if (modelo) carregarVersoes(marca, modelo);
  });

  selectVersao.addEventListener("change", () => {
    mostrarMensagemVeiculo("");
    sincronizarHiddenEBotao();
  });

  /** Restaura a seleção vinda da query string (ex.: link "Trocar veículo"),
   *  encadeando as chamadas de marca → modelo → versão. */
  async function inicializarVeiculo() {
    const veiculoInicial = {
      marca:  inputMarca.value  || "",
      modelo: inputModelo.value || "",
      versao: inputVersao.value || "",
    };

    await carregarFabricantes();
    if (veiculoInicial.marca && optionExiste(selectMarca, veiculoInicial.marca)) {
      selectMarca.value = veiculoInicial.marca;
      await carregarModelos(veiculoInicial.marca);

      if (veiculoInicial.modelo && optionExiste(selectModelo, veiculoInicial.modelo)) {
        selectModelo.value = veiculoInicial.modelo;
        await carregarVersoes(veiculoInicial.marca, veiculoInicial.modelo);

        if (veiculoInicial.versao && optionExiste(selectVersao, veiculoInicial.versao)) {
          selectVersao.value = veiculoInicial.versao;
        }
      }
    }
    sincronizarHiddenEBotao();
  }

  // ══════════════════════════════════════════════════════════════════════════
  // Busca de especificações + exibição em seções retráteis por categoria
  // ══════════════════════════════════════════════════════════════════════════

  function mostrarSpinner() {
    spinner.hidden      = false;
    estadoVazio.hidden  = true;
    estadoErro.hidden   = true;
    secoesWrap.hidden   = true;
    avisoLimite.hidden  = true;
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
    botaoExpandir.hidden = true;
  }

  function setStatus(texto, cls) {
    statusBusca.textContent = texto;
    statusBusca.className   = "status-busca" + (cls ? " " + cls : "");
  }

  /** Agrupa specs por categoria, preservando a ordem de chegada (já vem
   *  ordenado alfabeticamente por atributo dentro de cada categoria). */
  function agruparPorCategoria(specs) {
    const grupos = new Map();
    specs.forEach((s) => {
      const categoria = s.categoria || "Outros";
      if (!grupos.has(categoria)) grupos.set(categoria, []);
      grupos.get(categoria).push(s);
    });
    return grupos;
  }

  function linhaSpec(s) {
    const isVazio  = s.valor === VALOR_VAZIO;
    const classeVl = isVazio ? " valor--indisponivel" : "";
    const ariaVl   = isVazio ? ' aria-label="Não disponível"' : "";
    return (
      `<div class="spec-linha">` +
      `<span class="spec-linha__atributo">${esc(s.atributo)}</span>` +
      `<span class="spec-linha__valor${classeVl}"${ariaVl}>${esc(s.valor)}</span>` +
      `<span class="spec-linha__fonte">${esc(s.fonte)}</span>` +
      `</div>`
    );
  }

  function cardCategoria(nome, specs, indice) {
    return (
      `<details class="dobra especificacao-secao" open>` +
      `<summary class="dobra__resumo">` +
      `<span>${indice}. ${esc(nome)}</span>` +
      `<span class="selo selo--neutro">${specs.length} ${specs.length === 1 ? "item" : "itens"}</span>` +
      `</summary>` +
      `<div class="dobra__corpo">` +
      `<div class="spec-linha spec-linha--cabecalho">` +
      `<span>Critério</span><span>Valor</span><span>Fonte</span>` +
      `</div>` +
      specs.map(linhaSpec).join("") +
      `</div>` +
      `</details>`
    );
  }

  /** Constrói as seções por categoria a partir da lista de especificações.
   *  Limita a MAX_LINHAS (aplicado antes do agrupamento, como antes). */
  function renderizarSecoes(specs) {
    const total    = specs.length;
    const exibidas = specs.slice(0, MAX_LINHAS);

    avisoLimite.hidden = total <= MAX_LINHAS;

    if (!exibidas.length) {
      ocultarSpinner();
      estadoVazio.hidden = false;
      contadorSpecs.textContent = "";
      botaoCSV.hidden = true;
      botaoExpandir.hidden = true;
      return;
    }

    const grupos = agruparPorCategoria(exibidas);
    secoesLista.innerHTML = Array.from(grupos.entries())
      .map(([nome, itens], i) => cardCategoria(nome, itens, i + 1))
      .join("");

    ocultarSpinner();
    secoesWrap.hidden = false;

    contadorSpecs.textContent =
      total === 1
        ? `1 especificação encontrada em ${grupos.size} categoria(s)`
        : `${total} especificações encontradas em ${grupos.size} categoria(s)`;

    botaoCSV.hidden = false;
    botaoExpandir.hidden = false;
    botaoCSV._specs = exibidas;
  }

  botaoExpandir.addEventListener("click", () => {
    const detalhes = secoesLista.querySelectorAll("details.especificacao-secao");
    const algumFechado = Array.from(detalhes).some((d) => !d.open);
    detalhes.forEach((d) => { d.open = algumFechado; });
    botaoExpandir.textContent = algumFechado ? "Recolher tudo" : "Expandir tudo";
  });

  // ── Exportação CSV client-side ──────────────────────────────────────────────

  function exportarCSV(specs) {
    const cabecalho = "Atributo,Valor,Fonte,Categoria";
    const linhas    = specs.map((s) => {
      const campo = (v) => {
        const str = String(v ?? "").replace(/"/g, '""');
        return /[,"\n]/.test(str) ? `"${str}"` : str;
      };
      return `${campo(s.atributo)},${campo(s.valor)},${campo(s.fonte)},${campo(s.categoria)}`;
    });

    const csv  = [cabecalho, ...linhas].join("\n");
    const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
    const url  = URL.createObjectURL(blob);

    const veiculo = [inputMarca.value, inputModelo.value, inputVersao.value]
      .filter(Boolean).join("_").replace(/\s+/g, "-");
    const nome = `specs_${veiculo || "veiculo"}.csv`;

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

    const marca  = inputMarca.value;
    const modelo = inputModelo.value;
    const versao = inputVersao.value;

    const payload = { fabricante: marca, modelo, versao };

    botaoBuscar.disabled = true;
    setStatus("Consultando fontes…", "esta-rodando");
    mostrarSpinner();

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
      renderizarSecoes(specs);

    } catch (erro) {
      clearTimeout(timer);

      const isTimeout = erro.name === "AbortError";
      const msg = isTimeout
        ? "Tempo limite excedido (30 s). Tente novamente."
        : `Erro na requisição: ${erro.message}`;

      setStatus("Falha na busca", "esta-erro");
      mostrarErro(msg);

    } finally {
      botaoBuscar.disabled = !(inputMarca.value && inputModelo.value && inputVersao.value);
    }
  });

  // ── Inicialização ─────────────────────────────────────────────────────────────
  inicializarVeiculo();
})();
