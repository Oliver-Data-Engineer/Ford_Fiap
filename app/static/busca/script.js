const API_BASE = "http://127.0.0.1:8000/api";

const selectMarca = document.getElementById("marca");
const selectModelo = document.getElementById("modelo");
const selectVersao = document.getElementById("versao");
const mensagem = document.getElementById("mensagem");
const botaoBuscar = document.getElementById("botaoBuscar");

async function buscarJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    throw new Error(`Erro ${resp.status} ao consultar ${url}`);
  }
  return resp.json();
}

function preencherSelect(select, itens, placeholder) {
  select.innerHTML = "";

  const opcaoPadrao = document.createElement("option");
  opcaoPadrao.value = "";
  opcaoPadrao.textContent = placeholder;
  select.appendChild(opcaoPadrao);

  itens.forEach((item) => {
    const opcao = document.createElement("option");
    opcao.value = item;
    opcao.textContent = item;
    select.appendChild(opcao);
  });
}

function resetarSelect(select, placeholder) {
  select.innerHTML = `<option value="">${placeholder}</option>`;
  select.disabled = true;
}

function mostrarMensagem(texto) {
  mensagem.textContent = texto;
  mensagem.classList.toggle("visivel", Boolean(texto));
}

function atualizarBotaoBuscar() {
  const pronto = Boolean(
    selectMarca.value && selectModelo.value && selectVersao.value
  );
  botaoBuscar.hidden = !pronto;
}

async function carregarMarcas() {
  try {
    const { data } = await buscarJson(`${API_BASE}/marcas`);
    preencherSelect(selectMarca, data, "Selecione a marca");
  } catch (erro) {
    console.error(erro);
    mostrarMensagem("Não foi possível carregar as marcas.");
  }
}

async function carregarModelos(marca) {
  resetarSelect(selectModelo, "Carregando modelos...");
  resetarSelect(selectVersao, "Selecione o modelo primeiro");
  mostrarMensagem("");
  atualizarBotaoBuscar();

  try {
    const { data } = await buscarJson(
      `${API_BASE}/modelos?marca=${encodeURIComponent(marca)}`
    );
    preencherSelect(selectModelo, data, "Selecione o modelo");
    selectModelo.disabled = false;
  } catch (erro) {
    console.error(erro);
    resetarSelect(selectModelo, "Erro ao carregar modelos");
    mostrarMensagem("Não foi possível carregar os modelos dessa marca.");
  }
}

async function carregarVersoes(marca, modelo) {
  resetarSelect(selectVersao, "Carregando versões...");
  mostrarMensagem("");
  atualizarBotaoBuscar();

  try {
    const { data } = await buscarJson(
      `${API_BASE}/versoes?marca=${encodeURIComponent(marca)}&modelo=${encodeURIComponent(modelo)}`
    );
    preencherSelect(selectVersao, data, "Selecione a versão");
    selectVersao.disabled = false;
  } catch (erro) {
    console.error(erro);
    resetarSelect(selectVersao, "Erro ao carregar versões");
    mostrarMensagem("Não foi possível carregar as versões desse modelo.");
  }
}

selectMarca.addEventListener("change", () => {
  const marca = selectMarca.value;

  if (!marca) {
    resetarSelect(selectModelo, "Selecione a marca primeiro");
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarMensagem("");
    atualizarBotaoBuscar();
    return;
  }

  carregarModelos(marca);
});

selectModelo.addEventListener("change", () => {
  const marca = selectMarca.value;
  const modelo = selectModelo.value;

  if (!modelo) {
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarMensagem("");
    atualizarBotaoBuscar();
    return;
  }

  carregarVersoes(marca, modelo);
});

selectVersao.addEventListener("change", () => {
  mostrarMensagem("");
  atualizarBotaoBuscar();
});

botaoBuscar.addEventListener("click", () => {
  const params = new URLSearchParams({
    marca: selectMarca.value,
    modelo: selectModelo.value,
    versao: selectVersao.value,
  });

  window.location.href = `/dashboard?${params.toString()}`;
});

carregarMarcas();