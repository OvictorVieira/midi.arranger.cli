# Técnicas de transição em MIDI — manual de execução

> **Para que serve este documento.** A IA decide *o que* a música precisa. Este arquivo diz *como
> fazer aquilo em MIDI* para os eventos que costuram uma seção na seguinte: riser/downer, impacto e
> reverse/meia-lua (issue #23).
>
> **Regra de fonte.** Número com fonte vem citado. Número sem fonte vem marcado `[NÃO VERIFICADO]` —
> use, mas confira de ouvido antes de tratar como lei. Nunca apresente um `[NÃO VERIFICADO]` ao
> usuário como fato.

---

## 1. Proveniência dos números deste manual

A issue #23 descreve **comportamento** ("rampa até o downbeat", "três intensidades distintas",
"formato de meia-lua"), não números de referência medidos. Todo parâmetro numérico abaixo é
`CONVENCAO — <razão>` — escolha do motor para tornar o comportamento descrito operacional em MIDI,
nunca uma medição. Nenhuma técnica deste manual está `verified: true`.

---

## 2. Riser e downer

### 2.1 Comportamento

`riser`: rampa que sobe em direção ao downbeat da seção seguinte, terminando **antes** do primeiro
transiente da nova seção — um riser que invade o downbeat suja o ataque dela. Reaproveita o padrão de
um loop (lista de graus, mesma convenção de `element.degrees` que arp/sub já usam) subindo de altura
e de intensidade, como se o próprio loop principal virasse ruído ascendente. Emite CC de filtro
(CC74) e expression (CC11) acompanhando a rampa — os dois sempre monotônicos crescentes.

`downer`: a mesma mecânica, invertida — desce em vez de subir (registro descendente, CC
decrescente), mesmos números do riser.

### 2.2 Parâmetros

```technique
{
  "name": "riser",
  "family": "transitions",
  "summary": "Rampa ascendente que termina antes do downbeat da secao seguinte; CC de filtro e expression acompanham monotonicamente.",
  "verified": false,
  "description": "[NAO VERIFICADO] Ciclo de notas sobre `degrees` (mesma convencao de grau de escala 1-based de tools.validators.harmony.degrees_pcs), subindo do fundo ao topo do registro declarado ao longo de `duration_bars`. Velocity cresce dentro de `velocity_range`. CC74 e CC11 sobem monotonicamente dentro das faixas declaradas, em `cc_steps` pontos, terminando pelo menos `gap_before_boundary_ms` ANTES do downbeat da secao seguinte -- a mesma folga vale para o ultimo evento de nota. `downer` (mesma familia) le os MESMOS parametros e inverte a direcao (registro descendente, CC decrescente) -- nao ha bloco de tecnica separado para nao duplicar numero.",
  "parameters": [
    {"name": "duration_bars_range", "range": [1, 2], "source": "CONVENCAO — issue #23: 'Duracao configuravel em compassos, default 1 a 2'."},
    {"name": "gap_before_boundary_ms", "value": 30, "source": "CONVENCAO — issue #23 exige que 'riser que invade o downbeat suja o ataque'; 30ms de folga garante que o ultimo evento (nota e CC) sempre termine estritamente antes do downbeat, inaudivel como atraso, suficiente para nunca colidir com o transiente."},
    {"name": "notes_per_bar", "value": 8, "source": "CONVENCAO — densidade de colcheia para o loop reaproveitado como fonte de ruido ascendente; mesma ordem de grandeza de STEPS_PER_BAR/2 (tools/palette/rhythmic.py) usada no resto da paleta."},
    {"name": "velocity_range", "range": [60, 118], "source": "CONVENCAO — curva de intensidade crescente sem estourar o teto de velocity ao chegar perto do downbeat."},
    {"name": "cc_filter_range", "range": [20, 115], "source": "CONVENCAO — varredura tipica de filtro em riser eletronico: comeca fechado, abre quase total antes do downbeat."},
    {"name": "cc_expression_range", "range": [50, 120], "source": "CONVENCAO — acompanha o filtro, mesma logica de intensidade crescente."},
    {"name": "cc_steps", "value": 16, "source": "CONVENCAO — numero de mensagens da rampa de CC; continuo o bastante para soar suave, baixo o bastante para nao pressionar a porta DIN (mesma preocupacao pratica ja documentada em keys.pitch_bend e bass.sub_drop)."}
  ],
  "tools": {
    "generic": {"note": "loop/sample reaproveitado como fonte; CC74=filtro, CC11=expression"}
  }
}
```

---

## 3. Impacto

### 3.1 Comportamento

Hit no downbeat da transição. Três intensidades distintas (suave/média/forte), para o mesmo impacto
não se repetir idêntico ao longo da música — a intensidade cicla deterministicamente pela ordem de
ocorrência (`occurrence_index`), nunca por sorteio sem origem. Camadas com ataque alinhado (todas
soam exatamente no downbeat) mas caudas com durações diferentes.

### 3.2 Parâmetros

```technique
{
  "name": "impact",
  "family": "transitions",
  "summary": "Hit alinhado no downbeat da transicao, em camadas com caudas divergentes; intensidade cicla entre tres niveis por ocorrencia.",
  "verified": false,
  "description": "[NAO VERIFICADO] Todas as camadas (`layer_count`) atacam exatamente em `boundary_s` -- mesmo tick. Cada camada usa uma duracao de cauda diferente (`tail_durations_s`, ordenada da mais curta a mais longa). A intensidade (soft/medium/hard) e escolhida por `occurrence_index % 3` sobre `INTENSITY_LEVELS` -- ciclo deterministico, nao sorteio -- e define a faixa de velocity de TODAS as camadas daquele impacto. Roteamento recomendado: Logic Sampler (instrumento fica a cargo do plano/brief, esta tecnica nao hardcoda plugin).",
  "parameters": [
    {"name": "layer_count", "value": 3, "source": "CONVENCAO — issue #23: 'Ataques alinhados entre camadas, mas caudas com duracoes diferentes' — tres camadas dao espessura audivel sem empilhar um numero arbitrario."},
    {"name": "velocity_soft_range", "range": [60, 75], "source": "CONVENCAO — banda de intensidade 'suave', sem sobrepor a banda media."},
    {"name": "velocity_medium_range", "range": [85, 100], "source": "CONVENCAO — banda de intensidade 'media'."},
    {"name": "velocity_hard_range", "range": [108, 127], "source": "CONVENCAO — banda de intensidade 'forte', ate o teto de velocity."},
    {"name": "tail_durations_s", "value": [0.15, 0.6, 1.8], "source": "CONVENCAO — tres caudas de ordem de grandeza crescente (transiente curto, corpo medio, cauda longa), tipico de impacto em camadas; nao e medicao."}
  ],
  "tools": {
    "generic": {"note": "camadas empilhadas no mesmo canal/pitch por indice; instrumento real sugerido no plano, ex. Logic Sampler"}
  }
}
```

---

## 4. Reverse e meia-lua

### 4.1 Comportamento

Swell ou cauda reversa terminando **exatamente** no downbeat da seção seguinte. Curva de CC de
volume (CC7) e filtro (CC74) em formato de meia-lua: sobe durante a maior parte da janela e resolve
(desce) bem no fim, fechando exatamente no downbeat — diferente do riser, que só sobe e nunca chega a
descer. Suporta o modo `freeze`: em vez de gerar um pitch novo, congela e reverte o último evento
(pitch/velocity) da seção anterior como fonte do swell.

### 4.2 Parâmetros

```technique
{
  "name": "reverse",
  "family": "transitions",
  "summary": "Swell/cauda reversa que resolve exatamente no downbeat; CC de volume e filtro em formato de meia-lua (sobe e desce).",
  "verified": false,
  "description": "[NAO VERIFICADO] Uma nota sustentada de `boundary_s - duration_s` ate `boundary_s` (fim EXATO no downbeat, nunca antes nem depois). CC7 e CC74 formam um arco: sobem ate `resolved_fraction` da janela, entao descem ate `resolved_value_ratio` do pico no ultimo evento, cujo tempo e `boundary_s` -- a curva sobe E resolve, ao contrario do riser (so sobe). Modo `freeze`: quando a fonte e o ultimo evento da secao anterior (pitch/velocity fornecidos pelo chamador), o pitch do swell e o pitch congelado em vez do centro do registro declarado.",
  "parameters": [
    {"name": "duration_bars_range", "range": [0.5, 1.0], "source": "CONVENCAO — issue #23: 'Comprimento configuravel; default meio compasso a um compasso'."},
    {"name": "cc_volume_range", "range": [15, 118], "source": "CONVENCAO — CC7 sobe do quase-silencio ate quase o teto antes de resolver."},
    {"name": "cc_filter_range", "range": [25, 120], "source": "CONVENCAO — CC74 acompanha o volume no mesmo arco."},
    {"name": "resolved_fraction", "value": 0.85, "source": "CONVENCAO — fracao da janela onde a curva atinge o pico antes de comecar a resolver; os 15% finais formam a descida da meia-lua ate o downbeat."},
    {"name": "resolved_value_ratio", "value": 0.35, "source": "CONVENCAO — o valor final, no downbeat, fica em 35% do pico: resolve sem cair a zero, preparando a entrada da nova secao sem silencio abrupto."},
    {"name": "cc_steps", "value": 12, "source": "CONVENCAO — mesma pratica de bass.sub_drop.pitch_bend_curve_steps: continuo o bastante, sem pressionar a porta DIN."}
  ],
  "tools": {
    "generic": {"note": "swell/cauda reversa de sample; CC7=volume, CC74=filtro"}
  }
}
```
