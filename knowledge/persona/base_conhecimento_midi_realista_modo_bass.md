# Base de conhecimento — MIDI realista para MODO BASS 2

**Programação de baixo elétrico humanizado no Logic Pro 11, com integração ao Neural DSP Parallax**

Versão: 1.0  
Data: 20 de agosto de 2026  
Uso: material de referência para uma skill/agente de inteligência artificial que recebe, analisa, cria ou transforma arquivos MIDI de baixo.

---

## 1. Objetivo

Esta base ensina uma inteligência artificial a criar performances MIDI de baixo que explorem o motor físico do MODO BASS 2, em vez de apenas variar velocities aleatoriamente.

A skill deve ser capaz de:

- analisar uma linha de baixo existente;
- identificar estilo de execução: dedos, palheta ou slap;
- reconstruir uma execução fisicamente plausível;
- escolher corda, região do braço e mudanças de posição;
- classificar ataques, acentos, notas ligadas, slides e ghost notes;
- ajustar início, duração, velocity e articulação;
- gerar automações MIDI para MODO BASS 2;
- preservar sincronismo com kick, caixa, guitarra e intenção do riff;
- preparar um sinal de baixo adequado para processamento no Parallax;
- explicar todas as mudanças realizadas.

### Resultado esperado

O resultado não deve soar como “notas MIDI com pequenas variações”. Deve soar como uma pessoa escolhendo:

- qual corda tocar;
- onde posicionar a mão;
- qual dedo ou direção de palheta utilizar;
- quando atacar novamente;
- quando ligar duas notas;
- quando deslizar;
- quando abafar;
- quando deixar a corda ressoar;
- quando produzir ruídos mecânicos discretos.

---

## 2. Princípio central

> Humanização de baixo é modelagem de gesto físico, não randomização.

Velocity representa apenas uma parte da performance. O realismo depende da combinação entre:

1. intenção musical;
2. relação com a bateria;
3. duração das notas;
4. espaços e sobreposições;
5. articulação;
6. corda e casa;
7. posição da mão esquerda;
8. posição e técnica da mão direita;
9. ruídos de contato e deslocamento;
10. microtiming;
11. dinâmica por frase;
12. coerência física entre todos esses elementos.

A IK Multimedia informa que o MODO BASS 2 utiliza modelagem física e permite controlar em tempo real posição de execução, mute, slide, vibrato, técnica, cordas e outras propriedades de performance. [Fonte oficial: MODO BASS 2](https://www.ikmultimedia.com/products/modobass2/).

---

# Parte I — Modelo físico da performance

## 3. O que torna uma linha de baixo artificial

Uma linha normalmente soa dura quando apresenta vários destes problemas:

- todas as notas começam exatamente no grid;
- todas terminam exatamente no início da nota seguinte;
- todas possuem duração idêntica;
- todas são atacadas novamente, mesmo onde um músico faria hammer-on ou pull-off;
- notas distantes são conectadas sem deslocamento físico;
- o instrumento troca de corda e posição a cada nota;
- todas as velocities ficam próximas;
- as velocities são aleatórias, mas não seguem acentos ou frase;
- não há alternância de dedos ou palheta;
- não há ghost notes, abafamentos ou ruídos de parada;
- slides são simulados apenas trocando notas;
- notas repetidas soam como cópias;
- o baixo duplica a guitarra sem assumir função rítmica própria;
- o Parallax comprime e distorce uma performance artificial, tornando o problema mais evidente.

## 4. Ordem correta da humanização

A skill deve respeitar esta sequência:

1. identificar afinação e extensão do instrumento;
2. analisar ritmo, frase e relação com bateria/guitarra;
3. escolher técnica principal;
4. atribuir cordas e posições fisicamente plausíveis;
5. decidir ataques, ligados, slides, ghosts e abafamentos;
6. corrigir durações e sobreposições;
7. construir curva de velocity por frase;
8. aplicar microtiming seletivo;
9. inserir controles contínuos e ruídos discretos;
10. validar com MODO BASS limpo;
11. validar novamente com Parallax;
12. produzir relatório das alterações.

Não aplicar Humanize global antes de resolver articulação e execução física.

---

## 5. Afinação, cordas e casas

### 5.1 Representação interna

Para evitar diferenças de nomenclatura entre Logic, MODO e outros programas, a skill deve trabalhar internamente com **número MIDI absoluto**.

Exemplo em afinação científica:

| Nota | Número MIDI |
|---|---:|
| A0 | 21 |
| B0 | 23 |
| E1 | 28 |
| A1 | 33 |
| D2 | 38 |
| G2 | 43 |
| C3 | 48 |

O Logic pode mostrar a mesma altura com nome de oitava diferente. O número MIDI elimina essa ambiguidade.

### 5.2 Afinações de referência

```yaml
standard_4_string:
  strings_low_to_high: [E1, A1, D2, G2]
  midi: [28, 33, 38, 43]

standard_5_string:
  strings_low_to_high: [B0, E1, A1, D2, G2]
  midi: [23, 28, 33, 38, 43]

drop_a_5_string:
  strings_low_to_high: [A0, E1, A1, D2, G2]
  midi: [21, 28, 33, 38, 43]

drop_d_4_string:
  strings_low_to_high: [D1, A1, D2, G2]
  midi: [26, 33, 38, 43]
```

### 5.3 Cálculo de casa

Para uma nota `N` e uma corda aberta `S`:

```text
fret = N - S
```

A atribuição é possível quando:

```text
0 <= fret <= maximum_fret
```

Usar `maximum_fret = 24` como padrão configurável.

### 5.4 Custo de movimento

Quando uma nota pode ser executada em diferentes cordas, calcular:

```text
movement_cost =
    abs(current_fret - previous_fret) * fret_weight
  + string_distance * string_weight
  + position_shift_penalty
  + open_string_penalty_or_bonus
  + articulation_constraint_penalty
```

Sugestão:

- favorecer movimento de até quatro ou cinco casas;
- favorecer permanência na mesma região durante uma frase;
- para slide ou ligado, favorecer fortemente a mesma corda;
- para riff grave, favorecer corda mais grave e posições baixas;
- para frase melódica, permitir corda grave em casas altas quando o timbre encorpado for desejado;
- não trocar de corda sem motivo em cada nota;
- permitir mudança de posição durante pausas, notas longas ou slides deliberados.

### 5.5 Regras de plausibilidade

- Um baixo elétrico é normalmente monofônico. Acordes devem ser intencionais.
- Duas notas simultâneas precisam estar em cordas diferentes e ser fisicamente alcançáveis.
- Slide e hammer-on/pull-off devem preferir a mesma corda.
- Não é possível deslizar abaixo da pestana.
- Uma troca grande de posição exige pausa, slide ou tempo suficiente.
- Nota em corda solta possui timbre e sustain diferentes.
- Notas consecutivas iguais exigem novo ataque, salvo efeito específico de ressonância.
- Mudança de corda costuma alterar ataque, ruído e sustain.
- Um padrão extremamente rápido precisa considerar limite de alternância de dedos ou palheta.

---

# Parte II — MODO BASS 2

## 6. Por que o MODO BASS responde de maneira diferente

O MODO BASS sintetiza o comportamento do instrumento em tempo real. Velocity pode influenciar mais que volume: intensidade, ataque, brilho e, em determinadas articulações, velocidade de transição.

Por isso:

- comprimir o áudio não substitui uma curva de velocity musical;
- aumentar todas as velocities pode tornar o baixo uniforme e agressivo;
- um slide legato utiliza a velocity da nota de destino para determinar sua velocidade;
- posição, corda, ação, idade das cordas e técnica mudam a resposta ao mesmo MIDI.

Um guia técnico de Craig Anderton enfatiza a importância do toque na fonte MIDI, de pequenas pausas entre notas normalmente atacadas e do uso de slides. [Fonte: How to Get the Most Out of MODO Bass](https://craiganderton.org/how-to-get-the-most-out-of-modo-bass/).

## 7. Seções do teclado inferior

### 7.1 Área de keyswitches

As teclas coloridas fora da extensão musical controlam técnica, articulação, corda ou modo de execução.

### 7.2 Área tocável

É a extensão de notas do instrumento configurado.

### 7.3 Stop Strings

Nos baixos elétricos, a região próxima de E5 pode disparar a parada de todas as cordas. Confirmar no preset e versão atuais.

### 7.4 Área vermelha de posição

No mapeamento padrão descrito pelo manual, F5 até E7 selecionam a posição da mão esquerda no braço.

- a parte baixa da região representa posições próximas da pestana;
- a parte alta representa casas progressivamente mais altas;
- o controle ativa a lógica de fingering do tipo **Nearest**;
- isso influencia corda, casa, timbre, ruído e viabilidade de slides.

A posição também pode ser controlada continuamente por CC4.

### 7.5 Regra de segurança

Os nomes de oitava podem divergir entre MODO, Logic e bibliotecas. A skill deve:

1. obter o mapeamento atual da aba **Control**;
2. armazenar o número MIDI do keyswitch;
3. nunca depender somente do texto “C0”, “F5” etc.;
4. permitir um `octave_display_offset` configurável;
5. não aplicar Humanize aos keyswitches.

---

## 8. Controles importantes

O mapeamento abaixo representa os padrões encontrados na documentação do MODO BASS 2. Presets e versões podem alterar valores.

| Função | Tipo/padrão | Uso |
|---|---|---|
| Finger/Pizzicato | KS C#0 | ativa dedos/pizzicato |
| Pick | KS D#0 | ativa palheta |
| Slap | KS F#0 | ativa slap |
| Bend | CC5 | bend contínuo com faixa fixa documentada de ±4 semitons |
| Slide | Pitch Wheel | slide com faixa definida por Slide Range |
| Vibrato | CC1 | quantidade de vibrato |
| Hammer-on/Pull-off | KS C0 | ligado sem novo ataque convencional |
| Legato Slide | CC65 | slide quando notas se sobrepõem |
| Muting | CC9 | abafamento da mão direita |
| Ghost Mode | KS A#-1 | nota fantasma/percussiva |
| Harmonics | KS F0 | harmônico |
| Let Ring | CC64 | mantém ressonância |
| Index/Down/Slap | KS C#-1 | força dedo indicador, downstroke ou slap |
| Middle/Up/Pull | KS D#-1 | força dedo médio, upstroke ou pull |
| Left Hand Position | CC4 ou KS F5–E7 | posição no braço |
| Force String | KS específicos | força corda B/E/A/D/G/C |
| Pluck Position | CC3 | posição do ataque da mão direita |
| Chord Mode | CC2 | permite múltiplas notas simultâneas |
| Stop on Detach | KS G#0 | produz som de parada no Note Off |

Referência complementar do mapeamento: [guia de controles do MODO BASS 2](https://www.ongen-opt.com/entry/2023/10/09/220000).

## 9. Parâmetros do Play Style

### Comuns

- **Muting**: quantidade de abafamento.
- **Let Ring**: permite ressonância entre notas.
- **Fingering**:
  - First Position: primeiras casas;
  - Easy: comportamento fixo derivado de padrões comuns;
  - Nearest: procura a posição mais próxima da anterior ou da posição forçada.
- **Open String**: permite cordas soltas.
- **Detach Noise**: ruído ao soltar a nota/casa.
- **Slide Noise**: ruído de deslocamento pela corda.

### Finger

- Stroke: Alternate, Index ou Middle.
- Touch: Soft, Normal ou Hard.
- Pluck: posição entre braço e ponte.

### Pick

- Stroke: Alternate, Down ou Up.
- Scratch: comportamento/ângulo de palheta.
- Pluck: posição de ataque.

### Slap

- Stroke: Auto, Slap ou Pull.
- Threshold: velocity a partir da qual o modo Auto escolhe Pull.

[Referência de parâmetros do Play Style](https://www.ongen-opt.com/entry/2023/10/09/220000).

---

# Parte III — Velocity, duração e microtiming

## 10. Velocity não é volume aleatório

A skill deve calcular velocity a partir de funções musicais:

```text
velocity =
    base_for_style
  + metric_accent
  + drum_alignment_accent
  + phrase_shape
  + articulation_adjustment
  + stroke_adjustment
  + small_controlled_variation
```

### Componentes

- **base_for_style**: força média de dedos, palheta ou slap.
- **metric_accent**: peso do tempo/subdivisão.
- **drum_alignment_accent**: reforço quando coincide com kick importante.
- **phrase_shape**: crescendo, queda ou direção da frase.
- **articulation_adjustment**: ghost, ligado, harmônico etc.
- **stroke_adjustment**: diferenças entre dedos ou direções da palheta.
- **small_controlled_variation**: variação final pequena, limitada e reproduzível.

### Regra

O componente aleatório nunca deve ser maior que a intenção musical acumulada.

## 11. Faixas gerais de velocity

Valores iniciais; calibrar pela curva de velocity, modelo do baixo, Touch e arranjo.

| Função | Velocity inicial |
|---|---:|
| ghost/dead note | 20–50 |
| nota ligada suave | 50–75 |
| nota intermediária | 70–90 |
| ataque normal | 82–105 |
| acento | 102–118 |
| acento extremo/slap pull | 115–125 |

Evitar velocity 127 repetida. Reservar o topo da escala para eventos excepcionais.

## 12. Duração das notas

Expressar inicialmente como proporção do espaço até a nota seguinte.

| Articulação | Proporção inicial |
|---|---:|
| ghost note | 15–35% |
| staccato/mute | 35–65% |
| ataque normal apertado | 60–82% |
| ataque normal aberto | 78–95% |
| nota sustentada | 90–100% |
| Let Ring | pode ultrapassar evento seguinte quando fisicamente possível |
| hammer/pull | sobreposição de 5–25 ms |
| legato slide | sobreposição de 10–50 ms |

O artigo de Craig Anderton demonstra que deixar pequenas pausas entre notas normalmente reatacadas aumenta o realismo. [Fonte](https://craiganderton.org/how-to-get-the-most-out-of-modo-bass/).

## 13. Microtiming

### Âncoras

Manter próximas do grid:

- primeiro tempo;
- ataques principais com kick;
- entrada de breakdown;
- uníssonos decisivos com guitarra;
- início de seção.

### Notas não estruturais

Podem receber variação pequena:

- subdivisões intermediárias;
- ghost notes;
- pickups;
- finais de frase;
- antecipações humanas.

### Valores iniciais para metal moderno

- âncora: 0 a ±3 ms;
- ataque normal: ±3–8 ms;
- nota intermediária/ghost: ±5–12 ms;
- fill expressivo: até ±15 ms, se o groove permitir;
- nunca aplicar atraso independente a uma nota que precisa coincidir exatamente com kick/guitarra.

### Direção temporal

- baixo ligeiramente atrás pode soar pesado;
- baixo ligeiramente à frente pode aumentar urgência;
- não aplicar uma direção fixa à música inteira;
- usar relação com bateria como referência, não “humanização absoluta”.

---

# Parte IV — Fingerstyle

## 14. Quando usar dedos

Escolher Finger quando o objetivo incluir:

- ataque redondo e orgânico;
- dinâmica ampla;
- conexão entre notas;
- post-hardcore, rock alternativo, ambient e partes emocionais;
- versos menos agressivos;
- contraste com refrão ou breakdown de palheta;
- linhas com hammer-ons, pull-offs e slides frequentes.

Finger também pode ser agressivo usando Touch Hard, ação baixa e posição próxima da ponte.

## 15. Configuração inicial

```yaml
play_style: finger
stroke: alternate
touch: normal
pluck_position: middle_to_bridge
fingering: nearest
open_string: true
muting: 5_to_25_percent
detach_noise: 10_to_25_percent
slide_noise: 15_to_35_percent
```

Para parte suave, mover Pluck em direção ao braço e usar Touch Soft. Para parte agressiva, mover em direção à ponte e usar Touch Hard.

## 16. Alternância de dedos

Em repetição contínua:

```text
Index, Middle, Index, Middle...
```

A alternância deve sobreviver a mudanças de corda, salvo quando uma técnica real justificaria reinício.

### Diferença de velocity

Exemplo de oito colcheias:

```text
Index: 102
Middle: 94
Index: 98
Middle: 91
Index/acento: 108
Middle: 95
Index: 99
Middle: 90
```

Não usar diferença fixa idêntica. Variar por frase.

## 17. Velocity para fingerstyle

| Função | Faixa inicial |
|---|---:|
| ghost | 22–48 |
| hammer/pull | 52–78 |
| normal suave | 68–88 |
| normal | 80–102 |
| acento | 100–116 |
| ataque muito forte | 112–123 |

## 18. Articulações características

### Hammer-on

- nota de destino acima da origem;
- preferir intervalo de 1–4 semitons;
- mesma corda;
- nota de destino com velocity inferior ao ataque normal;
- pequena sobreposição;
- não produzir novo ataque forte.

### Pull-off

- nota de destino abaixo da origem;
- preferir intervalo pequeno;
- mesma corda;
- velocity da nota de destino normalmente menor;
- pequena sobreposição.

### Slide

- usar em mudança de posição, entrada de nota emocional ou final de frase;
- evitar slide em toda troca de nota;
- controlar velocidade pela velocity da nota de destino no Legato Slide.

### Ghost

- inserir em lacunas rítmicas;
- usar como preparação de acento;
- duração curta e velocity baixa;
- alinhar com gesto de bateria quando fizer sentido.

## 19. Padrão de exemplo — fingerstyle em colcheias

```yaml
meter: 4/4
subdivision: eighth_notes
events:
  - beat: 1.0
    role: metric_anchor
    finger: index
    velocity: 108
    gate: 0.84
    timing_ms: 0
  - beat: 1.5
    role: intermediate
    finger: middle
    velocity: 91
    gate: 0.78
    timing_ms: 4
  - beat: 2.0
    role: kick_alignment
    finger: index
    velocity: 101
    gate: 0.82
    timing_ms: 1
  - beat: 2.5
    role: ghost
    articulation: ghost
    velocity: 39
    gate: 0.25
    timing_ms: 7
```

---

# Parte V — Palheta

## 20. Quando usar palheta

Escolher Pick quando o objetivo incluir:

- ataque definido;
- metal moderno, metalcore, punk, pop punk e hard rock;
- sincronismo apertado com guitarras;
- riffs rápidos ou repetitivos;
- clank e médios agressivos para Parallax;
- palm muting;
- downstrokes acentuados e alternate picking rápido.

## 21. Configuração inicial

```yaml
play_style: pick
stroke: alternate
scratch: normal
pluck_position: middle_to_bridge
fingering: nearest
open_string: true
muting: 10_to_35_percent
detach_noise: 8_to_22_percent
slide_noise: 10_to_30_percent
```

Com Parallax, Scratch Hard, ação baixa e velocities extremas podem gerar excesso de clank. Ajustar ouvindo dentro da mix.

## 22. Alternate picking

Para semicolcheias contínuas:

```text
Down, Up, Down, Up...
```

### Regras

- downstroke tende a receber acentos maiores;
- upstroke pode ter ataque levemente inferior;
- não inverter aleatoriamente no meio do padrão;
- depois de pausa longa, normalmente reiniciar com downstroke;
- em gallops, preservar uma sequência mecanicamente coerente;
- em trecho propositalmente todo downstroke, validar se o BPM permite.

## 23. Velocity para palheta

| Função | Faixa inicial |
|---|---:|
| ghost/dead | 25–52 |
| upstroke intermediário | 72–94 |
| downstroke intermediário | 80–102 |
| ataque normal | 88–108 |
| acento downstroke | 104–120 |
| ataque extremo | 115–124 |

Exemplo:

```text
Down 112, Up 91, Down 101, Up 87,
Down 108, Up 92, Down 100, Up 85
```

## 24. Duração para palheta

### Riff aberto

- 75–95% do espaço;
- pequeno gap antes do próximo ataque;
- muting baixo.

### Riff apertado

- 50–78%;
- muting moderado;
- Stop on Detach seletivo;
- gap perceptível, mas não tão grande que o low end desapareça.

### Breakdown

- ataques principais podem ser curtos;
- deixar sustain suficiente para o sub e a compressão do Parallax desenvolverem corpo;
- alternar notas secas com uma nota longa cria impacto.

### Gallop

Padrão típico:

```text
long-short-short
```

Não usar três notas com duração e velocity iguais. A primeira geralmente recebe maior peso; as duas curtas mantêm fluxo.

## 25. Ligados com palheta

Hammer-on, pull-off e slides continuam possíveis, mas devem aparecer como contraste.

Exemplos:

- palhetar a primeira nota e fazer hammer-on na segunda;
- usar slide para chegar à primeira nota do refrão;
- usar pull-off curto no final de fill;
- não forçar novo ataque em todas as notas se o riff de guitarra utiliza ligados.

## 26. Padrão de exemplo — riff de semicolcheias

```yaml
style: pick
pattern_length: 1_bar
stroke_cycle: [down, up, down, up]
anchor_velocity: 112
down_velocity_range: [92, 108]
up_velocity_range: [80, 98]
gate_range: [0.58, 0.78]
anchor_timing_ms: 0
non_anchor_timing_ms: [-4, 6]
muting_cc9:
  verse: 35
  chorus: 18
  breakdown: 28
```

---

# Parte VI — Slap

## 27. Quando usar slap

Escolher Slap quando a linha depende de:

- contraste percussivo;
- alternância entre nota grave e pull agudo;
- funk, nu metal, rock alternativo e fills;
- ghost notes e dead notes;
- diálogo rítmico forte com bateria;
- eventos pontuais em produção de metal moderno.

Slap não deve ser escolhido apenas porque a velocity está alta.

## 28. Componentes

- **Slap**: ataque do polegar, normalmente em notas graves/médias.
- **Pull**: corda puxada, geralmente com ataque mais brilhante e velocity elevada.
- **Ghost/Dead**: som percussivo sem altura clara dominante.
- **Hammer/Pull-off**: conexões rápidas entre ataques principais.
- **Slide**: mudança expressiva de posição.

## 29. Configuração inicial

```yaml
play_style: slap
stroke: auto_or_explicit
auto_pull_threshold: 100_to_112
fingering: nearest
open_string: true
muting: 5_to_25_percent
detach_noise: 10_to_25_percent
slide_noise: 10_to_25_percent
```

Se a skill precisa de controle determinístico, preferir Slap/Pull explícitos em vez de depender somente do Threshold automático.

## 30. Velocity para slap

| Função | Faixa inicial |
|---|---:|
| ghost/dead | 20–48 |
| hammer/pull ligado | 48–76 |
| slap normal | 86–108 |
| slap acentuado | 104–120 |
| pull normal | 98–116 |
| pull acentuado | 112–125 |

O Threshold deve ficar acima das notas normais e abaixo das velocities reservadas ao Pull, quando Stroke estiver em Auto.

## 31. Duração para slap

- ghost/dead: 15–35%;
- slap seco: 35–65%;
- slap aberto: 60–85%;
- pull: 40–80%;
- hammer/pull-off: pequena sobreposição;
- notas graves longas são possíveis, mas não devem ocupar todas as lacunas percussivas.

## 32. Estrutura de groove

Uma linha realista alterna alturas e sons percussivos:

```text
Slap grave → ghost → pull → ghost → slap grave → hammer-on → pull
```

### Regras

- não usar Pull em toda nota aguda;
- ghost notes devem dialogar com caixa e hi-hat;
- não randomizar posições de ghost;
- manter espaço para os transientes respirarem;
- reservar velocities máximas para pulls ou slaps realmente acentuados;
- evitar Parallax excessivamente distorcido se ele destruir a diferença entre slap, pull e ghost.

## 33. Padrão de exemplo — slap

```yaml
events:
  - beat: 1.0
    articulation: slap
    velocity: 112
    gate: 0.60
  - beat: 1.5
    articulation: ghost
    velocity: 38
    gate: 0.22
  - beat: 2.0
    articulation: pull
    velocity: 119
    gate: 0.52
  - beat: 2.75
    articulation: ghost
    velocity: 34
    gate: 0.20
  - beat: 3.0
    articulation: slap
    velocity: 105
    gate: 0.65
```

---

# Parte VII — Slides, ligados e ruídos

## 34. Slide por Pitch Bend

### Uso

- slide livre;
- queda de nota;
- aproximação sem ataque de destino;
- efeito longo;
- final de frase.

### Processo

1. Definir Slide como Pitch Wheel.
2. Definir Slide Range em semitons.
3. Criar nota de origem.
4. Desenhar Pitch Bend durante a nota.
5. Retornar exatamente ao centro antes da próxima nota comum.

### Faixas

- 1–2 semitons: scoop ou gesto curto;
- 3–5: slide médio;
- 7–12: deslocamento dramático;
- evitar range 12 para todos os slides pequenos se isso tornar a edição imprecisa.

O MODO modela a passagem por casas, e slides para baixo são limitados pela posição física disponível. [Fonte técnica](https://craiganderton.org/how-to-get-the-most-out-of-modo-bass/).

## 35. Legato Slide por CC65

### Condições

- CC65 ativado;
- nota de origem ainda ativa quando a nota de destino começa;
- posição/corda fisicamente compatíveis;
- velocity de destino define velocidade do slide.

### Curva básica

```yaml
cc65:
  before_source: 127
  during_overlap: 127
  after_arrival: 0
overlap_ms: 10_to_50
target_velocity:
  slow_slide: 45_to_65
  medium_slide: 65_to_90
  fast_slide: 90_to_115
```

Essas faixas são pontos de partida; calibrar no instrumento.

### Diagnóstico de slide que não funciona

1. verificar sobreposição;
2. confirmar CC65;
3. confirmar mesma corda ou usar Force String;
4. escolher posição compatível com CC4/região vermelha;
5. verificar limite da pestana;
6. verificar Slide Range;
7. conferir se o keyswitch/CC foi deslocado pela humanização;
8. confirmar que o evento ocorre antes da nota.

## 36. Hammer-on/Pull-off

### Regras

- intervalos pequenos são mais naturais;
- mesma corda;
- ataque inicial mais forte;
- destino com velocity inferior;
- sobreposição curta;
- evitar novo ruído de palheta/dedo na nota de destino;
- usar articulation KS correspondente.

### Decisão

```text
if same_string
and abs(interval_semitones) <= 4
and phrase_is_connected
and no_strong_rearticulation_required:
    choose hammer_or_pull
```

## 37. Ghost notes

### Funções

- completar groove;
- preparar acento;
- imitar contato da mão com corda;
- ocupar espaço rítmico sem adicionar nova altura dominante.

### Regras

- duração muito curta;
- velocity baixa;
- posição rítmica intencional;
- frequência limitada;
- não converter automaticamente toda nota baixa em ghost;
- verificar com Parallax, que amplifica transiente e ruído.

## 38. Vibrato

Usar CC1 em notas sustentadas.

### Comportamento natural

- não começar no primeiro milissegundo;
- entrar depois do ataque;
- intensidade crescer e recuar;
- evitar em notas curtas;
- evitar mesma curva em todas as notas;
- mais comum em partes melódicas, fretless ou sustentadas.

## 39. Let Ring

Usar quando:

- corda aberta deve continuar;
- duas notas podem ressoar em cordas diferentes;
- a seção precisa de sustain orgânico;
- uma transição deve deixar cauda.

Não usar em riffs apertados ou quando a ressonância cria conflito harmônico.

## 40. Detach e Slide Noise

Esses controles são níveis globais/estilísticos, não substitutos de articulação.

Pontos de partida:

| Contexto | Detach Noise | Slide Noise |
|---|---:|---:|
| metal distorcido | 8–22% | 10–28% |
| finger orgânico | 12–30% | 18–40% |
| parte limpa/intimista | 15–35% | 20–45% |
| slap | 10–25% | 10–25% |

Com Parallax, reduzir antes de adicionar EQ. Distorção torna ruídos mais evidentes.

---

# Parte VIII — Logic Pro 11

## 41. Preparação da região

1. Duplicar a região MIDI original.
2. Manter uma versão `ORIGINAL` mutada.
3. Criar versão `PHYSICAL PASS`.
4. Remover notas simultâneas acidentais.
5. Corrigir notas fora da extensão.
6. Detectar quantização e subdivisão.
7. Separar keyswitches de notas musicais na análise.

## 42. Automation/MIDI no Piano Roll

O Logic permite criar e editar CC, Pitch Bend e automação de região no painel Automation/MIDI do Piano Roll. [Documentação oficial](https://support.apple.com/guide/logicpro/automationmidi-area-in-the-piano-roll-editor-lgcpa90a61bf/mac).

Procedimento:

1. abrir Piano Roll;
2. mostrar Automation/MIDI;
3. selecionar **Region**;
4. selecionar Pitch Bend ou o CC desejado;
5. desenhar eventos antes e durante as notas;
6. verificar retorno ao estado neutro.

Lanes importantes:

- Pitch Bend — slide livre;
- CC1 — vibrato;
- CC3 — posição do ataque;
- CC4 — posição da mão esquerda;
- CC9 — muting;
- CC64 — Let Ring;
- CC65 — Legato Slide.

## 43. Articulation Set

O Logic permite criar articulations e convertê-las em keyswitches ou mensagens MIDI para instrumentos de terceiros. [Articulation Set Editor — Apple](https://support.apple.com/guide/logicpro/manage-articulations-articulation-set-editor-lgcp33a49091/mac).

### Conjunto recomendado

```yaml
articulations:
  - normal
  - hammer_pull
  - legato_slide
  - ghost
  - harmonic
  - index_down_slap
  - middle_up_pull
  - force_b_string
  - force_e_string
  - force_a_string
  - force_d_string
  - force_g_string
  - stop_on_detach
```

### Observação

Para Legato Slide, existem duas opções:

1. desenhar CC65 manualmente;
2. remapear Legato Slide para keyswitch no MODO e incluí-lo no Articulation Set.

CC contínuos como posição e muting devem continuar em lanes de automação.

## 44. Humanize do Logic

O MIDI Transform → Humanize pode adicionar variação de posição, velocity e duração. [Documentação oficial do MIDI Transform](https://support.apple.com/guide/logicpro/midi-transform-window-presets-lgcp215831be/mac).

### Regras para a skill

- selecionar apenas notas musicais;
- excluir keyswitches;
- proteger âncoras;
- aplicar quantidade baixa;
- registrar seed quando houver aleatoriedade;
- revisar eventos de slide e ligados após aplicação;
- nunca aceitar Humanize como etapa final sem validação.

### Melhor abordagem

Aplicar alterações determinísticas por função e usar variação aleatória apenas como acabamento.

---

# Parte IX — Relação com bateria e guitarra

## 45. Kick

- ataques principais do baixo devem acompanhar kicks estruturais;
- nem todo kick exige nova nota;
- ghost kicks podem receber nota abafada ou nenhuma resposta;
- sustentar baixo através de kicks repetidos pode criar contraste;
- em breakdown, escolher se o baixo acompanha o ataque da guitarra ou sustenta fundação.

## 46. Caixa

- ghost notes podem antecipar ou responder à caixa;
- evitar baixo excessivamente movimentado em toda caixa se a voz precisa de espaço;
- uma nota longa atravessando a caixa pode aumentar peso.

## 47. Guitarra

- dobrar pitch não significa copiar exatamente duração e ataque;
- baixo pode sustentar enquanto guitarra faz staccato;
- baixo pode articular somente ataques estruturais;
- slides podem conectar blocos de guitarra;
- em Drop A, controlar sub e fundamental para não transformar cada chug em massa indistinta;
- se guitarra usa ligado, decidir se o baixo liga junto ou cria ataque complementar.

## 48. Classificação de sincronismo

Para cada nota:

```yaml
sync_role:
  - exact_anchor
  - kick_support
  - guitar_unison
  - anticipation
  - response
  - sustain_through
  - ghost_fill
```

O papel define microtiming, velocity e duração.

---

# Parte X — Integração com Parallax

## 49. Cadeia recomendada

```text
MODO BASS 2 limpo → Parallax → correções adicionais → bus de baixo
```

Dentro do MODO:

- desligar amp/cab/distorção internos quando o Parallax for o processador principal;
- enviar DI mono e sem clipping;
- ajustar performance antes do tone shaping;
- preservar headroom;
- não usar compressor do MODO para esconder velocities inadequadas.

O Parallax utiliza processamento paralelo/multibanda: low end comprimido e preservado, enquanto médios e agudos recebem distorção separada; o cabinet afeta médios/agudos sem comprometer o grave. [Fonte oficial: Parallax X](https://neuraldsp.com/plugins/parallax).

## 50. Validação em duas etapas

### MODO limpo

Verificar:

- diferença de ataques;
- gaps;
- hammer-ons/pull-offs;
- slides;
- mudanças de corda;
- ghost notes;
- ruídos mecânicos;
- dinâmica da frase.

### Com Parallax

Verificar:

- se ghost notes ficaram altas demais;
- se slide noise virou chiado excessivo;
- se todos os ataques ficaram com o mesmo volume;
- se clank encobriu diferenças de stroke;
- se sub desaparece em notas muito curtas;
- se compressão destruiu acentos;
- se o input está distorcendo além do necessário.

## 51. Regras de ganho

- não aumentar velocity apenas para alimentar mais distorção;
- ajustar input do Parallax separadamente;
- usar velocity para performance;
- usar gain/drive para timbre;
- comparar com bypass em loudness semelhante;
- não confundir “mais alto” com “mais real”.

---

# Parte XI — Algoritmo da skill

## 52. Entradas obrigatórias

```yaml
input:
  midi_file: required
  tempo_map: required_or_inferred
  time_signature: required_or_inferred
  bass_tuning: required
  string_count: required
  maximum_fret: default_24
  play_style: finger_pick_slap_or_auto
  genre: optional
  intensity: 0_to_100
  tightness: 0_to_100
  humanization: 0_to_100
  reference_drums_midi: optional
  reference_guitar_midi: optional
  song_sections: optional
  modo_mapping_profile: required_or_default
  random_seed: required_for_reproducibility
```

Se afinação ou número de cordas estiverem ausentes, a skill deve perguntar. Não inferir Drop A apenas pela nota mais baixa sem avisar.

## 53. Fases do algoritmo

### Fase 1 — Sanitização

- carregar tempo e métrica;
- separar notas, CC, pitch bend e keyswitches;
- remover eventos inválidos;
- identificar sobreposições;
- preservar cópia original;
- detectar extensão e subdivisão predominante.

### Fase 2 — Análise musical

- detectar frases por pausas, compassos e repetição;
- identificar acentos métricos;
- alinhar com kick/guitarra quando fornecidos;
- classificar notas como âncora, intermediária, pickup, ghost candidata ou sustain;
- detectar crescendos, direção melódica e mudanças de seção.

### Fase 3 — Técnica

- usar estilo explicitamente fornecido;
- em modo Auto, escolher estilo por seção, não nota por nota;
- permitir mudanças de Finger para Pick ou Slap apenas em fronteiras musicais claras;
- inserir keyswitch antes da primeira nota da seção.

### Fase 4 — Corda e posição

- gerar todas as posições possíveis;
- minimizar custo de movimento;
- manter frases na mesma região;
- preferir mesma corda para ligados/slides;
- marcar mudanças grandes de posição;
- converter mudanças expressivas em slides quando apropriado;
- emitir Force String somente quando necessário.

### Fase 5 — Articulação

Classificar cada transição:

```text
REATTACK
HAMMER_ON
PULL_OFF
LEGATO_SLIDE
PITCH_BEND_SLIDE
GHOST
HARMONIC
LET_RING
STOP
```

### Fase 6 — Duração

- encurtar reataques;
- criar gaps coerentes;
- sobrepor ligados e slides;
- manter sustain quando musical;
- considerar muting e subdivisão.

### Fase 7 — Velocity

- determinar base pelo estilo;
- adicionar acento métrico;
- adicionar relação com kick;
- aplicar curva da frase;
- ajustar pela articulação;
- ajustar por dedo/stroke;
- adicionar variação final pequena;
- limitar entre 1 e 127;
- reservar 120+ para eventos raros.

### Fase 8 — Microtiming

- proteger âncoras;
- variar notas intermediárias;
- posicionar ghosts de forma musical;
- manter keyswitches antes das notas;
- não mover CC/keyswitch de forma que articulação falhe.

### Fase 9 — Controles contínuos

- CC3 por seção;
- CC4 por mudança de posição;
- CC9 por nível de muting;
- CC1 em sustains selecionados;
- CC65 em slides legato;
- CC64 em ressonâncias intencionais;
- pitch bend centralizado após uso.

### Fase 10 — Validação

- verificar extensão;
- verificar casa máxima;
- verificar slide na mesma corda;
- verificar gaps e overlaps;
- verificar keyswitch antecipado;
- verificar reset de CC/Pitch Bend;
- verificar monofonia;
- verificar ausência de randomização excessiva;
- renderizar/escutar MODO limpo quando possível;
- renderizar/escutar com Parallax quando possível.

---

## 54. Pseudocódigo

```python
def humanize_bass(midi, config, drums=None, guitars=None):
    performance = sanitize(midi)
    phrases = detect_phrases(performance, config.tempo_map)

    for section in performance.sections:
        style = choose_style(section, config.play_style, config.genre)
        insert_style_switch(section.start, style)

        for phrase in section.phrases:
            roles = classify_rhythmic_roles(phrase, drums, guitars)
            positions = assign_strings_and_frets(
                phrase.notes,
                tuning=config.bass_tuning,
                max_fret=config.maximum_fret,
                movement_model="minimum_coherent_motion",
            )

            transitions = classify_transitions(
                phrase.notes,
                positions,
                style,
                roles,
            )

            apply_articulations(transitions, config.modo_mapping_profile)
            apply_note_lengths(phrase, transitions, style, config.tightness)
            apply_velocity_contour(phrase, roles, transitions, style, config.intensity)
            apply_stroke_or_finger_cycle(phrase, style)
            apply_selective_microtiming(phrase, roles, config.humanization)
            write_position_and_expression_cc(phrase, positions, transitions, style)

    validate_physical_playability(performance, config)
    validate_modo_events(performance, config.modo_mapping_profile)
    return performance, build_change_report(performance)
```

---

## 55. Contrato de saída

A skill deve produzir:

```yaml
output:
  transformed_midi: path
  mapping_profile: path_or_embedded
  report:
    detected_style:
    tuning:
    string_assignments:
    position_changes:
    articulations_added:
    velocity_statistics_before_after:
    timing_statistics_before_after:
    note_length_statistics_before_after:
    warnings:
    assumptions:
  reproducibility:
    seed:
    version:
    parameters:
```

### Relatório por evento alterado

```yaml
- note_id: 154
  pitch_midi: 33
  original:
    start_tick: 1920
    length_ticks: 480
    velocity: 100
  transformed:
    start_tick: 1924
    length_ticks: 405
    velocity: 108
    string: A
    fret: 0
    stroke: down
    articulation: reattack
  reason:
    - section_anchor
    - kick_alignment
    - intentional_open_string
```

---

# Parte XII — Perfis prontos

## 56. Finger orgânico

```yaml
style: finger
stroke: alternate
velocity:
  normal: [78, 100]
  accent: [100, 115]
  connected: [52, 76]
  ghost: [22, 46]
gate:
  normal: [0.76, 0.93]
  muted: [0.48, 0.70]
timing_ms:
  anchors: [-2, 3]
  intermediate: [-6, 9]
features:
  hammer_pull_probability_on_valid_transition: 0.35
  slide_probability_on_position_change: 0.18
  ghost_density_per_bar: [0, 2]
```

## 57. Palheta metal moderno

```yaml
style: pick
stroke: alternate
velocity:
  down: [88, 108]
  up: [78, 98]
  accent_down: [105, 120]
  connected: [58, 78]
  ghost: [25, 48]
gate:
  normal: [0.60, 0.82]
  open: [0.78, 0.94]
  breakdown: [0.50, 0.78]
timing_ms:
  anchors: [-1, 2]
  intermediate: [-4, 6]
features:
  restart_with_down_after_pause: true
  preserve_gallop_stroke_cycle: true
  slide_probability_on_section_entry: 0.15
```

## 58. Slap controlado

```yaml
style: slap
stroke: explicit
velocity:
  slap: [88, 112]
  slap_accent: [106, 121]
  pull: [100, 118]
  pull_accent: [114, 125]
  ghost: [20, 46]
  connected: [48, 74]
gate:
  slap: [0.38, 0.68]
  pull: [0.42, 0.76]
  ghost: [0.15, 0.32]
timing_ms:
  anchors: [-2, 3]
  ghost: [-8, 10]
features:
  ghost_density_per_bar: [1, 4]
  auto_pull_threshold: 108
```

Probabilidades são limites configuráveis e nunca substituem análise musical.

---

# Parte XIII — Checklist e testes

## 59. Checklist físico

- [ ] Todas as notas estão na extensão do baixo?
- [ ] Cada nota possui corda/casa possível?
- [ ] As mudanças de posição são plausíveis?
- [ ] Slides e ligados permanecem na mesma corda?
- [ ] Acordes são intencionais e executáveis?
- [ ] Cordas soltas são usadas conscientemente?
- [ ] Não há teletransporte entre casas sem tempo ou articulação?

## 60. Checklist de articulação

- [ ] Reataques possuem pequenos gaps?
- [ ] Ligados possuem pequenas sobreposições?
- [ ] CC65 está ativo antes do Legato Slide?
- [ ] Pitch Bend retorna ao centro?
- [ ] Ghost notes são curtas e discretas?
- [ ] Let Ring não cria conflito?
- [ ] Keyswitches estão antes das notas?
- [ ] Keyswitches não foram humanizados?

## 61. Checklist de estilo

### Finger

- [ ] Dedos alternam coerentemente?
- [ ] Hammer-ons/pull-offs aparecem onde naturais?
- [ ] Pluck e Touch combinam com a seção?

### Pick

- [ ] Down/up seguem ciclo físico?
- [ ] Acentos favorecem downstroke?
- [ ] Gallops têm durações e ataques diferentes?

### Slap

- [ ] Slap, pull e ghost têm papéis diferentes?
- [ ] Threshold não transforma notas normais em Pull?
- [ ] O groove preserva espaço percussivo?

## 62. Checklist de musicalidade

- [ ] Velocities seguem frase e bateria?
- [ ] Âncoras continuam firmes?
- [ ] Microtiming não destruiu o riff?
- [ ] O baixo tem função além de duplicar guitarra?
- [ ] Há contraste entre notas secas e sustentadas?
- [ ] O resultado soa melhor no contexto, não apenas em solo?

## 63. Checklist Parallax

- [ ] O sinal do MODO entra limpo e sem clipping?
- [ ] Ghosts e ruídos não ficaram altos demais?
- [ ] O sub aparece nas notas importantes?
- [ ] A distorção preserva diferenças de articulação?
- [ ] Velocity está sendo usada para performance, não para controlar drive?
- [ ] O bypass foi comparado em loudness semelhante?

---

## 64. Testes automatizados recomendados

```yaml
tests:
  - name: notes_within_instrument_range
  - name: fret_between_zero_and_maximum
  - name: no_unintended_polyphony
  - name: slides_use_valid_string_assignment
  - name: legato_slide_has_cc65_before_overlap
  - name: pitch_bend_returns_to_center
  - name: keyswitch_precedes_target_note
  - name: keyswitch_not_randomized
  - name: ghost_velocity_below_normal_range
  - name: anchors_not_displaced_beyond_limit
  - name: velocity_between_one_and_127
  - name: note_length_positive
  - name: deterministic_output_with_same_seed
  - name: original_midi_preserved
```

## 65. Métricas de diagnóstico

A skill deve calcular antes e depois:

- média, mediana e desvio de velocity;
- velocity por função métrica;
- distribuição de duração/gate;
- quantidade de overlaps;
- quantidade de gaps;
- deslocamento médio de timing;
- deslocamento máximo de âncoras;
- densidade de ghost notes;
- quantidade de slides e ligados por compasso;
- mudanças de corda por frase;
- distância média de movimento em casas;
- frequência de posições extremas;
- percentual de notas acima de velocity 120.

Valores extremos devem gerar aviso, não correção silenciosa.

---

# Parte XIV — Instrução pronta para a skill

## 66. Bloco operacional

```text
Ao humanizar baixo MIDI para MODO BASS 2, não comece por randomização.

Primeiro determine afinação, número de cordas, técnica e extensão. Converta todas as alturas em número MIDI absoluto. Para cada nota, calcule cordas e casas possíveis e escolha uma trajetória coerente de mão esquerda. Mantenha frases em uma região, favoreça mesma corda para slides e ligados e introduza mudança de posição somente quando houver tempo, pausa ou gesto expressivo.

Classifique cada transição como reataque, hammer-on, pull-off, legato slide, pitch-bend slide, ghost, harmonic, let-ring ou stop. Reataques devem receber pequenos gaps; hammer-ons, pull-offs e legato slides devem receber pequenas sobreposições. A velocity da nota de destino influencia a velocidade do Legato Slide no MODO BASS.

Construa velocity em camadas: base do estilo + acento métrico + relação com kick + curva da frase + articulação + dedo/stroke + variação final pequena. Nunca use valores aleatórios uniformes. Preserve downbeats e ataques estruturais próximos do grid. Aplique microtiming maior somente a notas intermediárias, pickups e ghosts.

Para Finger, mantenha alternância Index/Middle, use ligados em intervalos pequenos e reserve ataques fortes para acentos. Para Pick, mantenha ciclo Down/Up, favoreça Down nos acentos e respeite gallops e pausas. Para Slap, diferencie Slap, Pull e Ghost por velocity, duração e posição rítmica; não dependa apenas de threshold automático quando for necessário resultado determinístico.

Escreva keyswitches e CCs conforme o mapping profile atual do MODO. Nunca confie apenas no nome da oitava. Não humanize keyswitches. Use CC3 para Pluck Position, CC4 para Left Hand Position, CC9 para Muting, CC1 para Vibrato, CC64 para Let Ring, CC65 para Legato Slide e Pitch Bend para slides livres. Sempre restabeleça CCs e Pitch Bend ao estado necessário.

Valide primeiro no MODO BASS limpo. Depois valide com Parallax, verificando se distorção e compressão amplificaram ghost notes, slide noise, detach noise ou eliminaram a dinâmica. Velocity deve controlar performance; input e drive do Parallax devem controlar saturação.

Preserve o MIDI original, use seed reprodutível, gere relatório das mudanças e sinalize suposições ou impossibilidades físicas.
```

---

# Parte XV — Vídeos, documentação e estudo

## 67. Vídeos recomendados

1. [MODO BASS Playing Setup — IK Multimedia](https://www.youtube.com/watch?v=RGB1zqGXnsE) — slide, vibrato e posição da mão.
2. [How to Make MIDI Bass Sound 100% Real with MODO Bass](https://www.youtube.com/watch?v=6EkpJmS4z9Y) — erros comuns de programação.
3. [FREE MODO BASS 2 Tutorial / Key Switches](https://www.youtube.com/watch?v=GTpaQEF92LQ) — keyswitches e áreas do teclado.
4. [Logic Pro Articulation Set for MODO BASS](https://www.youtube.com/watch?v=1CgpvHPR2Q8) — organização de articulações no Logic.
5. [MODO BASS 2 Can’t Slide? Here’s Why](https://www.youtube.com/watch?v=5OWUBWIsfv4) — limitações físicas e slides.
6. [MODO BASS 2 Interface](https://www.youtube.com/watch?v=Md-i9SQ1NOk) — interface e seções.
7. [Master MODO BASS — Manual Read and Tutorial](https://www.youtube.com/watch?v=wliGV9oi8NY) — estudo extenso do manual.
8. [How to Dial in Parallax X — Neural DSP](https://www.youtube.com/watch?v=78bBjNL3ZQg) — processamento do baixo.

## 68. Fontes principais

- [IK Multimedia — MODO BASS 2](https://www.ikmultimedia.com/products/modobass2/)
- [Craig Anderton — How to Get the Most Out of MODO Bass](https://craiganderton.org/how-to-get-the-most-out-of-modo-bass/)
- [MODO BASS 2 Control and Play Style reference](https://www.ongen-opt.com/entry/2023/10/09/220000)
- [Apple — Automation/MIDI no Piano Roll](https://support.apple.com/guide/logicpro/automationmidi-area-in-the-piano-roll-editor-lgcpa90a61bf/mac)
- [Apple — Articulation Set Editor](https://support.apple.com/guide/logicpro/manage-articulations-articulation-set-editor-lgcp33a49091/mac)
- [Apple — MIDI Transform/Humanize](https://support.apple.com/guide/logicpro/midi-transform-window-presets-lgcp215831be/mac)
- [Neural DSP — Parallax X](https://neuraldsp.com/plugins/parallax)

## 69. Limitações

- O mapping do MODO pode mudar conforme versão, preset e personalização.
- A numeração de oitavas varia entre programas; usar números MIDI absolutos.
- Faixas de velocity, duração e timing são pontos de partida.
- O resultado final depende do modelo de baixo, strings, ação, Touch, curva de velocity, música e processamento.
- Uma skill sem acesso a áudio pode validar estrutura MIDI, mas não confirmar completamente o resultado sonoro.
- Quando possível, renderizar uma passagem com MODO limpo e com Parallax para comparação.

---

## 70. Regra final

> Uma boa humanização não chama atenção para a aleatoriedade. Ela faz cada nota parecer consequência natural do gesto anterior.

A skill deve preservar intenção, groove e repetibilidade. Menos eventos bem justificados produzem mais realismo que dezenas de articulações aplicadas sem contexto.
