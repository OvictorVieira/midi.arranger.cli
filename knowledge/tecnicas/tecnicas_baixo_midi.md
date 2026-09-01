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

## 0. Fonte: a própria página CONTROL do plugin

**Tudo nas seções 1 e 2 foi lido diretamente da página CONTROL do MODO BASS 1.5.2, com os defaults de
fábrica intactos.** É fonte primária, melhor que qualquer manual — é o estado real do plugin.

Isso encerrou uma contradição entre duas rodadas de pesquisa. Registro o que ficou provado, porque
evita refazer o trabalho:

- O mapa de keyswitch de duas fontes independentes estava **inteiramente correto**: 12 de 12
  conferem, e a convenção de oitava deduzida (dó central = C4, portanto C-1 = MIDI 0) também.
- Uma segunda pesquisa trouxe um mapa **substancialmente errado** — trocava Force A com Force D,
  Force D com Force G, e afirmava que A-1 era Let Ring quando é Force A. Não foi aplicado, e ainda
  bem.
- Sobre **MUTING**, quem estava certo era a segunda: **não existe CC padrão de fábrica.** As fontes
  que diziam CC 9 estavam erradas.

### Diferença que importa: BEND e SLIDE são coisas separadas

Confirmado na tela — são duas linhas distintas, com mecanismos distintos:

| | Tipo | Valor | Faixa |
|---|---|---|---|
| **BEND** | CC | **5** | não exposta na página CONTROL |
| **SLIDE** | **Pitch Wheel** | — | knob **SLIDE RANGE**, default **2** semitons |

Consequência prática: **não escreva slide como CC 5.** Slide sai pelo pitch wheel, e o quanto ele
percorre depende do knob SLIDE RANGE — que é **estado da interface, não do MIDI**. No default de
fábrica ele vale 2 semitons. Se o plano depende de um intervalo maior, ele precisa **declarar o
SLIDE RANGE esperado**, senão o resultado varia com o preset carregado.

Outro knob de topo com default de fábrica: **VIBRATO RATE = 4.0**.

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

Lido da página CONTROL, defaults de fábrica. Coluna MIDI derivada da convenção dó central = C4.

| Função | Nome no MODO | **MIDI** | Latch (default) |
|---|---|---|---|
| Forçar corda C | C-1 | **0** | Off |
| Forçar corda A | A-1 | **9** | Off |
| **GHOST MODE** (nota morta) | A#-1 | **10** | Off |
| Forçar corda B | B-1 | **11** | Off |
| **HAMMER-ON / PULL-OFF** | C0 | **12** | Off |
| Estilo: **dedo** | C#0 | **13** | — |
| Forçar corda D | D0 | **14** | Off |
| Estilo: **palheta** | D#0 | **15** | — |
| Forçar corda E | E0 | **16** | Off |
| **Harmônico** | F0 | **17** | Off |
| Estilo: **slap** | F#0 | **18** | — |
| Forçar corda G | G0 | **19** | Off |

**Sem atribuição de fábrica** — a página lista a função mas o tipo vem como `Off`:

| Função | Situação |
|---|---|
| MUTING | **sem CC padrão** — precisa ser atribuído pelo usuário |
| INDEX STROKE | sem atribuição |
| MIDDLE STROKE | sem atribuição |
| MASTER VOLUME | sem atribuição |

**`Latch` vem desligado em tudo que o expõe.** Com latch desligado o controle é **momentâneo**: vale
enquanto a tecla está pressionada. Os três keyswitches de estilo não expõem latch — o estilo persiste
até outro estilo ser escolhido.

Isso muda como escrever ghost note e hammer-on: **segure a tecla durante o trecho**, não escreva como
nota curta antes dele.

**Na versão 1 o keyswitch precisa ficar fora da região tocável.** Colocar keyswitch dentro da faixa
de notas do baixo faz a nota soar junto — a prioridade de keyswitch sobre nota é comportamento da
versão 2 e **não vale aqui**. Como a região tocável começa no MIDI 28 (mi grave do baixo de 4
cordas), todos os keyswitches de fábrica — que vão de 0 a 19 — estão seguros.

---

## 2. Mapa de CC

Lido da página CONTROL, defaults de fábrica.

| Função | Tipo | Valor | Latch |
|---|---|---|---|
| **VIBRATO** | CC | **1** | — |
| **CHORD MODE** | CC | **2** | Off |
| **PLUCK POSITION** | CC | **3** | — |
| **LEFT HAND POSITION** | CC | **4** | — |
| **BEND** | CC | **5** | — |
| **LET RING** | CC | **64** | Off |
| **LEGATO SLIDE** | CC | **65** | Off |
| **SLIDE** | **Pitch Wheel** | — | — |
| **MUTING** | **Off** | — | — |
| Seleção de corda | — | só por keyswitch | |

**MUTING não tem CC de fábrica.** Isso muda o desenho: um plano que pede palm mute precisa **declarar
qual CC o usuário atribuiu**, ou a técnica não sai. Não dá para assumir número nenhum.

Os CCs de 1 a 5 formam um bloco contíguo e fácil de lembrar: vibrato, acorde, ataque, mão, bend.

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
    {"name": "fingered_mediana", "value": 90, "range": [70, 110], "source": "CONVENCAO — centro da faixa da MIDI Association (70-110) para dedilhado; sem medicao publicada por estilo, calibrada para o motor (ver secao 7)"},
    {"name": "picked_mediana", "value": 100, "range": [80, 120], "source": "CONVENCAO — deslocada acima do dedilhado por ataque mais firme da palheta; sem medicao publicada por estilo, calibrada para o motor (ver secao 7)"},
    {"name": "slap_mediana", "value": 112, "range": [95, 127], "source": "CONVENCAO — proxima do teto por ataque percussivo do slap; sem medicao publicada por estilo, calibrada para o motor (ver secao 7)"},
    {"name": "pop_mediana", "value": 115, "range": [100, 127], "source": "CONVENCAO — acima do slap thumb por ataque mais agressivo do pop; sem medicao publicada por estilo, calibrada para o motor (ver secao 7)"},
    {"name": "picked_down_vs_up_delta", "range": [6, 10], "source": "CONVENCAO — golpe para baixo mais firme que para cima, delta sutil dentro do contorno; sem medicao publicada (ver secao 7)"}
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
    {"name": "velocity", "range": [25, 50], "source": "CONVENCAO — nao ha limiar de velocity documentado para ghost note no MODO BASS (gatilho e binario por keyswitch, secao 7); faixa baixa escolhida para o motor, sem medicao publicada"},
    {"name": "velocity_relativa_pct", "range": [20, 40], "source": "CONVENCAO — mesma lacuna do parametro `velocity` acima; percentual relativo escolhido para o fallback generico, sem medicao publicada"},
    {"name": "gate_pct", "range": [10, 25], "source": "CONVENCAO — nota curta e abafada tipica de ghost note; sem medicao publicada (ver secao 7)"}
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
    {"name": "velocity_relativa", "range": [-30, -15], "source": "CONVENCAO — nota ligada sai mais fraca que a atacada; nao ha limiar publicado, valor escolhido para o motor (ver secao 7)"},
    {"name": "overlap_ms", "range": [10, 40], "source": "CONVENCAO — o requisito publicado e apenas 'as notas precisam se sobrepor', sem limiar em ms (secao 7); janela escolhida para o motor"}
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
  "description": "ATENCAO: BEND e SLIDE sao linhas separadas na pagina CONTROL, com mecanismos distintos. BEND e CC 5; SLIDE e pitch wheel. NAO use CC 5 para slide. Slide de intervalo real sai pelo pitch wheel, escalado pelo SLIDE RANGE da interface, que e estado da GUI e nao do MIDI: se o plano depende de um intervalo especifico, ele precisa declarar o SLIDE RANGE esperado. No fallback generico, ponha a faixa de bend em 12 semitons e escreva a curva; volte SEMPRE ao centro antes da proxima nota nao deslizada.",
  "parameters": [
    {"name": "slide_range_default_semitons", "value": 2, "source": "pagina CONTROL do plugin, MODO BASS 1.5.2, default de fabrica"},
    {"name": "bend_por_semitom_com_faixa_12", "value": 683, "source": "MIDI Association — 8191/12, aritmetica"},
    {"name": "bend_por_oitava_com_faixa_12", "value": 8191, "source": "MIDI Association"},
    {"name": "duracao_curto_ms", "range": [40, 80], "source": "CONVENCAO — forma e duracao da curva sao convencao, nao medicao (secao 4); janela para slide de 1-3 semitons escolhida para o motor"},
    {"name": "duracao_longo_ms", "range": [100, 250], "source": "CONVENCAO — forma e duracao da curva sao convencao, nao medicao (secao 4); janela para slide de 5-12 semitons escolhida para o motor"},
    {"name": "resolucao_ms_por_evento", "range": [5, 10], "source": "CONVENCAO — abaixo de dez pontos no slide inteiro fica degrau audivel (secao 4); intervalo entre eventos de bend escolhido para o motor, sem medicao publicada"}
  ],
  "tools": {
    "generic": {"cc": "pitch_bend", "note": "faixa 12 semitons; curva S no slide longo; retorno obrigatorio ao centro"},
    "modo_bass": {"cc": "pitch_wheel", "note": "pitch wheel, escalado pelo knob SLIDE RANGE (estado da GUI, default 2 semitons). BEND e CC 5 e NAO serve para slide. LEGATO SLIDE e CC 65, confirmado na tela"}
  }
}
```

### 5.5 Palm mute

```technique
{
  "name": "palm_mute",
  "family": "bass",
  "summary": "Abafamento por gate/velocity e, no MODO BASS configurado, automacao CC9.",
  "verified": false,
  "description": "No MODO BASS mute NAO e um estilo separado: e uma quantidade continua aplicada por cima do estilo ativo. Na pagina CONTROL, ative MUTING como CC 9 antes de renderizar: o motor escreve CC9 antes de cada nota escolhida e CC9=0 no fim dela. Sem esse mapeamento no plugin, os eventos continuam validos no MIDI, mas nao controlam o timbre; a skill deve orientar essa configuracao antes de autorizar a tecnica.",
  "parameters": [
    {"name": "velocity", "range": [60, 100], "source": "CONVENCAO — mute abafa timbre, nao reduz forca de ataque a niveis de ghost note; faixa media escolhida para o motor, sem medicao publicada"},
    {"name": "gate_pct", "range": [25, 50], "source": "CONVENCAO — nota curta caracteristica de palm mute; sem medicao publicada (ver secao 7)"},
    {"name": "amount", "range": [18, 35], "source": "CONVENCAO — perfil de muting por secao em base_conhecimento_midi_realista_modo_bass.md (verso 35, refrão 18, breakdown 28); faixa moderada para automacao deterministica"}
  ],
  "tools": {
    "generic": {"note": "gate curto e velocity media; sem CC dedicado"},
    "modo_bass": {"cc": 9, "note": "Antes de renderizar, configure MUTING na pagina CONTROL como CC 9. O motor envia valor antes da nota e CC9=0 ao fim; nao e keyswitch"}
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
    {"name": "cc", "value": 1, "source": "pagina CONTROL do plugin, v1.5.2"},
    {"name": "vibrato_rate_default", "value": 4.0, "source": "knob VIBRATO RATE, default de fabrica"},
    {"name": "atraso_de_inicio_ms", "range": [150, 300], "source": "CONVENCAO — vibrato nunca comeca em t=0, mao real ataca e so depois vibra; janela de atraso escolhida para o motor, sem medicao publicada"},
    {"name": "profundidade_cc", "range": [60, 90], "source": "CONVENCAO — rampa de subida, sustentacao e rampa de descida sobre CC 1; profundidade escolhida para o motor, sem medicao publicada"}
  ],
  "tools": {
    "generic": {"cc": 1},
    "modo_bass": {"cc": 1, "source": "pagina CONTROL v1.5.2"}
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
    {"name": "picked_downstroke_velocity", "range": [85, 120], "source": "CONVENCAO — golpe para baixo mais firme; faixa escolhida para o motor, sem medicao publicada por estilo (ver secao 7)"},
    {"name": "picked_upstroke_velocity", "range": [70, 100], "source": "CONVENCAO — golpe para cima mais fraco que para baixo; faixa escolhida para o motor, sem medicao publicada por estilo (ver secao 7)"},
    {"name": "slap_velocity", "range": [95, 127], "source": "CONVENCAO — ataque percussivo do thumb; faixa escolhida para o motor, sem medicao publicada por estilo (ver secao 7)"},
    {"name": "pop_velocity", "range": [100, 127], "source": "CONVENCAO — pop mais agressivo que o thumb; faixa escolhida para o motor, sem medicao publicada por estilo (ver secao 7)"},
    {"name": "upstroke_atraso_ms", "range": [0, 8], "source": "CONVENCAO — leve atraso da mao no golpe para cima; janela escolhida para o motor, sem medicao publicada"}
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
    {"name": "cc", "value": 64, "source": "pagina CONTROL do plugin, v1.5.2"}
  ],
  "tools": {
    "generic": {"cc": 64},
    "modo_bass": {"cc": 64, "source": "pagina CONTROL v1.5.2"}
  }
}
```

### 5.9 Seleção de corda — a decisão que o plugin erra sozinho

```technique
{
  "name": "string_selection",
  "family": "bass",
  "summary": "Forcar a corda em que a nota soa. Em drop tuning o riff vive na corda grave, e o plugin nao sabe disso sozinho.",
  "verified": false,
  "description": "A MESMA ALTURA soa diferente em cordas diferentes: na corda grave, mais fundamental e mais corpo; numa corda mais aguda na mesma altura, mais harmonico e menos peso. O MODO BASS escolhe a corda sozinho otimizando ECONOMIA DE MAO — menos deslocamento, posicao mais confortavel. Isso e o oposto do que metal em drop quer: ali a escolha e por TIMBRE, e o riff fica na corda mais grave mesmo quando daria para tocar mais acima com menos esforco. Sempre que a corda for decisao intencional, FORCE por keyswitch; deixar no automatico entrega uma linha com as notas certas e o peso errado. O keyswitch e latchavel: ele vale ate outro keyswitch de corda mudar, entao declare a troca, nao repita a cada nota.",
  "parameters": [
    {"source": "pagina CONTROL v1.5.2", "name": "keyswitch_corda_C", "value": 0},
    {"source": "pagina CONTROL v1.5.2", "name": "keyswitch_corda_A", "value": 9},
    {"source": "pagina CONTROL v1.5.2", "name": "keyswitch_corda_B", "value": 11},
    {"source": "pagina CONTROL v1.5.2", "name": "keyswitch_corda_D", "value": 14},
    {"source": "pagina CONTROL v1.5.2", "name": "keyswitch_corda_E", "value": 16},
    {"source": "pagina CONTROL v1.5.2", "name": "keyswitch_corda_G", "value": 19},
    {"name": "cc_posicao_mao", "value": 4, "source": "pagina CONTROL do plugin, v1.5.2 — LEFT HAND POSITION"}
  ],
  "tools": {
    "generic": {"note": "sem controle de corda: o timbre sai do que o instrumento decidir. Declare no plano que a intencao de corda nao pode ser honrada nesta ferramenta"},
    "modo_bass": {
      "note": "LATCH vem DESLIGADO de fabrica: o keyswitch e momentaneo e vale enquanto segurado. Ligue LATCH na pagina CONTROL se quiser que a corda persista. CC 4 controla a posicao da mao de forma continua"
    }
  }
}
```

**Quando forçar e quando deixar automático.** Force sempre que a escolha de corda carregar intenção:
riff de drop na corda mais grave, nota pedal sustentada numa corda enquanto a mão trabalha em outra,
ou passagem em que você quer o timbre de uma corda solta. Deixe automático em linha de acompanhamento
onde só a altura importa — aí a economia de mão do plugin é uma escolha melhor que a nossa.

**A tabulação é a fonte da verdade quando existe.** Se o material de origem veio de tab, a corda já
está decidida ali e o gerador deve honrar, não recalcular. Quando não houver tab, a regra prática do
gênero: em afinação drop, o riff mora nas cordas mais graves e o gerador força; o que sobe de
registro é linha melódica, não riff.

### 5.10 Harmônico

```technique
{
  "name": "harmonic",
  "family": "bass",
  "summary": "Harmonico natural por keyswitch momentaneo.",
  "verified": false,
  "parameters": [
    {"name": "velocity", "range": [60, 100], "source": "CONVENCAO — harmonico soa mais suave que nota atacada normal; faixa escolhida para o motor, sem medicao publicada"}
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
| ~~Números de CC~~ | **fechado** — lidos da página CONTROL do plugin, v1.5.2, defaults de fábrica |
| ~~Mapa de keyswitch~~ | **fechado** — 12 de 12 conferidos contra a tela |
| ~~CC 65 para LEGATO SLIDE~~ | **fechado** — confirmado na tela; a fonte única estava certa |
| ~~CC de MUTING~~ | **fechado, e a resposta é que não existe** — precisa ser atribuído pelo usuário |
| Faixa do BEND (CC 5) em semitons | **não exposta na página CONTROL** — só o SLIDE RANGE aparece, e vale para o pitch wheel |
| Limiar de sobreposição em ms para hammer-on/pull-off | não documentado |
| Limiar de velocity para ghost note | não existe: o gatilho é o keyswitch, e é binário |
| Índice e médio (INDEX/MIDDLE STROKE) | existem como função mas **sem atribuição de fábrica** |
| Todos os valores de velocity, gate e timing da §5 | convenção de ofício, sem medição publicada |
| Offsets de gênero da §3.3 | extrapolados de dois estudos; só o ≈30 ms de funk tem fonte direta |

## Fontes

- **Página CONTROL do MODO BASS 1.5.2, defaults de fábrica** — fonte primária das seções 1 e 2
- [newdtm-rain.com — seção CONTROL do MODO BASS](https://www.newdtm-rain.com/article/modo-bass-control.html)
- [note.com/moonwhite — mapa de articulação do MODO BASS 2](https://note.com/moonwhite/n/nb457c31bf867)
- [Senn, Kilchenmann, von Georgi & Bullerjahn — microtiming e groove em swing e funk, Frontiers in Psychology](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2016.01487/full)
- [MIDI Association — Get Real and Get Funky](https://midi.org/get-real-and-get-funky-how-to-create-realistic-midi-bass-parts)
- [Sweetwater — Create Realistic MIDI Bass Parts](https://www.sweetwater.com/insync/create-realistic-midi-bass-parts/)
