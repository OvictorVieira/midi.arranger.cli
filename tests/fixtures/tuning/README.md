# Fixtures de deteccao de afinacao (issue #35)

O MIDI multi-instrumento real do usuario **nao esta versionado** neste
repositorio — arquivo de projeto pessoal, fora do escopo publico. Estas seis
fixtures sinteticas cobrem, uma a uma, as situacoes que o detector de
`tools.tuning` precisa lidar, com nas mesmas convencoes de exportacao do
Guitar Pro / Songsterr (um canal por corda).

Todas as fixtures sao geradas por `generate.py`, um script determinístico ao
lado deste README. Rode `python3 tests/fixtures/tuning/generate.py` para
regerar os `.mid`; as saidas sao byte-identicas entre execucoes.

Nos testes (`tests/test_tuning_fixtures.py`), as fixtures sao regeradas em um
diretorio temporario a cada rodada, o que garante que a suite pega
imediatamente qualquer drift entre o script de geracao e os `.mid`
versionados.

## Fixture A — `fixture_a_rhythm_guitar.mid`

Guitarra ritmica (patch GM 30 — Overdriven Guitar), track `Guitar`, 5 canais
com os minimos e a distribuicao de notas documentados na issue:

| Canal | MIDI corda | Notas | % da track |
|-------|------------|-------|-----------:|
| 0     | 32 (G#1)   | 28    | 28%        |
| 1     | 39 (D#2)   | 37    | 37%        |
| 2     | 44 (G#2)   | 29    | 29%        |
| 3     | 52 (E3)    | 3     | 3%         |
| 4     | 55 (G3)    | 3     | 3%         |

Total: 100 notas. Os canais 3 e 4 tem 3 notas cada e caem pela **TRAVA 2**
(`MIN_NOTES_PER_CHANNEL_FOR_INFERENCE = 8`). Sobram 3 candidatos, que
concentram 28 + 37 + 29 = **94% do total** — `low_strings_top3_percentage`
esperado = 94.0. Os intervalos das cordas graves ficam em `[7, 5]`, prefixo
que classifica como `drop`; o nome deriva da pitch class da corda mais grave
(MIDI 32 % 12 = 8 => G#), resultando em `Drop G#`.

## Fixture B — `fixture_b_bass_riff.mid`

Baixo (patch GM 33 — Electric Bass finger), track `Bass`, 3 canais com uma
concentracao de ~91,5% na corda mais grave aberta:

| Canal | MIDI corda | Notas | % da track |
|-------|------------|-------|-----------:|
| 0     | 21 (A0)    | 183   | 91,5%      |
| 1     | 28 (E1)    | 10    | 5,0%       |
| 2     | 33 (A1)    | 7     | 3,5%       |

Total: 200 notas. O canal 2 (7 notas) cai pela TRAVA 2. Sobra o par de
candidatos com intervalo `[7]`; prefix-match classifica como `drop`, com
nome `Drop A` (MIDI 21 % 12 = 9 => A). Confianca `low`, porque 2 canais
candidatos ficam abaixo do limiar de `high` (`MIN_CANDIDATES_FOR_HIGH_CONFIDENCE = 4`).

## Fixture C — `fixture_c_voice_wind_patch.mid`

Voz com 4 canais e intervalos `[5, 5, 4]` entre os minimos, mas patch GM 73
(Flute) e nome de track `Vocals`. A distribuicao **imita** o formato de um
instrumento de corda, mas nenhuma das tres evidencias da TRAVA 1 dispara:

- nome de track nao contem `guitar`/`bass`/`guitarra`/`baixo`;
- patch GM 73 esta fora de `GM_STRINGED_PROGRAMS`;
- nenhuma declaracao explicita.

Resultado esperado: `is_stringed=False`, `discard_reason='not_stringed'`,
`candidate_channels=()`, `tuning_class='unknown'`, `tuning_name=None`,
`confidence='unknown'`.

## Fixture D — `fixture_d_lead_guitar_low_count.mid`

Lead guitar (patch GM 30) com apenas dois canais dedilhados brevemente: 2
notas no canal 0 (MIDI 68) e 4 notas no canal 1 (MIDI 75). O instrumento e
de corda (passa TRAVA 1), mas ambos os canais caem pela **TRAVA 2** — os
minimos nao representam corda solta, sao nota casada de passagem.

Resultado esperado: `candidate_channels=()`, ambos os canais em
`discarded_channels` com `reason='low_note_count'`, `tuning_class='unknown'`,
`tuning_name=None`, `confidence='unknown'`.

## Fixture E — `fixture_e_standard_tuning.mid`

Afinacao padrao E: 6 canais com minimos `40, 45, 50, 55, 59, 64`
(intervalos `[5, 5, 5, 4, 5]`). Cada canal recebe exatamente
`MIN_NOTES_PER_CHANNEL_FOR_INFERENCE = 8` notas para todos passarem na
TRAVA 2. Serve para provar que **padrao nao e classificado como drop** e
que o vocabulario de classe inclui `standard`.

Resultado esperado: 6 candidatos, `tuning_intervals=(5, 5, 5, 4, 5)`,
`tuning_class='standard'`, `tuning_name='Standard E'`, `confidence='high'`
(6 candidatos >= 4).

## Fixture F — `fixture_f_single_channel_guitar.mid`

Track de corda unica (patch GM 30, nome `Guitar`) com 24 notas em MIDI 40
todas concentradas no canal 0. Passa TRAVA 1, mas nao ha separacao por
canal — a convencao "um canal por corda" nao foi aplicada nesse export.

Resultado esperado: exatamente 1 candidato, sem intervalos (`()`),
`tuning_class='unknown'`, `tuning_name=None`, `confidence='unknown'`,
`lowest_string_pitch=40`. Nao ha erro: o detector reporta a ausencia de
informacao de corda.
