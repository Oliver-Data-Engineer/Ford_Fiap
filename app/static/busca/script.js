/**
 * busca/script.js
 *
 * Gerencia os dropdowns em cascata marca → modelo → versão, consumindo
 * a API FastAPI real (GET /specs/fabricantes, /modelos, /versoes).
 *
 * Ao clicar em "Buscar", redireciona para /scraping com os parâmetros
 * do veículo selecionado na query string.
 */

const API_BASE = "http://127.0.0.1:8000";
const TIMEOUT_MS = 10_000; // 10 s por chamada (Req 1.2–1.4)

// ── Elementos ────────────────────────────────────────────────────────────────
const selectMarca  = document.getElementById("marca");
const selectModelo = document.getElementById("modelo");
const selectVersao = document.getElementById("versao");
const mensagem     = document.getElementById("mensagem");
const botaoBuscar  = document.getElementById("botaoBuscar");

// ── Utilitários ──────────────────────────────────────────────────────────────

/**
 * Fetch com timeout via AbortController.
 * Lança erro se o servidor não responder em TIMEOUT_MS.
 */
async function fetchComTimeout(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Preenche um <select> com itens.
 * Cada item pode ser string simples ou objeto {value, label}.
 */
function preencherSelect(select, itens, placeholder) {
  select.innerHTML = "";

  const padrao = document.createElement("option");
  padrao.value = "";
  padrao.textContent = placeholder;
  select.appendChild(padrao);

  itens.forEach((item) => {
    const opt = document.createElement("option");
    if (typeof item === "string") {
      opt.value = item;
      opt.textContent = item;
    } else {
      opt.value       = item.value;
      opt.textContent = item.label;
      // Preserva url_ficha e id_veiculo como data-attrs para uso posterior
      if (item.url_ficha)   opt.dataset.urlFicha  = item.url_ficha;
      if (item.id_veiculo)  opt.dataset.idVeiculo = item.id_veiculo;
      if (item.ano)         opt.dataset.ano        = item.ano;
    }
    select.appendChild(opt);
  });
}

/** Reseta um <select> para o estado desabilitado com mensagem de placeholder. */
function resetarSelect(select, placeholder) {
  select.innerHTML = `<option value="">${placeholder}</option>`;
  select.disabled = true;
}

/** Exibe ou limpa a mensagem de erro/aviso. */
function mostrarMensagem(texto) {
  mensagem.textContent = texto;
  mensagem.classList.toggle("visivel", Boolean(texto));
}

/** Atualiza visibilidade do botão "Buscar". */
function atualizarBotao() {
  const pronto = Boolean(
    selectMarca.value && selectModelo.value && selectVersao.value
  );
  botaoBuscar.hidden = !pronto;
}

// ── Carregamentos ─────────────────────────────────────────────────────────────

async function carregarFabricantes() {
  mostrarMensagem("");
  try {
    const json = await fetchComTimeout(`${API_BASE}/specs/fabricantes`);
    const fabricantes = (json.fabricantes || []).map((f) => ({
      value: f.nome_exibicao, // usado como parâmetro na próxima chamada
      label: f.nome_exibicao,
    }));
    if (!fabricantes.length) {
      mostrarMensagem("Nenhum fabricante encontrado.");
      return;
    }
    preencherSelect(selectMarca, fabricantes, "Selecione a marca");
  } catch (erro) {
    console.error("[busca] fabricantes:", erro);
    mostrarMensagem("Falha ao carregar as marcas. Tente novamente.");
  }
}

async function carregarModelos(fabricante) {
  resetarSelect(selectModelo, "Carregando modelos…");
  resetarSelect(selectVersao, "Selecione o modelo primeiro");
  mostrarMensagem("");
  atualizarBotao();

  try {
    const json = await fetchComTimeout(
      `${API_BASE}/specs/modelos?fabricante=${encodeURIComponent(fabricante)}`
    );
    const modelos = (json.modelos || []).map((m) => ({
      value: m.nome_exibicao,
      label: m.nome_exibicao,
    }));
    if (!modelos.length) {
      resetarSelect(selectModelo, "Nenhum modelo encontrado");
      mostrarMensagem("Nenhum resultado encontrado para a seleção atual.");
      return;
    }
    preencherSelect(selectModelo, modelos, "Selecione o modelo");
    selectModelo.disabled = false;
  } catch (erro) {
    console.error("[busca] modelos:", erro);
    resetarSelect(selectModelo, "Erro ao carregar modelos");
    mostrarMensagem("Falha ao carregar os modelos. Tente novamente.");
  }
}

async function carregarVersoes(fabricante, modelo) {
  resetarSelect(selectVersao, "Carregando versões…");
  mostrarMensagem("");
  atualizarBotao();

  try {
    const json = await fetchComTimeout(
      `${API_BASE}/specs/versoes` +
      `?fabricante=${encodeURIComponent(fabricante)}` +
      `&modelo=${encodeURIComponent(modelo)}`
    );
    const versoes = (json.versoes || []).map((v) => ({
      value:      v.versao,                              // texto que vai para a URL
      label:      `${v.versao}${v.ano ? " — " + v.ano : ""}`,
      url_ficha:  v.url_ficha  || "",
      id_veiculo: v.id_veiculo || "",
      ano:        v.ano        || "",
    }));
    if (!versoes.length) {
      resetarSelect(selectVersao, "Nenhuma versão encontrada");
      mostrarMensagem("Nenhum resultado encontrado para a seleção atual.");
      return;
    }
    preencherSelect(selectVersao, versoes, "Selecione a versão");
    selectVersao.disabled = false;
  } catch (erro) {
    console.error("[busca] versoes:", erro);
    resetarSelect(selectVersao, "Erro ao carregar versões");
    mostrarMensagem("Falha ao carregar as versões. Tente novamente.");
  }
}

// ── Eventos ───────────────────────────────────────────────────────────────────

selectMarca.addEventListener("change", () => {
  const marca = selectMarca.value;
  // Limpa e desabilita todos os campos dependentes (Req 1.5)
  resetarSelect(selectModelo, "Selecione a marca primeiro");
  resetarSelect(selectVersao, "Selecione o modelo primeiro");
  mostrarMensagem("");
  atualizarBotao();

  if (marca) carregarModelos(marca);
});

selectModelo.addEventListener("change", () => {
  const marca  = selectMarca.value;
  const modelo = selectModelo.value;
  // Limpa campos abaixo (Req 1.5)
  resetarSelect(selectVersao, "Selecione o modelo primeiro");
  mostrarMensagem("");
  atualizarBotao();

  if (modelo) carregarVersoes(marca, modelo);
});

selectVersao.addEventListener("change", () => {
  mostrarMensagem("");
  atualizarBotao();
});

botaoBuscar.addEventListener("click", () => {
  const params = new URLSearchParams({
    marca:  selectMarca.value,
    modelo: selectModelo.value,
    versao: selectVersao.value,
  });
  // Redireciona para a página de scraping com o veículo selecionado
  window.location.href = `/scraping?${params.toString()}`;
});

// ── Inicialização ─────────────────────────────────────────────────────────────
carregarFabricantes();
