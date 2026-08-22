# Corpus de bateria — MIDIs da banda do usuário

Dez MIDIs de bateria de músicas próprias, exportados do REAPER com Superior Drummer 3.

## Para que servem

**Vocabulário e estrutura.** Estes arquivos documentam quais articulações o usuário usa, com que peso, e
como ele constrói levada e virada. É a base do perfil de estilo `drums` quando o usuário disser "no
estilo das nossas músicas".

**Não servem como fonte de humanização.** Medição do acervo inteiro:

- offset mediano contra a grade: **exatamente 0** em todas as famílias (kick, caixa, hat, tom, prato)
- velocity travada: de **65% a 100%** das notas em 127, conforme o arquivo
- ghost notes na caixa: **0%** em nove dos dez arquivos
- autocorrelação lag-1 do hat: **≈ 0** (performance real dá ≈ −0,48)

A humanização, no fluxo do usuário, acontece dentro do Superior Drummer (Hit Variation, round robin,
adjacent layers), não no MIDI. Números de velocity e de timing vêm de `knoledgebase/tecnicas/` e do
ÂNCORA, nunca daqui.

## Fixture principal

`ENTRE NÓS.mid` é o mais chapado do conjunto: **100% das notas em velocity 127, uma única velocity
distinta, zero desvio de grade, zero ghost note**. É o pior MIDI possível do ponto de vista de intenção,
e por isso o melhor caso de teste do motor de técnicas — se ele sai com intenção, o motor funciona.

## Kit de origem

| Peça | Biblioteca |
|---|---|
| Kick, hi-hat, tons, surdos, ride, crashes | EZX Modern Metal (`EZX2_ModernMetal`) |
| Caixa (`Snare15`, baquetas) | EZX Metal! (`EZX_Metal`) |

Mapa de notas e aliases resolvidos em `knoledgebase/tecnicas/tecnicas_bateria_midi.md` §5.

## Perfil por arquivo

| Arquivo | BPM | Notas | Vel 127 | Velocities distintas | Fora da grade |
|---|---|---|---|---|---|
| ENTRE NÓS | 147 | 1037 | 100,0% | 1 | 0% |
| MARÉ DRUMS | 187 | 1356 | 97,6% | 7 | 0% |
| O PESO DRUMS | 188 | 801 | 96,5% | 2 | 0,1% |
| TEMPESTADE | 91 | 1281 | 96,2% | 7 | 14,2% |
| CRESCER | 170 | 1089 | 95,8% | 5 | 2,5% |
| DEIXE IR | 98 | 1005 | 89,5% | 13 | 2,7% |
| ATÉ AQUI | 126 | 1503 | 81,9% | 17 | 5,9% |
| PONTO DE LASTRO | 95 | 986 | 69,1% | 14 | 3,2% |
| REFLEXO | 136 | 1567 | 68,2% | 69 | 36,1% |
| FARDO | 90 | 755 | 65,4% | 16 | 11,1% |

`REFLEXO` destoa porque monta grooves vindos prontos das bibliotecas da Toontrack (The Metal Foundry,
The Progressive Foundry, EZX Duality I/II, Made of Metal, Modern Metal, Post-Metal, Death Metal) — esses
foram tocados por bateristas reais e mantêm a variação original.
