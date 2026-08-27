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
- Toda nota vinda do MIDI de origem é estrutural por definição — sobre ela o nível `technique` só
  pode acrescentar ornamento; nunca substitui pitch, posição ou duração da nota estrutural.
- Toda track de saída tocada pelo arranjador — elemento gerado ou track de `plan.edits` — carrega
  carimbo em `meta 0x01 text` no tick 0, com `role`, `plugin`, `preset`, `verified`, `techniques`
  aplicadas e, quando declarada, `suggested_plugin`/`suggested_preset`/`suggested_verified`. O
  formato é `midi-arranger v1|chave=valor|...`, ASCII puro, com `|` proibido nos valores. Track de
  origem não declarada em `plan.edits` NÃO recebe carimbo — sai byte-idêntica.
- NUNCA acrescentar exclusão a `tests/test_palette_integration.py::test_all_target_roles_are_covered`
  para o teste passar. A única exclusão permitida é `choir` (compartilha gerador com strings). Role
  que não gera de verdade sai de `_ROLE_RENDERERS`, não vira exceção do teste.
- Parâmetro declarado em `style.<família>.parameters` tem que **comandar** o resultado. Precedência:
  `parameters` do plano > receita da tool no manual > `range` do parâmetro no manual. Parâmetro
  aceito pelo schema, validado contra a faixa do manual e depois ignorado na aplicação é parâmetro
  mentiroso — mesma categoria de `_identity_apply`. Ao escrever técnica nova, leia `context.parameters`
  antes de cair na receita.
- Técnica documentada no manual mas **não implementada** fica fora de `SUPPORTED_TECHNIQUES`, e o
  plano que a declara recebe `PlanValidationError` explícito. Nunca aceitar e ignorar — no-op
  silencioso é o vício que esta base já rejeitou duas vezes (`_identity_apply` e o gerador de
  bateria de andaime). Hoje está nessa situação `drums.accent_hierarchy` (issue #50).
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
