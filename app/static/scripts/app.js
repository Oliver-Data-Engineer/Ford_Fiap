const API_BASE = "http://127.0.0.1:8000/api";

const selectMarca = document.getElementById("marca");
const selectModelo = document.getElementById("modelo");
const selectVersao = document.getElementById("versao");
const resultado = document.getElementById("resultado");

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

function mostrarResultado(texto) {
  resultado.textContent = texto;
  resultado.classList.toggle("visivel", Boolean(texto));
}

async function carregarMarcas() {
  try {
    const { data } = await buscarJson(`${API_BASE}/marcas`);
    preencherSelect(selectMarca, data, "Selecione a marca");
  } catch (erro) {
    console.error(erro);
    mostrarResultado("Não foi possível carregar as marcas.");
  }
}

async function carregarModelos(marca) {
  resetarSelect(selectModelo, "Carregando modelos...");
  resetarSelect(selectVersao, "Selecione o modelo primeiro");
  mostrarResultado("");

  try {
    const { data } = await buscarJson(
      `${API_BASE}/modelos?marca=${encodeURIComponent(marca)}`
    );
    preencherSelect(selectModelo, data, "Selecione o modelo");
    selectModelo.disabled = false;
  } catch (erro) {
    console.error(erro);
    resetarSelect(selectModelo, "Erro ao carregar modelos");
    mostrarResultado("Não foi possível carregar os modelos dessa marca.");
  }
}

async function carregarVersoes(marca, modelo) {
  resetarSelect(selectVersao, "Carregando versões...");
  mostrarResultado("");

  try {
    const { data } = await buscarJson(
      `${API_BASE}/versoes?marca=${encodeURIComponent(marca)}&modelo=${encodeURIComponent(modelo)}`
    );
    preencherSelect(selectVersao, data, "Selecione a versão");
    selectVersao.disabled = false;
  } catch (erro) {
    console.error(erro);
    resetarSelect(selectVersao, "Erro ao carregar versões");
    mostrarResultado("Não foi possível carregar as versões desse modelo.");
  }
}

selectMarca.addEventListener("change", () => {
  const marca = selectMarca.value;

  if (!marca) {
    resetarSelect(selectModelo, "Selecione a marca primeiro");
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarResultado("");
    return;
  }

  carregarModelos(marca);
});

selectModelo.addEventListener("change", () => {
  const marca = selectMarca.value;
  const modelo = selectModelo.value;

  if (!modelo) {
    resetarSelect(selectVersao, "Selecione o modelo primeiro");
    mostrarResultado("");
    return;
  }

  carregarVersoes(marca, modelo);
});

selectVersao.addEventListener("change", () => {
  if (!selectVersao.value) {
    mostrarResultado("");
    return;
  }

  mostrarResultado(
    `Selecionado: ${selectMarca.value} / ${selectModelo.value} / ${selectVersao.value}`
  );
});

carregarMarcas();