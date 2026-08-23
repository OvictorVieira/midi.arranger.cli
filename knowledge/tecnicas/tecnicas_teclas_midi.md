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

## 5. Rhodes — lacuna completa

**Não existe medição publicada** de performance em Rhodes ou piano elétrico no material que alcancei.
Tudo acima vem de piano acústico de cauda.

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
    {"name": "fhv_faixa_dinamica_total", "range": [0.21, 4.26], "source": "Goebl 2001, nota 6"}
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
    {"name": "perfil", "value": "acelerado_nao_linear", "source": "ISMIR 2015 — intervalos diminuem progressivamente do grave para o agudo"}
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
  "description": "Mandar CC64 junto com o note-on, ou antes dele, captura as notas do acorde ANTERIOR e suja a harmonia. O pedal desce depois que o acorde ja soou. O atraso diminui conforme o andamento sobe. Tambem medido: o tempo do pedal nao e invariante e os deslocamentos dele sao proporcionalmente MENORES que as mudancas no tempo dos acordes — o pe nao escala junto com as maos. E pedal nao e liga-desliga: meio-pedal opera entre 40 e 85 de CC64, mantendo os abafadores em contato leve para ressonancia simpatica sem sustentacao prolongada.",
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

## 7. Lacunas declaradas

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
