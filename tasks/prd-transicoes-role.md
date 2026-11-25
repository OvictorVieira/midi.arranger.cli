# PRD: Roles de plano para subdivision_flip, half_time_magnifier e false_downbeat

## Introdução

`tools/palette/transitions.py` já entrega três mecânicas de transição (issue #23,
última seção) como funções puras e testadas: `generate_subdivision_flip`,
`half_time_drum_pattern` e `false_downbeat_delay_s`. Nenhuma delas está amarrada
a um `role` ou campo de `element.pattern` do plano — hoje são código morto do
ponto de vista de quem escreve um `ArrangementPlan`. `riser`, `downer`, `impact`
e `reverse` já passaram por essa integração no mesmo módulo e servem de
precedente direto de como declarar role, validar em `plan.py`/`contract.py` e
despachar em `render.py`.

Este PRD cobre só a integração: nenhum número novo de manual, nenhuma mudança
de comportamento das três funções puras.

## Goals

- `subdivision_flip` e `half_time_magnifier` viram roles de plano dedicados,
  seguindo o mesmo padrão de `RISER_ROLES`/`DOWNER_ROLES`/`IMPACT_ROLES`/`REVERSE_ROLES`.
- `false_downbeat` vira um campo em `element.pattern` do elemento (riff) que
  sofre o atraso — não um role próprio, porque a função não gera notas, só
  desloca o instante de ataque de um elemento existente.
- As três mecânicas ficam validáveis em `plan.validate()` e renderizáveis em
  `tools.render.render()`, produzindo MIDI real a partir de um plano.
- Zero regressão: `SUPPORTED_ROLES`, roles/patterns existentes e todo teste
  atual continuam passando sem alteração de comportamento.

## User Stories

### US-001: Registrar `subdivision_flip` como role em `plan.py`/`contract.py`
**Description:** Como autor de plano, quero declarar `role: subdivision_flip`
com os parâmetros que `generate_subdivision_flip` já aceita, para que o
validador aceite o elemento antes do render.

**Acceptance Criteria:**
- [ ] `SUBDIVISION_FLIP_ROLES = ("subdivision_flip",)` adicionado em
  `tools/palette/transitions.py` (mesmo padrão de `RISER_ROLES` etc.), exportado
  em `__all__`.
- [ ] `plan.py`/`contract.py` (schema da fachada) aceitam `role:
  "subdivision_flip"` com campos de pattern equivalentes aos parâmetros
  keyword-only da função: `pitch` (obrigatório), `velocity`, `base_subdivision`
  (vocabulário fechado: `eighth`/`triplet`/`sixteenth`, de
  `SUBDIVISION_STEPS_PER_BEAT`), `flip_subdivision` (mesmo vocabulário),
  `flip_bars_before_boundary`.
- [ ] `plan.validate()` rejeita `base_subdivision`/`flip_subdivision` fora do
  vocabulário fechado com erro explícito citando o valor recebido e o
  vocabulário aceito.
- [ ] `plan.validate()` rejeita elemento `subdivision_flip` sem `pitch`.
- [ ] `SUPPORTED_ROLES` passa a conter `subdivision_flip` (derivado do
  dispatch central, nunca hardcoded numa lista separada).
- [ ] Testes unitários de `plan.validate()` cobrindo aceite e as duas rejeições
  acima.

### US-002: Dispatch de `subdivision_flip` em `render.py`
**Description:** Como usuário do arranjador, quero que um elemento `role:
subdivision_flip` no plano produza notas de verdade no MIDI de saída, usando a
função pura já testada.

**Acceptance Criteria:**
- [ ] `_ROLE_RENDERERS` em `tools/render.py` ganha entrada para
  `SUBDIVISION_FLIP_ROLES` chamando `generate_subdivision_flip` com
  `analysis`, `boundary_s` (resolvido do início/fim de seção, mesmo padrão de
  `riser`/`downer`), os campos de pattern do elemento e `seed` derivada de
  `context`/`TechniqueContext` (nunca `random` sem seed).
  Não implemente essa resolução do zero: leia como `_render_hat_elec_element`
  (ou o renderer de `riser`/`downer` mais próximo) já resolve `boundary_s` e a
  seed a partir do elemento/seção, e replique o mesmo caminho.
- [ ] Track de saída recebe o carimbo padrão (`meta 0x01 text`,
  `midi-arranger v1|...`) como qualquer outra track gerada.
- [ ] Teste de integração ponta a ponta: plano com elemento
  `subdivision_flip` → `render()` → MIDI de saída contém as notas esperadas
  (mesma densidade/pitch que o teste unitário de `generate_subdivision_flip`
  já valida em isolamento).
- [ ] Determinismo: mesmo plano + mesma seed → arquivo byte-idêntico em duas
  execuções.

### US-003: Registrar e despachar `half_time_magnifier` como role
**Description:** Como autor de plano, quero declarar `role:
half_time_magnifier` sobre um padrão de bateria existente, para que ele caia
em half-time enquanto outro elemento (ex.: arp) continua no grid rápido.

**Acceptance Criteria:**
- [ ] `HALF_TIME_MAGNIFIER_ROLES = ("half_time_magnifier",)` adicionado em
  `transitions.py`, exportado em `__all__`.
- [ ] `plan.py`/`contract.py` aceitam `role: "half_time_magnifier"` com um
  campo de pattern que carrega o `base_pattern` (sequência de 0/1 por step,
  mesma convenção de grid que `electronic._HAT_*_PATTERN` já usa) a ser
  reduzido — valide que é uma sequência de inteiros em `{0, 1}`, não vazia.
- [ ] `plan.validate()` rejeita `base_pattern` vazio ou com valores fora de
  `{0, 1}` com erro explícito.
- [ ] `SUPPORTED_ROLES` inclui `half_time_magnifier`.
- [ ] `_ROLE_RENDERERS` despacha para um renderer que chama
  `half_time_drum_pattern(base_pattern)` e materializa os steps resultantes em
  notas de bateria (reaproveite a resolução de pitch/velocity/grid de bateria
  já usada por outro role de bateria existente em `render.py` — não invente
  mapeamento de pitch novo).
- [ ] Teste de integração ponta a ponta: plano com elemento
  `half_time_magnifier` → `render()` → MIDI contém só os steps pares do
  `base_pattern` declarado.
- [ ] Testes unitários de `plan.validate()` cobrindo aceite e as duas
  rejeições acima.

### US-004: Campo `false_downbeat` em `element.pattern` de um riff
**Description:** Como autor de plano, quero marcar um elemento existente (o
riff que entra depois de uma transição reverse/impact) com um atraso de
`false_downbeat`, para que ele ataque `beats` tempos depois do boundary em vez
de exatamente no downbeat.

**Acceptance Criteria:**
- [ ] `plan.py`/`contract.py` aceitam um campo opcional em `element.pattern`
  (ex.: `false_downbeat_beats: float`) em qualquer role que já suporte
  `pattern` hoje — não cria role novo.
  - [ ] `plan.validate()` rejeita `false_downbeat_beats <= 0` com erro
  explícito.
- [ ] Em `render.py`, quando o elemento carrega `false_downbeat_beats`, o
  `start_s` do elemento (calculado normalmente pelo pipeline de seção/boundary
  já existente) é substituído pelo valor de
  `false_downbeat_delay_s(analysis, boundary_s, beats=false_downbeat_beats)`
  antes de gerar as notas do elemento — sem duplicar a lógica de resolução de
  `boundary_s` já usada pelos roles de transição.
- [ ] Teste de integração ponta a ponta: plano com um elemento marcado
  `false_downbeat_beats` → `render()` → primeira nota do elemento ataca no
  instante calculado por `false_downbeat_delay_s`, não no boundary bruto da
  seção.
- [ ] Elemento sem `false_downbeat_beats` continua com o comportamento atual,
  byte-idêntico (regressão zero).

## Functional Requirements

1. FR-1: `subdivision_flip` e `half_time_magnifier` são roles de plano
   registrados em `_ROLE_RENDERERS`/`SUPPORTED_ROLES`, seguindo exatamente o
   padrão estrutural de `riser`/`downer`/`impact`/`reverse` (constante de
   roles em `transitions.py`, dispatch central em `render.py`).
2. FR-2: `false_downbeat` não é role — é campo opcional de `element.pattern`
   que desloca o `start_s` de um elemento já existente.
3. FR-3: Nenhum vocabulário fechado (`base_subdivision`, `flip_subdivision`)
   é duplicado — reusar `SUBDIVISION_STEPS_PER_BEAT` já definido em
   `transitions.py`.
4. FR-4: Nenhum número novo de manual é inventado; todos os parâmetros
   numéricos usados pelas três funções já vêm de argumentos explícitos do
   plano ou de defaults já existentes no código das funções puras.
5. FR-5: Toda track gerada por `subdivision_flip`/`half_time_magnifier`
   recebe o carimbo padrão do arranjador (`meta 0x01 text`).
6. FR-6: `seed` chega a `generate_subdivision_flip` sempre explícita, derivada
   de `context`/plano — nunca `random` sem seed.

## Non-Goals (Out of Scope)

- Não pesquisar nem adicionar números novos a `knowledge/tecnicas/tecnicas_transicoes_midi.md`.
- Não mudar a assinatura ou o comportamento interno de `generate_subdivision_flip`,
  `half_time_drum_pattern` ou `false_downbeat_delay_s`.
- Não tocar `perc_elec`/`vox_chop` (issue #22, PRD separado).
- Não criar UI/CLI nova para declarar esses roles — só o schema de plano e o
  render.
- Não implementar variação pseudoaleatória adicional além da que já existe
  (o `seed` de `subdivision_flip` já é tratado pela função pura).

## Technical Considerations

- Trabalho de integração, não de pesquisa: seguir literalmente o precedente
  de `riser`/`downer`/`impact`/`reverse` em `transitions.py` + `render.py`
  para estrutura de código, nomes de constantes e forma de registro em
  `_ROLE_RENDERERS`.
- `half_time_magnifier` opera sobre bateria — reaproveitar resolução de
  pitch/canal de bateria já existente em `render.py`, nunca inventar mapeamento
  novo de pitch de kit.
- `false_downbeat` toca o cálculo de `start_s` de um elemento já renderizável
  por outro role — cuidado para não quebrar a ordem/pipeline documentada em
  AGENTS.md (`apply_edits` → técnicas de estilo → render por elemento →
  carimbos).
- Toda mudança em `ArrangementPlan` exige atualizar junto `tools/plan.py`
  (dataclass, serialização, validação) e `tools/contract.py` (JSON Schema),
  por regra do AGENTS.md.

## Success Metrics

- As quatro user stories fecham com testes verdes, incluindo os testes de
  integração ponta a ponta descritos.
- `python -m pytest` inteiro passa sem quebrar nenhum teste existente.
- Um plano de exemplo com os três mecanismos gera MIDI determinístico
  (byte-idêntico em duas execuções com a mesma seed).

## Open Questions

- Nenhuma: escopo e modelagem de role/campo já decididos com o usuário antes
  deste PRD (roles dedicados para subdivision_flip/half_time_magnifier;
  campo em element.pattern do riff para false_downbeat).
