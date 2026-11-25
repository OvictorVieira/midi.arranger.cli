# PRD: perc_elec e vox_chop (elementos rítmicos eletrônicos, issue #22)

## Introdução

`tools/palette/electronic.py` já entrega `hat_elec`, `sub` e `sub_drop` — as
três peças da família eletrônica rítmica da issue #22 que tinham número
medido ou comportamento descrito o bastante para render. `perc_elec`
(percussão eletrônica sobre a caixa) e `vox_chop` (chop de vocal) ficaram de
fora dessa rodada por corte de escopo documentado no topo do módulo — a issue
não traz medição numérica pra elas.

Isso não bloqueia a implementação: `sub`/`sub_drop` já estabeleceram o
precedente de declarar `CONVENCAO — <razão>` como `source` quando a issue
descreve comportamento sem número, em vez de esperar por dado que não existe.
Este PRD aplica o mesmo precedente às duas peças restantes, fechando a família
eletrônica rítmica da issue #22.

## Goals

- `perc_elec` e `vox_chop` viram roles de plano completos: bloco no manual
  (`knowledge/tecnicas/tecnicas_eletronico_midi.md`) com parâmetros honestos
  (`CONVENCAO` onde não há medição), função geradora em
  `tools/palette/electronic.py`, validação em `plan.py`/`contract.py`, e
  dispatch em `render.py`.
- `perc_elec`: camada de acento eletrônico sobre o backbeat, reforçando as
  posições de acento da caixa (mesmo mecanismo de bar/beat que `hat_elec` usa),
  pitch fixo, sem groove próprio.
- `vox_chop`: gate rítmico monofônico sobre pitch fixo, representando um
  slice/sample de vocal chopado — sem contorno melódico, sem side-chain.
- `SUPPORTED_ROLES` passa a conter os dois roles, derivado do dispatch
  central, seguindo a mesma estrutura de `hat_elec`.
- Nenhum número apresentado como medição real quando é escolha do motor —
  toda constante nova cita `CONVENCAO — <razão>` honesta.

## User Stories

### US-001: Bloco de manual para `perc_elec`
**Description:** Como leitor do manual, quero uma seção `drums.perc_elec` em
`tecnicas_eletronico_midi.md` com parâmetros e fonte declarada, para que o
motor tenha de onde ler os números sem hardcode.

**Acceptance Criteria:**
- [ ] Nova seção no manual com bloco ` ```technique ` `{"name": "perc_elec",
  "family": "drums", ...}`, seguindo o mesmo formato JSON do bloco `hat_elec`
  existente (`summary`, `verified`, `description`, `parameters[]`, `tools`).
- [ ] `verified: false` (nenhum número medido) e cada `parameter` cita
  `"source": "CONVENCAO — <razão concreta>"` — nunca `source` ausente/null.
- [ ] Parâmetros mínimos cobertos: `pitch` (sample fixo), `velocity_range`
  (ou `velocity_mean`/`velocity_stdev`), `gate_ms` (ou faixa equivalente),
  `accent_positions` ou mecanismo equivalente para alinhar com o backbeat da
  caixa (reaproveitar `analysis` do jeito que `hat_elec` reaproveita
  `bars_in_section`/step grid — não inventar detecção de caixa nova; se a
  posição de acento já está disponível em `Analysis`, use-a, senão declare
  posições fixas por beat como `CONVENCAO`).
- [ ] `tests/test_techniques_index.py` (varredura de `source` obrigatório)
  passa para o bloco novo sem exceção adicional.

### US-002: Bloco de manual para `vox_chop`
**Description:** Como leitor do manual, quero uma seção `drums.vox_chop` (ou
family equivalente — decidir junto de US-001 se cabe melhor em `drums` ou
nova family; se nova family, seguir o mesmo padrão de declaração de `family`
já usado por `bass.sub`) com parâmetros e fonte declarada.

**Acceptance Criteria:**
- [ ] Bloco `{"name": "vox_chop", ...}` no manual, mesmo formato dos
  anteriores, `verified: false`, todo parâmetro com `CONVENCAO — <razão>`.
- [ ] Parâmetros mínimos: `pitch` (sample fixo do slice vocal), `pattern_mode`
  (reaproveitar vocabulário fechado `HAT_PATTERN_MODES` se aplicável, ou um
  vocabulário fechado próprio documentado — decidir pelo comportamento
  descrito na issue, sem inventar modo que a issue não pede), `gate_ms` ou
  faixa de duração do chop, `velocity_range`.
- [ ] `tests/test_techniques_index.py` passa para o bloco novo.

### US-003: `generate_perc_elec` em `tools/palette/electronic.py`
**Description:** Como motor de render, quero uma função pura e testada que
gere as notas de `perc_elec` a partir do manual, seguindo a mesma estrutura de
`generate_hat_elec`.

**Acceptance Criteria:**
- [ ] `PERC_ELEC_ROLES = ("perc_elec",)` adicionado, exportado em `__all__`.
- [ ] `generate_perc_elec(analysis, section, *, seed, ...)` lê todos os
  números via `_technique_params("drums.perc_elec")` — zero hardcode.
- [ ] `seed` explícita obrigatória (`assert_traceable_seed`), mesmo padrão de
  `generate_hat_elec`.
- [ ] Saída 100% monofônica (`_enforce_monophony`, reaproveitado sem
  duplicar).
- [ ] Testes unitários: mesma seed + mesmo plano → saída byte-idêntica;
  cobertura de erro se o manual não declarar `drums.perc_elec`
  (`TechniqueError`, mesmo padrão de `_technique_params`).
- [ ] Docstring do módulo (topo de `electronic.py`) atualizada: remove
  `perc_elec` da lista do que "NÃO está implementado nesta rodada".

### US-004: `generate_vox_chop` em `tools/palette/electronic.py`
**Description:** Como motor de render, quero uma função pura e testada que
gere as notas de `vox_chop`, mesmo padrão estrutural das demais.

**Acceptance Criteria:**
- [ ] `VOX_CHOP_ROLES = ("vox_chop",)` adicionado, exportado em `__all__`.
- [ ] `generate_vox_chop(analysis, section, *, seed, ...)` lê parâmetros via
  `_technique_params("drums.vox_chop")` (ou family decidida em US-002) — zero
  hardcode.
- [ ] Pitch fixo por onset (nenhum `element.degrees`/contorno melódico —
  decisão já tomada: gate rítmico sobre pitch fixo, não `follow=riff`).
- [ ] Saída monofônica, `seed` explícita.
- [ ] Testes unitários equivalentes aos de US-003 (determinismo, erro de
  manual ausente).
- [ ] Docstring do módulo atualizada: remove `vox_chop` da lista de não
  implementados.

### US-005: Validação em `plan.py`/`contract.py`
**Description:** Como autor de plano, quero declarar `role: perc_elec` e
`role: vox_chop` com seus campos de pattern, e o validador aceitar/rejeitar
corretamente.

**Acceptance Criteria:**
- [ ] `plan.py`/`contract.py` aceitam os dois roles novos com os campos de
  pattern definidos em US-003/US-004 (ex.: `pattern_mode`, se aplicável).
- [ ] `plan.validate()` rejeita valor de `pattern_mode` fora do vocabulário
  fechado (se houver) com erro explícito.
- [ ] `SUPPORTED_ROLES` passa a incluir `perc_elec` e `vox_chop`.
- [ ] Testes unitários de `plan.validate()` cobrindo aceite e rejeição.

### US-006: Dispatch em `render.py`
**Description:** Como usuário do arranjador, quero que `role: perc_elec` e
`role: vox_chop` no plano produzam MIDI de verdade.

**Acceptance Criteria:**
- [ ] `PERC_ELEC_PATTERN_FIELDS`/`VOX_CHOP_PATTERN_FIELDS` (frozenset)
  adicionados junto dos demais `*_PATTERN_FIELDS`, seguindo
  `HAT_ELEC_PATTERN_FIELDS` como modelo — atualiza o dispatch de campo
  ignorado por role (regra do AGENTS.md: toda leitura nova de
  `element.pattern` atualiza o `*_PATTERN_FIELDS` do role correspondente).
- [ ] `_ROLE_RENDERERS` ganha entrada para `PERC_ELEC_ROLES` e
  `VOX_CHOP_ROLES`, cada uma chamando a função geradora correspondente (mesmo
  formato de `_render_hat_elec_element`).
- [ ] Track de saída recebe o carimbo padrão (`meta 0x01 text`).
- [ ] Teste de integração ponta a ponta pra cada elemento: plano → `render()`
  → MIDI contém as notas esperadas (mesma forma que o teste unitário da
  função geradora já valida em isolamento).
- [ ] Determinismo: mesmo plano + mesma seed → arquivo byte-idêntico em duas
  execuções, pros dois elementos.

## Functional Requirements

1. FR-1: `perc_elec` e `vox_chop` seguem exatamente a estrutura de
   `hat_elec`: bloco de manual → `_technique_params` → função geradora pura →
   role em `plan.py`/`contract.py` → dispatch em `render.py`.
2. FR-2: Todo parâmetro numérico novo introduzido por este PRD cita
   `source: "CONVENCAO — <razão concreta>"`, nunca fonte ausente e nunca
   atribuição falsa a medição que não existe.
3. FR-3: `perc_elec` reforça posições de backbeat (acento sobre a caixa) —
   não introduz groove/pattern próprio independente do que a caixa já
   estabelece na seção.
4. FR-4: `vox_chop` não varia pitch por onset — pitch fixo, fiel ao
   precedente "amostra única" de `hat_elec`/`sub_drop`.
5. FR-5: As duas funções geradoras são 100% monofônicas e determinísticas
   (`seed` explícita via `assert_traceable_seed`).
6. FR-6: `*_PATTERN_FIELDS` de cada role novo é atualizado junto do dispatch,
   sem campo de pattern lido silenciosamente sem entrar no frozenset
   correspondente.

## Non-Goals (Out of Scope)

- Não implementar side-chain/ducking real para `vox_chop` — é MIDI puro, sem
  processamento de áudio.
- Não fazer `vox_chop` seguir contorno melódico (`follow=riff`) — decisão já
  tomada: pitch fixo.
- Não medir número novo em arranjo de referência — todo parâmetro é
  `CONVENCAO`, não medição.
- Não tocar `tools/palette/transitions.py`/issue #23 (PRD separado).
- Não mudar comportamento de `hat_elec`/`sub`/`sub_drop` já existentes.

## Technical Considerations

- Reaproveitar `_hat_pattern`, `_enforce_monophony`, `_technique_params`,
  `bars_in_section` já existentes em `electronic.py` — não duplicar.
- Se `perc_elec` precisar de posições de acento de caixa que `Analysis` não
  expõe hoje, declarar posições fixas por beat como `CONVENCAO` em vez de
  criar detecção de caixa nova (fora de escopo).
- Toda mudança em `ArrangementPlan` exige atualizar junto `tools/plan.py` e
  `tools/contract.py` (regra do AGENTS.md).
- `tests/test_palette_integration.py::test_all_target_roles_are_covered` não
  aceita exclusão nova — os dois roles precisam ter renderer real registrado
  em `_ROLE_RENDERERS`.

## Success Metrics

- As seis user stories fecham com testes verdes, incluindo testes de
  integração ponta a ponta pros dois elementos.
- `python -m pytest` inteiro passa sem quebrar nenhum teste existente.
- Docstring de `electronic.py` não lista mais `perc_elec`/`vox_chop` como
  fora de escopo.

## Open Questions

- Nenhuma: comportamento de `perc_elec` (acento sobre backbeat) e `vox_chop`
  (gate rítmico monofônico, pitch fixo) já decididos com o usuário antes
  deste PRD. Decisão fina de nome de `family` para `vox_chop` (drums vs. nova
  family) fica a critério de quem implementar US-002, seguindo o padrão já
  estabelecido no manual.
