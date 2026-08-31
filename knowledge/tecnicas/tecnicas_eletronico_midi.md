# Técnicas de elementos eletrônicos em MIDI — manual de execução

> **Para que serve este documento.** A IA decide *o que* a música precisa. Este arquivo diz *como fazer
> aquilo em MIDI* para os elementos rítmicos eletrônicos do arranjador: hi-hat eletrônico, percussão
> eletrônica sobre a caixa, chop de vocal, sub de breakdown e sub-drop pontual.
>
> **Regra de fonte.** Número com fonte vem citado. Número sem fonte vem marcado `[NÃO VERIFICADO]` —
> use, mas confira de ouvido antes de tratar como lei. Nunca apresente um `[NÃO VERIFICADO]` ao
> usuário como fato.

---

## 1. Proveniência dos números deste manual — leia antes de confiar em qualquer valor

Os parâmetros de `drums.hat_elec` abaixo vêm de **medição direta de UM arranjo de referência real
do usuário/dono deste projeto** (issue #22 — victor.h.souza.vieira@gmail.com), feita por ele sobre o
próprio material de referência. Isso é diferente de:

- **Fonte publicada e verificável** (artigo, paper, documentação de plugin) — o leitor deste manual não
  tem acesso ao arranjo de referência bruto, só à afirmação do autor do projeto de que mediu aqueles
  números naquele arranjo específico.
- **Convenção de ofício sem medição nenhuma** (`CONVENCAO — <razão>`) — isso mentiria sobre a natureza
  do número, que É uma medição, só que de amostra única e não replicável por quem lê o manual.

Por isso os parâmetros medidos citam explicitamente **"Medição direta de arranjo de referência real do
usuário/dono do projeto (issue #22)"** como `source` — fonte primária declarada, não fonte publicada, e
**não generalização de gênero**: é o comportamento de UM arranjo eletrônico específico, não um estudo
estatístico de referências. Trate como ponto de partida plausível, não como lei do estilo eletrônico
rítmico em geral.

Os parâmetros de `bass.sub` e `bass.sub_drop` **não têm nenhum número na issue #22** — ela
só descreve comportamento ("envelope compatível com a duração do riff", "primeiro impacto pode ser
maior", "queda de pitch via pitch bend"), sem medição numérica. Todo parâmetro numérico desses dois
blocos é `CONVENCAO — <razão>` declarada, escolha do motor para tornar o comportamento descrito
operacional em MIDI.

---

## 2. Hi-hat eletrônico

### 2.1 Comportamento

Hi-hat eletrônico de bateria eletrônica/trap: pitch fixo (nunca varia dentro da track — é uma única
amostra/sample, não uma peça acústica com abertura variável), gate uniforme aproximando uma
semicolcheia, 100% monofônico (hits encostando um no outro, zero overlap), velocity e timing com
variação humana pequena e um viés sutil de adiantamento. Padrão configurável: semicolcheias contínuas,
semicolcheias com lacunas, ou half-time (colcheias).

### 2.2 Parâmetros

```technique
{
  "name": "hat_elec",
  "family": "drums",
  "summary": "Hi-hat eletronico monofonico: pitch fixo, gate de semicolcheia escalando com o BPM real do arquivo, velocity e offset com vies levemente adiantado.",
  "verified": true,
  "description": "Pitch NUNCA varia dentro da track (amostra unica, nao peca acustica). Gate mede-se em ms a um BPM de referencia e escala PROPORCIONALMENTE ao BPM real do arquivo sendo processado (gate_ratio = gate_ms_at_reference_bpm / step_ms_at_reference_bpm, aplicado ao step real). 100% monofonico: duracao de cada hit e sempre cortada antes do onset do proximo, mesmo quando o offset empurra dois hits para perto. Padroes suportados: 'sixteenth' (16as continuas), 'gaps' (16as com lacunas deterministicas), 'half_time' (colcheias).",
  "parameters": [
    {"name": "pitch", "value": 70, "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22) — mapeamento MIDI do hi-hat eletronico daquele arranjo especifico. Nao e convencao General MIDI (GM 42 e Closed Hi-Hat); nao generaliza para outro kit eletronico."},
    {"name": "reference_bpm", "value": 174, "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22) — BPM da faixa de referencia onde o gate foi medido."},
    {"name": "gate_ms_at_reference_bpm", "range": [83, 86], "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22) — duracao medida do hit de hi-hat naquele arranjo, a 174bpm. tools/palette/electronic.py escala esse valor proporcionalmente ao BPM real do arquivo em vez de aplicar o ms fixo."},
    {"name": "velocity_range", "range": [79, 113], "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22)."},
    {"name": "velocity_mean", "value": 95, "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22)."},
    {"name": "velocity_stdev", "value": 8, "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22)."},
    {"name": "offset_range_ms", "range": [-20, 20], "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22)."},
    {"name": "offset_bias_ms", "value": -4, "source": "Medicao direta de arranjo de referencia real do usuario/dono do projeto (issue #22) — vies levemente adiantado (nota cai, em media, 4ms antes do grid)."},
    {"name": "offset_stdev_ms", "value": 8, "source": "CONVENCAO — a issue #22 declara faixa (+-20ms) e vies (-4ms) do offset, mas nao um desvio-padrao. Usa a mesma ordem de grandeza do desvio de velocity medido (8) para produzir uma distribuicao plausivel dentro da faixa declarada, sem inventar uma precisao que a medicao nao deu."}
  ],
  "tools": {
    "generic": {"notes": [70], "note": "pitch fixo, sem variacao — trate como amostra de sample player, nao como peca de kit acustico"}
  }
}
```

---

## 3. Sub de breakdown e sub-drop

### 3.1 Comportamento

`sub`: linha de sub-bass sustentada de breakdown eletrônico. Segue tônica (nota pedal), o padrão do
kick (`analysis.kick_positions`), ou o contorno do riff (via `element.degrees`, mesmo mecanismo já usado
por outros roles da paleta) — escolha declarada em `element.pattern.follow`. O primeiro impacto de cada
seção pode soar mais forte que as repetições seguintes (acento estrutural de entrada). **Nunca gera
acorde: nota única sempre, sem exceção nem flag** — não existe parâmetro que ligue polifonia neste
elemento.

`sub_drop`: evento pontual, dispara na fronteira de uma seção declarada, com queda de pitch via pitch
bend (curva monotônica descendente). Também monofônico por construção — é um único evento, não uma
sequência.

### 3.2 Parâmetros

```technique
{
  "name": "sub",
  "family": "bass",
  "summary": "Sub-bass monofonico de breakdown: segue tonica, kick ou contorno do riff; primeiro impacto acentuado.",
  "verified": false,
  "description": "[NAO VERIFICADO] Nota unica sempre — nunca acorde, sem excecao. 'follow=tonic': uma nota pedal por bar cobrindo o bar inteiro. 'follow=kick': onset em cada analysis.kick_positions da secao, duracao ate o proximo kick ou fim da secao (o padrao do kick aproxima o ritmo do riff). 'follow=riff': usa element.degrees (mesma lista de graus que arp/rhythmic_machine ja aceitam) sobre a raiz do acorde/tom, um grau por beat.",
  "parameters": [
    {"name": "first_impact_velocity_boost", "value": 18, "source": "CONVENCAO — a issue #22 pede 'primeiro impacto pode ser maior que as repeticoes' sem numero. 18 pontos acima da velocity base (bucket 'normal', 82-105 em tools/constants.py) da acento perceptivel sem estourar o teto de 127 quando a base ja esta perto do topo da faixa."},
    {"name": "repeat_velocity_jitter", "value": 3, "source": "CONVENCAO — a issue #22 nao fala de variacao entre repeticoes, mas repeticao com pitch/velocity/duracao IDENTICOS em 6+ notas dispara o validador de artificialidade desta base (tools/validators/artifice.py, PATTERN_REPEATED_NOTES). +-3 de velocity e pequeno o bastante para nao contradizer 'nota unica sustentada' e grande o bastante para quebrar a robotizacao."}
  ],
  "tools": {
    "generic": {"note": "sample de sub-bass — pitch unico por onset, sem stacking"}
  }
}
```

```technique
{
  "name": "sub_drop",
  "family": "bass",
  "summary": "Evento pontual em fronteira de secao: nota unica com queda de pitch via pitch bend monotonico.",
  "verified": false,
  "description": "[NAO VERIFICADO] Um evento por fronteira de secao declarada — nunca uma sequencia. Nota unica no registro grave, pitch bend desce de 0 ate o valor minimo negociado (curva monotonica, sem re-subida durante a nota) ao longo de pitch_bend_curve_ms. tools/render.py grava pitchwheel bruto (sem negociar RPN de bend range) — o plugin alvo precisa ter o bend range configurado manualmente para a queda soar como o esperado (ex.: +-24 semitons); isso e limitacao documentada, nao lacuna silenciosa.",
  "parameters": [
    {"name": "duration_beats", "value": 1.0, "source": "CONVENCAO — issue #22 nao da duracao para o evento pontual; 1 beat cobre o tempo tipico de um impacto de drop antes do silencio/proxima secao, sem prender a nota por compassos inteiros."},
    {"name": "pitch_bend_curve_ms", "value": 400, "source": "CONVENCAO — issue #22 nao da tempo de descida; 400ms e audivelmente uma queda (nao instantanea, nao arrastada), escolha do motor."},
    {"name": "pitch_bend_curve_steps", "value": 12, "source": "CONVENCAO — numero de mensagens pitchwheel intermediarias na rampa; alto o bastante para soar continuo, baixo o bastante para nao pressionar a porta DIN (mesma preocupacao pratica documentada em keys.pitch_bend)."}
  ],
  "tools": {
    "generic": {"note": "pitchwheel bruto, sem RPN de bend range — configure o range no plugin alvo"}
  }
}
```
