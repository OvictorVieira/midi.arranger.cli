# Técnicas de baixo em MIDI — manual de execução

> **Para que serve.** A IA decide *o que* a música precisa. Este arquivo diz *como fazer aquilo em
> MIDI*: que keyswitch, que CC, que velocity, que offset. Toda receita é acionável — tanto para gerar
> uma linha do zero quanto para **acrescentar técnica a um baixo que já existe e está chapado**.
>
> **Ferramenta-alvo:** IK Multimedia **MODO BASS 1** — é a versão que o usuário tem. Todo item traz
> o fallback genérico. O que é exclusivo da versão 2 está fora deste manual.
>
> **Regra de fonte.** Número com fonte vem citado. Número sem fonte vem marcado `[NÃO VERIFICADO]` —
> use, mas confira de ouvido antes de tratar como lei. Nunca apresente um `[NÃO VERIFICADO]` ao
> usuário como fato.

---

## 0. O que é oficial e o que não é

O manual da versão 1 é a fonte de verdade aqui. Dele vêm, com texto citável: a faixa de keyswitch, a
zona de parada e posição de mão, o CC de vibrato, o CC de let ring, e — o mais importante — **a faixa
de bend fixa**.

O que **não** está no manual e veio de duas fontes independentes que concordam entre si: os números
de CC de slide, muting, pluck position e chord mode, e o mapa de keyswitch de estilo e articulação.
Estão marcados como não verificados nos blocos da §5.

**Se você abrir o capítulo CONTROL do seu manual e me passar o que ele diz sobre esses CCs, eles
sobem para verificado.** É a única lacuna que sobrou que depende de você.

### Diferença que importa: BEND e SLIDE são coisas separadas

O manual da v1 é explícito: **a faixa de BEND é fixa em ±1 semitom.** Isso não é o mesmo que slide.

| | Controle | Faixa |
|---|---|---|
| **BEND** | CC 5 | **±1 semitom, fixo** — não é ajustável |
| **SLIDE** | Pitch Wheel | escalada pelo controle **SLIDE RANGE** na interface |

Consequência prática: **não escreva slide como CC 5.** Bend de um semitom é ornamento de expressão,
não deslocamento de posição. Slide de intervalo maior sai pelo pitch wheel, e o quanto ele percorre
depende de onde o SLIDE RANGE está — que é estado da interface, não do MIDI. Se o plano depende de um
intervalo específico de slide, ele precisa declarar o SLIDE RANGE esperado, senão o resultado varia
com o preset carregado.

### Convenção de oitava

MODO BASS exibe nomes de nota com **dó central = C4, MIDI 0 = C-1**. Isso importa porque a maioria
dos DAWs exibe C3 = 60.

| DAW | Deslocamento vs o rótulo do MODO | "C0" do MODO aparece como |
|---|---|---|
| Logic, Cubase, Ableton, Studio One, Reaper | uma oitava abaixo | C-1 |
| MODO BASS (UI) | — | C0 |
| Cakewalk / BandLab | uma oitava acima | C1 |

**Programe sempre por número MIDI, nunca por nome de nota.** A derivação que confirma a convenção:
os keyswitches de força-corda vão de `C-1` a `G#0`; sob C3=60 isso seria MIDI 12–32, que colide com
o mi grave do baixo de 4 cordas (MIDI 28). Sob C4=60 são MIDI 0–20, limpos abaixo da região tocável.
Só a segunda leitura funciona.

---

## 1. Mapa de keyswitch

Todos remapeáveis na página CONTROL / MIDI Control.

| Função | Nome no MODO | **MIDI** | Latch |
|---|---|---|---|
| Forçar corda C | C-1 | **0** | latchável |
| Index stroke (dedo) / Down stroke (palheta) / **Forçar slap** | C#-1 | **1** | latchável |
| Middle stroke (dedo) / Up stroke (palheta) / **Forçar pop** | D#-1 | **3** | latchável |
| Forçar corda A | A-1 | **9** | latchável |
| **GHOST MODE** (nota morta) | A#-1 | **10** | **momentâneo** |
| Forçar corda B | B-1 | **11** | latchável |
| **HAMMER-ON / PULL-OFF** | C0 | **12** | **momentâneo** |
| Estilo: **dedo / pizzicato** | C#0 | **13** | latching |
| Forçar corda D | D0 | **14** | latchável |
| Estilo: **palheta** | D#0 | **15** | latching |
| Forçar corda E | E0 | **16** | latchável |
| Harmônico | F0 | **17** | momentâneo |
| Estilo: **slap** | F#0 | **18** | latching |
| Forçar corda G | G0 | **19** | latchável |
| Stop on detach | G#0 | **20** | latchável |
| Parar todas as cordas | E5 | **76** | — |
| Posição da mão esquerda | F5 … E7 | **77 … 100** | uma tecla por posição |

Faixa legal de keyswitch: **MIDI 0–100** (`C-1` a `E7`) — manual oficial v1.

**Momentâneo significa segurar.** `GHOST MODE` e `HAMMER-ON/PULL-OFF` valem enquanto a tecla está
pressionada — não são toggles. Escrever como nota curta antes do trecho não funciona.

**Na versão 1 o keyswitch precisa ficar fora da região tocável.** Colocar keyswitch dentro da faixa
de notas do baixo faz a nota soar junto — a prioridade de keyswitch sobre nota é comportamento da
versão 2 e **não vale aqui**. Como a região tocável começa no MIDI 28 (mi grave do baixo de 4
cordas), os keyswitches de 0 a 20 estão seguros; os de 76 a 100 ficam acima do braço.

---

## 2. Mapa de CC

| Função | CC padrão | Fonte |
|---|---|---|
| **VIBRATO** | **CC 1** | **manual oficial v1** — "By default, the vibrato is controlled by the mod wheel, MIDI CC #1" |
| **LET RING** | **CC 64** | **manual oficial v1** — "By default the assigned CC # is 64 (sustain pedal)" |
| **BEND** | **CC 5**, faixa **±1 semitom fixa** | faixa: manual oficial v1. Número do CC: terceiros |
| **SLIDE** | **Pitch Wheel**, escalado por SLIDE RANGE | terceiros |
| **MUTING** (profundidade) | **CC 9** | terceiros — contínuo, desenhe curva, não valor único |
| **Posição da mão esquerda** | **CC 4** | conceito no manual oficial ("can be done also with a MIDI continuous controller"); o número é de terceiros |
| **PLUCK POSITION** | **CC 3** | terceiros |
| **CHORD MODE** | **CC 2** | terceiros |
| **LEGATO SLIDE** | **CC 65** | **fonte única** — o mais frágil da tabela |
| Seleção de corda | — | só por keyswitch |

Todos remapeáveis na página CONTROL. Os CCs marcados como "terceiros" são os que o seu manual pode
confirmar.

**Articulação por CC é gated por valor, não por evento.** Mande um valor de início diferente de zero
e um `0` no fim para soltar. Esquecer o retorno a zero deixa a articulação presa.

---

## 3. Como acrescentar técnica a um baixo que já existe

Caminho principal quando o usuário chega com uma linha chapada e pede intenção.

### 3.1 Ordem de aplicação

1. **Contorno de velocity** — antes de qualquer ornamento, estabeleça o que é forte e o que é fraco.
2. **Ghost notes** — só depois que existe contorno, senão a ghost não contrasta com nada.
3. **Legato** — hammer-on e pull-off nas ligações naturais da frase.
4. **Slides** — nas transições de posição.
5. **Microtiming** — por último, porque depende de saber quem é estrutural e quem é ornamento.

### 3.2 A regra que vale mais que os números

Manter o **contorno** musical — tempos fortes e picos de frase mais altos — importa mais do que a
faixa exata de velocity. Uma linha com padrão de acento coerente lê como mais humana que uma com
velocity sorteada. Aleatoriedade sem intenção não é humanização.

### 3.3 Onde o baixo senta em relação ao tempo

O único número com fonte forte aqui: desvio padrão de timing medido em performance real, normalizado
por andamento. Num trecho de funk, `sΔt = 0,026 batidas` — cerca de **16 ms a 100 BPM**. Num trecho
de swing, `sΔt = 0,068 batidas` — cerca de **41 ms a 100 BPM**.

Grade prática por gênero — **extrapolação, não medição publicada**:

| Gênero | Offset vs grade | |
|---|---|---|
| Metal e rock moderno, apertado | −5 a 0 ms | travado no kick |
| Rock clássico | 0 a +10 ms | levemente atrás |
| Funk empurrado | −10 a −30 ms | o valor de ≈30 ms adiantado é o único com fonte de gênero |
| Funk e R&B laid back | +10 a +30 ms | |
| Neo-soul | +20 a +80 ms | |

**O offset é por seção, não por nota.** Fixe o viés do gênero e sorteie ±3–5 ms em cima dele.
Randomizar nota a nota destrói o feel em vez de criar.

---

## 4. Slide sem keyswitch — o fallback genérico

Quando não houver MODO BASS, slide vira pitch bend.

- Ponha a faixa de pitch bend do instrumento em **±12 semitons**, para qualquer intervalo caber.
- Com ±12 e bend de 14 bits (centro 8192): **uma oitava = 8191**, **um semitom ≈ 683**, **dois
  semitons ≈ 1366**. Fórmula: `offset = 8191 × semitons / faixa_em_semitons`.
- Descida de 3 semitons se escreve como **+2048 → 0**: a nota começa pré-dobrada e solta até o centro.
- **Sempre volte ao centro antes da próxima nota não deslizada.**

Forma e duração da curva são convenção, não medição: slide curto de 1 a 3 semitons em 40–80 ms,
linear ou com leve desaceleração; slide longo de 5 a 12 semitons em 100–250 ms, acelerando e depois
desacelerando, porque a mão real começa devagar, ganha velocidade e chega. Um evento de bend a cada
5–10 ms; menos de dez pontos no slide inteiro degrau audível.

---

## 5. Blocos de técnica — o formato que alimenta o índice

Mesmo formato do manual de bateria: bloco `technique` com JSON. Campos obrigatórios `name`,
`family`, `summary`, `verified`. Número sem `source` derruba `verified` para `false`.

### 5.1 Contorno de velocity

```technique
{
  "name": "velocity_contour",
  "family": "bass",
  "summary": "Estabelece o contorno dinamico da linha antes de qualquer ornamento.",
  "verified": false,
  "description": "Primeiro passo. Tempos fortes e picos de frase mais altos; passagens e aproximacoes mais baixas. O contorno coerente importa mais que a faixa exata — linha com acento organizado le como humana, linha com velocity sorteada nao. Ghost notes formam um cluster separado e NAO entram nesta distribuicao.",
  "parameters": [
    {"name": "span_tipico", "value": 40, "source": "MIDI Association — exemplo com nota mais fraca 70 e mais forte 110"},
    {"name": "fingered_mediana", "value": 90, "range": [70, 110]},
    {"name": "picked_mediana", "value": 100, "range": [80, 120]},
    {"name": "slap_mediana", "value": 112, "range": [95, 127]},
    {"name": "pop_mediana", "value": 115, "range": [100, 127]},
    {"name": "picked_down_vs_up_delta", "range": [6, 10]}
  ],
  "tools": {
    "generic": {"note": "aplica nas velocities existentes; nao gera nota nova"},
    "modo_bass": {"note": "o estilo ativo muda a resposta; troque o estilo antes de calibrar o contorno"}
  }
}
```

### 5.2 Ghost notes

```technique
{
  "name": "ghost_notes",
  "family": "bass",
  "summary": "Notas mortas entre as estruturais, no MODO BASS via keyswitch momentaneo segurado.",
  "verified": false,
  "description": "Insira ENTRE notas estruturais, nunca no lugar delas. Toque na corda em que a mao ja esta — nao invente altura arbitraria. Posicione nas subdivisoes fracas (o 'e' e o 'a' da semicolcheia). No MODO BASS o gatilho e binario por keyswitch momentaneo: nao ha limiar de velocity documentado.",
  "parameters": [
    {"name": "velocity", "range": [25, 50]},
    {"name": "velocity_relativa_pct", "range": [20, 40], "source": null},
    {"name": "gate_pct", "range": [10, 25]}
  ],
  "tools": {
    "generic": {"note": "nota curta de velocity baixa na mesma corda; sem keyswitch"},
    "modo_bass": {"keyswitch": 10, "keyswitch_name": "A#-1", "mode": "momentaneo", "note": "SEGURE a tecla durante o trecho; nao e toggle"}
  }
}
```

### 5.3 Hammer-on e pull-off

```technique
{
  "name": "hammer_pull",
  "family": "bass",
  "summary": "Ligado ascendente e descendente; no MODO BASS exige keyswitch segurado MAIS sobreposicao de notas.",
  "verified": false,
  "description": "No MODO BASS o keyswitch habilita a tecnica e a sobreposicao das duas notas a produz — os dois sao necessarios. A nota ligada sai mais fraca que a atacada. NAO ha limiar de sobreposicao documentado em milissegundos; o requisito publicado e apenas 'as notas precisam se sobrepor'.",
  "parameters": [
    {"name": "velocity_relativa", "range": [-30, -15]},
    {"name": "overlap_ms", "range": [10, 40]}
  ],
  "tools": {
    "generic": {"note": "sobreposicao legato de 10 a 40 ms; sem keyswitch"},
    "modo_bass": {"keyswitch": 12, "keyswitch_name": "C0", "mode": "momentaneo", "note": "SEGURE durante a passagem legato e sobreponha as notas"}
  }
}
```

### 5.4 Slide

```technique
{
  "name": "slide",
  "family": "bass",
  "summary": "Deslizamento entre notas; no MODO BASS via pitch wheel, no fallback via curva de pitch bend.",
  "verified": false,
  "description": "ATENCAO na v1: BEND e SLIDE sao coisas separadas. BEND (CC 5) tem faixa FIXA de +/-1 semitom pelo manual oficial e serve so para ornamento de expressao — NAO use CC 5 para slide. Slide de intervalo real sai pelo pitch wheel, escalado pelo SLIDE RANGE da interface, que e estado da GUI e nao do MIDI: se o plano depende de um intervalo especifico, ele precisa declarar o SLIDE RANGE esperado. No fallback generico, ponha a faixa de bend em 12 semitons e escreva a curva; volte SEMPRE ao centro antes da proxima nota nao deslizada.",
  "parameters": [
    {"name": "bend_range_semitons_fixo", "value": 1, "source": "manual oficial MODO BASS v1 — 'BEND range is fixed to +/- 1 semitone'"},
    {"name": "bend_por_semitom_com_faixa_12", "value": 683, "source": "MIDI Association — 8191/12, aritmetica"},
    {"name": "bend_por_oitava_com_faixa_12", "value": 8191, "source": "MIDI Association"},
    {"name": "duracao_curto_ms", "range": [40, 80]},
    {"name": "duracao_longo_ms", "range": [100, 250]},
    {"name": "resolucao_ms_por_evento", "range": [5, 10]}
  ],
  "tools": {
    "generic": {"cc": "pitch_bend", "note": "faixa 12 semitons; curva S no slide longo; retorno obrigatorio ao centro"},
    "modo_bass": {"cc": "pitch_wheel", "note": "escalado por SLIDE RANGE (estado da GUI). CC 5 e BEND com faixa fixa de +/-1 semitom, NAO serve para slide. LEGATO SLIDE em CC 65 tem fonte unica"}
  }
}
```

### 5.5 Palm mute

```technique
{
  "name": "palm_mute",
  "family": "bass",
  "summary": "Abafamento continuo por CC no MODO BASS, nao articulacao discreta.",
  "verified": false,
  "description": "No MODO BASS mute NAO e um estilo separado: e uma quantidade continua em CC 9 aplicada por cima do estilo ativo. Desenhe uma curva, nao um valor unico. Articulacao por CC e gated por valor — sem o retorno a zero, o mute fica preso.",
  "parameters": [
    {"name": "velocity", "range": [60, 100]},
    {"name": "gate_pct", "range": [25, 50]}
  ],
  "tools": {
    "generic": {"note": "gate curto e velocity media; sem CC dedicado"},
    "modo_bass": {"cc": 9, "note": "curva desenhada; retorno a 0 obrigatorio para soltar"}
  }
}
```

### 5.6 Vibrato

```technique
{
  "name": "vibrato",
  "family": "bass",
  "summary": "Modulacao de altura por CC 1, comecando DEPOIS do ataque.",
  "verified": true,
  "description": "Nunca comece o vibrato em t=0 — mao real ataca a nota e so depois vibra. Rampa de subida, sustentacao, rampa de descida antes do fim da nota.",
  "parameters": [
    {"name": "cc", "value": 1, "source": "manual oficial MODO BASS v1"},
    {"name": "atraso_de_inicio_ms", "range": [150, 300]},
    {"name": "profundidade_cc", "range": [60, 90]}
  ],
  "tools": {
    "generic": {"cc": 1},
    "modo_bass": {"cc": 1, "source": "manual oficial v1"}
  }
}
```

### 5.7 Estilo de ataque

```technique
{
  "name": "attack_style",
  "family": "bass",
  "summary": "Dedo, palheta ou slap — no MODO BASS sao keyswitches latching que persistem.",
  "verified": false,
  "description": "O estilo permanece ate outro keyswitch de estilo. Em slap, o plugin escolhe entre thumb e pop pelo registro e pela corda; para forcar, use os keyswitches 1 (slap) e 3 (pop) — as mesmas duas teclas carregam index/middle no estilo dedo e down/up no estilo palheta.",
  "parameters": [
    {"name": "picked_downstroke_velocity", "range": [85, 120]},
    {"name": "picked_upstroke_velocity", "range": [70, 100]},
    {"name": "slap_velocity", "range": [95, 127]},
    {"name": "pop_velocity", "range": [100, 127]},
    {"name": "upstroke_atraso_ms", "range": [0, 8]}
  ],
  "tools": {
    "generic": {"note": "sem keyswitch: diferencie so por velocity e timing"},
    "modo_bass": {
      "keyswitch_dedo": 13, "keyswitch_palheta": 15, "keyswitch_slap": 18,
      "keyswitch_forcar_primeiro": 1, "keyswitch_forcar_segundo": 3,
      "note": "estilo e latching; forcar stroke e latchavel"
    }
  }
}
```

### 5.8 Let ring

```technique
{
  "name": "let_ring",
  "family": "bass",
  "summary": "Sustentacao por CC 64; as notas podem ser curtas que o CC segura.",
  "verified": true,
  "parameters": [
    {"name": "cc", "value": 64, "source": "manual oficial MODO BASS v1"}
  ],
  "tools": {
    "generic": {"cc": 64},
    "modo_bass": {"cc": 64, "source": "manual oficial v1"}
  }
}
```

### 5.9 Harmônico

```technique
{
  "name": "harmonic",
  "family": "bass",
  "summary": "Harmonico natural por keyswitch momentaneo.",
  "verified": false,
  "parameters": [
    {"name": "velocity", "range": [60, 100]}
  ],
  "tools": {
    "generic": {"note": "sem keyswitch, o fallback e tocar a altura do harmonico direto"},
    "modo_bass": {"keyswitch": 17, "keyswitch_name": "F0", "mode": "momentaneo"}
  }
}
```

---

## 6. Plausibilidade física

- **Baixo é um instrumento só e a mão é uma.** Duas notas simultâneas só existem em corda dupla
  deliberada; acorde de baixo em registro grave vira lama e não é vocabulário do gênero.
- **Nota morta soa na corda em que a mão já está.** Ghost em altura arbitrária denuncia programação.
- **Slide precisa de origem e destino na mesma corda.** Slide de intervalo grande atravessando cordas
  não é o que a mão faz — é troca de posição, que soa diferente.
- **Vibrato depois do ataque, nunca junto.**

---

## 7. Lacunas declaradas

| Item | Situação |
|---|---|
| Números de CC de slide, muting, pluck position, chord mode, bend e posição de mão | vêm de duas fontes independentes que concordam; **o capítulo CONTROL do seu manual confirma ou corrige** |
| CC 65 para LEGATO SLIDE | **fonte única** — o item mais frágil do manual |
| Mapa de keyswitch de estilo e articulação | duas fontes concordantes, não oficial |
| Limiar de sobreposição em ms para hammer-on/pull-off | não documentado em lugar nenhum |
| Limiar de velocity para ghost note | sem evidência de que exista; assumido só por keyswitch |
| Se "Mute" existe como estilo discreto além do CC 9 | não confirmado na v1 |
| Todos os valores de velocity, gate e timing da §5 | convenção de ofício, sem medição publicada |
| Offsets de gênero da §3.3 | extrapolados de dois estudos; só o ≈30 ms de funk tem fonte direta |

**Resolvido pela confirmação de que a versão é a 1:** a faixa de bend deixou de ser conflito — é
±1 semitom fixa, com texto oficial. A zona alta de keyswitch (E5 parar tudo, F5–E7 posição de mão)
deixou de ser "vem da v1, não reconfirmado" e passou a ser simplesmente oficial. E a prioridade de
keyswitch dentro da região tocável, que é comportamento da v2, saiu do manual — na v1 keyswitch
dentro da faixa do baixo faz a nota soar junto.

---

## Fontes

- **MODO BASS User Manual v1 — texto oficial** (a versão que o usuário tem)
- [newdtm-rain.com — seção CONTROL do MODO BASS](https://www.newdtm-rain.com/article/modo-bass-control.html)
- [note.com/moonwhite — mapa de articulação do MODO BASS 2](https://note.com/moonwhite/n/nb457c31bf867)
- [Senn, Kilchenmann, von Georgi & Bullerjahn — microtiming e groove em swing e funk, Frontiers in Psychology](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2016.01487/full)
- [MIDI Association — Get Real and Get Funky](https://midi.org/get-real-and-get-funky-how-to-create-realistic-midi-bass-parts)
- [Sweetwater — Create Realistic MIDI Bass Parts](https://www.sweetwater.com/insync/create-realistic-midi-bass-parts/)
