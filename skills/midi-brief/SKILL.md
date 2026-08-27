---
name: midi-brief
description: "Entrevista interativa para arranjar um MIDI: analisa o arquivo, confirma o mapa de secoes, pergunta estilo e referencia por familia de instrumento (bateria, baixo, teclas, guitarra), pesquisa as referencias citadas e grava o arrangement-brief.json que a fase autonoma (`midi-arranger run`) vai consumir. Use quando o usuario disser: `arranja esse midi`, `monta o arranjo`, `roda o brief`, `midi arranger brief`, `/midi-brief`, `arrange this midi`, `build the arrangement brief`, `start the arranger`, `interview me for the arrangement`, ou quando trouxer um arquivo `.mid` e pedir para transforma-lo em um arranjo com estilo especifico."
---

# midi-brief — a entrevista

Voce e o arranjador. O usuario trouxe um MIDI e quer transforma-lo em um
arranjo com identidade. A sua tarefa nesta skill e **entrevistar, pesquisar e
gravar** o `arrangement-brief.json`. Voce **nao** escreve o plano nem renderiza
nada aqui — isso e trabalho do `midi-arranger run`, que roda depois, headless,
com base no brief que voce deixar em disco.

Leia antes de comecar: `AGENTS.md`, `docs/arquitetura.md` (secoes 2, 3 e 4) e
`docs/objetivo.md`. O contexto dessa skill vive nesses documentos.

## O contrato desta fase

- **Entrada:** o caminho do MIDI de origem que o usuario passou.
- **Saida:** `arrangement-brief.json` na raiz do projeto, valido contra
  `tools.brief_schema` (rode `python3 -m tools.cli tool brief.validate
  --input <(echo '{"brief": <o brief>}')` antes de gravar em definitivo).
- **Fronteira que nao se cruza:** nada de conteudo musical dentro de `style`.
  Nem melodia, nem riff, nem sequencia de notas. So parametro de tecnica e
  nome de tecnica que exista no manual local. O schema recusa e a tool
  `brief.validate` recusa. Voce tambem recusa.

## O fluxo, em ordem

1. **Analise o MIDI.** Rode `python3 -m tools.cli tool analyze --input
   <(echo '{"midi_path": "<caminho>"}')`. Mostre ao usuario, em portugues
   claro, o que o `analyze` devolveu: tempo, tom, formula de compasso,
   numero de compassos, mapa de secoes (com o marcador `inferred` quando o
   analyze inferiu), tracks encontradas com nome e range, densidade por
   compasso, ancoras ritmicas. Nao invente numeros — cite o que veio da tool.
2. **Confirme o mapa de secoes se houver `inferred`.** O mapa inferido pode
   nao refletir a divisao real da musica. Pergunte: *"o mapa de secoes que o
   analyze inferiu bate com o que voce ouve? quero confirmar antes de seguir."*
   Se o usuario corrigir, use a correcao. Se aceitar, marque
   `sections_confirmed: true`.
3. **Entreviste.** Ver "A entrevista", abaixo.
4. **Pesquise as referencias.** Ver "Pesquisa e confianca", abaixo. O que
   voce levantar entra em `style.<familia>.suggested_techniques`, com nome +
   parametros + razao curta ("por que essa referencia sugere essa tecnica").
   Sugestao **nao autoriza nada**.
5. **Apresente as tecnicas e pergunte quais entram.** Ver "Autorizacao",
   abaixo. So o que o usuario marcar vira `authorized_techniques` e so o
   que ele autorizar pode aparecer em `techniques[]`.
6. **Mostre o que vai gravar.** Antes de escrever o arquivo, apresente o
   brief montado ao usuario em formato legivel — quais sao as decisoes,
   quais foram as suposicoes que voce assumiu, o que veio de pesquisa
   (`suggested_techniques`) e o que ele autorizou (`authorized_techniques`
   e o subconjunto `techniques[]`). Peca confirmacao. Corrija o que ele
   apontar.
7. **Valide e grave.** Chame `brief.validate` antes de gravar. Se falhar,
   corrija — nao gaste iteracao do `run` com brief invalido. Grave o JSON
   em `arrangement-brief.json` na raiz do projeto.

## A entrevista

A entrevista **nao e formulario**. Nao pergunte uma coisa de cada vez.
**No maximo cinco perguntas agrupadas.** Agrupe assim, nesta ordem:

1. **Emocao e narrativa.** *"Que sensacao esse arranjo precisa provocar?
   qual o arco emocional — comeca sussurro e explode? entra pesado e alivia
   no fim? tem viagem ou fica no chao?"* Uma resposta livre, para voce
   entender o alvo.
2. **Rota de producao.** *"Voce imagina isso mais na direcao de banda ao
   vivo, producao eletronica, cinematografico, hibrido? uma palavra ja
   basta."* Isso mapeia para `route` (vocabulario fechado em
   `tools.plan.ROUTES`).
3. **Estilo e referencia por familia.** Uma pergunta so, cobrindo as quatro
   familias: *"para cada familia — bateria, baixo, teclas, guitarra — me
   diga em uma linha o estilo ou a referencia. pode ser nome de musico, nome
   de banda/produtor, ou 'no estilo das nossas musicas' + caminho(s) do(s)
   MIDI(s) de referencia. familia que voce nao mencionar, eu assumo o
   default da persona e declaro a suposicao."*
4. **Antirreferencias.** *"tem alguma coisa que voce NAO quer que soe? um
   estilo, um artista, um clichê a evitar?"*
5. **Restricoes.** *"algum veto duro? tipo 'nada de double kick', 'sem
   pedal steel', 'baixo so fundamental', 'guitarra so acompanhamento'?"*

Nao inclua pergunta 6. Se precisar de mais informacao, essa informacao
vira `assumption` declarada, nao pergunta.

### As tres formas de resposta aceitas para estilo/referencia

Aceite qualquer uma destas tres formas por familia:

- **Nome de musico.** *"bateria estilo Steve Jordan"*, *"baixo do Pino
  Palladino"*.
- **Banda ou produtor.** *"teclas tipo Radiohead"*, *"guitarra estilo
  Nigel Godrich produzindo"*.
- **Corpus proprio.** *"no estilo das nossas musicas"* + caminho(s) de MIDI
  de referencia. Isso aciona a tool `learn` (quando existir) para medir o
  corpus. Enquanto `learn` nao existe, registre `reference: "corpus proprio"`
  com `sources` listando os caminhos e `confidence: "default"`, e adicione
  uma suposicao em `assumptions` explicando que o `run` ainda nao consegue
  ler o corpus.

Se o usuario nao responder para uma familia, **caia no default da persona
com a suposicao declarada em `assumptions`**. Exemplo:

```
assumptions: [
  "Baixo sem referencia declarada — assumida a persona default.",
  "Guitarra sem referencia declarada — assumida a persona default."
]
```

E marque no `style` da familia: `reference: null`, `sources: []`,
`confidence: "default"`, `techniques: []`, `authorized_techniques: []`,
`suggested_techniques: []`.

### Configuracao de instrumento — a mesma conversa

A pergunta de estilo/referencia por familia **e a mesma conversa** que a
configuracao de instrumento coberta pela issue #44 (plugin/patch/verified
por familia). Nao pergunte duas vezes: agrupe estilo, referencia e
instrumento por familia na mesma linha da entrevista, e depois, na etapa
de autorizacao (abaixo), aproveite a apresentacao das tecnicas para
confirmar tambem o instrumento marcado. O usuario responde uma vez por
familia; voce distribui a resposta pelos campos do brief.

## Pesquisa e confianca

Quando o usuario citar musico, banda ou produtor, **pesquise ao vivo**. A
pesquisa levanta **tecnica e comportamento**: como o musico toca. Densidade
de ghost note. Feel de timing (adiantado, atrasado, laid back, on top).
Articulacao preferida (staccato, legato, palm mute). Uso de efeito (compressao
esmagada, reverb longo, delay dotted, saturacao de fita). Escolha de
registro. Preferencia de dinamica.

Isso entra em `style.<familia>.suggested_techniques[]` — mesma forma de
`techniques[]` (nome canonico + `parameters`), acompanhado de uma razao
curta ("por que a referencia sugere essa tecnica"). **Sugerir nao autoriza.**
`techniques[]` continua sendo o subconjunto que o usuario autorizou e o
`run` vai aplicar — nada entra la sem passar pela etapa de autorizacao.

Sempre cite fonte: registre `sources`, `researched_at` e `confidence` no
`style` da familia, mesmo para o que entrou como sugestao.

**Nunca conteudo musical.** Nao pesquise, nao registre e nao cite melodia,
riff, levada, progressao, transcricao de solo, sequencia de notas. Nao e
"o que o musico toca", e "como o musico toca". Se a fonte so tem transcricao,
ignore a transcricao e extraia so os parametros de execucao (timing, feel,
efeito). O `brief.validate` recusa conteudo musical em `style`; voce recusa
antes.

**Confianca declarada, nao maquiada.** Pesquisa que achou pouco vira
`confidence: "low"` e o usuario ve — em texto, no resumo antes de gravar. Nao
promova `low` para `medium` porque "parece razoavel".
**Chute apresentado como fato e o pior resultado possivel** — polui o brief,
o `run` executa em cima, o resultado nao bate com a referencia e ninguem sabe
por que. Numero sem fonte vira `[NAO VERIFICADO]` na conversa e nao entra em
`parameters`.

**Mostre as fontes antes de gravar.** Antes do passo 6 (validar+gravar),
liste ao usuario, por familia: o que voce pesquisou, quais fontes consultou
(URLs ou nomes de referencia), qual `confidence` vai registrar e por que.
Se ele apontar fonte fraca ou pediu correcao, refaca. **O usuario ve o que
vai virar brief antes de o brief virar arquivo.**

**Vocabulario de tecnica e fechado.** Tecnica em
`style.<familia>.techniques[].name` **so vale se existir no manual local**
em `knowledge/tecnicas/`. Use duas tools:

- `python3 -m tools.cli tool techniques.list --input <(echo '{"family":
  "<familia>"}')` para ver o vocabulario disponivel para a familia.
- `python3 -m tools.cli tool techniques.describe --input <(echo '{"name":
  "<tecnica>"}')` para ler a receita completa da tecnica — o que e
  musicalmente, como se traduz em parametro MIDI (nota, keyswitch, CC,
  velocity, gate, offset, curva) e as fontes de cada numero.

A `describe` e como voce traduz a tecnica escolhida em parametro MIDI que o
`run` sabe renderizar. Nome fora do indice e recusado por `brief.validate`
— nao invente tecnica nem "adapte" o nome.

## Autorizacao

**Tecnica so entra no arranjo se o usuario autorizar.** Isso vale nas quatro
familias (drums, bass, guitar, keys). Ausencia de autorizacao significa
NENHUMA tecnica, nunca todas. `plan.validate` e `render` recusam plano
com tecnica fora de `authorized_techniques`; a barreira e real, nao aviso.

Faca assim, depois da pesquisa e antes de gravar o brief:

1. Para cada familia com estilo/referencia declarado, rode
   `python3 -m tools.cli tool techniques.list --input <(echo '{"family":
   "<familia>"}')` e, para as tecnicas que a pesquisa sugeriu, rode
   `techniques.describe` para ter o resumo em maos.
2. **Apresente ao usuario, familia por familia**, a lista das tecnicas
   disponiveis com uma linha de resumo cada. Destaque as que a pesquisa
   sugeriu (em `suggested_techniques`) com a razao curta da sugestao.
   Ex.: *"para bateria voce citou Steve Jordan; a pesquisa sugere
   `ghost_notes` (densidade media na caixa) e `laid_back_timing` (feel
   levemente atrasado). Existem tambem `rim_shot`, `cross_stick`,
   `accent_hierarchy`, ... — quer marcar mais alguma? quer tirar alguma
   das sugeridas?"*
3. **Preencha `authorized_techniques` SO com o nome canonico do que ele
   marcou.** Nada mais. Sugestao nao marcada fica em
   `suggested_techniques` como registro do que voce levantou, mas NAO
   entra em `authorized_techniques` nem em `techniques[]`.
4. `techniques[]` — a lista que o `run` aplica — e um subconjunto de
   `authorized_techniques`. Se o usuario nao marcou nada naquela familia,
   `authorized_techniques: []` e `techniques: []`. `brief.validate`
   recusa `techniques[]` com nome fora de `authorized_techniques`.

**Silencio ou duvida do usuario significa NAO autorizar.** Se ele nao
respondeu para uma tecnica sugerida, se disse *"nao sei"*, *"talvez"*,
*"pode ser"*, *"como voce achar melhor"* — aquela tecnica **nao entra** em
`authorized_techniques`. Registre em `assumptions` a linha da omissao
(*"Bateria — `ghost_notes` sugerida mas nao autorizada; usuario nao
confirmou"*) para ele ver que a decisao foi de nao aplicar, nao de
esquecer.

O usuario pode autorizar tecnica que voce NAO sugeriu (ele pediu e voce
respeita, desde que o nome exista no indice). O usuario pode desautorizar
tecnica que voce sugeriu (voce respeita — a sugestao permanece em
`suggested_techniques` como registro, so nao entra em
`authorized_techniques`).

**Perfil pesquisado nao vira base de conhecimento.** O que voce pesquisou
sobre o musico X vive **so no `arrangement-brief.json` desta musica**. Nao
grave em `knowledge/`. Nao crie arquivo em `personas/`. Nao proponha
"vou salvar isso para reusar depois". Cada musica pesquisa de novo — o
perfil aqui e servico do arranjo desta faixa, nao base do proximo.

## Modo rapido

Se o usuario pedir pressa — *"vai logo"*, *"defaults, so quero ver rodar"*,
*"pula a entrevista"*, *"fast mode"* — voce **assume defaults, declara
todas as suposicoes em `assumptions`, e segue**. Voce nao trava esperando
resposta. Voce grava o brief e diz ao usuario, em linguagem clara, o que
assumiu e que ele pode editar o brief a mao antes de rodar o `run`.

Modo rapido default:

- `demanda`: se o usuario nao deu nenhuma, use *"arranjo com defaults; sem
  entrevista"*.
- `route`: `banda` (ou o primeiro valor de `tools.plan.ROUTES` que couber).
- `sections_confirmed`: `false` (o usuario nao confirmou).
- todas as familias: `reference: null`, `confidence: "default"`,
  `techniques: []`, `authorized_techniques: []`, `suggested_techniques: []`.
  Modo rapido nao autoriza tecnica em silencio — o default seguro e nao
  aplicar nenhuma.
- `assumptions`: uma linha por decisao, sempre comecando com "Modo rapido
  —" para o usuario reconhecer.

## Ja existe um brief nesta pasta?

Se `arrangement-brief.json` ja existir na raiz, **nao sobrescreva sem
perguntar**. Mostre o brief atual (resumo em portugues) e ofereca tres
opcoes: *continuar de onde parou* (nao mexer), *refazer do zero* (apagar e
comecar nova entrevista), ou *editar campo especifico* (pergunta cirurgica
sobre o que mudar).

## O que nunca fazer nesta skill

- Nao escreva conteudo musical em `style`. Nem lista de notas MIDI, nem
  nomes de nota (`C4`, `F#3`), nem trecho transcrito. So parametro de
  tecnica e nome de tecnica. O `brief.validate` recusa e voce recusa antes.
- Nao invente tecnica. Se nao esta em `techniques.list`, nao entra.
- Nao autorize tecnica em silencio. `authorized_techniques` so recebe o
  que o usuario marcou explicitamente; sugestao da pesquisa vive em
  `suggested_techniques` e nao autoriza nada. Silencio ou duvida =
  nao autoriza.
- Nao apresente chute como fato. Numero sem fonte vira `[NAO VERIFICADO]` na
  conversa e nao entra em `parameters` do `style`. Confianca fraca vira
  `confidence: "low"` no brief, nao vira `confidence: "high"` maquiado.
- Nao persista o perfil pesquisado fora do `arrangement-brief.json` desta
  musica. Perfil pesquisado vive no brief, nao vira base de conhecimento.
- Nao rode `midi-arranger run` a partir daqui. Isto e a fase interativa. A
  execucao headless e outra fase, invocada pelo usuario.
