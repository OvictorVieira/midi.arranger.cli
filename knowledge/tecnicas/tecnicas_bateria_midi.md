# Técnicas de bateria em MIDI — manual de execução

> **Para que serve este documento.** A IA decide *o que* a música precisa. Este arquivo diz *como fazer
> aquilo em MIDI*: que nota, que velocity, que offset, que CC. Toda receita aqui é acionável — pensada
> tanto para gerar uma levada do zero quanto para **acrescentar intenção a um MIDI que já existe e está
> chapado**.
>
> **Ferramentas-alvo:** Superior Drummer 3 e Addictive Drums 2. Todo item traz o fallback em General MIDI.
>
> **Regra de fonte.** Número com fonte vem citado. Número sem fonte vem marcado `[NÃO VERIFICADO]` — use,
> mas confira de ouvido antes de tratar como lei. Nunca apresente um `[NÃO VERIFICADO]` ao usuário como fato.

---

## 1. Mapas MIDI

### 1.1 Aviso que evita o erro mais caro

**AD2 e General MIDI divergem de forma destrutiva acima da nota 40.** Todo o bloco de hi-hat do AD2
ocupa o espaço que o GM reserva para crash, ride e tom. Um arquivo GM tocado num AD2 mal mapeado vira
lixo. O SD3, ao contrário, é largamente compatível com GM no kit central.

| Nota | General MIDI | Addictive Drums 2 | Superior Drummer 3 |
|---|---|---|---|
| 36 | Bass Drum 1 | **Kick** | **Kick Hit** |
| 37 | Side Stick | Snare Rimshot | Snare Sidestick |
| 38 | Acoustic Snare | **Snare Open Hit** | **Snare Center** |
| 40 | Electric Snare | Snare Open Hit (dbl) | Snare Rimshot |
| **42** | **Closed Hi-Hat** | **Snare SideStick** | **HH Closed Tip** |
| **44** | **Pedal Hi-Hat** | **Snare RimClick** | **HH Closed Pedal** |
| **46** | **Open Hi-Hat** | **Cymbal 1 Hit (dbl)** | **HH Open Edge 2** |
| **49** | **Crash 1** | **HH Closed1 Tip** | Cymbal 2 Crash |
| 51 | Ride 1 | HH Closed2 Tip | **Ride Bow Tip** |
| 53 | Ride Bell | HH Closed Bell | **Ride Bell Shank** |
| 57 | Crash 2 | HH Open D | Cymbal 4 Crash |

**Antes de gerar qualquer nota, o plano precisa declarar a ferramenta-alvo.** Sem isso, gere em GM e
avise que o mapa terá que ser convertido.

Fontes: [AD2 Keymap oficial](https://support.xlnaudio.com/hc/en-us/articles/16925247222045-Addictive-Drums-2-Keymap) ·
[GM Percussion Key Map](https://www.cs.cmu.edu/~music/cmp/archives/cmsip/readings/GMSpecs_PercMap.htm) ·
SD3: mapa padrão extraído do próprio plugin ([dump em fórum Steinberg](https://forums.steinberg.net/t/superior-drummer-3-drum-map/134328)) — expansões SDX divergem do Core, confira em `Settings > MIDI In/E-Drums`.

### 1.2 Superior Drummer 3 — notas de trabalho

| Peça | Notas | Observação |
|---|---|---|
| Kick | 35, 36 | |
| Caixa centro | 38 | o hit padrão |
| Caixa sidestick | 37, 76 | |
| Caixa rimshot | 40 | reserve para o acento mais forte |
| **Caixa flam** | **69** | articulação gravada de verdade — prefira a montar flam na mão |
| **Caixa ruff/drag** | **39** | idem |
| Caixa rim only | 71 | |
| HH fechado tip | 11, 42, 61 | |
| HH fechado edge | 22 | use no downbeat |
| HH fechado pedal | 10, 21, 44 | o "chick" do pé |
| HH aberto tip | 12–17 | graus 0 a 5 |
| HH aberto edge | 24–26, 46, 60, 64 | graus 1 a 4 |
| HH tight | 62 (edge), 63 (tip) | |
| HH open pedal | 23 | splash de pé |
| Toms | 41, 43 (surdos) · 45, 47, 48 (tons) | rimshot em 72–82 |
| Ride | 51 (bow tip), 53 (bell shank), 116 (bow shank), 59 (crash) | |
| Crashes | 49, 52, 55, 57 | |
| Mute/choke | 50, 54, 56, 58, 83, 94, 95, 106, 107, 118 | |

**Uma articulação pode legitimamente ocupar várias notas** no SD3 — é comportamento documentado, não
erro de mapa. Isso é útil: alternar entre as notas equivalentes emula as duas mãos e evita roubo de voz.

### 1.3 Addictive Drums 2 — notas de trabalho

| Peça | Notas |
|---|---|
| Kick | 36 |
| Caixa open / rimshot / sidestick / rimclick / shallow | 38 / 37 / 42 / 44 / 43 |
| Caixa alternadas ("dbl") | 39 (rimshot), 40 (open) |
| HH pedal closed / pedal open | 48 / 59 |
| HH closed tip / shaft / bell | 49, 51 / 50, 52 / 53 |
| HH open A→D (menos → mais aberto) | 54, 55, 56, 57 |
| Toms 4→1 | 65, 67, 69, 71 (rimshot em 66, 68, 70, 72) |
| Ride 1 tip / bell / shaft / choke | 60 / 61 / 62 / 63 |
| Cymbals 1–6 hit | 77, 79, 81, 89, 91, 93 (choke = hit + 1) |

**AD2 não tem flam, drag nem buzz roll como articulação.** Tem que montar na mão — ver §3.

### 1.4 Hi-hat: abertura por nota, não por CC

Para programação em DAW, **as duas ferramentas resolvem abertura pelo número da nota**, e é esse o
caminho a usar. O CC4 existe para bateria eletrônica e é ignorado quando você endereça as notas de grau
diretamente.

| | AD2 | SD3 |
|---|---|---|
| CC do pedal | CC4 (configurável, + CC secundário) | CC4 e CC1 (ambos por padrão) |
| Polaridade | não documentada | **0 = totalmente aberto, 127 = totalmente fechado** |
| Notas que dependem de CC | 7 (shaft), 8 (tip), 9 (bell) | 7, 8, 9 e 18, 19, 20 |
| Limiares CC → grau | **não publicados** `[NÃO VERIFICADO — conferir no plugin]` | **não publicados** `[NÃO VERIFICADO]` |

Nos MIDIs de referência do usuário **não há CC4 nenhum** — a abertura vem toda por nota. Siga esse padrão.

---

## 2. Como acrescentar intenção a uma levada que já existe

Esta seção é o caminho principal quando o usuário chega com um MIDI chapado e pede para melhorar.

### 2.1 Ordem de aplicação

Aplique nesta ordem. Trocar a ordem produz resultado pior porque cada passo assume o anterior.

1. **Hierarquia de acento** — antes de qualquer ornamento, estabeleça o que é forte e o que é fraco.
2. **Ghost notes** — só depois que existe hierarquia, senão a ghost não contrasta com nada.
3. **Diferenciação de articulação** — edge vs tip no hat, bow vs bell no ride.
4. **Flam / ruff** — nos pontos de chegada.
5. **Microtiming** — por último, porque depende de saber quem é âncora e quem é ornamento.

### 2.2 Passo 1 — hierarquia de acento

Se a levada chega com tudo em 127 (caso comum), a primeira coisa é redistribuir. Alvos:

| Camada | Velocity | Onde |
|---|---|---|
| Acento | 105–120 | backbeat da caixa (tempos 2 e 4), crash de chegada, acento de virada |
| Primario | 100–115 | bumbo em tempo forte (1 e 3), ride bell |
| Normal | 80–100 | bumbo secundario off-beat, ride, hi-hat no tempo |
| Suave | 55–79 | hi-hat contratempo, tom de preenchimento |
| Ghost | 20–45 | ghosts de caixa |

**Nunca escreva 127.** O teto é ~115: a camada mais alta da biblioteca é o hit mais duro e mais
comprimido, e usá-la em tudo apaga a hierarquia e não deixa headroom para um acento verdadeiro.

Fontes: [Toontrack — How to program drums](https://www.toontrack.com/blog/how-to-program-drums/) (kick primário 100–115, secundário 75–95; caixa 100–120; ghost 20–45) ·
[Audient](https://audient.com/tutorial/programming-realistic-drums/) (teto ~115).

**Por que velocity fixa soa falso, mecanicamente.** O SD3 tem por volta de 25 camadas de velocity
distribuídas em 127 valores — cerca de 5 unidades por camada. Programar tudo em 100 faz todo hit cair na
mesma camada, no mesmo trim de volume, sorteando do mesmo pool de round robin: você ouve o pool ciclar e
a hierarquia colapsa num timbre só. Mover ±5 a ±10 troca a *amostra*, não só o ganho — uma caixa em 95 e
uma em 110 são gravações fisicamente diferentes. É esse o payload inteiro de uma biblioteca multi-sampled.

Fontes: SD3 manual §3.5.12 ("There are 127 possible MIDI velocities but there are not 127 velocity
layers… samples from the appropriate layer are adjusted in volume") · AD2 manual (round robin evita o
"shotgun effect").

### 2.3 Passo 2 — inserir ghost notes numa levada que não tem

Receita direta:

1. Localize os backbeats da caixa (nota 38 nos tempos 2 e 4, ou onde a levada os colocar).
2. Candidatos a ghost: as semicolcheias **entre** os backbeats.
3. **Descarte a semicolcheia imediatamente anterior a um backbeat** — fisicamente desconfortável para
   um baterista real.
4. **Descarte ghosts consecutivas logo depois do backbeat.**
5. Insira **uma ghost isolada ou um par**. Três ou mais 16ªs de ghost seguidas leem como fake.
6. Velocity 20–45. Acima disso deixa de ser ghost e vira hit normal audível.
7. Ghost em 32ªs só em andamento lento.
8. Timing: 3–8 ms atrasado para sensação laid-back, 2–5 ms adiantado para urgência. É o elemento mais
   solto do kit.

Fontes: [Toontrack](https://www.toontrack.com/blog/how-to-program-drums/) (regras de posição e
20–45) · [Sample Focus](https://blog.samplefocus.com/blog/how-to-produce-ghost-notes-for-organic-drums/) (timing).

Densidade por gênero: `[NÃO VERIFICADO — sem fonte; derive do perfil de estilo pesquisado]`.

### 2.4 Passo 3 — diferenciar articulação

Levada chapada costuma usar uma nota só por peça. Diferenciar é ganho grande e barato.

| Peça | Não acentuado | Acentuado | Fonte |
|---|---|---|---|
| Hi-hat | contratempo com **tip** (SD3 42 · AD2 49/51) | downbeat com **edge/shaft** (SD3 22 · AD2 50/52) | Toontrack |
| Ride | **bow tip** (SD3 51 · AD2 60) | **bow shank** (SD3 116 · AD2 62); **bell** (SD3 53 · AD2 61) para levantar | Toontrack |
| Caixa | centro (38) | **rimshot** (SD3 40 · AD2 37) — só no backbeat mais forte; rimshot em tudo mata o contraste | Toontrack |

### 2.5 Passo 4 — flam

| Parâmetro | Valor | Fonte |
|---|---|---|
| Intervalo graça → principal | **8–15 ms** | [Moozix](https://moozix.com/blog/before-you-program-another-beat-learn-these-humanizing-secrets-697ab16d79e43) |
| Qual é a mais forte | a **segunda** (a principal) | [Audient](https://audient.com/tutorial/programming-realistic-drums/) |
| Razão de velocity | graça ≈ 30–45% da principal | `[NÃO VERIFICADO — derivado das faixas de velocity, nenhuma fonte afirma a razão]` |
| Teto de leitura | acima de ~35 ms deixa de ler como flam e vira duas 32ªs | `[NÃO VERIFICADO — conferir de ouvido]` |

**No SD3, use a nota 69 (`Snare Flams`)** — é um flam gravado de verdade, muito melhor que dois hits
deslocados. Mesma coisa para drag: nota 39 (`Snare Ruffs`).

**No AD2 não há flam.** Monte com dois hits e ponha a nota de graça na **alternada** do mesmo golpe
(principal em 38, graça em 40 `Snare Open Hit (dbl)`) para as duas não roubarem voz uma da outra.

Espaçamento de drag montado à mão: `[NÃO VERIFICADO — sem fonte. Prefira a articulação nativa.]`

### 2.6 Passo 5 — microtiming

| Escopo | Valor | Fonte |
|---|---|---|
| Limiar de percepção | ~5 ms | [Slam Tracks](https://www.slamtracks.com/2025/12/20/5-secrets-to-humanizing-midi-drums/) |
| Faixa musical útil | 5–10 ms (até 20 ms) | Slam Tracks · Moozix |
| Vira desleixo | 50 ms+ | Slam Tracks |
| Quantização parcial em vez de dura | 85–95% de força | [Nail The Mix](https://www.nailthemix.com/superior-drummer-3) |

**O número mais importante deste documento.** Medição de um take comercial real — hi-hat em 16ªs do Jeff
Porcaro em *I Keep Forgettin'* ([estudo PMC4454559](https://pmc.ncbi.nlm.nih.gov/articles/PMC4454559/)):

- **desvio padrão do intervalo entre onsets: 8,7 ms** — e isso é um profissional, não desleixo
- comparação: baterista tocando com clique a 180 BPM dá 15,6 ms
- **autocorrelação lag-1 = −0,48** — intervalo longo tende a ser seguido de curto
- amplitude com **distribuição bimodal**: dois clusters (acento e não-acento), não um borrão contínuo

Três consequências diretas para o gerador:

1. Mire **σ ≈ 8–9 ms** de jitter no hi-hat.
2. **Não use ruído branco.** Desvio i.i.d. uniforme não é como gente toca. Use uma série em que cada
   desvio cancela parcialmente o anterior (anticorrelação lag-1 ≈ −0,48).
3. **Velocity de hat deve ser bimodal** — cluster de acento e cluster de não-acento, com jitter dentro de
   cada um — em vez de uma faixa uniforme larga.

Orçamento por peça. A *estrutura* (kick e caixa apertados, hat e ghost soltos) tem fonte; os números
exatos por peça são síntese: `[NÃO VERIFICADO — conferir de ouvido]`.

| Peça | σ de timing | Jitter de velocity | Empurrão direcional |
|---|---|---|---|
| Kick primário | ±3–6 ms | ±5 | 5–20 ms atrasado para laid-back |
| Caixa backbeat | ±3–6 ms | ±5–8 | 5–15 ms adiantado para urgência |
| Hi-hat / ride ostinato | **σ ≈ 8–9 ms** (com fonte) | ±8–12, bimodal | preso à grade ou levemente solto |
| Ghost de caixa | mais solto do kit | ±8 | 3–8 ms atrasado |
| Toms em virada | ±5–8 ms | ±10 | viradas aceleram, e não de forma linear |
| Crashes | ±3–5 ms | ±8 | no downbeat ou um fio antes |

Combinação nomeada como eficaz: hat na grade, **kick 10 ms atrasado, caixa 8 ms adiantada** (Slam Tracks).

---

## 3. Técnicas que exigem montagem

### 3.1 Buzz roll

Não existe articulação de buzz roll em nenhuma das duas ferramentas. Represente como notas repetidas
densas (32ªs/64ªs) em velocity baixa subindo devagar — nunca como nota longa sustentada.

**No SD3, ligue o `Smoothing` na caixa antes.** É o controle feito exatamente para isso: sem ele cada
repetição redispara um ataque de baqueta duro. Vem **desligado por padrão** para bateria. (SD3 manual §3.5.14)

Forma exata da rampa de velocity: `[NÃO VERIFICADO — sem fonte]`.

Não confunda com `Snare Backward/Forward Swirl` (SD3 66/67) nem com a família Sweep do AD2 (26–35) —
aquilo é vassoura, não rufo.

### 3.2 Rufo acentuado — e por que ele não é buzz roll

**Rufo acentuado e buzz roll são rudimentos diferentes.** O buzz é de rebote múltiplo, sem batidas
discretas contáveis. O rufo acentuado é uma sucessão rápida de batidas **discretas e alternadas**,
com acentos periódicos. Em metalcore o segundo é muito mais comum que o primeiro, e é o que aparece
antes de refrão e breakdown.

O que o denuncia como programado tem nome, e não é falta de aleatoriedade: é o contorno virar uma
**onda quadrada deslocada** — todo acento na mesma velocity, toda batida suave na mesma velocity,
alternando. Referência de partida demonstrada na tela: acento **118**, suave **55**
([GetGood Drums, 1:05–1:16](https://www.youtube.com/watch?v=OPnrlXhJhOo), CONVENÇÃO). São valores
compatíveis com as faixas da Toontrack já usadas em §7.1 — acento 105–120, suave 55–79 — o que é
corroboração independente, não fonte nova.

O problema não são os valores. É a uniformidade.

#### As duas regras que consertam, e nenhuma é aleatória

**1. A mão dominante bate mais forte.** Baterista destro toca R L R L, e o acento cai na direita
"99 vezes em 100". Logo as batidas *suaves* tocadas pela **direita** ficam ligeiramente mais altas
que as suaves tocadas pela esquerda. Isso é assimetria por alternância de mão — depende de saber a
posição na sequência, não de sortear.

**2. A batida imediatamente antes do acento sobe.** Preparando o acento, o braço levanta e o
movimento do corpo faz a *outra* mão bater um pouco mais forte logo antes. Sobe, mas não até o nível
do acento.

Levar essa segunda regra ao extremo — subir **muito** a nota anterior ao acento — produz um efeito
"rolando" em vez de reto e rígido. É truque de execução real, atribuído no vídeo ao baterista Jason
Bowld (Bullet For My Valentine).

> **O autor rejeita o humanize automático na frente da câmera.** Ele abre o MIDI Transform → Humanize
> do Logic, aplica, ouve e desfaz: quer controle. É exatamente a ordem que este projeto adota —
> intenção determinística primeiro, componente aleatório depois e menor.

Generaliza para tom, bumbo e pratos. A forma da rampa de velocity de um **buzz** continua sem fonte;
este vídeo não fala de buzz.

### 3.3 Choke de prato

| Ferramenta | Como |
|---|---|
| AD2 | **nota de choke dedicada** (63, 78, 80, 82, 87, 90, 92, 94). Hi-hat não tem choke. |
| SD3 | três rotas: nota **Mute Hit** dedicada (50, 54, 56, 58, 83, 94, 95, 106, 107, 118); **Note Off**; ou aftertouch |

**Armadilha no SD3:** se `Mute Tail Trigger` estiver em Note Off, o *comprimento* da nota de prato passa
a ser musicalmente significativo — prato com nota curta vai chocar sozinho. O estado padrão num projeto
sem bateria eletrônica não está documentado: `[NÃO VERIFICADO — conferir no plugin]`.

Nenhuma das duas usa comprimento de nota para choke por padrão no AD2.

---

## 4. Plausibilidade física — o que denuncia programação

Estas regras valem para qualquer ferramenta e são o filtro mais barato contra som de máquina.

- **Duas mãos, um pé direito, um pé esquerdo no chimbal.** Dois pratos ou dois tons no mesmo tick só são
  legais se houver mão livre. Nota de hi-hat e nota de pedal de hi-hat no mesmo tick é bandeira vermelha,
  a menos que seja splash deliberado.
- **Abertura de hi-hat é estado, não evento.** Nota aberta seguida depois por pedal-fechado é o que um
  baterista faz. Alternar aberto ↔ fechado em 16ªs consecutivas não é.
- **Alterne a "mão".** Notas idênticas repetidas em velocity idêntica disparam round robin mas ainda leem
  como máquina. Use as notas equivalentes que as bibliotecas oferecem (AD2 `(dbl)` 39/40/45/46; SD3
  articulações multi-nota como Ride Bow Tip em 51 e 113) para as duas mãos não roubarem voz.
- **Dinâmica de golpe duplo:** o segundo hit costuma ser um pouco mais forte; em bumbo duplo, um pé é
  sempre mais fraco que o outro. (Audient)
- **Linear drumming** — estilo em que nenhuma peça soa simultaneamente com outra. Serve como garantia
  barata de que a virada é fisicamente tocável: se nenhuma nota divide tick com outra, ela é executável
  por definição. ([Wikipedia](https://en.wikipedia.org/wiki/Linear_drumming))

Referência do próprio material do usuário: 72,3% dos ticks têm nota única, 23,6% têm duas, 4,2% têm três
e apenas 2 ticks no acervo inteiro têm quatro. É um vocabulário bastante linear — respeite isso ao gerar.

### Ajustes de engine que precisam ficar ligados

| Ferramenta | Controle | Estado correto |
|---|---|---|
| SD3 | Hit Variation (Randomize Hits, **Use Adjacent Layers**, Use Alternate Hits) | ligado — desligar "remove the realistic feel" (manual §3.5.12) |
| AD2 | **"No Alts"** | desligado — ligar "make things sound more static and machine-like" (manual) |
| AD2 | Global Velocity | "Softer" limita máximo a 90; "Harder" impõe mínimo 60 — saiba qual está ativo antes de calibrar |

---

## 5. Vocabulário observado no material do usuário

Medido nos dez MIDIs de bateria fornecidos (Superior Drummer, grooves de The Metal Foundry, The
Progressive Foundry, EZX Duality I/II, Made of Metal, Modern Metal, Post-Metal e Death Metal).

| Nota | Peça | Hits | Presente em |
|---|---|---|---|
| 36 | Kick | 4397 | 10/10 |
| 38 | Caixa centro | 2179 | 10/10 |
| 57 | Crash 4 | 1000 | 9/10 |
| 42 | HH fechado tip | 932 | 6/10 |
| 49 | Crash 2 | 433 | 10/10 |
| 43 | Surdo 1 | 361 | 10/10 |
| 45 | Tom 3 | 283 | 10/10 |
| 52 | Crash 5 | 263 | 9/10 |
| 22 | HH fechado edge | 228 | 2/10 |
| 51 | Ride bow tip | 124 | 4/10 |
| 46 | HH aberto edge 2 | 84 | 6/10 |
| 39 | Caixa ruffs | 48 | 5/10 |
| 40 | Caixa rimshot | 23 | 2/10 |

### Kit real do usuário — resolvido a partir do estado do plugin

Extraído do bloco VST3 dos projetos REAPER da banda (todos os onze usam a mesma montagem):

| Peça | Biblioteca carregada |
|---|---|
| Kick, hi-hat, tons, surdos, ride, crashes | **EZX Modern Metal** (`EZX2_ModernMetal`) |
| Caixa (`Snare15`, baquetas) | **EZX Metal!** (`EZX_Metal`) |

Kit híbrido. Presets salvos: `Snare Slam` no canal de caixa, `Drum Stereo Bus` no bus, mixer completo em
preset de usuário `VITUXO`.

**As notas 31–34, que não constam do mapa Core do SD3, ficam resolvidas por esse estado** — o próprio
plugin declara os aliases:

| Nota | Peça | Alias no projeto |
|---|---|---|
| 31, 32 | **Crash 4 / Crash Ride** | `alias crash4 31 32` |
| 33 | **Caixa** | `alias snareR 38 6 33 39 66 68 69 70 125` |
| 34, 35 | **Kick** | `alias kickR 36 34 35` |

Outros aliases úteis do mesmo kit, que valem como mapa de trabalho para gerar contra o setup real:

```
kick        36 34 35
snare       38 6 33 39 66 68 69 70 125     (sidestick: 37 67 71 76 127 · FX/rimshot: 40 126)
hat tip     42 11 61 119                   (pedal: 44 10 21 · pedal aberto: 23)
hat fechado 22 65 122                      (tight edge 62 · tight tip 63)
hat aberto  24 13 120 123 (grau 1) · 25 14 46 (2) · 26 15 121 124 (3) · 60 16 17 (4) · 64 12 (lo)
hat ctrl    CC4, CC1
toms        48 81 82 (rack1) · 47 45 77-80 (rack2) · 43 74 75 (floor1) · 41 72 73 (floor2)
ride        51 84 87 89 92 96 99 101 104 108 111 113 116   (bell: 53 85 88 90 93 97 100 102 105 109 112 114 117)
crash       49 (crash2) · 57 (crash5) · 31 32 (crash4/ride)
```

Note que `hatsCtrl` está aliasado a **CC4 e CC1** — o kit aceita controle contínuo de chimbal, mas os
MIDIs existentes não usam. Abertura vem toda por número de nota.

**Importante sobre este material como referência:** ele documenta o *vocabulário* e a *linguagem de
groove* do usuário, não o feel de execução. Os arquivos estão quantizados (offset mediano zero em todas
as famílias) e com velocity travada (65% a 100% das notas em 127, conforme o arquivo). A humanização, no
fluxo atual do usuário, acontece dentro do Superior Drummer, não no MIDI. Não extraia deste acervo
nenhum número de velocity ou de timing — extraia escolha de peça, densidade e estrutura de virada.

---

## 6. Lacunas declaradas

| Item | Situação |
|---|---|
| Limiares CC4 → grau de abertura de hi-hat (AD2 e SD3) | não publicados; conferir no plugin |
| Estado padrão do `Mute Tail Trigger` no SD3 | não documentado |
| Mapa de notas das expansões SDX/ADpak | divergem do Core; tabelas aqui são Core |
| Contagem de camadas de velocity do AD2 | XLN não publica |
| Espaçamento de drag montado à mão | sem fonte — use articulação nativa |
| Forma da rampa de velocity de buzz roll | sem fonte |
| Limiar quantitativo de virada "de bom gosto" vs "atulhada" | sem fonte |
| Razão de velocity graça/principal no flam | derivada, não afirmada por fonte |
| ~~Notas 31–34 no material do usuário~~ | **resolvido** — ver §5, aliases do kit real |

---

## 7. Blocos de técnica — o formato que alimenta o índice

O índice de técnicas (`tools.techniques`) é derivado **destes blocos**, não do texto acima. Cada
técnica catalogável entra num bloco `technique` — um fenced code block com linguagem `technique` e um
objeto JSON dentro. Acrescentar um bloco novo faz a técnica aparecer no índice sem alterar Python
nenhum.

Campos obrigatórios de cada bloco: `name`, `family`, `summary`, `verified`. Opcionais: `description`,
`parameters` (lista com `name`, opcional `value`, `range`, `source`), `tools` (dict cuja chave é a
ferramenta-alvo — `generic`, `superior_drummer`, `addictive_drums`, `logic_sampler`, …). Todo número
sem `source` derruba `verified` para `false` no índice — o parser NÃO deixa `[NÃO VERIFICADO]` sair
como fato.

Se o parser não encontrar nenhum bloco `technique` num manual, ele **falha alto** — índice vazio
em silêncio faria o validador aceitar qualquer nome de técnica inventado pelo modelo.

### 7.1 Hierarquia de acento

> **NÃO IMPLEMENTADA NO MOTOR — ver issue #50.** A técnica continua documentada aqui porque o
> conhecimento está certo; o que estava errado era a implementação. Ela decidia a camada **só pela
> posição métrica**, sem nenhuma noção de virada, então dentro de uma virada — onde quase toda nota
> é contratempo — rebaixava tudo. Medido sobre `tests/fixtures/corpus_drums/DEIXE IR.mid`: 63 das 65
> caixas em contratempo com velocity de origem ≥ 110 saíam ≤ 45, e a mediana dos toms caía de 127
> para 67. A hierarquia invertia a intenção: quem escreve 127 não está escrevendo ghost note.
>
> Note que a tabela de camadas da §2.2 já distingue **"acento de virada"** (105–120) de **"tom de
> preenchimento"** (55–79). O motor nunca implementou a primeira e jogava todo tom na segunda —
> era leitura errada deste manual, não falta de informação.
>
> Reimplementar exige separar contexto de groove de contexto de virada, e o limiar quantitativo de
> virada é **lacuna declarada** na §11. Qualquer limiar adotado entra como `CONVENÇÃO`, com
> justificativa, nunca como número solto no dispatch.
>
> Enquanto isso, plano que declare `drums.accent_hierarchy` recebe `PlanValidationError` explícito.
> Não vira no-op silencioso.

```technique
{
  "name": "accent_hierarchy",
  "family": "drums",
  "summary": "Distribui velocity em quatro camadas (acento, normal, suave, ghost) antes de qualquer ornamento.",
  "verified": true,
  "description": "Primeiro passo antes de qualquer outro. Levada em 127 chapada colapsa a hierarquia — mova para os quatro clusters abaixo. Teto pratico ~115 (a camada mais alta ja e o hit mais duro e comprimido). Programar tudo em 100 faz o pool de round robin ficar audivel em uma unica camada de sample.",
  "parameters": [
    {"name": "accent",     "range": [105, 120], "source": "Toontrack — how to program drums"},
    {"name": "primary",    "range": [100, 115], "source": "Toontrack — how to program drums"},
    {"name": "normal",     "range": [80, 100],  "source": "Toontrack"},
    {"name": "soft",       "range": [55, 79],   "source": "Toontrack"},
    {"name": "ghost",      "range": [20, 45],   "source": "Toontrack"},
    {"name": "hard_ceiling", "value": 115,      "source": "Audient"},
    {"name": "fill_max_gap_beats",       "value": 0.25, "source": "CONVENCAO — mesmo gap maximo (16-avo) que `drums.accented_roll` usa em `roll_sequences`; agrupa notas em run so quando o intervalo cabe numa semicolcheia. Fecha a lacuna da secao 11 (`Limiar quantitativo de virada 'de bom gosto' vs 'atulhada' — sem fonte`); e escolha do motor, nao medicao"},
    {"name": "fill_min_notes",           "value": 4,    "source": "CONVENCAO — piso do run herdado do `drums.accented_roll` (minimo de 4 notas para chamar de sequencia). Fecha a lacuna da secao 11"},
    {"name": "fill_min_density_per_beat","value": 3.0,  "source": "CONVENCAO — densidade em notas por tempo; abaixo disso e groove, nao virada. Fecha a lacuna da secao 11"},
    {"name": "fill_min_piece_variety",   "value": 2,    "source": "CONVENCAO — minimo de familias GM distintas dentro do run (kick/snare/tom/hihat/cymbal). Ostinato de uma peca so (hihat, ride) nao e virada; ja duas pecas na mesma corrida rapida quase sempre e. Fecha a lacuna da secao 11"}
  ],
  "tools": {
    "generic": {"note": "aplique nas velocities existentes; nao gera nota nova"},
    "superior_drummer": {"engine": ["Hit Variation ligado", "Use Adjacent Layers ligado"]},
    "addictive_drums": {"engine": ["No Alts desligado"]}
  }
}
```

### 7.2 Ghost notes

```technique
{
  "name": "ghost_notes",
  "family": "drums",
  "summary": "Notas fantasmas de caixa entre backbeats, com regras de posicao para nao denunciar programacao.",
  "verified": true,
  "description": "Localize os backbeats de caixa. Candidatos a ghost sao semicolcheias entre eles. Descartes obrigatorios: (1) a 16a imediatamente anterior ao backbeat, (2) ghosts consecutivas logo depois do backbeat, (3) mais de duas ghosts seguidas em 16as. 32as so em andamento lento.",
  "parameters": [
    {"name": "velocity", "range": [20, 45], "source": "Toontrack"},
    {"name": "timing_offset_ms_laidback", "range": [3, 8], "source": "Sample Focus"},
    {"name": "timing_offset_ms_urgent",   "range": [-5, -2], "source": "Sample Focus"}
  ],
  "tools": {
    "generic": {"notes": [38], "velocity": [20, 45]},
    "superior_drummer": {"notes": [38], "velocity": [20, 45], "note": "use a nota central de caixa; velocity <45 vira ghost automaticamente"},
    "addictive_drums": {"notes": [38, 40], "velocity": [20, 45], "note": "alterne entre 38 e 40 (Snare Open dbl) para nao roubar voz entre ghosts consecutivas"}
  }
}
```

### 7.3 Flam

**A articulação gravada do SD3 e o flam montado à mão não são a mesma coisa, e há desacordo
documentado sobre qual usar.** O manual mandava usar a gravada; um tutorial de produção que
demonstra as duas na tela discorda, e a razão dele é específica: o espaçamento da articulação
gravada é largo demais para o gosto dele, e montar à mão permite apertar
([Levi Keller, 1:39–2:21](https://www.youtube.com/watch?v=x-Fjokn-YI4), CONVENÇÃO).

As duas posições são legítimas e dependem do resultado que se quer. O que fica como regra é:

| Se… | Então |
|---|---|
| O espaçamento da articulação gravada serve | Use nota 69. Uma nota, e o sample resolve |
| Quer flam mais apertado que o gravado | Monte à mão, **duas notas, grid desligado** |

E, montando à mão, há uma gradação de realismo que o mesmo tutorial demonstra (2:24–2:48): duas
notas na **mesma** altura tocam o mesmo sample ou grupo de samples e já soa melhor que a articulação;
mas colocar a segunda batida numa **amostra ou grupo ligeiramente diferente** imita melhor como um
baterista real bate no tambor duas vezes seguidas. É o mesmo princípio do round robin, aplicado de
propósito em vez de por sorteio.

> **Regra que sai daí e vale além do flam:** dois toms atacados **exatamente** no mesmo tick não
> existem em execução real. Num fill com dois toms simultâneos, flameie — "a real drummer is not
> going to be hitting those at exactly the same time" (Levi Keller, 3:11–3:33). Isso é decisão de
> colocação, não de timbre, e o arranjador pode aplicar sozinho.

O gap de 8–15 ms continua sendo o único número com fonte. **A razão de velocity entre a graça e a
principal, e o teto em que o flam deixa de ler como flam, continuam sem fonte** — nenhum dos dois
tutoriais dá número.

```technique
{
  "name": "flam",
  "family": "drums",
  "summary": "Nota de graca precedendo a principal em 8-15ms; a principal e mais forte.",
  "verified": false,
  "description": "DUAS ESTRATEGIAS, e ha desacordo documentado. A articulacao gravada do SD3 (nota 69) resolve com uma nota so, mas ha tutorial de producao que a considera larga demais e prefere montar a mao para apertar. Montando a mao: duas notas com o GRID DESLIGADO, senao a DAW gruda a graca na grade e o flam some. Gradacao de realismo, da pior para a melhor: articulacao gravada < duas notas na mesma altura < segunda batida numa amostra ou grupo LIGEIRAMENTE DIFERENTE, que imita como um baterista bate duas vezes seguidas. No AD2 nao ha articulacao gravada, entao a graca vai na alternada (40) para as duas nao roubarem voz. REGRA QUE VALE ALEM DO FLAM: dois toms atacados no mesmo tick nao existem em execucao real — num fill com toms simultaneos, flameie.",
  "parameters": [
    {"name": "gap_ms", "range": [8, 15], "source": "Moozix"},
    {"name": "grace_velocity_ratio", "value": 0.38, "source": "CONVENCAO — a razao graca/principal e derivada, nao afirmada por fonte (ver secao 11). 0.38 fica na faixa em que a graca soa como ornamento e nao como segunda batida; escolhido para o motor, nao medido"},
    {"name": "reading_ceiling_ms", "value": 35, "source": "CONVENCAO — teto operacional acima do qual as duas batidas passam a ser lidas como notas separadas em vez de flam; escolhido para o motor, sem medicao publicada"}
  ],
  "tools": {
    "generic": {"notes_main": [38], "notes_grace": [38], "tom_notes": [41, 43, 45, 47, 48], "note": "grid DESLIGADO para arrastar a graca livre; segunda batida em amostra diferente quando a lib permitir"},
    "superior_drummer": {"notes": [69], "notes_main": [38], "tom_notes": [41, 43, 45, 47, 48], "note": "articulacao Snare Flams gravada — uma nota so. Alternativa: montar a mao em 38 se o espacamento gravado ficar largo demais", "fonte_do_desacordo": "https://www.youtube.com/watch?v=x-Fjokn-YI4"},
    "addictive_drums": {"notes_main": [38], "notes_grace": [40], "tom_notes": [65, 67, 69, 71], "note": "AD2 nao tem articulacao de flam gravada"}
  }
}
```

### 7.4 Microtiming

```technique
{
  "name": "microtiming",
  "family": "drums",
  "summary": "Jitter musical no ostinato de chimbal (sigma ~8-9ms) com autocorrelacao lag-1 negativa e velocity bimodal.",
  "verified": true,
  "description": "Numero medido de take real (Jeff Porcaro em I Keep Forgettin', PMC4454559). NAO use ruido branco: gente toca com anticorrelacao (intervalo longo tende a ser seguido de curto). Velocity de hat deve ser bimodal, nao uniforme.",
  "parameters": [
    {"name": "hihat_timing_sigma_ms", "value": 8.7, "source": "PMC4454559 (Jeff Porcaro)"},
    {"name": "hihat_autocorr_lag1", "value": -0.48, "source": "PMC4454559"},
    {"name": "perception_threshold_ms", "value": 5, "source": "Slam Tracks"},
    {"name": "musical_range_ms", "range": [5, 20], "source": "Slam Tracks / Moozix"},
    {"name": "sloppy_threshold_ms", "value": 50, "source": "Slam Tracks"}
  ],
  "tools": {
    "generic": {"hihat_notes": [42, 44, 46], "note": "aplique como offset absoluto em ms a cada nota; nao substitui hierarquia de acento"},
    "superior_drummer": {"hihat_notes": [10, 11, 12, 13, 14, 15, 16, 17, 21, 22, 23, 24, 25, 26, 42, 44, 46, 60, 61, 62, 63, 64, 65, 119, 120, 121, 122, 123, 124], "note": "aliases de hi-hat do kit real do usuario e mapa de trabalho SD3"},
    "addictive_drums": {"hihat_notes": [48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 59], "note": "AD2 resolve abertura por numero de nota"}
  }
}
```

### 7.5 Buzz roll

```technique
{
  "name": "buzz_roll",
  "family": "drums",
  "summary": "Repeticoes densas em velocity baixa; NENHUMA das duas ferramentas tem articulacao de buzz roll.",
  "verified": false,
  "description": "Represente como 32as/64as em velocity subindo devagar. NAO use nota longa sustentada. NAO confunda com Snare Backward/Forward Swirl (SD3 66/67) nem com a familia Sweep do AD2 (26-35) — aquilo e vassoura, nao rufo. [NAO VERIFICADO] CONVENCAO operacional: a rampa linear entra no compasso como preparacao curta para a nota estrutural, porque o manual so documenta a direcao musical da rampa e nao a sua forma exata.",
  "parameters": [
    {"name": "grid", "value": "32nd/64th", "source": "CONVENCAO — a grade vem da descricao textual do manual (32as/64as); o valor exato por contexto nao e afirmado por fonte"},
    {"name": "velocity_ramp", "value": {"shape": "linear", "start_ratio": 0.35, "end_ratio": 0.78, "gate_ratio": 0.72, "window_beats": 1.0}, "range": null, "source": "CONVENCAO — a secao 11 declara que a FORMA da rampa de velocity do buzz nao tem fonte. Rampa linear curta ate a nota estrutural, escolhida para evitar semicolcheia mecanica e nota sustentada falsa. E escolha do motor, nao medicao"}
  ],
  "tools": {
    "superior_drummer": {"engine": ["ligar Smoothing na caixa antes"], "notes": [38], "note": "sem Smoothing cada repeticao redispara ataque duro"},
    "addictive_drums": {"notes": [38], "note": "sem articulacao dedicada; monte por repeticao"}
  }
}
```

### 7.6 Choke de prato

```technique
{
  "name": "cymbal_choke",
  "family": "drums",
  "summary": "Fecha o prato imediatamente; via nota dedicada, Note Off ou aftertouch, conforme a ferramenta.",
  "verified": true,
  "description": "AD2 tem notas de choke dedicadas (hi-hat NAO tem choke). SD3 tem tres rotas: nota Mute Hit, Note Off, ou aftertouch. Cuidado no SD3: se Mute Tail Trigger estiver em Note Off, o comprimento da nota do prato passa a ter significado musical.",
  "parameters": [],
  "tools": {
    "addictive_drums": {
      "target_notes": [60, 77, 79, 81, 89, 91, 93],
      "notes": [63, 78, 80, 82, 87, 90, 92, 94],
      "choke_after_beats": 0.5,
      "short_ceiling_beats": 0.25,
      "note": "hi-hat nao tem choke; duracao curta sem choke e convencao operacional para nao matar prato ja abafado"
    },
    "superior_drummer": {
      "target_notes": [49, 52, 55, 57, 59],
      "notes": [50, 54, 56, 58, 83, 94, 95, 106, 107, 118],
      "choke_after_beats": 0.5,
      "short_ceiling_beats": 0.25,
      "note": "Mute Hit dedicado; ou Note Off; ou aftertouch. Duracao curta sem choke e convencao operacional para nao matar prato ja abafado"
    }
  }
}
```

### 7.7 Diferenciacao de articulacao

```technique
{
  "name": "articulation_diff",
  "family": "drums",
  "summary": "Alterna edge/tip no hat, bow/bell no ride, centro/rimshot na caixa — leva chapada vira detalhada.",
  "verified": true,
  "description": "Rimshot na caixa apenas no backbeat mais forte; rimshot em tudo mata o contraste. No hat, tip no contratempo e edge/shaft no downbeat. No ride, bow tip como default e bell para levantar o compasso.",
  "parameters": [],
  "tools": {
    "superior_drummer": {
      "hat_tip": [42], "hat_edge": [22],
      "ride_bow_tip": [51], "ride_bow_shank": [116], "ride_bell": [53],
      "snare_center": [38], "snare_rimshot": [40]
    },
    "addictive_drums": {
      "hat_tip": [49, 51], "hat_edge": [50, 52],
      "ride_bow_tip": [60], "ride_bow_shank": [62], "ride_bell": [61],
      "snare_center": [38], "snare_rimshot": [37]
    }
  }
}
```

### 7.8 Rufo acentuado

```technique
{
  "name": "accented_roll",
  "family": "drums",
  "summary": "Sucessao rapida de batidas discretas alternadas com acentos periodicos. NAO e buzz roll — e o rufo que aparece antes de refrao e breakdown.",
  "verified": false,
  "description": "MARCADO NAO VERIFICADO: os valores vem de tutorial de fabricante de biblioteca de bateria, demonstrados na tela, sem medicao. O que denuncia programacao tem nome: o contorno vira ONDA QUADRADA DESLOCADA, todo acento na mesma velocity e toda suave na mesma velocity. O problema nao sao os valores, e a uniformidade. DUAS REGRAS DETERMINISTICAS consertam, e nenhuma e sorteio. (1) MAO DOMINANTE: destro toca R L R L e o acento cai na direita 99 vezes em 100, entao as batidas SUAVES da direita ficam um pouco mais altas que as suaves da esquerda — depende da posicao na sequencia, nao de random. (2) LIFT PRE-ACENTO: a batida imediatamente anterior ao acento sobe, porque o braco levanta e o corpo faz a outra mao bater mais forte; sobe mas NAO ate o nivel do acento. Levar a regra 2 ao extremo produz efeito rolando em vez de reto. NAO substituir isso por funcao de humanize da DAW: o proprio autor aplica e desfaz na camera porque quer controle. Generaliza para tom, bumbo e pratos.",
  "parameters": [
    {"name": "velocity_acento", "value": 118, "source": "CONVENCAO — GetGood Drums, demonstrado na tela, https://www.youtube.com/watch?v=OPnrlXhJhOo 1:05-1:16"},
    {"name": "velocity_suave", "value": 55, "source": "CONVENCAO — mesma fonte; coincide com o piso da faixa soft da Toontrack em accent_hierarchy"},
    {"name": "delta_mao_dominante", "value": 6, "source": "CONVENCAO — ajuste operacional derivado da regra de mao dominante; sem medicao publicada"},
    {"name": "delta_lift_pre_acento", "value": 14, "source": "CONVENCAO — ajuste operacional derivado da regra de lift pre-acento; sem medicao publicada"},
    {"name": "sticking_padrao", "value": "RLRL com acento na dominante", "source": "CONVENCAO — mesma fonte, 2:32-2:47"}
  ],
  "tools": {
    "generic": {"note": "precisa saber a posicao de cada nota na sequencia e qual mao a toca; jitter aleatorio nao produz nenhuma das duas regras"},
    "superior_drummer": {"notes": [38], "note": "Hit Variation ligado para as repeticoes nao reusarem o mesmo sample"},
    "addictive_drums": {"notes": [38, 40], "note": "alternar entre 38 e 40 evita roubo de voz entre batidas consecutivas"}
  }
}
```
