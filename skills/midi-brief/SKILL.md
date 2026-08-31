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
  `tools.brief_schema` (rode `echo '{"brief": <o brief>}' | python3 -m
  tools.cli tool brief.validate --input -` antes de gravar em
  definitivo — `--input` so aceita `-` para stdin ou um arquivo
  regular; substituicao de processo tipo `<(...)` NAO e arquivo regular
  e falha com `E_INPUT_FILE`).
- **Fronteira que nao se cruza:** nada de conteudo musical dentro de `style`.
  Nem melodia, nem riff, nem sequencia de notas. So parametro de tecnica e
  nome de tecnica que exista no manual local. O schema recusa e a tool
  `brief.validate` recusa. Voce tambem recusa.

## O fluxo, em ordem

1. **Analise o MIDI.** Rode `echo '{"midi_path": "<caminho>"}' | python3
   -m tools.cli tool analyze --input -`. Mostre ao usuario, em portugues
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

### Configuracao de instrumento de corda — a mesma conversa (issue #44)

A pergunta de estilo/referencia por familia **e a mesma conversa** que a
configuracao de instrumento de corda (guitarra e baixo). Nao pergunte duas
vezes: agrupe estilo, referencia e instrumento de corda na mesma linha da
entrevista. O usuario responde uma vez por familia; voce distribui a
resposta pelos campos `style` e `instruments` do brief.

**Por que isso importa.** Configuracao de instrumento e informacao POR
MUSICA — nunca conhecimento de repositorio, nunca algo que a IA "lembra"
de perguntar. Caso real que motivou esta regra: o arranjador humanizou um
arquivo sem nunca perguntar a afinacao; o usuario informou depois
"guitarras 7 cordas Drop G#, baixo 4 cordas Drop G# finger" — e a
informacao batia exatamente com o arquivo (guitarra ritmica com minimo em
MIDI 32, o piso exato do Drop G# de 7 cordas, zero notas abaixo
disso). Sem perguntar, a ferramenta so tem palpite. Tres coisas dependem
da afinacao declarada: a inferencia automatica de `tools.tuning`, o piso
fisico que o motor de tecnicas usa para recusar nota abaixo da corda
solta mais grave, e o export achatado (um canal por track, nao por
corda) — onde a deteccao automatica simplesmente nao funciona.

**Pergunte SO pela familia que existe no MIDI de origem.** Olhe o
`tuning_inference` que o `analyze` do passo 1 devolveu: track com
`is_stringed: true` cujo nome ou `governing_programs` (24-31 = guitarra,
32-37 = baixo) indicam guitarra vira pergunta de guitarra; indicam baixo
vira pergunta de baixo. **Use `governing_programs`, NUNCA `gm_programs`**
para decidir a familia: `gm_programs` e o historico bruto de TODO
`program_change` que a track ja declarou, mesmo o que nunca tocou nota
nenhuma; `governing_programs` e so o patch que realmente rege pelo menos
uma nota (o que soa). Uma track com `program_change` de baixo (32)
substituido por guitarra (24) antes da primeira nota tem
`gm_programs=[24,32]` mas `governing_programs=[24]` — perguntar
configuracao de baixo nesse caso seria perguntar por um instrumento que
nao existe de verdade na track.

**`is_stringed: true` NAO e o unico sinal de presenca.** A classificacao
automatica (`tools.tuning`) so confirma corda quando ha nome inequivoco
ou patch GM regente — o proprio caso real que motivou a issue #44 e um
export ACHATADO (`Deixe Ir - MIX`, um canal por track, nao por corda),
onde a deteccao automatica **nao funciona**, e a track de guitarra pode
sair com nome generico de DAW (`Rhy DI`, `Gtr Print`) sem patch GM
nenhum: `is_stringed: false`, `discard_reason: not_stringed`. Exigir
`is_stringed: true` antes de perguntar reproduziria exatamente o defeito
que a issue #44 corrigiu — nao pergunta justo onde a pergunta mais
importa. Por isso a familia conta como presente quando QUALQUER destes
sinais aparece:
- alguma track tem `is_stringed: true` para aquela familia (sinal
  automatico), OU
- o usuario, na pergunta 3 da entrevista ("estilo e referencia por
  familia"), deu uma referencia real para guitarra/baixo — nome de
  musico, banda, produtor ou corpus proprio — em vez de silencio/default.
  Uma referencia de guitarra so faz sentido se ha guitarra na musica;
  tratar isso como sinal fecha o buraco do export achatado sem inventar
  heuristica nova de classificacao automatica.

MIDI sem nenhum dos dois sinais para aquela familia NAO pergunta
configuracao de corda — a pergunta so aparece pra familia presente por
pelo menos um caminho.

**Para cada familia de corda presente, pergunte em uma linha:**

*"Pra [guitarra/baixo]: quantas cordas, e qual a afinacao (nome como
'Drop C', 'Drop G#', 'E padrão' — ou, se preferir, as notas de cada
corda solta, da mais grave pra mais aguda)? Se nao souber, tudo bem, diga
'nao sei'."* Para baixo, acrescente na mesma linha: *"e e tocado com dedo,
palheta ou slap?"* mais *"a track de baixo esta escrita na altura que
soa, ou uma oitava ACIMA de como soa? Baixo e instrumento transpositor —
soa uma oitava abaixo do que esta escrito na partitura/piano-roll na
convencao padrao (altura escrita); se a track ja guarda a nota que
realmente soa, e altura soante."*

**Resposta "nao sei" e aceita e vira ausencia declarada, nunca um
chute.** Grave `instruments.<familia>.known: false` com `strings`,
`tuning`, e (para baixo) `playing_style`/`notation` todos `null`. Nao
adivinhe um numero porque "a maioria das guitarras tem 6 cordas" — isso e
exatamente o vicio que a issue #44 corrigiu.

**Resolvendo o nome da afinacao.** Quando o usuario responder por nome
("Drop C", "Drop G#", "E padrão"), rode `echo '{"name":
"guitar.drop_tuning"}' | python3 -m tools.cli tool techniques.describe
--input -` para ver `tools.generic.afinacoes` — a tabela de afinacoes
conhecidas do manual, por numero de cordas. **Nunca resolva o nome de
cabeca ou com uma tabela sua** — so o manual conta. Se o nome declarado
(com aquele numero de cordas) nao aparecer na tabela — como "Drop G#" de
7 cordas no caso real acima, que nao esta documentado — **pergunte as
notas de cada corda solta (numero MIDI ou nome de nota com oitava), da
mais grave pra mais aguda**, e grave as duas coisas: o nome que o
usuario deu (`tuning.name`, so como registro) e as notas que ele
confirmou (`tuning.notes`). Nao grave so o nome quando ele nao resolveu —
`brief.validate` recusa em `E_BRIEF_TUNING_NAME_UNKNOWN`.

**Convertendo nome de nota com oitava para MIDI.**
`tools.brief_schema` so aceita `tuning.notes` como inteiros MIDI —
`brief.validate` recusa string. Se o usuario responder com nome de nota
com oitava (ex.: `G#1`), converta ANTES de gravar, usando a mesma
convencao cientifica que `pretty_midi` (ja dependencia deste repo) usa —
Do central e a oitava 4, e o MIDI 0 comeca na oitava -1: rode `python3 -c
"import pretty_midi as pm; print(pm.note_name_to_number(nome_da_nota))"`
para cada nota, nunca calcule de cabeca nem invente tabela propria. Confirme
com o usuario o numero MIDI resultante antes de gravar (a conversao de
oitava e um ponto classico de erro por um). Se preferir, peca direto o
numero MIDI e pule a conversao inteiramente.

**A declaracao do usuario vence a deteccao automatica.** `tools.tuning`
(a inferencia automatica de afinacao) continua rodando por conta propria
a partir da distribuicao de canais do MIDI. Quando ela e a declaracao do
usuario concordam, otimo — reforca a confianca. Quando discordam, **o
relatorio mostra os dois valores e diz que esta usando o declarado** —
contradicao e aviso, nunca erro, e nunca silenciosamente ignorada.

Grave a estrutura em `instruments`:

```
instruments: {
  guitar: { known, strings, tuning: { name, notes } | null },
  bass: {
    known, strings, tuning: { name, notes } | null,
    playing_style: "finger" | "pick" | "slap" | null,
    notation: "written" | "sounding" | null,
  },
}
```

`instruments` NUNCA carrega o mesmo texto duas vezes em formatos
diferentes — `tuning.notes` e a UNICA fonte de verdade numerica; `strings`
e a contagem que ela tem que bater. Familia ausente do MIDI de origem
simplesmente nao aparece em `instruments` — nao grave entrada vazia so
pra "completar".

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

- `echo '{"family": "<familia>"}' | python3 -m tools.cli tool
  techniques.list --input -` para ver o vocabulario disponivel para a
  familia.
- `echo '{"name": "<tecnica>"}' | python3 -m tools.cli tool
  techniques.describe --input -` para ler a receita completa da tecnica — o que e
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
   `echo '{"family": "<familia>"}' | python3 -m tools.cli tool
   techniques.list --input -` e, para as tecnicas que a pesquisa sugeriu, rode
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
- Nao pergunte configuracao de instrumento de corda pra familia ausente do
  MIDI de origem, e nao chute numero de cordas/afinacao quando o usuario
  disser "nao sei" — grave `instruments.<familia>.known: false` com tudo
  `null`. Nao resolva nome de afinacao de cabeca: so o manual
  `guitar.drop_tuning` (via `techniques.describe`) resolve; nome que nao
  aparece la vira pergunta pelas notas das cordas soltas.
- Nao rode `midi-arranger run` a partir daqui. Isto e a fase interativa. A
  execucao headless e outra fase, invocada pelo usuario.
