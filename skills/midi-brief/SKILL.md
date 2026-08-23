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
4. **Pesquise as referencias.** Ver "Pesquisa e confianca", abaixo. Nesta
   iteracao a instrucao e apenas: cite fonte, registre `sources`,
   `researched_at`, `confidence` no `style` da familia.
5. **Mostre o que vai gravar.** Antes de escrever o arquivo, apresente o
   brief montado ao usuario em formato legivel — quais sao as decisoes,
   quais foram as suposicoes que voce assumiu, o que veio de pesquisa. Peca
   confirmacao. Corrija o que ele apontar.
6. **Valide e grave.** Chame `brief.validate` antes de gravar. Se falhar,
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
`confidence: "default"`, `techniques: []`.

## Pesquisa e confianca

Nesta iteracao a instrucao e apenas o esqueleto: quando o usuario citar
musico, banda ou produtor, pesquise ao vivo o que voce encontrar sobre
**tecnica e comportamento** — nunca conteudo musical (isso vem no proximo
passo da skill, US-003).

Registre no `style` da familia: `reference` (texto que o usuario deu),
`researched_at` (data ISO 8601), `sources` (URLs ou referencias
consultadas), `confidence` do vocabulario fechado
(`high | medium | low | default`).

Tecnica escolhida em `style.<familia>.techniques[].name` **precisa vir do
manual local**. Use `python3 -m tools.cli tool techniques.list --input
<(echo '{"family": "<familia>"}')` para ver o vocabulario. Nome fora do
indice e recusado por `brief.validate`.

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
  `techniques: []`.
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
- Nao apresente chute como fato. Numero sem fonte vira `[NAO VERIFICADO]` na
  conversa e nao entra em `parameters` do `style`. Confianca fraca vira
  `confidence: "low"` no brief, nao vira `confidence: "high"` maquiado.
- Nao persista o perfil pesquisado fora do `arrangement-brief.json` desta
  musica. Perfil pesquisado vive no brief, nao vira base de conhecimento.
- Nao rode `midi-arranger run` a partir daqui. Isto e a fase interativa. A
  execucao headless e outra fase, invocada pelo usuario.
