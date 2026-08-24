# Técnicas de guitarra em MIDI — manual de execução

> **Para que serve.** A IA decide *o que* a música precisa. Este arquivo diz *como fazer aquilo em
> MIDI*. Cobre guitarra elétrica em metal moderno, metalcore e post-hardcore.
>
> **Regra de fonte.** Número com fonte vem citado. Número sem fonte **não existe neste arquivo** —
> vira lacuna declarada. As lacunas estão no fim, com o que foi procurado, para ninguém gastar
> pesquisa duas vezes no mesmo lugar nem preencher com estimativa plausível.
>
> **O perfil deste manual é diferente dos outros três.** Bateria e teclas têm medição de laboratório.
> Guitarra quase não tem: o que existe é **documentação oficial de plugin**, e ela é farta. A
> consequência prática é que aqui o caminho confiável é quase sempre *acionar o recurso do
> instrumento*, não *simular o gesto com número inventado*.

---

## 0. Convenções, antes de qualquer coisa

### 0.1 Nome de nota é ambíguo. Número MIDI não é

| Convenção | Dó central | Mi grave da guitarra (MIDI 40) |
|---|---|---|
| Científica (SPN) | C4 = 60 | **E2** |
| Yamaha/Kontakt — usada por Ample, MusicLab e pelo display do Kontakt | C3 = 60 | **E1** |

A âncora aritmética é `m = 69 + semitons_desde_A4`, com A4 = 69 e C4 = 60
([SPN](https://en.wikipedia.org/wiki/Scientific_pitch_notation), MEDIDO). MusicLab documenta
"Note # 22 → A#-1" na Stroke Map, o que fixa C-2 = 0 e portanto C3 = 60
([RealLPC p.77](https://www.musiclab.com/assets/files/RealLPC.pdf), OFICIAL). Ample documenta o range
tocável como "E1–C5", e o mi grave da guitarra é MIDI 40 — logo E1 = 40, mesma convenção
([Ample tutorial](https://www.amplesound.net/en/tutorial.asp), OFICIAL).

**Regra:** o plano e o MIDI carregam sempre **número absoluto**. Nome de nota só aparece ao ler
manual de plugin, e é convertido na hora usando a convenção daquele plugin.

### 0.2 Pitch bend — a aritmética

Fonte de toda esta tabela: [studiocode.dev — MIDI pitch bend](https://studiocode.dev/kb/MIDI/midi-pitch-bend/) (OFICIAL).

| Item | Valor |
|---|---|
| Mensagem | `0xEn <LSB> <MSB>` |
| Resolução | 14 bits, 0–16383 |
| Centro | **8192** (data bytes `00 64`) |
| Valor | `value = 128 × MSB + LSB` |
| Range padrão | **±2 semitons** |
| Definir range | RPN `00 00`; Data Entry MSB = semitons, LSB = cents |
| Converter | `semitons = valor_bend × (range / 8192)` |
| Passos por semitom | `8192 / range` — range ±2 → 4096; range ±12 → 682,67 |

**Contraindicação estrutural, e ela manda em três técnicas deste manual.** Pitch bend é *de canal*:
dobra **todas** as notas soando naquele canal. Bend de uma nota dentro de um power chord é impossível
em canal único. Os três caminhos legítimos:

- **MPE** — 2 a 16 canais, um por nota, range por nota de 48 semitons por padrão
  ([MPE spec](https://midi.org/midi-polyphonic-expression-mpe-specification-adopted) e
  [studiocode MPE](https://studiocode.dev/resources/mpe/), OFICIAL);
- **canal por corda** — MusicLab e Ample expõem isso (§7);
- **recurso do plugin** — Ample dobra só a nota mais grave por padrão; Shreddage tem Unison Bend.

### 0.3 CCs padrão

| CC | Função | Fonte |
|---|---|---|
| 1 | Modulation wheel — vibrato na maioria das libs | [MIDI CC list](https://midi.org/midi-1-0-control-change-messages) |
| 7 | Volume | idem |
| 11 | Expression — swells, violining | idem |
| 64 | Sustain (0–63 off, 64–127 on) | idem |

### 0.4 Fonte envenenada — bloqueada

`cmuse.org` publica páginas "calculadora" geradas automaticamente que emitem números precisos sem
metodologia: "72% de gate", "staccato 25% / half gate 50% / tight 80% / full 100% / legato 110%",
"release abaixo de 35 ms". A única atribuição rastreável foi verificada e é **falsa** — o número de
35 ms é atribuído à Nail The Mix, e a
[página citada](https://www.nailthemix.com/how-to-edit-guitars-in-pro-tools) não contém esse número.

**Nunca citar cmuse.org.** Se um número deste manual reaparecer com essa origem, ele é falso.

---

## 1. Palm mute / chug

A mão da palheta encosta nas cordas junto ao cavalete, matando o sustain e deixando o ataque
percussivo. É o motor rítmico de praticamente todo riff de metal moderno.

O que é documentado, e o que muda o jeito de programar: **as duas libs melhor documentadas modulam a
profundidade do mute pela velocity**, não por keyswitch. Ample diz que velocity menor dá mute mais
profundo; Shreddage 3 foi "recorded with multiple degrees of palm muting, from very muted to
half-muted. The amount of palm muting is controlled by velocity". Usar keyswitch fixo joga fora essa
dimensão inteira.

A justificativa física para abafar acorde com distorção está documentada: "sustain sound coming from
each string simultaneously makes large amounts of overlapping overtones after distortion"
([Palm mute](https://en.wikipedia.org/wiki/Palm_mute), OFICIAL).

**O que não existe:** faixa de velocity de chug, gate absoluto em ms, e razão gate/step para chug
apertado vs solto. Procurado, não achado — ver Lacunas. A única fonte com esses números era a
envenenada de §0.4.

```technique
{
  "name": "palm_mute",
  "family": "guitar",
  "summary": "Mao da palheta abafa junto ao cavalete. Motor ritmico do riff — e nas libs modernas a profundidade e controlada por VELOCITY, nao por keyswitch.",
  "verified": true,
  "description": "NAO use keyswitch fixo em lib que modula mute por velocity: Ample e Shreddage 3 ambas variam a PROFUNDIDADE do abafamento pela velocity, e um keyswitch travado joga fora essa dimensao. Chug vive nas 2 ou 3 cordas mais graves da afinacao declarada. O 'aperto' do chug e posicao de note-OFF, nao de note-on — mas o valor absoluto do gate NAO TEM FONTE, entao nao invente: derive do contexto ritmico ou deixe o plugin resolver. Em patch generico sem articulacao, a unica orientacao com fonte e reduzir a duracao soante para 1/2 ou 1/4 da escrita.",
  "parameters": [
    {"name": "shreddage3_velocity_mute", "range": [1, 59], "source": "CONVENCAO — resposta de staff da ISW em forum, https://www.kvraudio.com/forum/viewtopic.php?t=580620 ; nao confirmado no PDF oficial"},
    {"name": "shreddage3_velocity_sustain", "range": [60, 119], "source": "idem"},
    {"name": "generico_fator_de_duracao", "range": [0.25, 0.5], "source": "CONVENCAO — forum MuseScore, https://musescore.org/en/node/293227"},
    {"name": "generico_program_gm", "value": 29, "source": "CONVENCAO — Electric Guitar (muted), https://musescore.org/en/node/293227"},
    {"name": "velocity_de_chug_que_soa_humana", "source": null},
    {"name": "gate_absoluto_ms", "source": null},
    {"name": "razao_gate_step_apertado_vs_solto", "source": null}
  ],
  "tools": {
    "generic": {"note": "sem articulacao: encurtar a duracao soante para 1/2 ou 1/4 da escrita; GM program 29"},
    "ample": {"keyswitch": 26, "keyswitch_nome": "D0", "range_tocavel": [40, 84], "regra": "velocity MENOR = mute MAIS profundo"},
    "ample_metal": {"keyswitch": 24, "keyswitch_nome": "C0", "range_tocavel": [36, 84], "note": "C0 cobre Sustain + Palm Mute + Artificial Harmonic no mesmo keyswitch"},
    "shreddage3": {"articulacao": "Mute", "regra": "profundidade por velocity; ativar Vel Scale nas articulacoes de palm mute para a profundidade continuar variando dentro da sub-faixa"},
    "musiclab_reallpc": {"fx": [1, 2, 35], "fx_nomes": ["Mute", "BridgeMute", "VeloMute"], "note": "BridgeMute par1 = ctrl 0-128, par2 = sensibilidade a velocity 0-3; em 'off' a velocity nao altera o comprimento"}
  }
}
```

---

## 2. Power chord — aberto vs abafado

Tônica + quinta justa, às vezes + oitava: intervalos **0, +7, (+12)** semitons. MusicLab lista isso
na Interval Map como id 4, "Power (5th + 4th up)"
([RealLPC p.102](https://www.musiclab.com/assets/files/RealLPC.pdf), OFICIAL).

A alternância aberto ↔ abafado é o que dá forma à frase de riff, e o mecanismo é trocar a articulação
Sustain ↔ Mute — por velocity ou por keyswitch, conforme §1.

**A regra "sem terças no grave" tem efeito documentado, mas mecanismo não.** A Wikipedia documenta o
acúmulo de harmônicos sobrepostos após a distorção; o nome do mecanismo (distorção de
intermodulação) não tem fonte que eu tenha achado. Use a regra, cite o efeito, não invente o
mecanismo.

```technique
{
  "name": "power_chord",
  "family": "guitar",
  "summary": "Tonica + quinta justa (+ oitava). Intervalos 0, +7, +12 — a unica sonoridade de acorde que sobrevive ao alto ganho.",
  "verified": true,
  "description": "Nao empilhar triade completa na regiao grave em patch de alto ganho: acordes cheios com distorcao geram grande quantidade de harmonicos sobrepostos, e o resultado e lama. O efeito e documentado; o mecanismo nomeado nao tem fonte. O contraste aberto/abafado se faz trocando a articulacao Sustain <-> Mute, nao mudando a nota. Maximo de notas simultaneas = numero de cordas do instrumento. O gate do power chord aberto e a diferenca de velocity entre aberto e abafado NAO TEM FONTE.",
  "parameters": [
    {"name": "intervalos_semitons", "value": [0, 7, 12], "source": "definicao intervalar; MusicLab Interval Map id 4 'Power (5th + 4th up)', https://www.musiclab.com/assets/files/RealLPC.pdf p.102"},
    {"name": "gate_do_aberto", "source": null},
    {"name": "delta_velocity_aberto_vs_abafado", "source": null}
  ],
  "tools": {
    "generic": {"note": "nota aberta ringing precisa de gate que ultrapasse o step; nota abafada nao"},
    "musiclab_reallpc": {"fx": 29, "fx_nome": "Interval", "param": 4, "note": "gera o power chord a partir de nota unica e forca modo monofonico enquanto ativo"},
    "shreddage3": {"note": "Fretting Mode Polyphonic prioriza usar o maximo de cordas simultaneas"}
  }
}
```

---

## 3. Pinch harmonic

O polegar toca a corda logo após a palheta, cancelando a fundamental e deixando um harmônico agudo
gritar. É harmônico **artificial**, e a altura resultante depende de qual parcial sobrevive: nó a 1/2
da corda dobra a frequência (**+12 semitons**), nó a 1/3 dá **+19**
([American Guitar Academy](https://www.theamericanguitaracademy.com/post/natural-artificial-and-pinch-harmonics-explained), MEDIDO).

### A armadilha que custa um acento

Em Ample, **velocity 127 é reservada**: "Notes of velocity less than 127 will be Sustain. For
acoustics, notes of velocity 127 will be Pop. For electrics, notes of velocity 127 will be either
Artificial Harmonic or Pop depending on Accentuate Mode"
([Ample tutorial](https://www.amplesound.net/en/tutorial.asp), OFICIAL).

Consequência direta: **nessas libs o teto de acento normal é 126**. Uma nota de sustain escrita com
127 vira pinch harmonic sem ninguém ter pedido — e o erro é silencioso, porque o MIDI está
"correto".

```technique
{
  "name": "pinch_harmonic",
  "family": "guitar",
  "summary": "Polegar cancela a fundamental e um parcial agudo grita. Ornamento de acento — e em Ample ele sequestra a velocity 127.",
  "verified": true,
  "description": "ATENCAO ao teto de velocity. Em Ample, qualquer nota de Sustain com velocity 127 vira Pop ou Artificial Harmonic conforme o Accentuate Mode. Nessas libs o acento normal tem teto 126; escrever 127 dispara o ornamento em silencio. Em patch generico sem articulacao, transponha a nota escrita pelo intervalo do parcial — a nota escrita passa a ser a SOANTE, nao a digitada. Qual parcial os guitarristas de metal mais atingem na pratica NAO TEM FONTE.",
  "parameters": [
    {"name": "parcial_2_semitons_acima", "value": 12, "source": "no a 1/2 da corda dobra a frequencia, https://www.theamericanguitaracademy.com/post/natural-artificial-and-pinch-harmonics-explained"},
    {"name": "parcial_3_semitons_acima", "value": 19, "source": "no a 1/3 da corda, idem"},
    {"name": "ample_velocity_gatilho", "value": 127, "source": "https://www.amplesound.net/en/Settings_and_CPCv2.pdf secao 1.2.9"},
    {"name": "ample_teto_de_acento_normal", "value": 126, "source": "derivado do gatilho oficial acima"},
    {"name": "parcial_mais_usado_em_metal", "source": null},
    {"name": "velocity_fora_de_libs_que_usam_127", "source": null}
  ],
  "tools": {
    "generic": {"note": "transpor a nota escrita pelo intervalo do parcial; a nota escrita e a soante"},
    "ample": {"keyswitch": 24, "keyswitch_nome": "C0", "velocity": 127, "alternativa": {"keyswitch": 25, "keyswitch_nome": "C#0", "velocity": 127}},
    "ample_metal": {"keyswitch": 24, "keyswitch_nome": "C0", "note": "C0 cobre Artificial Harmonic"},
    "musiclab_reallpc": {"fx_nome": "PinchHarmonics", "note": "som nao-cromatico para notas da Main zone; atribuivel a Key/Pedal/Mod.Wheel/Velocity Switch"},
    "shreddage3": {"note": "presente nas versoes full; NAO listado no manual do Stratus FREE"}
  }
}
```

---

## 4. Bend e pre-bend

O único conjunto de **faixas em milissegundos publicadas oficialmente** que existe para bend de
guitarra é o FX MAP da MusicLab. Ele é range de *parâmetro do plugin*, não medição de performance — a
distinção importa e está registrada nas lacunas.

| FX | O que faz | Intervalo | Tempo |
|---|---|---|---|
| 21 `Bend` | sobe **até** a nota tocada | 1–2 st | 100–800 ms |
| 22 `ReverseBend` | **pre-bend e release** até a nota tocada | 1–2 st | 100–800 ms |
| 23 `UnisonBend` | uníssono | 1–2 st | 100–800 ms |

Fonte: [RealLPC p.102](https://www.musiclab.com/assets/files/RealLPC.pdf) (OFICIAL).

Valores alvo de pitch bend, pela fórmula de §0.2:

| Bend | range ±2 | range ±12 |
|---|---|---|
| +1 semitom | 12288 | 8875 |
| +2 semitons | 16383 (máximo) | 9557 |
| −12 semitons | inalcançável | 0 |

Mecânica do pre-bend, oficial — **os tempos, não**:

```
antes do Note On : PB já no valor alvo
Note On
hold             : duração NÃO ENCONTRADA
release          : rampa PB → 8192, duração NÃO ENCONTRADA
Note Off, PB 8192
```

> **A regra que quebra a música se for esquecida:** nunca deixar o pitch bend fora de 8192 depois do
> Note Off. A próxima nota nasce desafinada, e o erro se propaga por toda a track até o próximo
> reset.

Guitar Pro exporta Slide e Bend como eventos **Pitch Wheel (0xE0) em canal MIDI separado** das notas
([Guitar Pro](https://www.guitar-pro.com/blog/p/44070-how-to-connect-guitar-pro-with-your-daw-to-use-vsti),
OFICIAL) — relevante ao ler MIDI que veio de tablatura.

```technique
{
  "name": "bend",
  "family": "guitar",
  "summary": "Empurrar a corda para elevar a altura continuamente. Pre-bend chega ao alvo antes do ataque e solta.",
  "verified": true,
  "description": "SEMPRE retornar o pitch bend a 8192 depois do Note Off: senao a proxima nota nasce desafinada e o erro se propaga pela track inteira. Bend dentro de acorde em canal unico e IMPOSSIVEL — use MPE, canal por corda, o comportamento 'so a nota mais grave' da Ample, ou o Unison Bend da Shreddage. As faixas de 100-800 ms sao range de PARAMETRO do plugin, nao medicao de performance: nao as apresente como o tempo tipico de um bend real. Duracao medida, formato da curva, e tempos de hold e release do pre-bend NAO TEM FONTE.",
  "parameters": [
    {"name": "centro_pitch_bend", "value": 8192, "source": "https://studiocode.dev/kb/MIDI/midi-pitch-bend/"},
    {"name": "range_default_semitons", "value": 2, "source": "idem; Shreddage 3 tambem usa +-2 por padrao"},
    {"name": "passos_por_semitom_range_2", "value": 4096, "source": "8192/2, aritmetica da spec"},
    {"name": "passos_por_semitom_range_12", "value": 682.67, "source": "8192/12, aritmetica da spec"},
    {"name": "musiclab_intervalo_semitons", "range": [1, 2], "source": "FX 21/22/23, https://www.musiclab.com/assets/files/RealLPC.pdf p.102"},
    {"name": "musiclab_tempo_ms", "range": [100, 800], "source": "idem — range de parametro, NAO medicao"},
    {"name": "shreddage3_unison_bend_intervalo_max_semitons", "value": 5, "source": "quarta justa; https://impactsoundworks.com/docs/Shreddage%203%20Stratus%20Free%20Manual.pdf"},
    {"name": "ample_mod_wheel_range_semitons", "range": [1, 12], "source": "https://www.amplesound.net/en/Settings_and_CPCv2.pdf secao 1.1.6"},
    {"name": "duracao_real_medida_ms", "source": null},
    {"name": "formato_da_curva", "source": null},
    {"name": "pre_bend_hold_e_release_ms", "source": null}
  ],
  "tools": {
    "generic": {"note": "pre-bend = PB no alvo ANTES do Note On, rampa de volta a 8192 depois; ampliar range via RPN 00 00 se passar de 2 semitons"},
    "ample": {"poly_bend_toggle": "padrao desligado = so a nota mais grave dobra", "mod_wheel_range": [1, 12]},
    "shreddage3": {"pitch_bend_range": 2, "unison_bend": "dobra a nota mais grave enquanto ela estiver a no maximo uma quarta justa da mais aguda"},
    "musiclab_reallpc": {"fx": [21, 22, 23], "cc_range": 59, "cc_bender_up_mode": 57, "cc_bender_down_mode": 48}
  }
}
```

---

## 5. Vibrato

> **Este bloco está marcado `verified: false` de propósito.** Rate e extent de vibrato de guitarra só
> aparecem em blog de ensino, com URL mas sem metodologia. Os *mecanismos* são oficiais; os
> *números* são convenção. Não trate 5–7 Hz como medição.

As definições são de fonte acadêmica: extent é "how far above and below the mid-frequency each cycle
fluctuates - in percentage or cents, with 6% or 100 cents making one semitone"; rate é "number of
cycles per second - in Hertz"
([Timbre & Orchestration](https://timbreandorchestration.org/writings/timbre-lingo/2022/6/27/vibrato)).

### O que é oficial, e é o que importa

**Vibrato não começa junto com o ataque.** Ample documenta um estágio "Start" no envelope SAHDS
existindo exatamente para isso: "The modulation doesn't work during the Start time. It ensures that
fast notes will not be vibrated"
([Settings & CPC §1.1.9](https://www.amplesound.net/en/Settings_and_CPCv2.pdf), OFICIAL). O
mecanismo é oficial; **o valor do atraso não está publicado**.

Shreddage separa dois modos, e a diferença muda a decisão: *Emulated* é modulação de pitch do Kontakt
com Speed e Depth controláveis; *Fingered* é **performance amostrada**, e nele o depth **não pode ser
alterado** — é uma gravação, não uma modulação.

```technique
{
  "name": "vibrato",
  "family": "guitar",
  "summary": "Oscilacao periodica da altura por movimento de dedo na corda. E o que separa uma nota de guitarra de uma nota de teclado.",
  "verified": false,
  "description": "MARCADO NAO VERIFICADO: rate e extent so tem fonte de blog de ensino, sem metodologia. Nao apresente 5-7 Hz como medicao. O que e oficial: vibrato NAO comeca junto com o ataque — Ample tem um estagio 'Start' no envelope justamente para impedir que notas rapidas sejam vibradas, embora o valor do atraso nao esteja publicado. Em instrumento amostrado, PREFIRA o vibrato do proprio plugin a desenhar pitch bend a mao: no modo Fingered da Shreddage o vibrato e uma gravacao real, e desenhar por cima soma dois vibratos. Vibrato por pitch bend de canal afeta o acorde inteiro — em power chord isso e errado, porque o guitarrista vibra UMA corda.",
  "parameters": [
    {"name": "rate_hz", "range": [5, 7], "source": "CONVENCAO — blog de ensino, https://clefarc.com/blogs/guitar-performance-technique/how-to-master-electric-guitar-vibrato-a-complete-guide-to-speed-depth-expression-techniques"},
    {"name": "extent_cents", "range": [20, 100], "source": "CONVENCAO — mesma fonte"},
    {"name": "cents_por_semitom", "value": 100, "source": "https://timbreandorchestration.org/writings/timbre-lingo/2022/6/27/vibrato"},
    {"name": "atraso_de_inicio_ms", "source": null},
    {"name": "rate_e_depth_medidos_em_metal", "source": null}
  ],
  "tools": {
    "generic": {"cc": 1, "note": "CC1 e o controlador de vibrato na maioria das libs"},
    "shreddage3": {"cc": 1, "alternativa": "aftertouch", "modos": ["Emulated", "Fingered"], "note": "Fingered e performance amostrada e o depth NAO pode ser alterado"},
    "ample": {"cc": 1, "envelope": "SAHDS — Start impede vibrato em notas rapidas; Mod Time = velocidade, Mod Pitch = profundidade", "auto_mod": "modulacao segue o envelope com a mod wheel parada"},
    "musiclab_reallpc": {"cc_modo_da_wheel": 60, "cc_range": 62, "cc_depth": 14, "cc_freq": 15, "note": "CC60: 0-15 Off, 16-47 Slide, 48-79 Pitch, 80-111 Modulation"}
  }
}
```

---

## 6. Slide e glissando

Deslizar o dedo pela corda sem repalhetar. O FX MAP da MusicLab é de novo a única fonte com faixas em
ms ([RealLPC p.101–102](https://www.musiclab.com/assets/files/RealLPC.pdf), OFICIAL):

| FX | O que faz | Range | Tempo |
|---|---|---|---|
| 8 | `Slide (Legato)` — glissando entre duas notas, ataque só na primeira | 0–48 st | **30–150 ms** |
| 17 | `SlideUp` — slide **até** a nota tocada | 1–12 st | **40–500 ms** |
| 18 | `SlideUpTrig` — dispara **a partir** da nota sustentada | 1–12 st | 40–500 ms |
| 19 | `SlideDown` (fall) | 1–12 st | 40–500 ms |
| 20 | `SlideDownTrig` | 1–12 st | 40–500 ms |

Shreddage modela uma assimetria real que vale conhecer: `Slide Volume Realism` faz "slide transitions
and destinations will be attenuated over time, to emulate the physical constraint of continuously
sliding on a string without repicking". Slide longo perde energia — porque a corda não foi atacada de
novo.

```technique
{
  "name": "slide",
  "family": "guitar",
  "summary": "Deslizar o dedo pela corda sem repalhetar. Legato liga duas notas; shift percorre o caminho de forma audivel.",
  "verified": true,
  "description": "Slide legato EXIGE sobreposicao entre as notas: sem sobreposicao o motor dispara um ataque novo e o efeito nao acontece. Em patch generico, glissando = um Note On na origem + rampa de pitch bend ate o destino, com o range RPN ajustado para cobrir o intervalo; acima de 12 semitons e preciso ampliar o range ou usar notas cromaticas discretas. Slide para cima e para baixo NAO sao simetricos no timbre: a corda perde energia porque nao foi atacada de novo, e a Shreddage modela isso com atenuacao progressiva. Velocidade de slide medida em performance real NAO TEM FONTE.",
  "parameters": [
    {"name": "musiclab_legato_range_semitons", "range": [0, 48], "source": "FX 8, https://www.musiclab.com/assets/files/RealLPC.pdf p.101"},
    {"name": "musiclab_legato_tempo_ms", "range": [30, 150], "source": "FX 8, idem"},
    {"name": "musiclab_shift_range_semitons", "range": [1, 12], "source": "FX 17/18/19/20, idem p.102"},
    {"name": "musiclab_shift_tempo_ms", "range": [40, 500], "source": "FX 17/18/19/20, idem p.102"},
    {"name": "shreddage3_slide_speed_pct", "range": [50, 200], "source": "time-stretch da transicao, https://impactsoundworks.com/docs/Shreddage%203%20Stratus%20Free%20Manual.pdf"},
    {"name": "velocidade_medida_semitons_por_segundo", "source": null}
  ],
  "tools": {
    "generic": {"note": "Note On na origem + rampa de PB ate o destino; ampliar o range via RPN se passar de 2 semitons"},
    "ample": {"keyswitch": 28, "keyswitch_nome": "E0", "regra": "velocity alta muda de casa, baixa nao", "slide_in_out": {"keyswitch": 27, "keyswitch_nome": "D#0", "note": "antes da nota = Slide In; durante = Slide Out"}, "slide_guitar": {"keyswitch": 30, "keyswitch_nome": "F#0"}},
    "ample_metal": {"keyswitch": 25, "keyswitch_nome": "C#0", "slide_in_out": {"keyswitch": 27, "keyswitch_nome": "D#0"}},
    "shreddage3": {"slide_speed_pct": [50, 200], "vel_to_slide_speed": "a velocity da nota escala a velocidade da transicao"},
    "musiclab_reallpc": {"fx": [8, 17, 18, 19, 20], "cc_range_up": 58, "cc_range_down": 63, "cc_range_wheel": 61, "note": "velocity da tecla de trigger afeta a dinamica do slide"}
  }
}
```

---

## 7. Hammer-on e pull-off

Soar a nota seguinte só com a mão do braço. **É mais fraca que uma nota palhetada** — Shreddage
aplica uma redução de volume estática exatamente para modelar isso: "decreases the volume of the
hammer-on articulation by a static amount to emphasize the strength difference between sustain
picking and fretting without the pick" (OFICIAL).

A direção é oficial. **O valor não é.** Programar as duas notas com a mesma velocity é irreal, mas
quanto reduzir em unidades MIDI não tem fonte.

### A restrição física que nenhum motor conserta

Hammer-on e pull-off só existem **na mesma corda**. MusicLab documenta isso explicitamente para o
Tapping FX. Um "legato" entre duas notas que exigiriam cordas diferentes é fisicamente impossível —
e é um dos erros mais comuns de quem escreve linha de guitarra num piano roll.

Sobre o limiar de sobreposição, a única fonte que achei é fórum de desenvolvedores Kontakt, e ela diz
apenas que "the overlap only needs to be a **few milliseconds** generally", variando por script
([VI-Control](https://vi-control.net/community/threads/duration-of-overlap-to-trigger-legato.31460/),
CONVENÇÃO). Nenhum número específico.

```technique
{
  "name": "hammer_pull",
  "family": "guitar",
  "summary": "Nota soada so com a mao do braco, sem palheta. Mais fraca que a palhetada — e so existe na MESMA corda.",
  "verified": true,
  "description": "RESTRICAO FISICA: hammer-on e pull-off so existem na mesma corda. Um legato entre notas que exigiriam cordas diferentes e impossivel de tocar, e e um dos erros mais comuns de linha escrita no piano roll. A nota ligada e MAIS FRACA que a palhetada — a direcao e oficial (Shreddage aplica reducao estatica de volume), mas o VALOR em unidades MIDI nao tem fonte: derive do contexto, nao invente constante. O limiar de sobreposicao para disparar legato tambem nao tem numero publicado; a unica fonte diz 'a few milliseconds' e varia por script.",
  "parameters": [
    {"name": "musiclab_range_semitons", "range": [0, 48], "source": "FX 7 HammerOn, https://www.musiclab.com/assets/files/RealLPC.pdf p.101"},
    {"name": "musiclab_tapping_range_semitons", "value": 24, "source": "Tapping FX, monofonico, mesma corda, idem p.56"},
    {"name": "sobreposicao_para_disparar_ms", "source": null},
    {"name": "reducao_de_velocity_da_nota_ligada", "source": null}
  ],
  "tools": {
    "generic": {"note": "sobrepor as notas; reduzir a velocity da ligada em relacao a palhetada"},
    "ample": {"keyswitch": 29, "keyswitch_nome": "F0", "auto_legato": "dispara em sobreposicao na mesma corda; exige Keyboard Mode e Solo Mode DESLIGADOS"},
    "ample_metal": {"keyswitch": 26, "keyswitch_nome": "D0"},
    "shreddage3": {"parametro": "Hammer-On Range", "volume_realism": "reducao estatica de volume na articulacao de hammer-on", "mono_lead_mode": "soltar a nota mais recente segurando as antigas retrigga em legato"},
    "musiclab_reallpc": {"fx": [7, 48, 36], "cc_legato": 18, "cc_select": 35, "cc_steps": 36}
  }
}
```

---

## 8. Tremolo picking

Repetir a mesma nota o mais rápido possível com palhetada alternada.

**A regra prática que sai da documentação oficial é contraintuitiva:** quando a lib tem articulação de
tremolo real, dispare **uma nota longa** em vez de sequenciar 32avos. A articulação amostrada já
carrega a variação real entre golpes — e sequenciar à mão em lib com round robin limitado produz o
"machine gun effect", contra o qual a Shreddage precisa oferecer Anti-Repetition com probabilidade
ajustável.

Que golpe para cima e para baixo são **samples diferentes** é oficial: Shreddage expõe `Downstroke
Offset` e `Upstroke Offset` separados, e fala em "downstroke RRs" e "upstroke RRs".

Referência de grade útil, e ela é oficial: "16th notes at 120BPM are **125 ms** apart"
([Shreddage 3](https://impactsoundworks.com/docs/Shreddage%203%20Stratus%20Free%20Manual.pdf)).

```technique
{
  "name": "tremolo_picking",
  "family": "guitar",
  "summary": "Repetir a mesma nota o mais rapido possivel com palhetada alternada. Textura continua de tensao.",
  "verified": false,
  "description": "MARCADO NAO VERIFICADO: a taxa de 12-18 notas/s vem de blog de professor de guitarra, sem medicao. REGRA PRATICA: quando a lib tem articulacao de tremolo real, dispare UMA NOTA LONGA em vez de sequenciar 32avos — a articulacao amostrada ja carrega a variacao entre golpes, e sequenciar a mao em lib com round robin limitado produz o machine gun effect. Golpe para cima e para baixo sao pools de samples DISTINTOS, fato oficial; a diferenca de velocity entre eles nao tem fonte.",
  "parameters": [
    {"name": "taxa_shred_notas_por_segundo", "range": [12, 18], "source": "CONVENCAO — blog de professor, https://tomhess.net/HowToTremoloPickFastOnGuitar.aspx"},
    {"name": "referencia_16avos_120bpm_ms", "value": 125, "source": "https://impactsoundworks.com/docs/Shreddage%203%20Stratus%20Free%20Manual.pdf"},
    {"name": "musiclab_humanizacao_de_tremolo_ms", "range": [0, 100], "source": "slider Tremolo/Trill, https://www.musiclab.com/assets/files/RealLPC.pdf p.96"},
    {"name": "delta_velocity_down_vs_up", "source": null},
    {"name": "teto_de_bpm_para_downpicking_sustentado", "source": null}
  ],
  "tools": {
    "generic": {"formula": "notas_por_segundo = (BPM / 60) * subdivisoes_por_tempo"},
    "musiclab_reallpc": {"fx": [13, 14], "duration_map": {"0": "4th", "1": "4T", "2": "8th", "3": "8T", "4": "16th", "5": "16T", "6": "32nd", "7": "32T", "8": "64th", "9": "64T"}, "note": "sincronizado ao host"},
    "shreddage3": {"parametro": "Tremolo Speed", "picking_mode": "Alternate", "anti_repetition": "ligar com probabilidade ajustavel"},
    "ample": {"note": "nao localizei articulacao de tremolo dedicada na tabela oficial de keyswitches"}
  }
}
```

---

## 9. Dive bomb

Baixar a alavanca para derrubar a altura de uma nota sustentada.

**Este é o bloco mais pobre do manual, e é honesto dizer isso.** A Wikipedia descreve o efeito e
**não quantifica** a queda. A citação comum de 12–24 semitons aparece só em blog sem metodologia.
Profundidade, duração e formato da curva: todos sem fonte.

O que é sólido é a mecânica MIDI, e ela tem uma armadilha: **o range padrão de ±2 semitons não alcança
um dive**. É obrigatório enviar RPN `00 00` com Data Entry MSB ≥ à profundidade desejada **antes** de
começar. Sem isso, o dive vira um bendinho de um tom.

O vocabulário notacional a renderizar está documentado pelo MuseScore: **dive**, **pre-dive**
(alavanca acionada antes do ataque), **dip** (uma descida-subida), **scoop** (movimento leve para
baixo), **hold line**, **slack line**
([MuseScore](https://handbook.musescore.org/idiomatic-notation/guitar/guitar-bends), OFICIAL).

> **Não usar o `FeedBacker` da MusicLab como substituto.** Ele tem seleções harmônicas (8, 5', 8', 5'',
> 8'', 5''') e é efeito adjacente, não dive bomb.

```technique
{
  "name": "dive_bomb",
  "family": "guitar",
  "summary": "Alavanca derruba a altura de uma nota sustentada por um intervalo largo. Encerramento de frase.",
  "verified": true,
  "description": "O RANGE PADRAO NAO ALCANCA. E obrigatorio enviar RPN 00 00 com Data Entry MSB maior ou igual a profundidade desejada ANTES de comecar a rampa; sem isso o dive vira um bend de um tom. Retornar o pitch bend a 8192 depois do Note Off. Pitch bend de canal derruba TODAS as notas soando: dive so faz sentido em nota unica, ou o acorde inteiro desce junto. Profundidade em semitons, duracao e formato da curva NAO TEM FONTE — a Wikipedia descreve o efeito e explicitamente nao quantifica. Nenhum dos tres plugins verificados tem keyswitch dedicado; o caminho e pitch bend cru. NAO usar o FeedBacker da MusicLab como substituto: e efeito adjacente.",
  "parameters": [
    {"name": "rpn_para_ampliar_range", "value": "RPN 00 00, Data Entry MSB = semitons", "source": "https://studiocode.dev/kb/MIDI/midi-pitch-bend/"},
    {"name": "valor_pb_no_fundo_com_range_12", "value": 0, "source": "aritmetica da spec: -12 semitons com range 12"},
    {"name": "profundidade_semitons", "source": null},
    {"name": "duracao_ms", "source": null},
    {"name": "formato_da_curva", "source": null}
  ],
  "tools": {
    "generic": {"sequencia": ["RPN 00 00 + Data Entry MSB >= profundidade", "Note On", "rampa de PB para baixo", "Note Off", "PB 8192"], "figuras": ["dive", "pre-dive", "dip", "scoop", "hold line", "slack line"]},
    "ample": {"note": "sem keyswitch dedicado nas tabelas oficiais"},
    "shreddage3": {"note": "sem keyswitch dedicado nas tabelas oficiais"},
    "musiclab_reallpc": {"note": "sem keyswitch dedicado; FeedBacker NAO e substituto"}
  }
}
```

---

## 10. Dead notes

A mão do braço abafa a corda e a palheta ataca: sai só o transiente, quase sem altura. É o que
preenche os espaços entre chugs, **porque a mão da palheta nunca para**.

Shreddage chama isso de `Choke`: "Quick, short strums across all strings, muted to the point where
there is **very little pitch**" (OFICIAL).

### A pegadinha da MusicLab

No RealLPC, a distinção cheio/abafado nas Repeat zones é **posicional**, não de velocity: tecla branca
repete o som cheio, tecla preta repete o abafado. Programar isso como velocity naquele plugin
simplesmente não funciona ([RealLPC p.17](https://www.musiclab.com/assets/files/RealLPC.pdf),
OFICIAL).

A ausência de dead notes é o sinal mais forte de riff programado. Mas **densidade e velocity não têm
fonte** — não inventar.

```technique
{
  "name": "dead_notes",
  "family": "guitar",
  "summary": "Corda abafada pela mao do braco, atacada pela palheta: so o transiente. Preenche os espacos entre chugs.",
  "verified": true,
  "description": "A AUSENCIA de dead notes e o sinal mais forte de riff programado — a mao da palheta de um guitarrista nunca para, entao as subdivisoes vazias entre chugs carregam transiente. Mas densidade e velocity NAO TEM FONTE: derive do contexto ritmico, nao de constante inventada. PEGADINHA da MusicLab: nas Repeat zones a distincao cheio/abafado e POSICIONAL (tecla branca vs preta), nao de velocity — programar por velocity naquele plugin nao funciona.",
  "parameters": [
    {"name": "faixa_de_velocity", "source": null},
    {"name": "gate_ms", "source": null},
    {"name": "densidade_entre_chugs", "source": null}
  ],
  "tools": {
    "generic": {"note": "nota curta de baixa velocity nas subdivisoes vazias entre chugs"},
    "ample": {"scratch": 89, "silent_press": 91, "silent_stroke": 92, "nomes": {"89": "F5", "91": "G5", "92": "G#5"}},
    "shreddage3": {"articulacao": "Choke"},
    "musiclab_reallpc": {"fx": [1, 2, 35], "stroke_map_abafados": {"20": "Muted Upstrum", "19": "Muted Downstrum", "7": "Muted Top Upstrum", "6": "Muted Top Downstrum"}, "note": "nas Repeat zones a distincao e posicional: tecla branca = cheio, tecla preta = abafado"}
  }
}
```

---

## 11. Harmônicos naturais

Tocar de leve sobre um nó da série harmônica e atacar. Esta é a tabela mais bem fundamentada do
manual inteiro, porque é física
([série harmônica](https://en.wikipedia.org/wiki/Harmonic_series_(music)), MEDIDO):

| Casa do nó | Parcial | Razão | Semitons acima da solta | Desvio do temperamento igual |
|---|---|---|---|---|
| 12 | 2 | 2:1 | **+12** | 0 cents |
| 7 (e 19) | 3 | 3:1 | **+19** | +2 cents |
| 5 | 4 | 4:1 | **+24** | 0 cents |
| 4 | 5 | 5:1 | **+28** | **−14 cents** |
| 3,2 | 6 | 6:1 | **+31** | +2 cents |
| 2,7 | 7 | 7:1 | **+34** | **−31 cents** |
| 2,3 | 8 | 8:1 | **+36** | 0 cents |

Só as casas **12, 7 e 5** são confiáveis em todas as cordas. E os desvios em cents importam de
verdade se o harmônico for dobrado por outro instrumento: o parcial 5 está 14 cents **abaixo** e o
parcial 7 está 31 cents **abaixo** do temperamento igual. Dobrar isso com um sintetizador afinado
produz batimento.

```technique
{
  "name": "natural_harmonics",
  "family": "guitar",
  "summary": "Tocar de leve sobre um no da serie harmonica: soa um parcial agudo e puro, sem a fundamental.",
  "verified": true,
  "description": "Em patch generico, escrever a nota SOANTE = MIDI da corda solta + o intervalo do parcial. So as casas 12, 7 e 5 sao confiaveis em TODAS as cordas; nos mais altos existem mas nao em toda corda. Os desvios em cents importam se o harmonico for dobrado por outro instrumento: o parcial 5 esta 14 cents abaixo e o parcial 7 esta 31 cents abaixo do temperamento igual, e dobrar com sintetizador afinado produz batimento. Faixa de velocity e gate NAO TEM FONTE.",
  "parameters": [
    {"name": "casa_12_semitons", "value": 12, "source": "razao 2:1, https://en.wikipedia.org/wiki/Harmonic_series_(music)"},
    {"name": "casa_7_semitons", "value": 19, "source": "razao 3:1, +2 cents do temperamento igual, idem"},
    {"name": "casa_5_semitons", "value": 24, "source": "razao 4:1, idem"},
    {"name": "casa_4_semitons", "value": 28, "source": "razao 5:1, -14 cents do temperamento igual, idem"},
    {"name": "parcial_7_semitons", "value": 34, "source": "razao 7:1, -31 cents do temperamento igual, idem"},
    {"name": "faixa_de_velocity", "source": null},
    {"name": "gate_ms", "source": null}
  ],
  "tools": {
    "generic": {"note": "nota soante = corda solta + intervalo do parcial; casas confiaveis: 12, 7, 5"},
    "ample": {"keyswitch": 25, "keyswitch_nome": "C#0", "range_tocavel": [52, 84], "regra": "velocity < 127 = Natural, velocity 127 = Artificial"},
    "musiclab_reallpc": {"fx": [3, 57], "fx_nomes": ["Harmonics", "MelHarmonics"]},
    "shreddage3": {"note": "presente nas versoes full; nao listado no manual do Stratus FREE"}
  }
}
```

---

## 12. Rake e pick scrape — duas coisas diferentes

Não misturar. Rake é ênfase antes de uma nota; scrape é um evento de ruído independente.

### 12.1 Rake

Arrastar a palheta por cordas abafadas imediatamente **antes** da nota alvo, gerando cliques que dão
ênfase. Definição de fabricante: "dragging the pick across one or more muted strings leading up to
sounding a note" ([Sweetwater](https://www.sweetwater.com/insync/pick-rake/), OFICIAL). Quantas
cordas: "the two to four strings below the note you're intending to play are muted"
([TrueFire](https://truefire.com/guitar-lessons/60-electric-guitar-techniques-you-must-know/rake-demonstration/v40337),
CONVENÇÃO).

Shreddage confirma a ordem: "If the RAKE articulation is enabled and playable in TACT, it will play
**before** a sustain articulation. The **Time** control sets the delay between the rake sample and the
following sustain sample". O parâmetro existe; **a faixa não está publicada**.

```technique
{
  "name": "rake",
  "family": "guitar",
  "summary": "Arrastar a palheta por cordas abafadas logo ANTES da nota alvo. Da enfase por transiente, nao por velocity.",
  "verified": true,
  "description": "O rake vem ANTES da nota, nao junto: em Shreddage o sample de rake toca antes do sustain, e o controle Time define o intervalo. Em patch generico, isso significa notas curtas de baixa velocity nas cordas imediatamente mais graves, terminando no ataque da nota alvo. O delay em ms entre rake e nota, e a velocity das cordas abafadas, NAO TEM FONTE — o parametro existe na Shreddage mas a faixa nao esta publicada.",
  "parameters": [
    {"name": "cordas_abafadas", "range": [2, 4], "source": "CONVENCAO — curso instrucional, https://truefire.com/guitar-lessons/60-electric-guitar-techniques-you-must-know/rake-demonstration/v40337"},
    {"name": "delay_entre_rake_e_nota_ms", "source": null},
    {"name": "velocity_das_cordas_abafadas", "source": null}
  ],
  "tools": {
    "generic": {"note": "notas curtas e fracas nas cordas imediatamente mais graves, terminando no ataque da nota alvo"},
    "shreddage3": {"articulacao": "RAKE", "controle": "Time", "note": "toca antes da articulacao de sustain"}
  }
}
```

### 12.2 Pick scrape

Arrastar a palheta **no sentido do comprimento** de uma corda encapada.

Aqui há um detalhe de programação que vale mais que o resto: os ruídos de release da Shreddage
(`[REL] Pitched` e `[REL] Unpitched`) são disparados por **note-off**, não por note-on. A duração da
nota controla quando o ruído acontece — e **notas todas com o mesmo gate produzem ruídos de release em
grade perfeita**, o que é audivelmente artificial. É um vetor de artificialidade que a maioria
esquece, porque não está no note-on.

```technique
{
  "name": "pick_scrape",
  "family": "guitar",
  "summary": "Palheta arrastada no comprimento de uma corda encapada. Ruido de transicao, nao nota.",
  "verified": true,
  "description": "Scrape e evento independente, geralmente em transicao de secao — nao confundir com rake, que e enfase antes de uma nota. DETALHE QUE DENUNCIA PROGRAMACAO: os ruidos de release da Shreddage sao disparados por NOTE-OFF, entao a duracao da nota controla quando o ruido acontece. Notas todas com o mesmo gate produzem ruidos de release em grade perfeita, o que e audivelmente artificial. Duracao e velocity de um pick scrape NAO TEM FONTE.",
  "parameters": [
    {"name": "musiclab_variacoes_de_scrape", "value": 46, "source": "FX 6, controladas por notas MIDI 40-85 da Main zone, https://www.musiclab.com/assets/files/RealLPC.pdf p.57"},
    {"name": "duracao_ms", "source": null},
    {"name": "velocity", "source": null}
  ],
  "tools": {
    "generic": {"note": "evento de ruido em transicao de secao"},
    "ample": {"hit_top_open_pick_scrape": 101, "hit_top_mute": 102, "hit_rim": 103, "scratch": 89},
    "musiclab_reallpc": {"fx": [6, 53], "note": "FX 6 tem 46 scrapes mapeados em MIDI 40-85; FX 53 param 0-2 seleciona o grupo"},
    "shreddage3": {"articulacoes": ["[REL] Pitched", "[REL] Unpitched"], "note": "disparadas por NOTE-OFF — o gate controla o ruido"}
  }
}
```

---

## 13. Double tracking

O guitarrista grava a mesma parte duas vezes, uma para cada lado. **Duplicar a track MIDI não
reproduz isso**, e a razão é técnica, não estética: a cópia dispara os *mesmos samples*, é coerente em
fase. Somar dá +6 dB e nenhuma largura; atrasar dá comb filtering, não largura.

As duas libs melhor documentadas resolvem trocando **samples**, não tempo:

- MusicLab: o patch Double-track alimenta 2 saídas a partir de uma track MIDI, gerando parte duplicada
  real "with **no identical samples playing simultaneously in different channels**" (p.98–99);
- Shreddage: "Each virtual guitar uses a **different sequence of samples** during playback".

### A armadilha documentada

Shreddage avisa que, ao usar NKIs separados, é preciso "set the anti-repetition parameters to be
**identical** in each instance. Otherwise, **phasing will occur** as different NKIs may trigger the
same sample in differing track numbers". Ou seja: o instinto de "randomizar diferente em cada
instância" produz o problema que se queria evitar. A solução da Shreddage é multi-tracking interno,
não NKIs separados.

Truque oficial para engrossar: reduzir o knob `Tune` do Kontakt em ~−5 semitons e aumentar `Transpose`
na UI da S3 em +5 semitons — "the result is the same pitch but with a more robust tone".

```technique
{
  "name": "double_tracking",
  "family": "guitar",
  "summary": "Mesma parte gravada duas vezes, uma por lado. Duplicar a track MIDI NAO reproduz isso — a copia e coerente em fase.",
  "verified": true,
  "description": "NUNCA duplicar a track MIDI para uma segunda instancia com o mesmo estado de round robin: a copia dispara os mesmos samples, soma da +6 dB e nenhuma largura, e atrasar da comb filtering em vez de largura. Se o instrumento tem multi-tracking, USE O RECURSO — as duas libs documentadas resolvem trocando SAMPLES, nao tempo. ARMADILHA: a Shreddage avisa que anti-repetition DIFERENTE entre NKIs separados causa fase, o oposto do instinto; a solucao dela e multi-tracking interno. Offset de timing, de velocity e detune entre duas takes humanas reais NAO TEM FONTE.",
  "parameters": [
    {"name": "musiclab_double_trk_delay_pct", "range": [0, 400], "source": "slider do Timing panel, https://www.musiclab.com/assets/files/RealLPC.pdf p.53"},
    {"name": "shreddage3_truque_de_engrossar_semitons", "value": 5, "source": "Tune do Kontakt -5 e Transpose da S3 +5, https://impactsoundworks.com/docs/Shreddage%203%20Stratus%20Free%20Manual.pdf"},
    {"name": "offset_de_timing_entre_takes_reais_ms", "source": null},
    {"name": "offset_de_velocity_entre_takes", "source": null},
    {"name": "detune_em_cents_para_doubling", "source": null}
  ],
  "tools": {
    "generic": {"note": "sem multi-tracking na lib, o caminho honesto e duas execucoes MIDI diferentes, nao uma copia"},
    "musiclab_reallpc": {"patch": "Double-track", "workflow": "versao multi-out, pan hard L/R no mixer da DAW, amps e efeitos diferentes por canal"},
    "shreddage3": {"recurso": "Multi-Tracking", "aviso": "com NKIs separados, anti-repetition tem que ser IDENTICO nos dois, senao ocorre fase"}
  }
}
```

---

## 14. Voicing de guitarra não é voicing de teclado

Esta é a única restrição de guitarra deste manual que tem **artigo acadêmico** por trás: "A playable
chord voicing requires **at most one pitch per string** and all non-open string pitches must fall
within a window of **6 frets**, as finger stretching over 6 frets is not acceptable"
([arXiv 2510.10619](https://arxiv.org/pdf/2510.10619), MEDIDO).

Some-se o espaçamento das cordas — 5 semitons entre todas, exceto 4 entre G3 e B3 — e saem três regras
que explicam por que tríade de teclado soa errada em patch de guitarra:

- Duas notas a um semitom ou um tom de distância na mesma oitava são geralmente **impossíveis**:
  exigiriam a mesma corda, ou cordas adjacentes em casas incompatíveis.
- Voicings de guitarra contêm naturalmente **4ªs e 5ªs**, não terças empilhadas — é a grade
  5/5/5/4/5 que produz isso.
- Direção do strum: para baixo começa pela corda grave, para cima pela aguda (implícito no Stroke Map
  da MusicLab, OFICIAL).

```technique
{
  "name": "chord_voicing",
  "family": "guitar",
  "summary": "Uma altura por corda, e as notas pisadas dentro de uma janela de 6 casas. E o que separa voicing de guitarra de voicing de teclado.",
  "verified": true,
  "description": "Um voicing so e valido se cada altura puder ser atribuida a uma corda distinta com altura >= a da corda solta, e as notas pisadas couberem numa janela de 6 casas. Duas notas a um semitom ou um tom de distancia na mesma oitava sao geralmente IMPOSSIVEIS na guitarra — e por isso que triade fechada de teclado soa errada em patch de guitarra. Voicings de guitarra contem naturalmente quartas e quintas, nao tercas empilhadas, por causa da grade de 5/5/5/4/5 semitons entre as cordas. Maximo de notas simultaneas = numero de cordas. Direcao do strum: para baixo comeca pela corda grave, para cima pela aguda.",
  "parameters": [
    {"name": "janela_maxima_de_casas", "value": 6, "source": "https://arxiv.org/pdf/2510.10619 — artigo academico"},
    {"name": "alturas_por_corda", "value": 1, "source": "idem"},
    {"name": "espacamento_entre_cordas_padrao_semitons", "value": [5, 5, 5, 4, 5], "source": "derivado de E2 A2 D3 G3 B3 E4, https://en.wikipedia.org/wiki/Guitar_tunings"},
    {"name": "musiclab_strum_time_ms", "range": [5, 200], "source": "Timing panel, https://www.musiclab.com/assets/files/RealLPC.pdf p.53"},
    {"name": "musiclab_slow_strum_time_ms", "range": [45, 300], "source": "idem"},
    {"name": "estagiamento_de_power_chord_palhetado", "source": null}
  ],
  "tools": {
    "generic": {"note": "validar cada voicing contra a afinacao declarada antes de escrever"},
    "musiclab_reallpc": {"note": "notas fora do alcance da corda sao tocadas na corda mais proxima — o resultado nao e o que foi escrito", "cc_strum_time": 56},
    "shreddage3": {"fretting_modes": ["Natural", "Sweep", "Moving Lead", "Polyphonic"], "note": "Hand Size define o alcance; nota fora dele obriga a mao a mudar de posicao"}
  }
}
```

---

## 15. Direção da palhetada

Shreddage expõe quatro modos, e o `Economy` merece atenção porque descreve um comportamento real que
ninguém programa à mão: "When changing a string, stroke direction that was used on the last note
played on the previous string is preserved for the first note played on the new string, then
alternates from there".

**Regra:** em lib que tem esse parâmetro, escolher o Picking Mode em vez de tentar simular o padrão
por velocity — o motor já tem pools de samples separados por direção. Em libs sem o parâmetro, o
padrão de velocity a usar **não tem fonte**.

```technique
{
  "name": "picking_direction",
  "family": "guitar",
  "summary": "Golpe para baixo e para cima sao samples diferentes. Em lib que tem Picking Mode, escolher o modo vence simular por velocity.",
  "verified": true,
  "description": "Em Shreddage, escolher o Picking Mode em vez de tentar expressar a direcao por velocity: o motor tem pools de samples separados por direcao (Downstroke Offset e Upstroke Offset sao parametros independentes). Economy Mode descreve um comportamento real que ninguem programa a mao — ao trocar de corda, a direcao do ultimo golpe da corda anterior e preservada no primeiro golpe da nova, e so entao alterna. Em libs sem esse parametro, o padrao de velocity que expressa down-picking vs alternate NAO TEM FONTE. Teto de BPM para down-picking sustentado tambem nao.",
  "parameters": [
    {"name": "delta_volume_down_vs_up", "source": null},
    {"name": "padrao_de_velocity_down_vs_alternate", "source": null},
    {"name": "teto_de_bpm_para_downpicking", "source": null}
  ],
  "tools": {
    "generic": {"note": "sem parametro de direcao na lib, nao ha padrao de velocity com fonte para simular"},
    "shreddage3": {"picking_modes": ["Up", "Down", "Alternate", "Economy"], "note": "Downstroke Offset e Upstroke Offset sao independentes"},
    "musiclab_reallpc": {"stroke_map": {"17": "Full Downstrum", "18": "Full Upstrum", "19": "Muted Downstrum", "20": "Muted Upstrum", "21": "Slow Downstrum", "22": "Slow Upstrum"}}
  }
}
```

---

## 16. Afinação e registro

Âncora: afinação padrão **E2–A2–D3–G3–B3–E4**, com E2 = 82,41 Hz
([Guitar tunings](https://en.wikipedia.org/wiki/Guitar_tunings), OFICIAL) e E2 = MIDI 40 (SPN,
MEDIDO). As linhas marcadas *derivado* são transposições aritméticas dessa âncora.

### 6 cordas

| Afinação | Notas | MIDI (grave→agudo) | Categoria |
|---|---|---|---|
| E padrão | E2 A2 D3 G3 B3 E4 | **40 45 50 55 59 64** | OFICIAL |
| Drop D | D2 A2 D3 G3 B3 E4 | **38 45 50 55 59 64** | OFICIAL |
| D padrão | D G C F A D | **38 43 48 53 57 62** | OFICIAL |
| Drop C | C G C F A D | **36 43 48 53 57 62** | OFICIAL |
| Drop C# | C# G# C# F# A# D# | **37 44 49 54 58 63** | derivado |
| Drop B | B F# B E G# C# | **35 42 47 52 56 61** | derivado |
| Drop A# | A# F A# D# G C | **34 41 46 51 55 60** | derivado |
| Drop A | A E A D F# B | **33 40 45 50 54 59** | derivado |

### Estendidas

| Afinação | Notas | MIDI | Categoria |
|---|---|---|---|
| 7 cordas, B padrão | B E A D G B E | **35 40 45 50 55 59 64** | OFICIAL |
| 7 cordas, Drop A | A E A D G B E | **33 40 45 50 55 59 64** | OFICIAL |
| 8 cordas, F# padrão | F# B E A D G B E | **30 35 40 45 50 55 59 64** | OFICIAL |
| 8 cordas, Drop E | E B E A D G B E | **28 35 40 45 50 55 59 64** | nome OFICIAL, notas derivadas |

Fonte das estendidas:
[Strandberg](https://strandbergguitars.com/en-WW/magazine/7-and-8-string-guitar-tunings-with-strandberg-).

### Alcances tocáveis

| Instrumento | Casas | Range MIDI |
|---|---|---|
| 6 cordas E padrão | 22 | 40–86 |
| 6 cordas E padrão | 24 | 40–88 |
| 6 cordas Drop C | 24 | 36–86 |
| 7 cordas B padrão | 24 | 35–88 |
| 8 cordas F# padrão | 24 | 30–88 |

> **A regra que mais importa aqui:** nunca gerar nota abaixo da corda solta mais grave da afinação
> declarada. Os motores não erram ruidosamente — MusicLab realoca para a corda mais próxima
> (p.25, OFICIAL) e Shreddage move a mão virtual (OFICIAL). Em ambos os casos toca, e em ambos os
> casos **não é o que foi escrito**.

Associação com gênero, com fonte: Drop D é documentado para heavy metal, hard rock, alternative rock,
grunge e alternative metal; Drop C, "because of its heavier tone, it is most commonly used in rock and
heavy metal music" (ambos OFICIAL, Wikipedia). Afinações típicas de **metalcore vs djent** não têm
fonte.

```technique
{
  "name": "drop_tuning",
  "family": "guitar",
  "summary": "A afinacao declarada define o piso absoluto de altura. Nota abaixo da corda solta mais grave nao existe no instrumento.",
  "verified": true,
  "description": "NUNCA gerar nota abaixo da corda solta mais grave da afinacao declarada. O erro e silencioso: MusicLab realoca para a corda mais proxima e Shreddage move a mao virtual, entao toca — mas nao e o que foi escrito. Chug vive nas 2 ou 3 cordas mais graves. Cada tupla em tools.generic.afinacoes e o conjunto de cordas solta, do grave para o agudo, em numero MIDI absoluto; o primeiro elemento e o piso. Afinacoes tipicas de metalcore vs djent NAO TEM FONTE.",
  "parameters": [
    {"name": "ancora_e2_midi", "value": 40, "source": "E2 = 82.41 Hz, https://en.wikipedia.org/wiki/Guitar_tunings + SPN"},
    {"name": "casas_tipicas", "range": [22, 24], "source": "derivado dos ranges tocaveis publicados"},
    {"name": "afinacoes_de_metalcore_vs_djent", "source": null}
  ],
  "tools": {
    "generic": {
      "afinacoes": {
        "e_padrao": [40, 45, 50, 55, 59, 64],
        "drop_d": [38, 45, 50, 55, 59, 64],
        "d_padrao": [38, 43, 48, 53, 57, 62],
        "drop_c": [36, 43, 48, 53, 57, 62],
        "drop_c_sharp": [37, 44, 49, 54, 58, 63],
        "drop_b": [35, 42, 47, 52, 56, 61],
        "drop_a_sharp": [34, 41, 46, 51, 55, 60],
        "drop_a": [33, 40, 45, 50, 54, 59],
        "sete_cordas_b": [35, 40, 45, 50, 55, 59, 64],
        "sete_cordas_drop_a": [33, 40, 45, 50, 55, 59, 64],
        "oito_cordas_f_sharp": [30, 35, 40, 45, 50, 55, 59, 64],
        "oito_cordas_drop_e": [28, 35, 40, 45, 50, 55, 59, 64]
      },
      "derivadas": ["drop_c_sharp", "drop_b", "drop_a_sharp", "drop_a", "oito_cordas_drop_e"]
    },
    "ample_metal": {"range_tocavel": [36, 84], "note": "ja parte de Drop C"},
    "musiclab_reallpc": {"note": "a 6a corda pode ser E1 (40), D1 (38) ou C1 (36) = padrao / Drop D / Drop C"},
    "shreddage3": {"capo_position": "redefine o conjunto de cordas soltas"}
  }
}
```

---

## 17. Onde a track de guitarra fica no tempo

Este não é gesto de execução, é decisão de arranjo — e tem fonte oficial, o que é raro neste manual.

Shreddage recomenda deslocar a track de guitarra **20–30 ms para trás** (offset negativo) numa mix de
rock/metal, e explica por quê: "drum transients hitting a master compressor will compress guitar notes
that arrive after them, whereas guitar transients that arrive before the compression has fully kicked
in will have a chance to be lively" (OFICIAL).

Duas armadilhas de latência, ambas oficiais:

- **Shreddage:** manter a Poly Input Latency em ou perto de 0 ms para leads rápidos. "16th notes at
  120 BPM are 125 ms apart. Playing passages at, or faster than, 120 BPM 16th notes at 125 ms latency
  will result in malfunctioning fretting selection." O piso interno é 50 µs, existindo para que
  keyswitch e voicing possam ficar **no mesmo tick** da nota — não é preciso empurrar keyswitch para
  trás da grade.
- **Ample:** Start Time 50 ms + Track Delay 0 ms para tocar ao vivo; Start Time 0 ms + Track Delay
  **−50 ms** para playback e export. A conversão é `Track Delay(beat) = Time(s) × Tempo / 60`; 50 ms a
  120 BPM = 0,1 beat = **48 ticks**.

```technique
{
  "name": "track_offset",
  "family": "guitar",
  "summary": "A track de guitarra anda para tras da grade numa mix de metal, para o transiente chegar antes do compressor fechar.",
  "verified": true,
  "description": "Deslocar a track inteira 20 a 30 ms para TRAS (offset negativo). O motivo e oficial: transiente de bateria batendo num compressor de master comprime a guitarra que chega depois, enquanto a guitarra que chega antes da compressao fechar sai viva. Se houver poly input latency, somar esse numero; se houver global sample offset, subtrair. NAO empurrar keyswitch para tras da grade em Shreddage: o piso interno de 50 microssegundos existe exatamente para que keyswitch e voicing possam ficar no mesmo tick da nota.",
  "parameters": [
    {"name": "offset_da_track_ms", "range": [-30, -20], "source": "https://impactsoundworks.com/docs/Shreddage%203%20Stratus%20Free%20Manual.pdf"},
    {"name": "shreddage3_piso_de_latencia_us", "value": 50, "source": "idem — 0.05 ms sempre aplicados"},
    {"name": "shreddage3_teto_de_latencia_para_leads_ms", "value": 0, "source": "idem — 16avos a 120 BPM distam 125 ms; latencia alta quebra a selecao de casa"},
    {"name": "ample_track_delay_para_export_ms", "value": -50, "source": "https://www.amplesound.net/en/Settings_and_CPCv2.pdf secao 1.3"},
    {"name": "ample_conversao_ticks_50ms_120bpm", "value": 48, "source": "idem — Track Delay(beat) = Time(s) * Tempo / 60"},
    {"name": "desvio_de_timing_medido_em_guitarristas_ms", "source": null},
    {"name": "aperto_da_guitarra_de_metal_depois_da_edicao_ms", "source": null}
  ],
  "tools": {
    "generic": {"note": "offset negativo de 20 a 30 ms na track inteira"},
    "shreddage3": {"note": "keyswitch e voicing no MESMO tick da nota; nao antecipar manualmente"},
    "ample": {"live": {"start_time_ms": 50, "track_delay_ms": 0}, "export": {"start_time_ms": 0, "track_delay_ms": -50}}
  }
}
```

---

## 18. Humanização — o que a MusicLab publica

A MusicLab é o único fabricante dos três que publica **faixas numéricas** de humanização
([RealLPC p.95–96](https://www.musiclab.com/assets/files/RealLPC.pdf), OFICIAL). São faixas máximas de
slider, não medições de performance — mas são a única referência oficial que existe para a ordem de
grandeza.

| Parâmetro | Faixa máxima |
|---|---|
| Attack Time | 0–50 ms |
| Stroke Time (atraso de note-on) | 0–100 ms |
| Strum Time (janela entre notas simultâneas) | 0–200 ms |
| Tremolo/Trill | 0–100 ms |
| Pitch (todos os sons, e mute/bridge mute) | 0–50 cents |

Cada parâmetro tem um segundo slider de sensibilidade 0–100%: "at 100% all playing notes will be
affected".

Dois recursos que descrevem **erro humano**, e valem como vocabulário de realismo mesmo em outra lib:
`Random Mutes` troca Sustain por Mute aleatoriamente, "simulates the effect of a guitarist accidentally
not pressing the string hard enough"; `Resonance` dispara a corda solta adjacente à tocada, "simulates
the effect of a guitarist accidentally striking or resonating strings".

E o fato que amarra tudo: **velocity seleciona samples, não só volume.** Ample documenta que "Different
samples are used for different velocity layers", e que Velocity Sensitivity = 0 desacopla loudness de
velocity. Velocity chapada reutiliza **um** sample e soa mecânica *independentemente* de qualquer
randomização de volume aplicada depois. A direção está provada; **a faixa concreta de variação não tem
fonte** — não inventar.

---

## Lacunas

O que foi procurado e não achado com fonte utilizável. Nada aqui foi preenchido com estimativa.

### Números de técnica

| Lacuna | O que foi procurado |
|---|---|
| Velocity de chug em guitarra que soa humana | programação de MIDI de guitarra em metal, djent, metalcore. Só existe número para **baixo** — adjacente, não transposto |
| Gate absoluto do chug em ms, e razão gate/step apertado vs solto | idem. A única fonte com números era a envenenada de §0.4 |
| Diferença de velocity e gate entre power chord aberto e abafado | — |
| Duração medida de um bend real, e formato da curva | análise de trajetória de pitch; só há modelos de deslocamento físico, não timing |
| Hold e release do pre-bend | — |
| Atraso de início do vibrato em ms | mecanismo documentado (estágio "Start" da Ample), valor não publicado |
| Rate e depth de vibrato medidos em guitarristas de metal | há estudo de vibrato em cantores e cordas eruditas — adjacente, **não usado** |
| Sobreposição em ms para disparar legato | a fonte diz apenas "a few milliseconds" |
| Quanto reduzir a velocity de hammer-on/pull-off | direção documentada, valor não |
| Velocity entre golpe para baixo e para cima | — |
| Teto de BPM para down-picking sustentado | — |
| Profundidade e duração de dive bomb | Wikipedia explicitamente não quantifica; a citação de 12–24 semitons vem de blog sem metodologia |
| Keyswitch de dive/whammy em qualquer um dos três plugins | não consta nas tabelas oficiais obtidas |
| Velocity, gate e densidade de dead notes | — |
| Velocity e gate de harmônico natural | — |
| Delay em ms entre rake e nota | o parâmetro existe na Shreddage, a faixa não é publicada |
| Duração e velocity de pick scrape | — |
| Estagiamento de power chord palhetado (não strummed) | as faixas de strum time achadas são para acordes strummed |
| Qual parcial os guitarristas de metal mais atingem em pinch harmonic | — |

### Números de realismo

| Lacuna | O que foi procurado |
|---|---|
| Offset de timing entre duas execuções humanas em double tracking | double tracking, quad tracking, ADT |
| Offset de velocity entre as duas takes, e detune para doubling artificial | — |
| Limiar de comb filtering (<10 ms) e de Haas (~20–30 ms) | muito citados, sem fonte primária obtida |
| Delay em que double tracking vira slapback | — |
| Desvio de microtiming medido em guitarristas profissionais | estudo acadêmico de precisão de timing |
| Quão apertada é a guitarra de metal depois da edição, em ms | a [discussão profissional](https://gearspace.com/threads/beat-detective-how-to-use-properly.441836/) trata como decisão de ouvido, sem tolerância numérica |
| Defaults de humanize das DAWs (Logic, Cubase, Reaper, Ableton) | — |
| Limite inferior útil de guitarra distorcida antes de colidir com o baixo | — |
| Mecanismo de distorção de intermodulação para "sem terças no grave" | o efeito está documentado; o mecanismo nomeado não |
| Afinações típicas de metalcore vs djent | há associação de gênero para Drop D, Drop C e Drop C#; nada específico de djent |

### Plugins não cobertos

| Plugin | Status |
|---|---|
| Native Instruments Session Guitarist | não pesquisado |
| Orange Tree Samples Evolution | não pesquisado |
| Prominy SC Electric Guitar / LPC; Vir2 Electri6ity | não pesquisado |
| Shreddage 3 — tabela completa de keyswitch por articulação | vive no manual do TACT, PDF separado, não obtido |
| Shreddage 3 — números MIDI absolutos dos keyswitches de strum | só os nomes de nota estão publicados, e eles não fecham com um range de 6 cordas em nenhuma das duas convenções de oitava. **Confirmar no Kontakt antes de programar** |
| Shreddage 3 — ranges tocáveis por instrumento (Hydra 8 cordas, Abyss) | não encontrado |
