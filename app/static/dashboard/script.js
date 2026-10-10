const resumo = document.getElementById("resumo");

const veiculoSelecionado = {
  marca: resumo.dataset.marca,
  modelo: resumo.dataset.modelo,
  versao: resumo.dataset.versao,
};

// Placeholder: aqui futuramente vamos chamar a API de detalhes/fichadetalhe
// usando os dados acima (marca, modelo, versão) para montar a dashboard de verdade.
console.log("Veículo selecionado:", veiculoSelecionado);