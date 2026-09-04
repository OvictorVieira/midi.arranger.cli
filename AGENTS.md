# midi.arranger.cli — regras do projeto

Leia `docs/arquitetura.md` e `docs/objetivo.md` antes de qualquer alteração.

## A fronteira que não se cruza

Duas metades. **Nada em `tools/` importa de `bin/`, nem depende de LLM.** Um teste de arquitetura
garante isso. As tools precisam rodar e ser testadas sem modelo nenhum.

| | `bin/` + `prompts/` | `tools/` |
|---|---|---|
| O que é | O harness: invoca a CLI de IA do usuário | O maquinário determinístico |
| Natureza | Não-determinístico | **Determinístico**: mesma entrada, mesma saída |

## Regras invioláveis

- O MIDI de origem **nunca** é sobrescrito.
- Track não declarada para edição sai **nota a nota idêntica**.
- Em `plan.edits`, `track` endereça todas as tracks do MIDI de origem com aquele `track_name`
  exato; nomes repetidos de DAW são tratados como uma unidade e o relatório informa quantas tracks
  físicas foram atingidas.
- Mesmo plano, mesma origem, mesma seed: arquivo **byte-idêntico**.
- Nenhum parâmetro sorteado sem origem declarada. O componente aleatório nunca supera a soma das
  intenções determinísticas.
- Perfil de artista pesquisado **nunca** vira base de conhecimento — vive no plano daquela música.
- A pesquisa levanta **técnica e comportamento**, jamais conteúdo musical.
- Em `style.<familia>.techniques[].name`, valide contra `tools.techniques.build_index()`; não
  duplique nem hardcode o índice no schema ou em `tools/plan.py`.
- `plan.validate` só aceita `style.<familia>.techniques[].name` quando a técnica existe no manual
  **e** está em `SUPPORTED_TECHNIQUES`; técnica apenas documentada é erro de validação, não erro tardio
  de render.
- `tools/techniques/index.py` apenas lê os manuais; técnicas aplicáveis pelo motor são registradas
  explicitamente em `tools/techniques/engine.py`, e `SUPPORTED_TECHNIQUES` deve ser derivado desse
  registro.
- Técnica documentada no manual não é automaticamente técnica suportada pelo motor: só registre em
  `SUPPORTED_TECHNIQUES` quando houver aplicador real, nunca placeholder/no-op.
- Toda técnica aplicável registrada em `tools/techniques/engine.py` recebe
  `context: TechniqueContext`; o despacho exige `seed` explícita e toda variação pseudoaleatória deve
  derivar de `context.rng(...)`.
- Aplicador registrado no registro global de `tools/techniques/engine.py` deve ser autocontido:
  não capture estado global/nonlocal nem dependa de helper global; quando precisar de números do
  manual, leia-os pelo índice (`build_index()`) dentro da aplicação.
- Quando a aplicação de técnica recebe ferramenta-alvo, o despacho resolve a receita do manual:
  usa a receita específica quando existir, cai em `generic` com warning `W_NO_TOOL_RECIPE`, e falha
  antes de chamar a função se não houver receita específica nem `generic`.
- `tools/techniques/notes.py` classifica notas como `structural` ou `ornamental` por derivação em
  ticks; não grave essa marcação como metadado no MIDI, porque o round-trip não preserva isso de
  forma confiável.
- O contrato do nível `humanize` é checado em `tools.techniques.engine.TechniqueRegistry.apply`:
  técnicas desse nível podem mudar timing, velocity e duração, mas não contagem, pitches ou ordem de
  `note_on` por track/canal/altura. A fotografia do contrato trata nota como par fechado:
  `note_off` e `note_on` com velocity 0 são equivalentes, e `note_off` órfão ou `note_on` sem
  fechamento são violação.
- O contrato do nível `technique` também é checado em `TechniqueRegistry.apply`: pode acrescentar
  ornamentos, CC e pitch bend, mas pitch e posição das notas estruturais são intocáveis; velocity e
  duração estrutural só mudam com flags explícitas no registro da técnica.
- A única exceção para pitch estrutural em técnica de bateria é troca de articulação da mesma peça
  (`drums.articulation_diff`): registre `allow_structural_pitch_change=True` e preserve contagem,
  track, canal, início, duração e velocity. Não use essa flag para trocar conteúdo musical.
- A idempotência de ornamentos do nível `technique` também fica em `TechniqueRegistry.apply`: ao
  reaplicar, nota extra com a mesma assinatura exata já existente (track/canal/pitch/início/fim),
  CC ou pitch bend com mesma assinatura (track/canal/tick/valor) é descartado antes da validação
  do contrato.
- Ao implementar técnica idempotente, recalcule os mesmos alvos de ornamento na reaplicação e deixe
  `TechniqueRegistry.apply` descartar duplicatas; não escolha substitutos só porque o ornamento já
  existe no MIDI de entrada.
- A plausibilidade física de ornamentos do nível `technique` é validada em
  `tools/techniques/physical.py`, chamada pelo despacho central; parâmetros físicos explícitos como
  `tuning`/`afinacao`, `open_strings`, `max_fret`, `hand` e `max_hand_span` entram por
  `TechniqueContext.parameters`.
- Na plausibilidade física de bateria, só `35`, `36` e `44` contam como pé; qualquer outra nota do kit
  conta como mão, incluindo `48` (tom) e `59` (ride/crash).
- Em `style.<familia>.parameters`, aceite apenas número escalar ou par `[min, max]`; sequências de
  notas/tempos e chaves de conteúdo musical são bloqueadas em `tools/plan.py` e no schema da fachada.
  A ÚNICA exceção é `StyleTechnique.style`: seleção de técnica de execução (dedo/palheta/slap) contra
  vocabulário FECHADO em `tools/style_schema.py::STYLE_TECHNIQUE_STYLE_VALUES` — nunca texto livre.
  Técnica nova que precisar de outra categoria acrescenta à lista fechada, nunca aceita string fora
  dela. `render._style_technique_parameters` repassa como `context.parameters["style"]`.
- Regras estruturais compartilhadas de `style` vivem em `tools/style_schema.py`; use esse helper em
  domínio e fachada em vez de duplicar listas de chaves musicais, detecção anticópia ou schema de
  `techniques[]`.
- Quando `style.<familia>.parameters.<nome>` casar parâmetro declarado por técnica citada em
  `style.<familia>.techniques`, valor fora do `range` do manual é erro; parâmetro-lacuna sem
  `value`, sem `range` e sem `source` passa só com aviso, nunca com clamp silencioso.
- Defaults de `style` são uma normalização em memória: use `tools.plan.normalize_style_defaults()`;
  `plan.validate()` continua read-only e a fachada `plan.validate` expõe a cópia em `normalized_plan`.
- `tools.render.render()` normaliza defaults de `style` em memória antes de avisos/validadores;
  `confidence: low` e `confidence: default` viram warning de render, nunca erro.
- `tools.render.render()` aplica `style.<familia>.techniques[]` em dois alvos: (a) tracks
  recém-renderizadas da família correspondente e (b) tracks copiadas do MIDI de origem que estão
  nomeadas em `plan.edits`, mapeando `profile` para família (`bass`→`bass`, `drums`→`drums`,
  `keys`→`keys`); `profile: generic` não tem família e não recebe técnica. Track de origem não
  declarada em `plan.edits` continua saindo nota a nota idêntica.
- Ordem inviolável no pipeline de `tools.render.render()`: primeiro `apply_edits` (humanização por
  profile), depois `_apply_style_techniques_to_edit_tracks` (motor de técnicas sobre as tracks da
  origem editadas), depois o render por elemento com o motor de técnicas de estilo no loop, e por
  último os carimbos (`_stamp_element_tracks` inline no loop, `_stamp_edit_tracks` numa passada
  única no fim).
- O motor de humanização por profile (`plan.edits[].profile` + `intensity`, executado por
  `tools/humanize.py` e `tools/edits.py`) é SEPARADO do motor de técnicas de
  `style.<familia>.techniques` (`tools/techniques/engine.py`) e roda ANTES dele. Ele lê
  `velocity_ranges`, `gate_ratios` e `timing_jitter_ms` de um `tools.style_profile.StyleProfile`
  imutável (frozen dataclass, mapas `MappingProxyType`), cujo `StyleProfile.default()` reproduz
  byte a byte `VELOCITY_RANGES`, `GATE_RATIOS` e `TIMING_JITTER_MS` de `tools/constants.py` — os
  dicionários do `constants.py` continuam sendo a fonte declarada do default e não devem ser
  movidos nem apagados. O construtor rejeita configuração fora do domínio físico (velocity em
  [0,127], gate em [0,1], timing jitter em [0,250] ms) com `ValueError`, antes de qualquer render.
- Retrocompatibilidade obrigatória do `StyleProfile`: `tools/humanize.py`
  (`base_velocity`, `VelocityEngine`, `MicrotimingEngine`, `DurationEngine`) e
  `tools/edits.py` (`apply_edits`, `apply_edit`) aceitam `profile`/`style_profile` como
  keyword-only opcional no FINAL da assinatura. Chamador antigo (inclusive `tools/render.py`) que
  não passa nada continua byte-idêntico, porque a implementação cai em `StyleProfile.default()`.
  Perfil muda a FAIXA de sorteio, nunca a fórmula de cálculo — se alguma story exigir mudar a
  fórmula interna dos motores, PARE E REPORTE em vez de inventar.
- Toda nota vinda do MIDI de origem é estrutural por definição — sobre ela o nível `technique` só
  pode acrescentar ornamento; nunca substitui pitch, posição ou duração da nota estrutural.
- Toda track de saída tocada pelo arranjador — elemento gerado ou track de `plan.edits` — carrega
  carimbo em `meta 0x01 text` no tick 0, com `role`, `plugin`, `preset`, `verified`, `techniques`
  aplicadas e, quando declarada, `suggested_plugin`/`suggested_preset`/`suggested_verified`. O
  formato é `midi-arranger v1|chave=valor|...`, ASCII puro, com `|` proibido nos valores. Track de
  origem não declarada em `plan.edits` NÃO recebe carimbo — sai byte-idêntica.
- `instrument.preset`/`suggested_instrument.preset` nunca é nome de preset inventado, mesmo marcado
  `verified: false` — mesma regra que já rejeitou `_identity_apply` e a atribuição falsa a
  `cmuse.org`: chute marcado como chute continua sendo chute apresentado como fato. `presets.scan`
  (`tools/presets.py`, só roda em sessão local com acesso ao filesystem do usuário) faz **sweep
  genérico** de locais canônicos macOS (`~/Library/Audio/Presets`, `/Library/Audio/Presets`,
  `~/Music/Audio Music Apps/Plug-In Settings`, `~/Library/Application Support/<Vendor>`,
  `~/Documents/<Vendor>`, `/Users/Shared/<Vendor>`), com whitelist de vendors conhecidos e
  whitelist de extensões de preset — funciona em qualquer Mac, não hardcoded no filesystem do
  autor. Antes do sweep, resolve automaticamente ponteiros locais de library (por exemplo, symlink
  `~/Library/Application Support/Spectrasonics/STEAM` para volume externo) e relata
  `searched_roots`, `discovered_roots` e `unresolved_roots`. O fluxo normal nunca pede que o usuário
  configure path/env; `extra_roots`, `MIDI_ARRANGER_PRESET_ROOTS` e `SPECTRASONICS_STEAM_ROOT` são
  apenas escape hatches de diagnóstico/compatibilidade. O harness compara `plugins.scan` com o
  inventário, inspeciona configs/symlinks/aliases locais de forma read-only quando faltar library e
  repete `presets.scan` com `extra_roots`; só pede ação ao usuário quando o recurso está realmente
  inacessível (volume desmontado/permissão). Preset achado lá é o único que pode virar nome exato
  com `verified: true`. Sem preset
  real, a sugestão cai para a categoria do instrumento (ex.: "Synth Piano — escolha o preset na
  sua biblioteca"), nunca um nome plausível inventado. Libraries em DB binário proprietário
  (Toontrack Superior3, EZdrummer, EZbass) aparecem em `opaque_libraries` com motivo — o harness
  deve avisar que existe conteúdo, mas jamais inventar nome de preset a partir delas. `._*`
  (AppleDouble) e diretórios de sample/cache/log são ignorados na varredura.
  DBs da Spectrasonics não entram nessa categoria opaca: começam com manifesto `FileSystem` legível;
  leia apenas esse manifesto para obter nomes reais e pare antes do payload concatenado. Ao varrer
  STEAM, limite a descida a `<Produto>/Settings Library/{Patches,Multis}` — nunca atravesse
  `Soundsources`, samples ou wavetables.
- NUNCA acrescentar exclusão a `tests/test_palette_integration.py::test_all_target_roles_are_covered`
  para o teste passar. A única exclusão permitida é `choir` (compartilha gerador com strings). Role
  que não gera de verdade sai de `_ROLE_RENDERERS`, não vira exceção do teste.
- Parâmetro declarado em `style.<família>.parameters` tem que **comandar** o resultado. Precedência:
  `parameters` do plano > receita da tool no manual > `range` do parâmetro no manual. Parâmetro
  aceito pelo schema, validado contra a faixa do manual e depois ignorado na aplicação é parâmetro
  mentiroso — mesma categoria de `_identity_apply`. Ao escrever técnica nova, leia `context.parameters`
  antes de cair na receita.
- **Técnica só se aplica se o usuário autorizou.** Vale para bateria, baixo, guitarra e teclas.
  Ausência de autorização significa NENHUMA técnica, nunca "todas" — o default seguro é não mexer no
  material do usuário. Pesquisa e autorização são coisas separadas, mesmo padrão de
  `suggested_plugin`/`suggested_preset`: o arranjador sugere, o usuário marca, e só o que ele marcou
  vira `plan.style.<família>.techniques[]`. O mecanismo vive em três camadas: (a) o brief separa
  `style.<família>.suggested_techniques[]` (o que a pesquisa levantou) de
  `style.<família>.authorized_techniques[]` (o que o usuário marcou), validado por
  `tools/brief_schema.py` — `techniques[]` do brief é subconjunto de `authorized_techniques`; (b)
  `tools/plan.py::validate` lê o brief apontado por `plan.brief_ref.path`, exige que
  `brief_ref.sha256` case com `tools.brief_ref.brief_sha256()` do arquivo (autorização pode ter sido
  editada depois de aprovada) e recusa `plan.style.<família>.techniques[]` com nome fora de
  `authorized_techniques` da mesma família; plano sem `brief_ref` com técnica declarada em qualquer
  família também é erro — sem brief não há autorização; (c) `tools/render.py` repete a barreira
  antes de aplicar qualquer técnica, para plano construído em memória sem passar por `plan.load`
  (`RenderError` explícito citando família e técnica). A skill `midi-brief` pergunta ao usuário
  quais técnicas entram e preenche `authorized_techniques` só com o que ele marcou — silêncio ou
  dúvida NÃO autoriza.
- Técnica documentada no manual mas **não implementada** fica fora de `SUPPORTED_TECHNIQUES`, e o
  plano que a declara recebe `PlanValidationError` explícito. Nunca aceitar e ignorar — no-op
  silencioso é o vício que esta base já rejeitou duas vezes (`_identity_apply` e o gerador de
  bateria de andaime). Hoje estão nessa situação as técnicas de baixo `bass.slide`, `bass.vibrato`
  e `bass.harmonic` (fora do escopo da issue #47). Inventário canônico do
  motor em `docs/arquitetura.md` (§4, "Inventário de técnicas do motor"); o teste
  `test_supported_techniques_is_derived_from_the_registry` em `tests/test_techniques_engine.py`
  afirma a tupla exata e quebra o build se um registro fantasma aparecer. Inventário atual de
  bateria: `drums.accent_hierarchy`, `drums.accented_roll`, `drums.articulation_diff`,
  `drums.buzz_roll`, `drums.cymbal_choke`, `drums.flam`, `drums.ghost_notes` e `drums.microtiming`.
- `bass.string_selection` força a corda em que cada nota estrutural do baixo soa, via keyswitch
  momentâneo do MODO BASS (manual §5.9): agrupa notas consecutivas na mesma corda (a mais grave que
  alcança o pitch dentro de `max_fret`, default 24) num run só, segura o keyswitch pressionado
  (par note_on/note_off) do início ao fim do run — o LATCH do plugin vem desligado de fábrica — e é
  NO-OP em qualquer ferramenta que não seja `modo_bass` (a própria receita `generic` do manual diz
  que a intenção de corda não pode ser honrada nela). Afinação vem de `context.parameters["tuning"]`
  com fallback no default físico de 4 cordas; contagem de cordas fora de 4/5/6 também é NO-OP, pois
  não há convenção documentada de ordem de corda para esses casos.
- `drums.accent_hierarchy` foi reintroduzida na issue #50 com detecção determinística de virada
  (`tools/techniques/_fill_detection.py`, mesmo padrão de `roll_sequences` do `drums.accented_roll`)
  e invariante de pressão em duas camadas: (a) piso `soft_ceiling+1` impede que nota escrita no
  topo caia para ghost/soft, (b) piso `original - pressure_max_drop` (default 15, CONVENÇÃO no
  manual) limita quanto uma nota pode ser rebaixada, garantindo que a mediana por peça por
  arquivo não caia mais que 15 pontos. Dentro de janela classificada como virada, tom/caixa/prato
  vão para `accent_ceiling` (não para ghost/soft), corrigindo o defeito que rebaixou 63 caixas de
  127 para <=45 em DEIXE IR na primeira implementação. A invariante de pressão é aplicada DEPOIS do
  clamp de `hard_ceiling`, não antes: `pressure_max_drop` configurado abaixo do default pode
  ultrapassar o teto prático de ~115, porque a promessa do parâmetro manda sobre o teto comum (achado
  do Codex no PR #59). Os quatro limiares de virada (`fill_max_gap_beats`, `fill_min_notes`,
  `fill_min_density_per_beat`, `fill_min_piece_variety`) são resolvidos via
  `context.parameters`/`recipe`/manual e repassados a `fill_windows`, não hardcoded no módulo de
  detecção — mesma regra de "parâmetro não pode ser mentiroso" do restante deste arquivo.
- Inventário atual de teclas (issue #14): `keys.damper_pedal`, `keys.expression`,
  `keys.modulation` e `keys.pitch_bend` — nível `technique`, só acrescentam CC/pitch bend, nunca
  mudam pitch/posição/duração da nota estrutural — mais `keys.human_articulation`,
  `keys.rolled_chord` e `keys.voice_dynamics`, nível `humanize`, que mexem só em duração, timing e
  velocity e não escrevem evento novo nenhum. As sete restantes documentadas no manual
  (`keys.melody_lead`, `keys.hand_asynchrony`, `keys.bass_anticipation`,
  `keys.syncopated_pedal`, `keys.vibrato`, `keys.rhodes_touch`, `keys.hammond_dynamics`) continuam
  FORA de `SUPPORTED_TECHNIQUES` — plano que as declare recebe `PlanValidationError` explícito. Os
  motivos concretos de cada recusa estão em `docs/arquitetura.md` §4 e não são "falta de tempo":
  `melody_lead`/`hand_asynchrony`/`bass_anticipation` exigiriam fazer uma voz soar ANTES de notas
  escritas no mesmo tick, o que muda a ordem dos `note_on` congelada pelo contrato `humanize`.
  `filtro` (CC74) e `portamento`
  (CC5/CC65), citados na issue #14 original, NÃO têm bloco de técnica próprio no manual (só
  aparecem em prosa na §7.4 de `tecnicas_teclas_midi.md`): são pesquisa futura, não bug desta
  rodada — não inventar bloco de técnica novo em cima deles.
- Inventário atual de guitarra (issue #19): `guitar.bend`, `guitar.dead_notes`,
  `guitar.double_tracking`, `guitar.hammer_pull`, `guitar.palm_mute`, `guitar.pinch_harmonic` e
  `guitar.vibrato`. `guitar.bend` e `guitar.vibrato` só entram em nota que soa SOZINHA no canal —
  o manual é categórico que bend dentro de acorde em canal único é impossível e que vibrato de
  canal em power chord está errado — e as duas sempre devolvem o pitch bend ao centro antes do
  note_off. As onze restantes continuam FORA de `SUPPORTED_TECHNIQUES`, cada uma com motivo
  concreto registrado em `docs/arquitetura.md` §4; em especial `guitar.picking_direction`,
  `guitar.rake` e `guitar.pick_scrape` ficam de fora porque os números de que o aplicador
  precisaria são `source: null` no manual, e `guitar.track_offset` porque offset negativo de track
  não é dado de nota (MIDI não tem tempo negativo, e recuar a guitarra empurrando as outras tracks
  violaria a saída nota a nota idêntica).
- Técnica de nível `humanize` **não pode inverter a intenção da origem**: nota que a origem escreveu
  no topo da faixa não pode sair na camada mais baixa. Foi assim que `accent_hierarchy` transformou
  63 caixas de 127 em 32 e matou as viradas de DEIXE IR. Ao mexer em velocity, meça **por peça e por
  trecho** contra a origem — média e desvio globais já mascararam essa inversão nesta base.
- Parâmetro de intensidade zerado (`density=0.0` e equivalentes) significa **desligar a técnica**,
  não "mínimo de um". Em loop de seleção, cheque o teto ANTES de acrescentar o candidato: checar
  depois deixa `wanted == 0` passar sempre por um elemento.
- `plan.brief_ref.sha256` deve ser calculado com `tools.brief_ref.brief_sha256()`; é o SHA-256 dos
  bytes exatos do `arrangement-brief.json`, mesmo formato de `.midiarranger/brief.sha256`.
- Todo `plan.elements[]` deve carregar `rationale` string não vazia após `strip()`; fixtures e
  testes precisam usar uma razão real do elemento, não placeholder.
- Número sem fonte é marcado `[NÃO VERIFICADO]` e **jamais** apresentado como fato.
- Todo `value`/`range` numérico em `knowledge/tecnicas/*.md` tem `source` preenchido: ou cita fonte
  real, ou declara `"source": "CONVENCAO — <razão>"` (mesma forma já usada em `drums.flam` e nas
  técnicas de guitarra marcadas `verified: false`). Número nunca fica órfão — `source` ausente ou
  `null` num parâmetro com `value`/`range` é erro de varredura, não "lacuna": lacuna é parâmetro sem
  `value` e sem `range`, que documenta ausência de número, não convenção disfarçada. Isso já aconteceu
  três vezes por caminhos diferentes (atribuição falsa a `cmuse.org` no manual de guitarra,
  `_identity_apply`/gerador de bateria de andaime, e `drums.flam` em #46 com `grace_velocity_ratio` e
  `reading_ceiling_ms` sem marcar convenção); `build_index()` já derruba `verified` para `false` nesse
  caso como SINAL, mas o teste de varredura em `tests/test_techniques_index.py` é a BARREIRA que
  falha o build citando manual, técnica e parâmetro.
- Determinismo nas tools: sem relógio, sem `random` sem seed, sem rede.
- Ao adicionar leitura de `element.pattern` em `tools/render.py`, atualize o conjunto
  `*_PATTERN_FIELDS` do role correspondente para evitar aviso falso de campo ignorado.
- Ao adicionar uma nova família de `role` renderizável, atualize o dispatch central
  `_ROLE_RENDERERS` em `tools/render.py`; `SUPPORTED_ROLES` é derivado dele e os testes garantem
  que todo role exportado renderiza de fato.
- Em strings/choir com `pattern.tutti=true`, `element.layers` é limitado por
  `STRINGS_TUTTI_MAX_VOICES`; dimensione buckets/tracks pelo número efetivo e avise quando reduzir.
- `tools.render.render()` valida `ArrangementPlan` em memória com `plan.validate()` antes de carregar
  MIDI ou rodar validadores; plano inválido deve falhar como `PlanValidationError`, não como erro
  interno do pipeline.
- Em `tools.edits.apply_edit`, retiming pode mudar ticks, velocity e duração, mas a sequência de
  `note_on` por canal/altura deve preservar a ordem original.
- Ao adicionar campo em `ArrangementPlan`, atualize juntos `tools/plan.py` (dataclass,
  serialização e validação) e `tools/contract.py` (JSON Schema das tools).
- Nunca inferir afinação sem evidência de instrumento de corda (`instrument_name`, patch General
  MIDI em `GM_GUITAR_PROGRAMS`/`GM_BASS_PROGRAMS`, ou declaração explícita via
  `declared_stringed_tracks`). Sem evidência, `tools.tuning.tuning_inference` marca
  `discard_reason=not_stringed` e não classifica. Afinação `unknown` **nunca** vem acompanhada de
  `tuning_name` — a regra é garantida estruturalmente por `_tuning_name`/`_classify_confidence` em
  `tools/tuning.py` e não deve ser contornada no consumidor.
- Afinação só é nomeada quando os intervalos são inequívocos e nenhum canal relevante foi
  descartado. Prefixo ambíguo (menos que `MIN_INTERVALS_FOR_CLASSIFICATION`, ou casando DROP e
  STANDARD ao mesmo tempo) resulta em `unknown` sem `tuning_name`; qualquer descarte de canal
  força `confidence != high` e `inference_incomplete=true`.
- Nome de track em `tools/tuning.py` casa por PALAVRA (não substring), tratando `_`, `-` e `.`
  como separador além de whitespace — DAW sanitiza espaço no export (`Guitar_1`, `bass-gtr`,
  `Guitar.L` casam). Qualificador de sopro, percussão, voz ou synth logo depois de `bass`
  (`_BASS_DISQUALIFIERS`: clarinet, trombone, flute, drum, sax/saxophone, tuba, oboe, bassoon,
  choir, voice, synth) tira a track de corda: `Bass Clarinet`, `Bass_Drum`, `bass-flute`,
  `Bass Synth` NÃO casam. `Bass`, `Bass Guitar`, `Electric Bass`, `bass 2` continuam casando.
- Patch GM só conta como evidência de corda quando **rege nota**: a classificação usa
  `_governing_programs_by_channel` (patch vigente no `note_on`), nunca a lista histórica de
  `program_change`. Canal que declara guitarra e depois flauta antes da primeira nota toca flauta.
  Canal regido por patch de corda **e** de não-corda é ambíguo e fica fora da inferência.
  `gm_programs` na saída continua relatando todos os patches observados — o relato é completo,
  a decisão é que é restrita.
- `GM_BASS_PROGRAMS` é 32-37, **não** 32-39: GM 38/39 (`Synth Bass 1/2`) são sintetizador, não têm
  corda e portanto não têm afinação a inferir. Manter 38/39 contradiria `_BASS_DISQUALIFIERS`,
  que já tira `Bass Synth` do casamento por nome.
- `analyze.tracks[i]` e `analyze.tuning_inference[i]` **não compartilham índice**: `tracks[]` é
  indexado por `Instrument` do pretty_midi (quebrado por canal/programa) e `tuning_inference[]` por
  SMF track física lida com `mido`. Uma SMF track sem nome com notas em três canais vira três
  entradas de um lado e uma do outro. Por isso `declared_stringed_tracks` casa por NOME, e nome
  declarado que não bate com track nenhuma emite `W_DECLARED_TRACK_NOT_FOUND` — declaração órfã
  nunca pode ser no-op silencioso.

## Qualidade

- Todo módulo com teste. `python -m pytest` da raiz.
- Teste de regressão obrigatório para todo bug corrigido.
- **Nunca** criar teste artificial para inflar cobertura. Todo teste cobre cenário real com asserção
  de comportamento observável.
- **Nunca** editar `quality-baseline.json` à mão para o gate passar.
- Dependências das tools: só `mido` e `pretty_midi`. Nenhuma nova sem perguntar.
- Python ≥ 3.11.
- Testes do harness usam mocks reutilizáveis em `tests/harness/fixtures/bin/`; copie esses binários
  para um `PATH` temporário e asserte o `MOCK_LOG` (`ARG_*`, `STDIN`, `PROMPT`) em vez de criar mocks
  ad hoc ou chamar CLIs reais.
- Os mocks do harness acrescentam cada invocação ao `MOCK_LOG`; para testes multi-iteração, conte
  blocos `BIN<<END`, enquanto `read_mock_log` continua representando a última invocação.
- Para testes de conclusão do `run`, defina `MOCK_COMPLETE_ON=<n>` nos mocks do harness; a invocação
  `n` emitirá a sentinela de conclusão.

> **Nunca escreva a sentinela literal neste arquivo.** Ralph — e o nosso próprio harness — usam a
> presença dela para distinguir um *driver prompt* de um arquivo de regras do projeto. Documentá-la
> aqui faz o arquivo de regras ser confundido com driver e entregue ao agente como se fosse tarefa.
> Isso já aconteceu e custou oito iterações. Refira-se a ela como "a sentinela de conclusão"; a
> forma literal vive apenas em `prompts/` e no código que a procura.
- Para testes em que o agente altera o brief durante o `run`, defina `MOCK_REWRITE_BRIEF_ON=<n>` nos
  mocks do harness; a invocação `n` reescreverá `arrangement-brief.json` no `project_root` do prompt.
- Para testes de ponta a ponta do `run`, defina `MOCK_WRITE_PROGRESS=1` nos mocks do harness; cada
  invocação acrescentará uma entrada determinística ao `progress_file` anunciado no prompt.
- O prompt de `run` entrega metadados primeiro e depois o conteúdo do driver resolvido; por padrão
  vem de `prompts/<TOOL>.md`, mas `MIDI_ARRANGER_PROMPT_FILE` tem precedência e caminho inexistente
  deve falhar sem fallback silencioso.
- Testes de prompt driver ficam em `tests/harness/` e devem comparar nomes de tools contra
  `tools.registry.list_tools()`; importe `tools.contract` antes, porque o registry é populado por
  efeito colateral no import.

## Commits

Formato `type: descrição concisa` — conventional commits, **sem scope, sem Co-Authored-By**.
Tipos: `feat` `fix` `test` `chore` `docs` `refactor`.
Nunca `--no-verify`. Nunca amend em commit publicado. Nunca force-push em `main`.

## Worktrees

`.worktrees/<numero-da-issue>-<slug>/`, sempre dentro do repositório, sempre a partir de
`origin/main` atualizado.

## Skills

- A entrevista de arranjo vive em `skills/midi-brief/SKILL.md`. Um agente sem sistema de skill deve
  ler esse arquivo diretamente — é o mesmo contrato.

## Instalação

`./install.sh [root]` escreve em exatamente três lugares e em nenhum outro:

| Onde | O quê | Variável |
|---|---|---|
| `$XDG_BIN_DIR/midi-arranger` | O shim que entra no `PATH` | `XDG_BIN_DIR` (default `<root>/.local/bin`) |
| `$MIDI_ARRANGER_HOME/` | O corpo: `bin`, `prompts`, `tools`, `knowledge`, `skills`, `AGENTS.md`, `requirements.txt` | `MIDI_ARRANGER_HOME` (default `<root>/.local/share/midi-arranger`) |
| `<provider>/skills/midi-brief` | Symlink para o corpo instalado | — |

- `root` posicional é só para teste; sem argumento é `$HOME`.
- O symlink da skill aponta para o **corpo instalado**, nunca para o checkout: harness e skill
  precisam ser sempre a mesma versão. Depois de um `git pull`, rode `./install.sh` de novo.
- O shim é arquivo, não symlink — `bin/midi-arranger` deriva `PROMPTS_DIR` do próprio diretório, e um
  symlink no `PATH` faria isso apontar para fora da instalação.
- Reinstalar é idempotente e remove do corpo o que sumiu da origem.
- Dependência Python faltando **avisa** e não aborta; o instalador não roda `pip`, porque isso
  escreveria fora dos diretórios declarados. Python < 3.11 aborta antes de instalar qualquer coisa.
- `CLAUDE.md` é uma linha, `@AGENTS.md` — o Claude Code não lê `AGENTS.md`, e duplicar a instrução
  criaria duas verdades.
