/*
 * test_realce.js — regressão do realce de sintaxe da aba Pipelines.
 *
 * O bug original: encadear vários .replace() fazia a regra de palavra-chave
 * casar o `class` DENTRO do `<span class="tk-com">` já inserido, quebrando a
 * tag e vazando markup para a tela. O realce precisa ser feito numa passada só.
 *
 * Rodar:  node tests/test_realce.js
 */
const fs = require('fs');
const path = require('path');

const falhas = [];

function checar(condicao, descricao) {
  console.log((condicao ? '  OK   ' : '  FALHA') + '  ' + descricao);
  if (!condicao) falhas.push(descricao);
}

// ── Extrai esc() e realcar() do script real, sem duplicar a implementação ────
const fonte = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'static', 'pipeline', 'script.js'),
  'utf8'
);

function extrair(nome, inicio) {
  const i = fonte.indexOf(inicio);
  if (i === -1) throw new Error(`Trecho '${nome}' não encontrado em script.js`);
  return i;
}

const trecho = fonte.slice(
  extrair('esc', '  function esc(valor)'),
  fonte.indexOf('  // ── Inicialização')
);

// Recria só o necessário: esc, PALAVRAS, TOKENS e realcar
const contexto = {};
const partes = [
  trecho.slice(trecho.indexOf('function esc'), trecho.indexOf('function api')),
  trecho.slice(trecho.indexOf('var PALAVRAS')),
];
new Function('exports', partes.join('\n') + '\nexports.esc = esc; exports.realcar = realcar;')(contexto);

const { esc, realcar } = contexto;

/** Simula o parser do browser: o texto visível não pode conter markup. */
function textoVisivel(html) {
  return html
    .replace(/<span class="tk-(?:kw|str|com|num)">/g, '')
    .replace(/<\/span>/g, '');
}

function desescapar(t) {
  return t
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&');
}

console.log('\n=== Realce de sintaxe: fidelidade do texto ===');

const amostras = [
  ['classe Python (o bug original)', 'class Minha:\n    def rodar(self):\n        return 1\n'],
  ['docstring com aspas triplas', '"""\nDoc da DAG — não editar.\n"""\nimport os\n'],
  ['comentário de linha', '# comentário com "aspas" e 42\nx = 1\n'],
  ['apóstrofo solto em comentário', "# não é possível abrir o arquivo\ny = 2\n"],
  ['string com palavra reservada', 'nome = "class import def"\n'],
  ['HTML dentro de string', 'html = "<span class=\'x\'>oi</span>"\n'],
  ['e comercial e entidades', 'a = "tom & jerry" # 1 & 2\n'],
  ['números e caminhos Windows', "RAIZ = 'C:\\\\Users\\\\x'\nretries = 10\n"],
];

amostras.forEach(function ([nome, codigo]) {
  const html = realcar(codigo);
  const visivel = desescapar(textoVisivel(html));

  checar(visivel === codigo, `texto preservado — ${nome}`);
  checar(!visivel.includes('class="tk-'), `sem vazamento de markup — ${nome}`);
});

console.log('\n=== Realce de sintaxe: tokens corretos ===');

const html = realcar('def rodar():\n    return "texto"  \n');
checar(html.includes('<span class="tk-kw">def</span>'), 'palavra-chave marcada');
checar(html.includes('<span class="tk-kw">return</span>'), 'return marcado');
checar(/tk-str">&quot;texto&quot;<\/span>/.test(html), 'string marcada');

const comentado = realcar('# nota\nx = 7\n');
checar(comentado.includes('<span class="tk-com"># nota</span>'), 'comentário marcado');
checar(comentado.includes('<span class="tk-num">7</span>'), 'número marcado');

// Palavra reservada dentro de string NÃO pode virar palavra-chave
const dentroDeString = realcar('a = "def import"\n');
checar(!dentroDeString.includes('tk-kw'), 'palavra reservada dentro de string não é pintada');

// Entidades não podem ser partidas pela regra de número
const comEntidade = realcar("x = 'it's'\n");
checar(!/&#<span/.test(comEntidade), 'entidade HTML não é partida pela regra de número');

console.log('\n=== XSS: conteúdo hostil vindo da API ===');

const hostil = '<script>alert(1)</script>';
checar(!esc(hostil).includes('<script'), 'esc() neutraliza tags');
checar(!textoVisivel(realcar(hostil)).includes('<script'), 'realce não reintroduz a tag');

console.log('\n' + '='.repeat(62));
if (falhas.length) {
  console.log(`${falhas.length} VERIFICAÇÃO(ÕES) FALHARAM:`);
  falhas.forEach((f) => console.log('  - ' + f));
  process.exit(1);
}
console.log('Todas as verificações de realce passaram.');
console.log('='.repeat(62));
