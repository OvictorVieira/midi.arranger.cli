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
