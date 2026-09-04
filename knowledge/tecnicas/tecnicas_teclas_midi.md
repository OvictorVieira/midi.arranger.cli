# Técnicas de teclas em MIDI — manual de execução

> **Para que serve.** A IA decide *o que* a música precisa. Este arquivo diz *como fazer aquilo em
> MIDI*. Cobre a mecânica de quem toca com **duas mãos** — piano e Rhodes são instrumentos de duas
> mãos, e é isso que separa MIDI que soa como piano de MIDI que soa como pianista.
>
> **Regra de fonte.** Número com fonte vem citado. Número sem fonte vem marcado `[NÃO VERIFICADO]`.
> Este manual tem uma proporção alta de dados **medidos em laboratório** — aproveite isso e não
> misture com convenção.

---

## 0. O achado que derruba a abordagem ingênua

A intuição de todo mundo é: *"pianista toca a melodia um pouco antes do acompanhamento, então
adianto a melodia em 30 ms."* **Isso está quase errado, e do jeito que importa.**

Goebl (2001) mediu 22 pianistas profissionais tocando Chopin num Bösendorfer SE290 com resolução de
1,25 ms — cerca de 26 mil notas. O *melody lead* de 20–30 ms existe e é real **no som**. Mas quando
ele reconstruiu o momento em que o **dedo toca a tecla**, subtraindo o tempo de viagem do martelo
para cada velocity, o adiantamento **colapsou para ≈ 0 ms**.

Os dedos batem **juntos**. O que separa o som é a física: tecla golpeada mais forte chega à corda
antes. A viagem do martelo vai de **25 ms no forte a 160 ms no piano** — uma janela de 135 ms que
depende só da força.

### A consequência que mais importa para nós

| Se o alvo é… | O que fazer |
|---|---|
| Piano modelado fisicamente, que já simula tempo de ataque por velocity | **NÃO adicione o lead** — você estaria contando duas vezes |
| Sampler com início de sample independente de velocity (**a maioria**) | **Adicione o lead manualmente** — é a única forma de reproduzir o resultado acústico |

Rhodes cai no segundo caso. Sampler de Rhodes não gera esse atraso sozinho.

### E o debate, porque ele não está fechado

Palmer (1989, 1996) defende que o adiantamento é **recurso expressivo deliberado**: cresce com
expressividade pretendida, com familiaridade e com nível do pianista, e cai quando se pede execução
"sem música". Repp (1996) e Goebl (2001) defendem que é **artefato de velocity**, e Goebl mostra o
colapso a zero no nível do dedo.

**Os dois podem estar certos.** Um dos 22 pianistas manteve ~20 ms de adiantamento mesmo no nível do
dedo — prova de que o gesto deliberado é possível. Goebl chama essa estratégia de "bastante rara".
Os próprios pianistas relatam que destacam uma voz tocando **mais forte**, não mais cedo.

**Regra prática que sai daí:** dirija o adiantamento pela **diferença de velocity**, não por uma
constante. A relação é monótona e a curva da ação é côncava, não linear — a correlação do
adiantamento com o previsto pela ação chegou a r = 0,79, contra r = −0,73 da correlação linear
simples com velocity.

---

## 1. Assincronia entre as mãos

O maior conjunto disponível: **63.344 assincronias medidas** na obra completa de Chopin gravada por
Magaloff num Bösendorfer SE (Goebl, Flossmann & Widmer, 2010). Convenção de sinal: positivo = mão
direita adiantada.

| Estatística | Valor |
|---|---|
| Média com sinal | **+4,4 ms** |
| Moda | **+13 ms** |
| Forma | assimétrica para o lado negativo, com bojo secundário de −100 a −300 ms das antecipações de baixo |

A média com sinal esconde o que interessa. O número para programar é a **assincronia sem sinal, por
peça**:

| Textura | Assincronia média sem sinal |
|---|---|
| Acorde denso, homofônico | **15–22 ms** |
| Típica, maioria das 150 peças | **25–40 ms** |
| Melodia sobre acompanhamento, andamento lento | **55–80 ms** |

Duas regras medidas saem daí:

- **Textura de acorde denso aperta as mãos; melodia sobre acompanhamento solta.**
- **Mais rápido aperta.** Correlação com taxa de eventos: r = −0,280, n = 150, p < 0,001. E a
  variabilidade também cai: r = −0,417.

**Uma constante global de humanização contradiz as três coisas.**

### Antecipação de baixo — rara e mirada

Quando a mão esquerda adianta de propósito, o efeito é grande: tipicamente **~50 ms**, chegando a
**180–185 ms**. E, ao contrário do melody lead, ele **aumenta** no nível do dedo — é intenção, não
artefato.

Mas a frequência desmonta o instinto de aplicar sempre:

| Posição | Frequência |
|---|---|
| Primeiro tempo do compasso | **1,80%** dos eventos simultâneos |
| Outros tempos fortes | **1,48%** |
| Contratempos | **0,66%** |

**Um a dois por cento.** Programar antecipação de baixo em todo acorde erra por duas ordens de
grandeza.

### Rubato extremo

Em trechos de *tempo rubato* antigo — melodia flutua, acompanhamento segura o tempo — as assincronias
chegam a **±265 ms**. Peças lentas têm mais de cinco desses trechos; movimentos de sonata, cerca de
1,8 por peça.

---

## 2. Vozeamento — qual nota é mais forte

O achado mais categórico do conjunto todo, e o mais fácil de aplicar:

> "Todos os pianistas tocaram a primeira voz consistentemente mais forte que as outras. **Nenhum dos
> pianistas escolheu outra voz para ser a mais forte.**"

Vinte e dois profissionais, duas peças, sem uma exceção. **A melodia é mais forte que tudo, sempre.**

E a regra generalizada, que vale para melodia no topo, no meio ou no baixo: **a voz mais forte é a
que adianta**, e o quanto acompanha a diferença de intensidade.

| Situação | Comportamento medido |
|---|---|
| Melodia no meio do acorde | adiantamento **diminui** |
| Voz interna deliberadamente destacada | ela vira a mais forte e adianta ~20 ms; a voz de cima é atenuada; a mão esquerda atrasa ~40 ms |
| Melodia no baixo | fenômeno de sinal oposto e **muito maior** — ~50 ms, até 185 ms |

Faixa dinâmica total usada pelos pianistas: velocity final de martelo de **0,21 a 4,26 m/s**. Abaixo
de ~0,21 m/s o martelo não alcança a corda.

### A conversão de velocity de martelo para MIDI

O mapeamento é **logarítmico**, não linear. Goebl & Bresin (2003) mediram num Yamaha Disklavier de
cauda com sensores ópticos e derivaram:

```
velocity_MIDI = 57,96 + 71,3 × log₁₀(v)          v em m/s
```

| Velocidade do martelo | Velocity MIDI | |
|---|---|---|
| 1,0 m/s | ≈ 58 | pianíssimo |
| 2,0 m/s | ≈ 79 | piano |
| 3,0 m/s | ≈ 92 | mezzo forte |
| 5,0 m/s | ≈ 108 | fortíssimo |

**Nunca converta linearmente.** Uma escala linear de 0,21 a 4,26 m/s para 0–127 comprimiria toda a
região expressiva do piano ao pianíssimo numa faixa estreita de velocity, e é o erro que faz
programação de piano soar sem dinâmica no meio-termo.

### Relação de dinâmica entre as mãos

A mão direita toca **10 a 20% mais forte** que a esquerda — cerca de **+8 a +15 unidades de velocity
MIDI**. Medido comparando pianistas especialistas com amadores: os especialistas mantêm essa
diferenciação de forma rigorosa e consistente; os amadores não.

Regra que sai daí: **em passagem melódica, a mão esquerda nunca tem velocity igual ou maior que a
direita.** Isso vale junto com a regra da §2 — a melodia é sempre a voz mais forte.

---

## 3. Restrições físicas

**Verificado:** oitava do teclado convencional tem **6,5 polegadas** (≈165 mm) de dó a dó; teclados
alternativos de pesquisa usam 6,0" e 5,5". Pianistas de mão pequena mostram **11,2 a 13,8% mais**
ativação de extensores em acordes de grande extensão. Profundidade de curso da tecla: **~9,5 mm**.

### Extensão da mão — medida, não convenção

Wagner (1988) mediu mais de 200 pianistas; Parncutt e colegas (1997) modelaram os dados. A
terminologia é deles:

| | Semitons | Intervalo |
|---|---|---|
| `MaxComf` — confortável | **9 a 10** | sexta maior à oitava |
| `MaxPrac` — prática máxima | **11 a 13** | sétima maior à décima |
| `MaxPoss` — virtuosística absoluta | **14 a 17** | estiramento extremo entre polegar e mínimo |

**Regra de validação que sai daí:** disposição de notas numa única mão que exceda **13 semitons**
denuncia arranjo não executável por mão humana normal. É checagem barata e pega vozeamento de
teclado escrito como se fosse de órgão.

**Ainda não verificado — convenção de ensino:** quantas notas por mão são realistas em textura densa,
quais vozeamentos exigem duas mãos, e o ponto de divisão entre as mãos.

Há um **proxy medido** para a divisão de mãos que vale usar: o estudo dos 63 mil eventos assumiu que
a direita toca a pauta de cima e a esquerda a de baixo, e defendeu isso como razoável para o Chopin
inteiro porque a assincronia **dentro** de uma mão é muito menor que **entre** as mãos. É divisão por
pauta, não por altura fixa.

---

## 4. Pedal

Pouco número concreto disponível — os dois estudos diretamente relevantes não são acessíveis por
completo.

**Verificado (Repp, 1997, 10 pianistas, 3 andamentos):** o tempo do pedal **não é invariante**, nem
em absoluto nem em relativo. As ações de pedal acontecem **um pouco mais cedo em andamento rápido**,
e esses deslocamentos são **proporcionalmente menores que as mudanças no tempo dos acordes** — o pé
não escala junto com as mãos. Tempo de pedal também carrega forte diferença individual entre
pianistas.

Sistemas modernos capturam posição contínua: duração de uso, de afundamento total e de
**meio-pedal**, e profundidade no início e no fim do acorde. **Pedal não é liga-desliga.**

**Não verificado:** o deslocamento em milissegundos do pedal-depois-da-nota no pedal sincopado. É
convenção de ensino consolidada — solta na harmonia nova, repisa logo depois do acorde soar — e é
consistente com o achado de que o pé tem tempo periférico e sensível a andamento, **mas não achei
valor medido**.

---

## 5. Rhodes — lacuna de performance, não de física

> **Esta seção foi corrigida.** Ela dizia "lacuna completa", e isso estava errado pela metade. A
> lacuna de **performance** é real e agora está confirmada com rigor. Mas existe medição física
> revisada por pares e existe o **manual de serviço oficial da CBS/Fender (1979)**, com toda a
> geometria da ação e tolerâncias. Ver §7.7 e o bloco `keys.rhodes_touch`.

**Não existe nenhum estudo publicado de performance de Rhodes** — timing, dinâmica ou articulação. A
confirmação é firme: arquivo completo do DAFx 1998–2025, OpenAlex com quatro consultas, Crossref,
arXiv (**0 resultados**), Semantic Scholar e Zenodo. Há três papers de física e zero de performance.
Tudo nas seções 0 a 4 vem de piano acústico de cauda.

O que dá para inferir da mecânica, e **está marcado como inferência, não medição**:

1. **O melody lead por artefato de velocity não transfere.** Ele existe no piano porque a viagem do
   martelo varia de 25 a 160 ms conforme a força. A ação de haste do Rhodes tem geometria diferente e
   muito mais curta. Qualquer adiantamento de melodia no Rhodes precisa ser **posto de propósito no
   MIDI**, porque nem o instrumento nem o sampler geram sozinhos. É a afirmação mais defensável que
   se pode fazer sobre Rhodes, e ela segue direto do mecanismo medido.
2. **O modelo de pedal não porta.** O Rhodes não tem o borrão harmônico do abafador de cauda.
3. O que é **comportamento do músico** — voz mais forte lidera, antecipação de baixo rara e mirada no
   downbeat, textura e andamento governam o aperto das mãos — é a parte mais plausivelmente
   transferível. Mas a transferência **não foi testada**.

---

## 6. Blocos de técnica

Mesmo formato dos outros manuais.

### 6.1 Melody lead

```technique
{
  "name": "melody_lead",
  "family": "keys",
  "summary": "A voz mais forte soa antes das outras. E consequencia da velocity, nao gesto de timing — e isso muda como programar.",
  "verified": true,
  "description": "ATENCAO ao alvo. Em piano modelado fisicamente, que simula tempo de ataque por velocity, adicionar lead CONTA DUAS VEZES — nao adicione. Em sampler com inicio de sample independente de velocity (a maioria, e todo Rhodes), adicione manualmente: e a unica forma de reproduzir o resultado acustico. Dirija pela DIFERENCA DE VELOCITY entre as vozes, com curva concava, nao por constante fixa. A relacao medida com o previsto pela acao chega a r=0.79, contra r=-0.73 da correlacao linear com velocity.",
  "parameters": [
    {"name": "lead_normal_ms", "range": [20, 30], "source": "Goebl 2001 JASA 110(1) — 22 pianistas, ~26mil notas, resolucao 1.25ms"},
    {"name": "lead_melodia_enfatizada_ms", "range": [40, 50], "source": "Goebl 2001, Fig.8"},
    {"name": "lead_no_nivel_do_dedo_ms", "value": 0, "source": "Goebl 2001 — colapsa a zero ao subtrair a viagem do martelo"},
    {"name": "limiar_perceptivo_ms", "value": 30, "source": "Goebl, Flossmann & Widmer 2010 — constante de trabalho"},
    {"name": "viagem_martelo_forte_ms", "value": 25, "source": "Askenfelt & Jansson 1991 JASA 90"},
    {"name": "viagem_martelo_piano_ms", "value": 160, "source": "Askenfelt & Jansson 1991"}
  ],
  "tools": {
    "generic": {"note": "sampler de inicio fixo: ADICIONE o lead. Piano modelado: NAO adicione, ja vem da simulacao"},
    "rhodes": {"note": "sempre adicione — a acao de haste nao gera o atraso, e nenhum sampler de Rhodes simula"}
  }
}
```

### 6.2 Assincronia entre as mãos

```technique
{
  "name": "hand_asynchrony",
  "family": "keys",
  "summary": "As maos nao caem juntas, e o quanto depende da textura e do andamento — nao de uma constante.",
  "verified": true,
  "description": "Use a assincronia SEM SINAL, escolhida pela textura. Acorde denso aperta; melodia sobre acompanhamento solta; andamento rapido aperta e reduz a variabilidade. Uma constante global de humanizacao contradiz as tres coisas medidas. Metade da variancia e inexplicavel pelos preditores conhecidos, entao depois da regra deterministica acrescente jitter estocastico de magnitude comparavel — regra 100% deterministica le como mecanica pelo motivo oposto ao da quantizacao.",
  "parameters": [
    {"name": "media_com_sinal_ms", "value": 4.4, "source": "Goebl/Flossmann/Widmer 2010 — 63.344 eventos, Chopin completo por Magaloff"},
    {"name": "moda_com_sinal_ms", "value": 13, "source": "idem"},
    {"name": "sem_sinal_acorde_denso_ms", "range": [15, 22], "source": "idem, Fig.3a"},
    {"name": "sem_sinal_tipico_ms", "range": [25, 40], "source": "idem"},
    {"name": "sem_sinal_melodia_sobre_acomp_ms", "range": [55, 80], "source": "idem"},
    {"name": "correlacao_com_andamento", "value": -0.280, "source": "idem, n=150, p<0.001"},
    {"name": "variancia_inexplicada_pct", "value": 50, "source": "Goebl 2001 — velocity explica cerca de metade"}
  ],
  "tools": {"generic": {"note": "positivo = mao direita adiantada"}}
}
```

### 6.3 Antecipação de baixo

```technique
{
  "name": "bass_anticipation",
  "family": "keys",
  "summary": "Mao esquerda adiantada de proposito. Grande quando acontece, mas acontece em 1 a 2% dos acordes.",
  "verified": true,
  "description": "Diferente do melody lead, este e INTENCIONAL: aumenta no nivel do dedo em vez de colapsar. Concentra-se em evento metricamente importante. Programar em todo acorde erra por duas ordens de grandeza — a frequencia medida e de 1 a 2%, mirada no primeiro tempo. Fica menos frequente conforme o andamento sobe.",
  "parameters": [
    {"name": "limiar_de_definicao_ms", "value": 50, "source": "Goebl/Flossmann/Widmer 2010 — definicao operacional"},
    {"name": "tamanho_tipico_ms", "value": 50, "source": "Goebl 2001, Fig.9"},
    {"name": "tamanho_maximo_ms", "range": [180, 185], "source": "Goebl 2001"},
    {"name": "frequencia_primeiro_tempo_pct", "value": 1.80, "source": "Goebl/Flossmann/Widmer 2010"},
    {"name": "frequencia_outros_tempos_fortes_pct", "value": 1.48, "source": "idem"},
    {"name": "frequencia_contratempo_pct", "value": 0.66, "source": "idem"}
  ],
  "tools": {"generic": {}}
}
```

### 6.4 Vozeamento por dinâmica

```technique
{
  "name": "voice_dynamics",
  "family": "keys",
  "summary": "A melodia e a voz mais forte. Sem excecao em 22 profissionais medidos.",
  "verified": true,
  "description": "O achado mais categorico do conjunto: nenhum dos 22 pianistas escolheu outra voz para ser a mais forte. A regra generalizada e que a voz mais forte e a que adianta, e o quanto acompanha a diferenca de intensidade — vale para melodia no topo, no meio ou no baixo. Quando uma voz interna e destacada, ela vira a mais forte, adianta ~20ms, a voz de cima e atenuada e a mao esquerda atrasa ~40ms.",
  "parameters": [
    {"name": "melodia_sempre_mais_forte_pct", "value": 100, "source": "Goebl 2001 — 22 de 22 pianistas"},
    {"name": "fhv_melodia_normal_ms", "value": 1.01, "source": "Goebl 2001 — m/s de velocity de martelo, NAO velocity MIDI"},
    {"name": "fhv_melodia_enfatizada", "value": 1.28, "source": "Goebl 2001"},
    {"name": "fhv_faixa_dinamica_total", "range": [0.21, 4.26], "source": "Goebl 2001, nota 6"},
    {"name": "delta_midi_melodia_vs_acompanhamento", "value": 7, "source": "DERIVADO: 71.3 * log10(1.28 / 1.01) = 7.3 unidades MIDI, aplicando a conversao logaritmica medida de Goebl & Bresin 2003 (§2 deste manual: velocity_MIDI = 57.96 + 71.3 * log10(v)) sobre fhv_melodia_enfatizada e fhv_melodia_normal acima — nenhum numero novo, so a aritmetica entre dois parametros ja sourced"}
  ],
  "tools": {
    "generic": {"note": "ATENCAO: os valores sao velocity de martelo em m/s. A conversao para velocity MIDI e NAO-LINEAR e especifica do instrumento. Nao converta linearmente para 0-127"}
  }
}
```

### 6.5 Espalhamento de acorde arpejado

```technique
{
  "name": "rolled_chord",
  "family": "keys",
  "summary": "Acorde rolado de baixo para cima, com espalhamento que ACELERA — nao e uniforme.",
  "verified": true,
  "description": "O achado que importa: a taxa de rolagem NAO e constante. Os intervalos entre notas sucessivas diminuem progressivamente do grave para o agudo — o rolo acelera. Espalhar 20ms fixos entre cada nota e o erro classico e soa mecanico. O espalhamento TOTAL entre a primeira nota e a ultima fica entre 30 e 120ms; distribua esse total com intervalos decrescentes. Os dois corpora de piano mais citados excluiram arpejos da analise de proposito, entao este dado vem de outro estudo — confira o valor exato no artigo antes de calibrar fino.",
  "parameters": [
    {"name": "espalhamento_total_ms", "range": [30, 120], "source": "Fu, Xia, Dannenberg & Wasserman, ISMIR 2015 — modelo estatistico sobre performances de pianistas profissionais"},
    {"name": "perfil", "value": "acelerado_nao_linear", "source": "ISMIR 2015 — intervalos diminuem progressivamente do grave para o agudo"},
    {"name": "razao_entre_intervalos_sucessivos", "value": 0.8, "source": "CONVENCAO — a fonte publica o PERFIL (intervalos decrescentes do grave para o agudo) e o TOTAL (30-120ms), mas nao a razao entre um intervalo e o seguinte; 0.8 deixa o ultimo intervalo em 0,64 do primeiro num acorde de quatro notas (quatro notas sao TRES intervalos, e 0,8 ao quadrado e 0,64) — decrescimo audivel sem colapsar as duas ultimas notas no mesmo tick; razao escolhida para o motor, sem medicao publicada"}
  ],
  "tools": {"generic": {"note": "de baixo para cima; a nota de topo cai no tempo; intervalos DECRESCENTES entre notas sucessivas"}}
}
```

### 6.6 Pedal sincopado

```technique
{
  "name": "syncopated_pedal",
  "family": "keys",
  "summary": "Pedal desce DEPOIS do acorde soar, entre 50 e 150ms — nunca junto e nunca antes.",
  "verified": true,
  "description": "Mandar CC64 junto com o note-on, ou antes dele, captura as notas do acorde ANTERIOR e suja a harmonia. O pedal desce depois que o acorde ja soou. O atraso diminui conforme o andamento sobe. Tambem medido: o tempo do pedal nao e invariante e os deslocamentos dele sao proporcionalmente MENORES que as mudancas no tempo dos acordes — o pe nao escala junto com as maos. CUIDADO com o meio-pedal: a faixa 40-85 descreve o comportamento de um instrumento medido, NAO um padrao. O half-damper nao e padronizado em lugar nenhum do corpus MMA, e um receptor conforme le 63 como OFF total e 64 como ON total — nele o pedal PULA em vez de graduar. Use meio-pedal so quando souber que o instrumento-alvo o implementa; ver keys.damper_pedal.",
  "parameters": [
    {"name": "atraso_apos_o_acorde_ms", "range": [50, 150], "source": "Repp 1996b/1996c/1997, reanalisado em Lehtonen et al. 2007 — Analysis and modeling of piano sustain-pedal effects"},
    {"name": "dependencia_de_andamento", "value": "atraso_diminui_com_bpm_maior", "source": "Lehtonen et al. 2007"},
    {"name": "meio_pedal_cc64", "range": [40, 85], "source": "tese Aalto sobre efeitos do pedal de sustentacao"}
  ],
  "tools": {
    "generic": {"cc": 64, "note": "soltar ANTES da harmonia nova, repisar DEPOIS que ela soa"},
    "rhodes": {"note": "o modelo de pedal do piano de cauda NAO porta — o Rhodes nao tem o borrao harmonico do abafador"}
  }
}
```

---

## 7. Expressão — pitch bend, modulation e os CCs

As seções 0 a 6 tratam da **mecânica de duas mãos**: quem toca antes, quem toca mais forte, quando o
pé desce. Esta seção trata do outro eixo — o que se faz com a nota **depois** que ela começou. É onde
vivem bend e modulation, e é o que separa uma nota de teclado de uma nota que respira.

Todo o material desta seção vem de **documento primário lido na íntegra**: MIDI 1.0 Detailed
Specification 4.2.1, General MIDI 1 e 2, DLS Level 2.2, MPE v1.1, RP-021, CA-026, CA-022, mais
manuais de fabricante.

### 7.1 Pitch bend — três coisas que quase todo mundo erra

**Primeira: os dois bytes são obrigatórios.** O pitch bend é o único caso em que a spec exige LSB
*e* MSB, e ela diz por quê: *"This takes into account human hearing which is particularly sensitive
to pitch changes."* Escrever só o MSB — atalho que alguns editores fazem — produz uma curva de **128
passos em vez de 16.384**.

> O degrau audível que se atribui ao MIDI quase nunca vem da taxa de eventos. Vem daqui.

**Segunda: a ordem é LSB primeiro.** `En ll mm`. Centro = `En 00 40` = 8192.

**Terceira, e a mais cara: a faixa default é indefinida.** A MIDI 1.0 não fixa nada —
*"Sensitivity of Pitch Bend Change is selected in the receiver."* Quem fixa é GM1 e GM2 (±2
semitons) e o MPE (**±48** nos member channels). Fator de **24×** entre os dois regimes.

| Regime | Faixa default |
|---|---|
| MIDI 1.0 | **não define** |
| GM1 / GM2 | ±2 semitons |
| MPE — member channel | **±48 semitons** |
| MPE — manager channel | ±2 semitons |
| DLS Level 2 | 12.800 cents de escala no RPN0 |

O modo de falha mais escandaloso: patch MPE-capaz rodando em modo normal, recebendo dados escritos
para ±2. Bends **24× maiores**.

**Conclusão operacional: sempre enviar o RPN 0, em toda trilha que use bend.**

```
Bn 65 00      CC101 = 0    RPN MSB
Bn 64 00      CC100 = 0    RPN LSB    -> RPN 0 selecionado
Bn 06 ss      CC6   = ss   semitons
Bn 26 cc      CC38  = cc   cents      [opcional, e ignorável por spec]
Bn 65 7F      CC101 = 127  RPN Null
Bn 64 7F      CC100 = 127  RPN Null   -> destrava o Data Entry
```

O par RPN Null no fim não é decorativo: sem ele, qualquer CC6 posterior — inclusive um mandado por
engano por outro plugin — continua caindo no Pitch Bend Sensitivity.

E duas armadilhas de estado: **pitch bend sobrevive ao Program Change em GM2**, então um bend
residual vaza entre patches; e o RPN 0 é persistente, então uma faixa herdada de outra sessão
sobrevive em silêncio.

Sobre densidade de eventos: **não existe recomendação oficial.** Existe o teto físico — 320 µs por
byte a 31,25 kBaud, logo ≈1.042 mensagens/s numa porta DIN, compartilhados com todo o resto. O piso
não tem fonte, e há razão técnica: a interpolação entre eventos é feita **pelo instrumento**, não
pelo protocolo.

### 7.2 Modulation (CC1) — o que ela controla é uma aposta, exceto em GM2

A resposta honesta é em camadas, e a própria MMA admite a ambiguidade: *"The amount of modulation to
apply when the Modulation Wheel is moved has never been defined."*

| Fonte | O que garante |
|---|---|
| MIDI 1.0 | **nada** — só reserva o número |
| MMA CA-026 | "geralmente vibrato", explicitamente a critério do fabricante |
| GM1 | "a coisa mais natural" — e dá três exemplos incompatíveis entre si |
| **GM2** | **vibrato, obrigatoriamente.** LFO triangular ou senoidal, linear em cents, ±50 cents default |
| DLS 2 | três destinos possíveis (pitch, filtro, ganho), todos com peso default zero |

Fora de GM2, *mod wheel = vibrato* é aposta. Em biblioteca orquestral moderna o CC1 costuma ser
sequestrado para crossfade de dinâmica — comportamento sem nenhum respaldo em spec.

**Se o vibrato "não aparece", o culpado costuma ser o RPN 5, não o CC1.** ±50 cents é discreto. O
RPN 5 (`Modulation Depth Range`) é o que escala a profundidade, e um device GM2 tem que aceitar pelo
menos ±600 cents.

### 7.3 CC11 é dinâmica, CC7 é mixagem — e a razão está na letra da spec

A distinção não é preciosismo. O GM2 diz para que ela existe: *"This enables a listener, after the
fact, to adjust the relative mix of instruments **without destroying the dynamic expression** of that
instrument."*

**Fazer crescendo em CC7 é erro de arquitetura**, não de gosto: destrói exatamente a possibilidade
que justifica o CC11 existir.

A curva é normativa e é quadrática:

```
ganho_dB = 40 * log10(cc7/127) + 40 * log10(cc11/127)
```

| CC | dB |
|---|---|
| 127 | 0 |
| 96 | −4,9 |
| 64 | **−11,9** |
| 32 | −23,9 |
| 16 | −36,0 |
| 0 | **−∞** |

Três consequências que mudam como se desenha um swell:

- **Metade do curso já custa −11,9 dB.** Uma rampa linear de CC11 não é um crescendo linear em dB.
- **Os 20 dB inferiores estão espremidos abaixo de CC11 ≈ 13.** Rampa que começa em 0 tem salto
  audível no início, e é ali que o *zipper noise* aparece nos 128 passos.
- **CC11 = 0 é silêncio absoluto, não fade.** Swell que termina em 0 corta a cauda seca.

E a assimetria de estado: **CC11 volta a 127 em qualquer Reset All Controllers; CC7 não é resetado
por nada** — nem por CC121, nem por Program Change. Um CC7 baixo esquecido no topo da trilha é um
bug silencioso.

Sobre a **forma temporal** da rampa — linear, exponencial, S — **não existe recomendação da MMA**, e
a ausência é significativa: quando a MMA quer normatizar uma curva, ela publica a fórmula (Pan em
RP-037, volume em §3.3.4, taxa de chorus em §4.5). Aqui não publicou.

O que existe de medido é adjacente e precisa de rótulo: tons **descendentes** precisam ser ~4 dB mais
altos que ascendentes simétricos para serem percebidos com a mesma loudness (Ponsot, Susini &
Meunier, 2015 — tons de 2 s, variação de 15 dB). **Não é sobre CC11.** Está aqui como a evidência
mais próxima que existe, não como recomendação.

### 7.4 CC74 e os Sound Controllers — precisão histórica que importa

A MIDI 1.0 **4.2.1 (1996)** define apenas cinco Sound Controllers (70–74) e diz literalmente
`75-79 (no defaults)`. Os nomes Decay / Vibrato Rate / Depth / Delay vêm **exclusivamente da
RP-021** (1999). Citar "a spec MIDI 1.0" para CC75–78 sem qualificar a edição é impreciso.

| CC | Função | Natureza |
|---|---|---|
| 70 | Sound Variation | switch; decidido no Note-On, nota soando não muda |
| 71 | Timbre / Harmonic Intensity | **absoluto** |
| 72 | Release Time | relativo, null em 64 |
| 73 | Attack Time | relativo, null em 64 |
| 74 | **Brightness** — cutoff do filtro | **relativo**, null em 64 |
| 75–78 | Decay / Vib Rate / Vib Depth / Vib Delay | relativos, só a partir da RP-021 |

**CC71 é absoluto, CC74 é relativo.** CC74 = 0 não é "filtro fechado": é "o mais fechado que o preset
permite". No MPE o CC74 é a terceira dimensão de toque, e o valor precisa vir **antes do Note On** —
depois, perde-se o estado inicial da nota.

### 7.5 CC64 — binário por definição, e o padrão que soa humano

A regra geral de switches da spec: **0–63 é OFF, 64–127 é ON**. O GM2 reafirma para o damper. O
half-damper existe como **uma frase marcada `[optional]`** no GM2 §3.3.7 — é a única menção normativa
em todo o corpus MMA. Nenhum documento define que valor corresponde a que fração de levantamento,
nem se a resposta é linear.

> Isso **corrige** o que estava no §6.6 deste manual: a faixa 40–85 para meio-pedal descreve o
> comportamento de um instrumento medido, não um padrão. Num receptor conforme, 63 é OFF total e 64
> é ON total — o pedal pula.

E há um limite estrutural permanente: *"All controller numbers 64 and above have single-byte values
only, with no corresponding LSB."* Meio-pedal fica em 128 passos para sempre.

O padrão que soa humano é específico: CC64 alternando **127 / 0**, ficando em 127 a maior parte do
tempo e caindo a zero por um instante **logo depois** de cada mudança harmônica — depois, não junto.
E o GM2 **exige** que timbres de piano respondam a re-damper, isto é, damper pisado *após* o
note-off. Um CC64 quantizado para a grade cai antes do note-off e destrói o re-damper. É um dos tells
mais confiáveis de piano programado.

Duas regras de higiene: **libere o pedal antes de qualquer All Notes Off** (o hold tem prioridade
sobre CC123, e a trilha termina com notas presas); e **CC66 Sostenuto só prende o que já está
segurado** — nota tocada depois passa direto.

### 7.6 Vibrato de teclado — a inversão que o faz soar mecânico

No instrumento acústico a **taxa** é quase uma constante fisiológica e a **extensão** é o parâmetro
negociável. No sintetizador é o inverso: a taxa é um knob livre. **Essa inversão é a razão de
vibrato de synth soar mecânico.**

| | Taxa | Extensão |
|---|---|---|
| DLS Level 2 (synth, default) | **5 Hz** (faixa 0,1–20 Hz) | teto ±1.200 cents, default 0 |
| GM2 (synth, mod wheel cheia) | **não definida** | **±50 cents** |
| Voz lírica | **6,1 Hz** | < ±100 cents |
| Cordas e sopros | 5–6 Hz | **< ±50 cents** |
| Coro | — | **≤ ±10 cents** |

O DLS Level 2 é o **único valor absoluto de taxa de vibrato em todo o corpus MMA**. GM2 não define
Hz: o CC76 é relativo, sem unidade física. Qualquer número em Hz atribuído "ao padrão MIDI" é falso.

Dois fatos medidos que matam o LFO fixo:

- **A taxa varia ±10% entre notas do mesmo intérprete e acelera ~13% no fim da nota** (Prame 1992,
  10 cantores líricos, 25 notas — presente em todas as 25).
- **A extensão escala com a dinâmica**: 0,6–0,7 semitom em *pp* → ≈1,0 semitom em *ff*. Profundidade
  constante entre uma nota piano e uma forte é fisicamente impossível no acústico.

Sobre o **início** do vibrato, o resultado mais importante é negativo: Prame não achou **nenhum**
padrão de onset em 10 cantores profissionais. Não escreva "vibrato entra após N ms" — é invenção. O
que é consistente é o comportamento no **fim** da nota.

Do lado da máquina há convenção de implementação convergente: DLS fixa **10 ms de default, faixa até
10 s**; Diva e Pigments, dois fabricantes independentes, param o fade-in em ~20 s. E **CC78 não é
portável** — não existe mapeamento oficial CC78 → milissegundos em fabricante nenhum. Para resultado
reprodutível entre engines, desenhe o onset como rampa de CC1 saindo de 0 no note-on.

Vibrato em nota curta é tell de programação: se o onset real é da ordem de centenas de ms, nota mais
curta que isso não teria vibrato numa execução real.

### 7.7 Rhodes — a lacuna de performance é real, mas a de física não é

O §5 deste manual declarava Rhodes como lacuna completa. **Estava parcialmente errado.**

Não existe mesmo **nenhum** estudo publicado de performance de Rhodes — timing, dinâmica,
articulação. A confirmação é firme: varredura no arquivo completo do DAFx 1998–2025, OpenAlex com
quatro consultas, Crossref, arXiv (**0 resultados** para `all:Rhodes AND all:piano`), Semantic
Scholar e Zenodo. Há três papers de física e zero de performance.

Mas existe o **manual de serviço oficial da CBS/Fender (1979)**, que publica toda a geometria da ação
com tolerâncias, e existe medição física revisada por pares. Disso saem três regras acionáveis.

**O Rhodes não tem mecanismo de escape.** O martelo fica em contato direto com a tecla o tempo
inteiro — ação comparável à vienense. O que o fabricante chama de "escapement" é outra coisa: o vão
residual entre a ponta do martelo e a tine.

**Pressão sustentada do dedo abafa a nota.** Está na letra do fabricante, e é o oposto do piano de
cauda. Consequência em MIDI: **duração longa com overlap pesado é fisicamente implausível no
Rhodes.**

**Não existe curva de velocity canônica do Rhodes.** A "Dynamic Response" é definida pela fábrica
como *"percentage of volume increase in response to increased weight of touch"* — e depende do gap
pickup-tine, que é **ajustável**. O mesmo instrumento vai de pouco a muito dinâmico. E as pontas de
martelo vão de durômetro 30 no grave a 90 e *wrapped* no agudo, então uma curva global aplicada ao
teclado inteiro contraria o instrumento.

Um quarto fato, medido, que muda a forma de emular: **o "bark" do Rhodes vem do pickup, não da
tine.** A tine se move quase senoidalmente; o direct-out é muito mais complexo, e a diferença é o
campo magnético. Duas fontes independentes convergem. Emular Rhodes com saturação e filtro
pós-oscilador é conceitualmente correto; emular com inarmonicidade de corda é errado.

### 7.8 Hammond — velocity é irrelevante, e isso muda tudo

A tecla do Hammond é uma **chave**, não um martelo. *"The ORGAN Voice Section does not receive
Velocity"* (Hammond-Suzuki), e a Clavia confirma independentemente: *"Organ sounds will always be
played back at nominal level regardless of incoming MIDI Velocity data."* Com a curva em `Off`, o
valor fixo é **100**.

**Automação de velocity num patch de órgão é trabalho perdido.** Toda a dinâmica vem de **CC11**.

E CC7 no lugar de CC11 mata o instrumento: dá fade linear sem a mudança de timbre nem a interação
com o overdrive. *"Swell is not only a volume control - it also changes the character of the sound in
a special way."*

Três detalhes que denunciam órgão programado:

- **CC11 chegando a 0 não é o comportamento vintage.** No B-3 o pedal fechado ainda soa, com graves e
  agudos atenuados de forma desigual em torno de 800 Hz.
- **Legato total apaga a percussão.** Ela é single-trigger e só rearma quando **todas** as teclas são
  soltas. Acordes sobrepostos eliminam o efeito.
- **O key click existe no ataque *e* na soltura**, e é função de articulação, não de intensidade.
  Notas longas e legato fazem o click sumir, e o resultado vira órgão de igreja genérico.

> **Sobre os 70 ms de key click que circulam por aí:** não é a duração do click. É a constante de
> decaimento de um envelope **escolhido à mão** num modelo de síntese aditiva, e o artigo diz isso na
> própria frase. Duração e espectro medidos num B-3 real: não existem em fonte publicada.

Velocity não é 100% inútil: quando o clone oferece `VMC MODE = Velocity`, ela altera o **intervalo
entre os contatos** e portanto o caráter do click — nunca o nível.

### 7.9 O que denuncia teclado programado — e a ressalva que o gênero impõe

Três tells, em ordem de força:

1. **Acorde perfeitamente simultâneo.** Todos os note-ons no mesmo tick. É o mais forte, e o único
   com base medida — vem da literatura de duas mãos já fechada nas seções 0 a 3.
2. **Notas com 100% da duração nominal, coladas.** A referência quantificada é razão de articulação
   **0,75** para toda nota acima de **100 ms**.
3. **Notas repetidas idênticas** — mesma velocity, mesma duração, sem micropausa entre elas.

E dois de higiene de dados: controladores MIDI puxam a velocity para cima, e a correção documentada é
**multiplicativa (0,7–0,9)**, nunca subtrativa — offset fixo destrói a proporção entre as notas. Para
consertar timing de acorde sem matá-lo, desliza-se o *cluster* inteiro sem alterar o espaçamento
interno.

> **A ressalva que o gênero impõe, e ela é importante.** Em metalcore e rock eletrônico "programado"
> é estética, não falha — os próprios produtores do gênero programam sem se considerarem tecladistas
> e não tratam isso como defeito. Pelas fontes de produção, o que ainda soa **colado por cima** é
> **densidade errada e entrada em seção errada**, não a ausência de rubato.

O critério documentado de integração é o de Ken Andrews mixando Paramore: a camada está integrada
enquanto o ouvinte continua ouvindo *uma banda de guitarra*. E quando há teclado demais, a correção
citada **não é EQ — é mute seletivo por seção**, abrindo buracos em vez de espremer tudo.

---

## 8. Blocos de técnica — expressão

### 8.1 Pitch bend

```technique
{
  "name": "pitch_bend",
  "family": "keys",
  "summary": "Unico controlador continuo de altura do MIDI 1.0. Os dois bytes sao obrigatorios, e a faixa default e indefinida.",
  "verified": true,
  "description": "TRES ERROS CAROS. (1) Escrever so o MSB produz curva de 128 passos em vez de 16384 — a spec exige LSB E MSB, e diz por que: o ouvido e implacavel com degrau de altura. O degrau que se atribui ao MIDI quase sempre vem daqui, nao da taxa de eventos. (2) A ordem e LSB PRIMEIRO: En ll mm. (3) A faixa default e INDEFINIDA na MIDI 1.0 — quem fixa e GM1/GM2 (2 semitons) e o MPE (48 nos member channels), fator de 24x. Patch MPE-capaz em modo normal recebendo dado escrito para 2 semitons produz bend 24x maior; e o modo de falha mais escandaloso. CONCLUSAO: sempre enviar RPN 0 no inicio de toda trilha que use bend, e fechar com RPN Null, senao qualquer CC6 posterior continua caindo no Pitch Bend Sensitivity. Bend sobrevive ao Program Change em GM2 e vaza entre patches. Nao existe recomendacao oficial de eventos por segundo: existe o teto fisico, e o piso depende do smoothing do instrumento, nao do protocolo.",
  "parameters": [
    {"name": "centro", "value": 8192, "source": "MIDI 1.0 Detailed Spec 4.2.1 — data bytes 00 40, hex 2000"},
    {"name": "resolucao_passos", "value": 16384, "source": "MIDI 1.0 Detailed Spec 4.2.1 — 14 bits"},
    {"name": "passos_para_baixo", "value": 8192, "source": "DERIVADO da spec: centro 8192, minimo 0"},
    {"name": "passos_para_cima", "value": 8191, "source": "DERIVADO da spec: centro 8192, maximo 16383 — o range e assimetrico em 1 passo"},
    {"name": "range_default_gm", "value": 2, "source": "GM System Level 1 p.3 e GM2 v1.2a 3.4.1 — semitons, [required]"},
    {"name": "range_default_mpe_member", "value": 48, "source": "MPE v1.1 2.2.5 — semitons"},
    {"name": "range_minimo_exigido_gm2", "value": 12, "source": "GM2 v1.2a 3.4.1 — o device deve aceitar ao menos +-12"},
    {"name": "granularidade_cents_range_2", "value": 0.0244, "source": "DERIVADO: 400 cents / 16384 passos"},
    {"name": "teto_mensagens_por_segundo_din", "value": 1042, "source": "DERIVADO: 3 bytes x 320us a 31.25 kBaud, MIDI 1.0 Detailed Spec 4.2.1"},
    {"name": "eventos_por_segundo_recomendados", "source": null},
    {"name": "duracao_tipica_de_bend_em_teclado_ms", "source": null}
  ],
  "tools": {
    "generic": {
      "mensagem": "En ll mm — LSB primeiro",
      "centro": "En 00 40",
      "rpn_0": ["Bn 65 00", "Bn 64 00", "Bn 06 ss", "Bn 26 cc", "Bn 65 7F", "Bn 64 7F"],
      "note": "SEMPRE retornar a 8192 depois do Note Off; RPN Null no fim da sequencia nao e opcional"
    },
    "mpe": {"range_member_channel": 48, "range_manager_channel": 2, "note": "recomenda numero inteiro de semitons e LSB zero ou ausente"}
  }
}
```

### 8.2 Modulation

```technique
{
  "name": "modulation",
  "family": "keys",
  "summary": "CC1. O que ela controla e aposta fora de GM2 — a propria MMA diz que o efeito nunca foi definido.",
  "verified": true,
  "description": "A MMA e explicita: 'The amount of modulation to apply when the Modulation Wheel is moved has never been defined.' Em camadas: MIDI 1.0 nao garante nada, CA-026 diz 'geralmente vibrato mas a criterio do fabricante', GM1 diz 'a coisa mais natural' e da tres exemplos incompativeis, GM2 obriga vibrato com LFO triangular ou senoidal linear em cents, e DLS 2 permite tres destinos com peso default zero. FORA DE GM2, mod wheel igual a vibrato e APOSTA — em biblioteca orquestral o CC1 costuma ser sequestrado para crossfade de dinamica, sem respaldo em spec nenhuma. Se o vibrato NAO APARECE, o culpado costuma ser o RPN 5 e nao o CC1: 50 cents e discreto. CC1 nao e resetado por Program Change em GM2, entao um valor residual alto faz o proximo patch entrar com vibrato indesejado. Canais de ritmo nao devem responder a CC1.",
  "parameters": [
    {"name": "cc", "value": 1, "source": "MIDI 1.0 Detailed Spec 4.2.1, Table III — Modulation wheel or lever"},
    {"name": "cc_lsb", "value": 33, "source": "MIDI 1.0 Detailed Spec 4.2.1 — Controls 32-63 dao LSB para 0-31"},
    {"name": "default", "value": 0, "source": "GM2 v1.2a 3.3.2"},
    {"name": "profundidade_default_cents", "value": 50, "source": "GM2 v1.2a 3.4.4 — RPN 5 default 00H/40H, mod wheel cheia"},
    {"name": "alcance_minimo_exigido_cents", "value": 600, "source": "GM2 v1.2a 3.4.4"},
    {"name": "teto_dls_cents", "value": 1200, "source": "DLS 2.2 Table 6 — Vib LFO CC1 to Pitch"},
    {"name": "efeito_sonoro_garantido_fora_de_gm2", "source": null}
  ],
  "tools": {
    "generic": {"cc": 1, "escala": "RPN 5 — Bn 65 00 / Bn 64 05 + CC6/CC38", "forma_de_onda": "triangular ou senoidal, exigido por GM2", "curva": "linear em cents"},
    "gm2": {"cc": 1, "note": "unico regime em que CC1 = vibrato e garantido"},
    "rhodes": {"note": "sampler de Rhodes tipicamente usa CC1 para outra coisa; conferir o implementation chart antes"}
  }
}
```

### 8.3 Expression

```technique
{
  "name": "expression",
  "family": "keys",
  "summary": "CC11 e a dinamica escrita na musica; CC7 e o fader do canal. Trocar os dois destroi a possibilidade de remixar.",
  "verified": true,
  "description": "A separacao existe por um motivo declarado na spec: permitir ajustar a mix depois SEM destruir a expressao dinamica. Fazer crescendo em CC7 e erro de arquitetura, nao de gosto. A CURVA E QUADRATICA e normativa: ganho_dB = 40*log10(cc7/127) + 40*log10(cc11/127). Consequencias: metade do curso ja custa 11.9 dB, os 20 dB inferiores estao espremidos abaixo de CC11 igual a 13, e rampa linear de CC11 NAO e crescendo linear em dB. CC11 igual a 0 e silencio absoluto, nao fade — swell que termina em zero corta a cauda seca. ASSIMETRIA DE ESTADO: CC11 volta a 127 em qualquer Reset All Controllers; CC7 nao e resetado por nada, nem por CC121 nem por Program Change, entao CC7 baixo esquecido no topo da trilha e bug silencioso. NAO EXISTE recomendacao da MMA sobre a FORMA TEMPORAL da rampa, e a ausencia e significativa: quando a MMA quer normatizar curva, ela publica a formula.",
  "parameters": [
    {"name": "cc_expression", "value": 11, "source": "MIDI 1.0 Detailed Spec 4.2.1, Table III"},
    {"name": "cc_volume", "value": 7, "source": "idem — Channel Volume, formerly Main Volume"},
    {"name": "default_cc11", "value": 127, "source": "GM2 v1.2a 3.3.6, [required]"},
    {"name": "default_cc7", "value": 100, "source": "GM2 v1.2a 3.3.4, [required]"},
    {"name": "db_em_cc_64", "value": -11.9, "source": "GM2 v1.2a 3.3.4, tabela oficial"},
    {"name": "db_em_cc_96", "value": -4.9, "source": "GM2 v1.2a 3.3.4; o GM1 Developer Guidelines arredonda para -4.8, a formula da -4.86 e prevalece"},
    {"name": "db_em_cc_32", "value": -23.9, "source": "GM2 v1.2a 3.3.4"},
    {"name": "cc11_lsb", "value": 43, "source": "MIDI 1.0 Detailed Spec 4.2.1, Table III"},
    {"name": "forma_temporal_recomendada_da_rampa", "source": null}
  ],
  "tools": {
    "generic": {
      "formula_db": "40*log10(cc7/127) + 40*log10(cc11/127)",
      "cc_dinamica": 11,
      "cc_mixagem": 7,
      "note": "nunca terminar swell em 0; nunca fazer crescendo em CC7"
    },
    "hammond": {"cc": 11, "note": "o swell pedal E o CC11 — confirmado por Clavia e Hammond-Suzuki independentemente"}
  }
}
```

### 8.4 Damper pedal

```technique
{
  "name": "damper_pedal",
  "family": "keys",
  "summary": "CC64 e binario por definicao: 0-63 OFF, 64-127 ON. Meio-pedal nao e padronizado em lugar nenhum.",
  "verified": true,
  "description": "A regra geral de switches da spec e 0-63 OFF e 64-127 ON, e o GM2 reafirma para o damper. O HALF-DAMPER existe como UMA FRASE marcada [optional] no GM2 3.3.7, unica mencao normativa em todo o corpus MMA: nenhum documento define que valor corresponde a que fracao de levantamento nem se a resposta e linear. Num receptor conforme, 63 e OFF total e 64 e ON total, e o pedal PULA. Isso corrige keys.syncopated_pedal, cuja faixa 40-85 descreve um instrumento medido e nao um padrao. LIMITE PERMANENTE: controladores 64 e acima nao tem LSB, entao meio-pedal fica em 128 passos para sempre. O PADRAO QUE SOA HUMANO e CC64 alternando 127/0, ficando em 127 a maior parte do tempo e caindo a zero por um instante LOGO DEPOIS de cada mudanca harmonica — depois, nao junto. O GM2 EXIGE re-damper em timbres de piano, isto e, damper pisado APOS o note-off; CC64 quantizado para a grade cai antes e destroi o re-damper. HIGIENE: soltar o pedal antes de qualquer All Notes Off, porque o hold tem prioridade sobre CC123 e a trilha termina com notas presas.",
  "parameters": [
    {"name": "cc", "value": 64, "source": "MIDI 1.0 Detailed Spec 4.2.1, Table III — Damper pedal (sustain)"},
    {"name": "limiar_off_max", "value": 63, "source": "MIDI 1.0 Detailed Spec 4.2.1, Controller Effect; reafirmado em GM2 3.3.7"},
    {"name": "limiar_on_min", "value": 64, "source": "idem"},
    {"name": "default", "value": 0, "source": "GM2 v1.2a 3.3.7, [required]"},
    {"name": "cc_sostenuto", "value": 66, "source": "GM2 v1.2a 3.3.9 — prende apenas o que ja esta segurado"},
    {"name": "cc_soft", "value": 67, "source": "GM2 v1.2a 3.3.10 — binario mesmo em GM2, nao existe una corda gradual padronizado"},
    {"name": "passos_maximos_de_meio_pedal", "value": 128, "source": "MIDI 1.0 Detailed Spec 4.2.1 — controladores 64+ nao tem LSB"},
    {"name": "mapeamento_padronizado_de_meio_pedal", "source": null}
  ],
  "tools": {
    "generic": {"cc": 64, "padrao_humano": "127 a maior parte do tempo, queda a 0 logo DEPOIS de cada troca harmonica", "note": "nunca escrever valor intermediario esperando meio-pedal generico"},
    "rhodes": {"note": "o Rhodes nao tem o borrao harmonico do abafador de cauda; o modelo de pedal de piano nao porta"}
  }
}
```

### 8.5 Vibrato de teclado

```technique
{
  "name": "vibrato",
  "family": "keys",
  "summary": "No acustico a taxa e quase constante fisiologica e a extensao varia. No synth e o inverso — e essa inversao e o que soa mecanico.",
  "verified": true,
  "description": "GM2 NAO DEFINE taxa em Hz: CC76 e relativo, sem unidade fisica, e qualquer numero em Hz atribuido ao padrao MIDI e falso. O unico valor absoluto de taxa em todo o corpus MMA e o DLS Level 2: 5 Hz de default, faixa 0.1 a 20 Hz. DOIS FATOS MEDIDOS MATAM O LFO FIXO: a taxa varia cerca de 10 por cento entre notas do mesmo interprete e ACELERA cerca de 13 por cento no fim da nota, presente em 25 de 25 notas analisadas; e a extensao ESCALA COM A DINAMICA, de 0.6-0.7 semitom em pp para cerca de 1.0 em ff, o que torna profundidade constante fisicamente impossivel. SOBRE O INICIO o resultado mais importante e NEGATIVO: nao ha padrao de onset em 10 cantores profissionais. Nao escreva 'vibrato entra apos N ms' — e invencao; trate o onset como aleatorizado por nota. CC78 NAO E PORTAVEL: nao existe mapeamento oficial para milissegundos em fabricante nenhum, entao desenhe o onset como rampa de CC1 saindo de 0 no note-on. Vibrato em nota curta e tell de programacao. Nao use onda quadrada nem S&H: GM2 exige triangular ou senoidal.",
  "parameters": [
    {"name": "taxa_default_dls_hz", "value": 5.0, "source": "DLS 2.2 Table 5 — unico valor absoluto de taxa no corpus MMA"},
    {"name": "taxa_faixa_dls_hz", "range": [0.1, 20.0], "source": "DLS 2.2 Table 5 e 1.7.1.1"},
    {"name": "profundidade_gm2_cents", "value": 50, "source": "GM2 v1.2a 3.4.4 — mod wheel cheia, default"},
    {"name": "voz_lirica_taxa_hz", "value": 6.1, "source": "MEDIDO — Prame 1992, 10 cantores, gravacoes comerciais"},
    {"name": "aceleracao_no_fim_da_nota_pct", "value": 13, "source": "MEDIDO — Prame 1992, presente nas 25 notas analisadas"},
    {"name": "variacao_de_taxa_entre_notas_pct", "value": 10, "source": "MEDIDO — Prame 1992, intra e entre interpretes"},
    {"name": "cordas_e_sopros_extensao_cents", "value": 50, "source": "MEDIDO — Sundberg 1994, teto; muito menor que voz"},
    {"name": "coro_extensao_cents", "value": 10, "source": "MEDIDO — Sundberg 1994, teto"},
    {"name": "onset_delay_default_dls_ms", "value": 10, "source": "DLS 2.2 Table 5"},
    {"name": "onset_delay_faixa_dls", "range": [0.01, 10.0], "source": "DLS 2.2 Table 5 — 10 ms a 10 s, em segundos"},
    {"name": "onset_tipico_em_performance_ms", "source": null},
    {"name": "mapeamento_cc78_para_ms", "source": null}
  ],
  "tools": {
    "generic": {"cc_gesto": 1, "cc_rate": 76, "cc_depth": 77, "cc_delay": 78, "note": "CC76/77/78 sao RELATIVOS com null em 64 e nao sao portaveis; prefira rampa de CC1"},
    "gm2": {"forma_de_onda": ["triangular", "senoidal"], "curva": "linear em cents", "escala": "RPN 5"},
    "rhodes": {"note": "o vibrato do Rhodes de fabrica e tremolo (amplitude), nao vibrato de altura — nao modelar com pitch"}
  }
}
```

### 8.6 Toque do Rhodes

```technique
{
  "name": "rhodes_touch",
  "family": "keys",
  "summary": "Martelo em contato direto com a tecla, sem escape. Pressao sustentada do dedo ABAFA a nota — o oposto do piano de cauda.",
  "verified": true,
  "description": "NAO EXISTE curva de velocity canonica do Rhodes. A Dynamic Response e definida pela fabrica como percentual de aumento de volume em resposta ao peso do toque, e DEPENDE do gap pickup-tine, que e ajustavel: o mesmo instrumento vai de pouco a muito dinamico conforme a regulagem. Alem disso as pontas de martelo vao de durometro 30 no grave a 90 e wrapped no agudo, entao curva global aplicada ao teclado inteiro contraria o instrumento. CONSEQUENCIA MIDI DIRETA: duracao longa com overlap pesado e fisicamente implausivel, porque pressao sustentada do dedo abafa a nota quando o escapement e curto. Vocabulario: o Rhodes NAO tem mecanismo de escape; escapement no Rhodes e o vao residual entre a ponta do martelo e a tine. O BARK VEM DO PICKUP, nao da tine — a tine se move quase senoidalmente e a diferenca esta no campo magnetico, com duas fontes independentes convergindo. Emular com saturacao e filtro pos-oscilador e correto; emular com inarmonicidade de corda e errado. Velocidade de martelo muda o ESPECTRO, nao so o volume: velocity uniforme perde variacao de timbre, nao so dinamica. NAO EXISTE nenhum estudo de performance de Rhodes — confirmado em DAFx 1998-2025, OpenAlex, Crossref, arXiv, Semantic Scholar e Zenodo.",
  "parameters": [
    {"name": "key_dip_mm", "value": 9.525, "source": "Rhodes Keyboard Instruments Service Manual, CBS 1979, cap.4 — 3/8 pol +- 1/32"},
    {"name": "escapement_grave_mm", "range": [6.35, 9.525], "source": "idem, Fig 4-2; o proprio manual repete o grave como 4.762-9.525 noutra passagem"},
    {"name": "escapement_medio_mm", "range": [1.588, 3.175], "source": "idem, Fig 4-2"},
    {"name": "escapement_agudo_mm", "range": [0.794, 2.381], "source": "idem, Fig 4-2"},
    {"name": "escapement_ideal_mm", "value": 0.794, "source": "idem — inatingivel no grave pelo chicoteio da tine mais longa"},
    {"name": "gap_pickup_tine_mm", "range": [1.588, 3.175], "source": "idem, Volume Adjustment"},
    {"name": "gap_minimo_pos_1972_mm", "value": 0.508, "source": "idem — regioes media e aguda"},
    {"name": "curva_velocidade_de_tecla_para_spl", "source": null},
    {"name": "conversao_velocity_midi_para_forca_de_martelo", "source": null},
    {"name": "timing_dinamica_ou_articulacao_medidos_em_performance", "source": null}
  ],
  "tools": {
    "generic": {"note": "sem CC nem keyswitch especifico do Rhodes em spec nenhuma"},
    "rhodes": {
      "durometro_por_regiao": {"1-30": 30, "31-40": 50, "41-50": 70, "51-64": 90, "65-88": "wrapped"},
      "note": "split de curva de velocity por regiao acompanha o durometro; curva unica contraria o instrumento",
      "banda_morta_dos_modelos_hz": [1000, 2000]
    }
  }
}
```

### 8.7 Dinâmica de Hammond

```technique
{
  "name": "hammond_dynamics",
  "family": "keys",
  "summary": "A tecla e uma chave, nao um martelo. Velocity nao faz nada no volume — toda a dinamica e CC11.",
  "verified": true,
  "description": "AUTOMACAO DE VELOCITY NUM PATCH DE ORGAO E TRABALHO PERDIDO. Dois fabricantes independentes confirmam que o motor tonewheel ignora velocity, e com a curva em Off o valor fixo e 100. CC7 no lugar de CC11 mata o instrumento: da fade linear sem a mudanca de timbre nem a interacao com overdrive, porque o swell nao e so volume. TRES TELLS. CC11 chegando a 0 nao e o comportamento vintage: no B-3 o pedal fechado ainda soa, com graves e agudos atenuados de forma desigual em torno de 800 Hz. Legato total apaga a percussao, que e single-trigger e so rearma quando TODAS as teclas sao soltas. E o key click existe no ataque E na soltura, sendo funcao de articulacao e nao de intensidade — notas longas e legato fazem o click sumir e o resultado vira orgao de igreja generico. Velocity nao e 100 por cento inutil: quando o clone oferece VMC MODE igual a Velocity, ela altera o intervalo entre os contatos e portanto o carater do click, nunca o nivel. ATENCAO aos 70 ms de key click que circulam: e constante de decaimento de um envelope escolhido a mao num modelo de sintese, nao medicao.",
  "parameters": [
    {"name": "velocity_fixa_com_curva_off", "value": 100, "source": "Hammond SKX PRO Reference Guide p.57"},
    {"name": "contatos_por_tecla", "value": 9, "source": "Hammond SKX PRO RG p.165 — 9 molas planas contra 9 barramentos"},
    {"name": "cc_dinamica", "value": 11, "source": "Nord Electro 2 Manual pp.15 e 41; Hammond SKX PRO RG p.570"},
    {"name": "fronteira_da_curva_de_loudness_hz", "value": 800, "source": "Hammond SKX PRO RG pp.571-572 — LIMIT HF acima, LIMIT LF abaixo"},
    {"name": "atenuacao_maxima_do_limit_db", "value": -40, "source": "idem"},
    {"name": "corte_de_agudos_do_leslie_122_hz", "value": 6000, "source": "Hammond SKX PRO RG p.188 — para reduzir key click"},
    {"name": "corte_de_agudos_do_leslie_147_hz", "value": 8000, "source": "idem"},
    {"name": "duracao_medida_do_key_click_em_b3_real", "source": null},
    {"name": "espectro_medido_do_key_click", "source": null},
    {"name": "faixa_em_db_do_pedal_de_um_b3_real", "source": null}
  ],
  "tools": {
    "generic": {"cc": 11, "note": "nunca automatizar velocity; nunca usar CC7 para dinamica; nao terminar CC11 em 0"},
    "hammond": {
      "cc_drawbars_superior": [16, 17, 18, 19, 20, 21, 22, 23, 24],
      "cc_drawbars_inferior": [70, 71, 72, 73, 74, 75, 76, 77, 78],
      "cc_percussao_on_off": 87,
      "cc_rotor_speed": 82,
      "aviso": "o mapa de drawbars do Nord COLIDE com os Sound Controllers CC70-CC78 da MMA; em setup multitimbral isso e conflito real"
    }
  }
}
```

### 8.8 Articulação humana

```technique
{
  "name": "human_articulation",
  "family": "keys",
  "summary": "Tres tells de teclado programado: acorde simultaneo, nota com 100% da duracao, e notas repetidas identicas.",
  "verified": true,
  "description": "EM ORDEM DE FORCA. (1) Acorde perfeitamente simultaneo, todos os note-ons no mesmo tick, e o tell mais forte e o unico com base medida — vem da literatura de duas maos ja fechada nas secoes 0 a 3 deste manual. (2) Nota com 100 por cento da duracao nominal, colada na seguinte: a referencia quantificada e razao de articulacao 0.75 para toda nota acima de 100 ms. (3) Notas repetidas identicas, mesma velocity e mesma duracao, sem micropausa entre elas. HIGIENE DE DADOS: controladores MIDI puxam a velocity para cima, e a correcao documentada e MULTIPLICATIVA de 0.7 a 0.9, nunca subtrativa — offset fixo destroi a proporcao entre as notas. Para consertar timing de acorde sem mata-lo, deslize o cluster INTEIRO sem alterar o espacamento interno, posicionando o centro nocional a cerca de um terco da distancia entre o primeiro e o ultimo onset. RESSALVA DO GENERO: em metalcore e rock eletronico programado e estetica, nao falha, e os proprios produtores do genero programam sem se considerarem tecladistas. Pelas fontes de producao o que ainda soa colado por cima e DENSIDADE ERRADA e ENTRADA EM SECAO ERRADA, nao ausencia de rubato.",
  "parameters": [
    {"name": "razao_de_articulacao", "value": 0.75, "source": "MEDIDO — Friberg, Bresin & Sundberg 2006, regra Overall articulation k=1; ressalva: e sobre rendering expressivo em geral, nao sobre teclado em metal"},
    {"name": "limiar_de_aplicacao_ms", "value": 100, "source": "idem — aplica a toda nota mais longa que isso"},
    {"name": "multiplicador_de_velocity_min", "value": 0.7, "source": "CONVENCAO — Mike Senior, Sound on Sound, Better MIDI Pianos"},
    {"name": "multiplicador_de_velocity_max", "value": 0.9, "source": "idem; exemplo trabalhado 0.85"},
    {"name": "centro_nocional_do_acorde", "value": 0.33, "source": "CONVENCAO — mesma fonte; fracao da distancia entre o primeiro e o ultimo onset"},
    {"name": "micropausa_entre_notas_repetidas_ms", "source": null},
    {"name": "faixa_de_velocity_de_take_humano_de_teclado", "source": null}
  ],
  "tools": {
    "generic": {
      "note": "nunca alinhar note-ons de acorde no mesmo tick; nunca aplicar quantizacao em massa",
      "correcao_de_velocity": "multiplicativa 0.7-0.9 na faixa inteira antes de qualquer edicao fina"
    }
  }
}
```

---

## 9. Lacunas declaradas

| Item | Situação |
|---|---|
| Desvio padrão do melody lead por voz | plotado graficamente no artigo, não impresso em número |
| ~~Conversão de m/s para velocity MIDI~~ | **fechada** — Goebl & Bresin 2003, equação logarítmica na §2 |
| ~~Relação de velocity entre mão esquerda e direita~~ | **fechada** — direita 10 a 20% mais forte |
| ~~Espalhamento de acorde arpejado, e se acelera~~ | **fechada** — ISMIR 2015: 30–120 ms totais, acelerando |
| ~~Extensão de mão em semitons~~ | **fechada** — Wagner 1988 / Parncutt 1997, §3 |
| ~~Offset em ms do pedal sincopado~~ | **fechada** — 50 a 150 ms, Lehtonen 2007 |
| Notas por mão em textura densa, ponto de divisão entre as mãos | convenção de ensino apenas |
| Valores de jazz e pop para adiantamento de mão | citados mas não medidos nas fontes alcançadas |
| **Rhodes, qualquer medição** | **lacuna completa** — nada publicado |

---

## Fontes

- [Goebl, W. (2001). Melody lead in piano performance: expressive device or artifact? *JASA* 110(1):563–572](http://iwk.mdw.ac.at/goebl/papers/Goebl_JASA2001_melodyLead.pdf)
- [Goebl, Flossmann & Widmer (2010). Investigations into between-hand synchronization in Magaloff's Chopin. *Computer Music Journal* 34(3):35–44](http://iwk.mdw.ac.at/goebl/papers/GoeblFlossmannWidmer-CMJ2010-Async.pdf)
- Palmer (1989) *JEP:HPP* 15:331–346 e Palmer (1996) *Music Perception* 14:23–56 — via Goebl 2001
- [Repp (1996). Patterns of note onset asynchronies in expressive piano performance. *JASA* 100:3917–3932](https://doi.org/10.1121/1.417245) — pago, citado via Goebl 2001
- Askenfelt & Jansson (1990, 1991) *JASA* 88 e 90 — medição da ação do Steinway B, via Goebl 2001
- Repp (1997). The effect of tempo on pedal timing in piano performance. *Psychological Research* 60:164–172 — resumo
- [Bernays & Traube (2014). *Frontiers in Psychology* 5:157](https://doi.org/10.3389/fpsyg.2014.00157)
- Goebl & Bresin (2003). *JASA* 114:2273–2283 — conversão de velocity de martelo para MIDI no Yamaha Disklavier
- Fu, Xia, Dannenberg & Wasserman (2015), ISMIR — espalhamento de acorde arpejado
- Lehtonen, Penttinen, Rauhala & Välimäki (2007). Analysis and modeling of piano sustain-pedal effects — reanálise de Repp 1996b/1996c/1997
- Wagner (1988) e Parncutt et al. (1997) — ergonomia de extensão de mão, amostra de mais de 200 pianistas
- [Chi et al. (2021) *Appl Ergon*, PMID 34246074](https://pubmed.ncbi.nlm.nih.gov/34246074/) · [Turner et al. (2026) *Appl Ergon*, PMID 41385802](https://pubmed.ncbi.nlm.nih.gov/41385802/) · [Sakai et al. (2006) *J Hand Surg Am*, PMID 16713851](https://pubmed.ncbi.nlm.nih.gov/16713851/)
