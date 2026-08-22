# Fixture golden: `ancora_arranjo_atual.mid`

Fonte: `~/Desktop/O Naufrago Arranjos/Ancora/Ancora - Arranjo Atual Midi.mid`
SHA-256: `e2727e269436ee09e0ced1e5b41345592ec3fec6d869938d4ac62ac0b41a35df`

O arranjo escrito a mao serve como referencia de calibracao dos validadores
`placement` e `harmony_validator` (US-009). Tudo abaixo foi extraido com
`python3 -m midiarranger analyze` sobre o proprio MIDI mais conferencia
manual dos indices de `pretty_midi.instruments` (a ordem do pretty_midi e
estavel e casa com os indices de `mido.tracks[i]` com um offset de -1 por
causa da meta-track vazia inicial).

## Secoes (0-based, half-open `[start_bar, end_bar)`)

| Label       | Kind      | start_bar | end_bar | Bars | Fonte  |
|-------------|-----------|-----------|---------|------|--------|
| INTRO A     | intro     | 4         | 14      | 10   | marker |
| INTRO B     | intro     | 14        | 30      | 16   | marker |
| VERSE 1     | verse     | 30        | 46      | 16   | marker |
| CHORUS 1    | chorus    | 46        | 62      | 16   | marker |
| VERSE 2     | verse     | 62        | 78      | 16   | marker |
| PRE-CHORUS  | pre       | 78        | 87      | 9    | marker |
| CHORUS 2    | chorus    | 87        | 103     | 16   | marker |
| INTERLUDE   | interlude | 103       | 119     | 16   | marker |
| CHORUS 3    | chorus    | 119       | 135     | 16   | marker |
| OUTRO       | outro     | 135       | 162     | 27   | marker |

Tom global detectado: `D#` (menor natural). Compassos totais: 163.

## Tracks do arranjo real (`pretty_midi.instruments`)

Tracks source do Songsterr (Lead/Rhythm/Bass/Drums/Vocals) nao entram no
plano golden — elas sao o insumo, nao camada de arranjo. As colunas
listam apenas as camadas realmente escritas a mao no arranjo, com o
element_id correspondente do `plan_ancora_golden.json`.

| pretty_midi# | Nome no MIDI          | element_id         | Notas | Faixa MIDI |
|--------------|-----------------------|--------------------|-------|------------|
| 8            | Steinway Grand Piano  | `piano_hook_ch1`   | 96    | 59-70      |
| 9            | Steinway Grand Piano  | `piano_hook_ch2`   | 96    | 59-70      |
| 10           | Steinway Grand Piano  | `piano_hook_ch3`   | 96    | 59-70      |
| 13           | Wide Suitcase         | `rhodes_verse1`    | 24    | 47-58      |
| 14           | Wide Suitcase         | `rhodes_verse2`    | 39    | 44-58      |
| 17           | Breathing Strings     | `strings_bed`      | 49    | 35-78      |
| 20           | Emerald Haze Pad      | `pad_outro`        | 4     | 47-51      |
| 23           | Pulse Wave Bass       | `sub_bass_outro`   | 48    | 35-42      |

O mapeamento `element_id -> instrument index` vive em `GOLDEN_TRACK_INDEX`
(em `test_golden_ancora.py`). O plano em disco nao carrega esse indice
porque o schema descreve intencao musical, nao amarracao a MIDI de origem
— revalide os indices se o fixture for regerado.

## Densidade por (elemento, secao)

Notas por bar computadas com `find_bar(analysis, note.start)` sobre o
tempo real do MIDI. Secoes onde o elemento e declarado mas nao produz nota
disparam **aviso** de cobertura em `validate_placement` (nao bloqueia).

| element_id         | Secao       | Notas | Bars | Notas/Bar |
|--------------------|-------------|-------|------|-----------|
| `piano_hook_ch1`   | CHORUS 1    | 96    | 16   | 6.00      |
| `piano_hook_ch2`   | PRE-CHORUS  | 0     | 9    | 0.00 *    |
| `piano_hook_ch2`   | CHORUS 2    | 96    | 16   | 6.00      |
| `piano_hook_ch3`   | INTERLUDE   | 3     | 16   | 0.19      |
| `piano_hook_ch3`   | CHORUS 3    | 93    | 16   | 5.81      |
| `rhodes_verse1`    | VERSE 1     | 24    | 16   | 1.50      |
| `rhodes_verse2`    | VERSE 2     | 27    | 16   | 1.69      |
| `rhodes_verse2`    | PRE-CHORUS  | 12    | 9    | 1.33      |
| `strings_bed`      | CHORUS 2    | 8     | 16   | 0.50      |
| `strings_bed`      | INTERLUDE   | 41    | 16   | 2.56      |
| `pad_outro`        | OUTRO       | 4     | 27   | 0.15      |
| `sub_bass_outro`   | CHORUS 3    | 1     | 16   | 0.06      |
| `sub_bass_outro`   | OUTRO       | 47    | 27   | 1.74      |

`*` `piano_hook_ch2` declara PRE-CHORUS por conta do pickup ritmico
esperado — o Steinway#9 do MIDI real nao emite nota no PRE-CHORUS, entao
`validate_placement` deve gerar um **aviso** de cobertura para esta
secao. Aviso e esperado; nao bloqueia o teste positivo.

## Calibracao dos limiares

`DENSITY_LOW_AXIS_THRESHOLD=3` e `DENSITY_MULTIPLIER=2.0` continuam sendo
os defaults de `placement.py`. Nenhum elemento do golden excede o piso
de 2x da media das densidades ativas por secao — nenhuma secao com
`energy.densidade <= 3` tem 2+ elementos ativos ao mesmo tempo (OUTRO
tem `densidade=4`, INTRO A tem `densidade=3` mas so recebe o `piano_bed`
implicito e nenhum elemento do golden). Se um ajuste for necessario em
rodadas futuras, o novo default deve ser comentado com a justificativa
extraida do golden.

## Regenerando o fixture

1. Copie o MIDI de referencia para
   `common/midiarranger/tests/fixtures/ancora_arranjo_atual.mid`.
2. Recalcule o sha256 (via `hashlib.sha256`) e atualize `source_midi.sha256`
   em `plan_ancora_golden.json`.
3. Rode `python3 -m pytest common/midiarranger/tests/test_golden_ancora.py`
   para validar que a estrutura ainda casa com o novo arquivo.
