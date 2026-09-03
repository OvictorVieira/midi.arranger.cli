# Arquitetura

> Referência única do desenho. As issues detalham a implementação de cada peça e apontam para cá
> em vez de repetir contexto.

---

## 1. O modelo: Ralph, aplicado a música

O harness segue o modelo do [Ralph](https://github.com/OvictorVieira/ralph). Ele **não** implementa
laço de agente, **não** integra SDK de provider e **não** precisa de MCP. Ele:

1. invoca a CLI de IA que o usuário já tem instalada, com o adaptador de invocação daquela ferramenta;
2. entrega um **prompt driver** que diz o que fazer nesta iteração;
3. deixa o agente ler e escrever arquivos de estado;
4. procura a sentinela de conclusão no stdout;
5. repete com **contexto limpo** até concluir ou estourar o número de iterações.

O que torna isso viável: **todo agente suportado já tem acesso a shell.** As tools em Python são
simplesmente comandos. Nenhuma camada de protocolo é necessária.

### Ferramentas suportadas e a forma de invocação de cada uma

Cada CLI recebe prompt e flags de um jeito diferente. O adaptador absorve isso.

| Ferramenta | Como recebe o prompt | Flags de operação autônoma | Effort |
|---|---|---|---|
| `claude` | stdin | `--print --dangerously-skip-permissions` | `--effort` |
| `codex` | stdin, via `exec -` | `--dangerously-bypass-approvals-and-sandbox -C <root>` | `-c model_reasoning_effort="..."` |
| `agy` | stdin | `--print --dangerously-skip-permissions` | `--effort` |
| `cursor` | argumento posicional | `--print --force` | dentro da string do modelo |
| `opencode` | argumento posicional | `run --auto` | `--variant` |
| `amp` | stdin | `--dangerously-allow-all` | não tem |
| `gemini` | `--prompt` | `--approval-mode yolo` | não tem |

Nenhum modelo é fixado no código. Sem `--model`, cada CLI usa o default que o usuário configurou.

---

## 2. Os dois comandos

O loop do Ralph é headless — e a nossa ferramenta precisa **entrevistar** o usuário. A saída é
separar em duas fases, exatamente como `/prd` e `ralph`:

```
midi-arranger brief musica.mid          INTERATIVO
    │  entrevista por família de instrumento
    │  pesquisa as referências citadas
    └─→ arrangement-brief.json

midi-arranger run --tool claude 12      AUTÔNOMO
    │  itera com contexto limpo
    │  analisa, planeja, constrói, valida, corrige
    └─→ musica_arranged.mid + arrangement-plan.json + relatório
```

`brief` roda o agente em modo interativo normal. `run` roda o laço headless.

---

## 3. O fluxo completo

```
usuário: "arranja isso, bateria estilo <fulano>, teclas tipo <banda>, ghost notes no baixo"
    │
    ├── 1. ANALISAR      tool               → seções, tom, acordes, densidade, registro
    ├── 2. ENTREVISTAR   harness (brief)    → estilo e referência por família; confirma seções
    ├── 3. PESQUISAR     harness (brief)    → técnica e comportamento das referências
    ├── 4. CONSULTAR     tool               → como reproduzir aquilo em MIDI
    ├── 5. DECIDIR       harness (run)      → escreve arrangement-plan.json
    ├── 6. VALIDAR PLANO tool               → schema, técnicas conhecidas, coerência
    ├── 7. CONSTRUIR     tool               → gera e remodela; escreve o MIDI
    ├── 8. VERIFICAR     tool               → relatório JSON com severidade por item
    ├── 9. CORRIGIR      harness (run)      → se disparou, ajusta o plano e volta ao 7
    └── 10. ENTREGAR                        → MIDI + plano + relatório
```

Passos 1, 4, 6, 7 e 8 são **tools**: determinísticos, testáveis, sem LLM. Passos 2, 3, 5 e 9 são
**harness**: é onde o modelo pensa.

O passo 9 fecha o ciclo. A ferramenta não entrega saída com validador reclamando sem avisar.

---

## 4. Estado em disco

Contexto limpo a cada iteração significa que **todo estado vive em arquivo**.

| Arquivo | Papel | Quem escreve |
|---|---|---|
| `arrangement-brief.json` | O que o usuário quer: demanda, respostas da entrevista, perfis de estilo pesquisados com `sources`, `researched_at` e `confidence` | fase `brief` |
| `arrangement-plan.json` | O que será construído: seções, elementos, `style`, `edits`, `rationale` por elemento | fase `run` |
| `progress.txt` | Log append-only: o que cada iteração fez | fase `run` |
| `.midiarranger/` | Estado interno, última execução, arquivo de execuções anteriores | harness |

`arrangement-plan.json` é a **fronteira entre o não-determinístico e o determinístico**. Acima dele
é IA; abaixo é máquina testável. É por isso que o perfil de estilo pesquisado aterrissa nele em vez
de ser consumido em memória: o render fica determinístico, auditável e re-executável sem refazer
pesquisa.

### Bloco `session` (issue #96)

`session` é a **fronteira de trabalho**: identifica uma rodada focada com um `intent` (o que o
usuário está fazendo nessa passada) e um recorte de famílias em jogo. Vive no brief e é herdado
pelo plano — nunca inventado pelo agente no meio do caminho. O bloco é **opcional**: brief e plano
sem `session` continuam válidos e byte-idênticos ao que sempre foram (modo monolítico).

Campos:

- `id`: string não vazia (o brief usa UUID; o plano só exige não-vazio — o brief é a fonte).
- `intent`: vocabulário FECHADO — `edit`, `create`, `layer`, `transition`, `mixed`.
- `families_in_scope`: subconjunto sem duplicatas de `bass`/`drums`/`guitar`/`keys`.
- `created_at`: ISO-8601 UTC — `YYYY-MM-DDTHH:MM:SS[.fff]Z`, data real do calendário.

**Fronteira de escopo do plano.** Quando `session.families_in_scope` está declarado:

- nenhum `plan.style.<outra-família>.techniques[]` pode aparecer com item — validado por
  `tools.plan.validate` em `_validate_session_scope`;
- nenhum `plan.elements[]` cujo `role` mapeie para família fora do escopo entra (o mapeamento
  role→família reusa `ROLE_STYLE_FAMILIES` de `tools/plan.py`, o mesmo que o render já usa);
- `plan.edits[]` fica livre: track do MIDI que não entra no escopo sai byte-idêntica, sem receber
  técnica. É a mesma regra que já vale para família sem entrada em `plan.style` no render.

**Persistência append-only.** Quando o consumidor (harness/CLI) decide arquivar a sessão, chama
`tools.sessions.archive_session(plan, base_dir)`. O arquivo vai em
`<base>/.midiarranger/sessions/<id>-<intent>-<famílias-com-dash>.json` — nome determinístico, sem
timestamp (o `created_at` do plano já carrega o momento). Se o arquivo já existir, é erro
(`SessionArchiveError`): colisão aponta bug de id duplicado, nunca sobrescreve histórico. O módulo
NÃO é chamado por `tools/render.py` — a ordem inviolável do pipeline não muda por causa desta
issue, e o consumidor é quem invoca a persistência explicitamente.

### Bloco `influence` (perfil por música)

`InfluenceProfile` é o contrato entre a **pesquisa** feita pela IA do usuário e o **dicionário de
técnicas** consumido pelo maquinário. Vive por música, em memória ou serializado, e nunca vira
persona persistente de artista nem base de conhecimento em `knowledge/`. O módulo é
`tools/influence.py`; a validação é determinística (sem relógio, sem rede, sem aleatoriedade) e
exposta como `tools.influence.validate(profile)`. Erros carregam `code`, `path` do finding e
`hint` acionável.

Estrutura de `InfluenceProfile` v1:

- `version` (const `1`)
- `project_ref: str | None` — identificador local; **jamais** identidade de artista.
- `sources[]`: `{id, url, title, retrieved_at}` (data ISO curta `YYYY-MM-DD`).
- `findings[]`: achados que o motor sabe executar hoje ou que informam parametrização do plano.
- `unmapped_findings[]`: achados válidos que o motor **ainda não** sabe executar — ficam registrados
  para não se perder, mas nunca viram técnica aplicada. Mesmas regras estruturais dos findings.

Cada `InfluenceFinding` carrega:

- `id`, `family` (⊂ `STYLE_FAMILIES`), `dimension` (vocabulário fechado abaixo),
- `intensity` ∈ `{off, subtle, medium, strong}`,
- `confidence` ∈ `{high, medium, low, default}` (reusa `CONFIDENCE_LEVELS` do brief),
- `semantic_value: str` — descrição parafraseada do comportamento,
- `source_ids: tuple[str, ...]` referenciando `sources[].id`,
- `user_stated: bool` — `True` quando é preferência explícita do usuário (sem fonte),
- `summary: str` — resumo parafraseado.

Vocabulário fechado de `dimension` (`INFLUENCE_DIMENSIONS`, snake_case inglês para casar com o
resto do maquinário):

`timing_feel`, `dynamics`, `articulation`, `density`, `arrangement_function`, `register`,
`section_behavior`, `execution_technique`.

Regras invioláveis embutidas no validador:

1. **Vocabulário fechado, não texto livre**: `family`, `dimension`, `intensity`, `confidence` fora
   dos valores aceitos é erro (`E_INFLUENCE_UNKNOWN_*`), nunca aceito em silêncio.
2. **Fonte obrigatória, exceto preferência do usuário**: finding sem `source_ids` só passa com
   `user_stated=True` (`E_INFLUENCE_FINDING_NO_SOURCE`). Finding com `source_ids` **e**
   `user_stated=True` é contradição (`E_INFLUENCE_FINDING_SOURCE_AND_USER`) — o validador não
   adivinha qual dos dois vale.
3. **Confiança declarada por finding**, não pela família inteira.
4. **Barreira anticópia estrutural** compartilhada com `style`: `find_style_musical_content`
   (`tools/style_schema.py`) é reusada sobre o payload — chaves de conteúdo musical (`notes`,
   `pattern`, `riff`, `melody`, ...), sequências de números em faixa MIDI, arrays de eventos com
   pitch+time, arrays de nomes de nota são erro em qualquer profundidade
   (`E_INFLUENCE_MUSICAL_CONTENT`).
5. **Barreira anticópia semântica** sobre `semantic_value` e `summary`: string com 3+ tokens em
   sequência batendo `NOTE_NAME_RE` (ex. `"C4 D4 E4"`) ou 3+ inteiros em faixa MIDI
   (ex. `"60 64 67"`) é erro. Menção isolada em prosa (`"tônica em C"`, `"pedal em 40"`) passa.
6. **Sem números exatos de MIDI** inventados pela IA: o perfil registra **comportamento**, não
   **conteúdo**. Números vêm dos manuais em `knowledge/tecnicas/` e do motor.

Exemplos mínimos:

Bateria (achado com fonte):

```json
{
  "id": "f_drums_ghost",
  "family": "drums",
  "dimension": "articulation",
  "semantic_value": "usa ghost notes como articulacao de dinamica",
  "intensity": "medium",
  "confidence": "high",
  "source_ids": ["src_1"],
  "user_stated": false,
  "summary": "A referencia articula pressao com ghost notes em vez de acentuar"
}
```

Baixo (feel de timing):

```json
{
  "id": "f_bass_feel",
  "family": "bass",
  "dimension": "timing_feel",
  "semantic_value": "atrasa levemente contra a bateria em versos",
  "intensity": "subtle",
  "confidence": "medium",
  "source_ids": ["src_2"],
  "user_stated": false,
  "summary": "Push-pull sutil de timing contra o backbeat"
}
```

Teclas (preferência explícita do usuário, sem fonte):

```json
{
  "id": "f_keys_preference",
  "family": "keys",
  "dimension": "arrangement_function",
  "semantic_value": "teclas ficam de pad, nao respondem melodia",
  "intensity": "strong",
  "confidence": "default",
  "source_ids": [],
  "user_stated": true,
  "summary": "Preferencia declarada pelo usuario"
}
```

Guitarra em `unmapped_findings` (comportamento válido, motor ainda não executa):

```json
{
  "id": "u_guitar_whammy",
  "family": "guitar",
  "dimension": "execution_technique",
  "semantic_value": "uso de whammy bar com pitch bend profundo",
  "intensity": "strong",
  "confidence": "high",
  "source_ids": ["src_1"],
  "user_stated": false,
  "summary": "Tecnica levantada mas ainda nao executada pelo motor"
}
```

### Bloco `style`

`style` é o recorte do perfil pesquisado que o maquinário determinístico pode auditar e aplicar.
Ele fica dentro do `arrangement-plan.json`, por família (`bass`, `drums`, `guitar`, `keys`), e
nunca vira arquivo em `knowledge/`. Cada família declara:

- `reference`: a referência pesquisada, como string não vazia.
- `researched_at`: data ISO-8601 (`YYYY-MM-DD`) da pesquisa.
- `sources`: lista não vazia de fontes quando há referência.
- `confidence`: vocabulário fechado: `high`, `medium`, `low` ou `default`.
- `techniques`: nomes validados contra `tools.techniques.build_index()`, em forma canônica ou
  simples quando o caminho da família desambigua.
- `parameters`: apenas número escalar ou par `[min, max]`.
- `techniques[].style`: única exceção não numérica do bloco `style`. Seleção fechada de técnica de
  execução (dedo/palheta/slap, ex. `bass.attack_style`), validada contra
  `STYLE_TECHNIQUE_STYLE_VALUES` — nunca string livre. Existe porque a escolha não tem como ser
  número: o manual da técnica declara a categoria, não uma faixa.

O bloco é estruturalmente anticópia: chaves ou formas que carreguem notas, tempos, riffs, grooves,
frases, melodias, motivos ou sequências musicais são erro. Quando um parâmetro casa com uma técnica
citada e o manual declara `range`, valor fora da faixa é erro, nunca clamp silencioso.

No render, `style.<familia>.techniques[]` é aplicado pelo motor determinístico de
`tools/techniques/engine.py` em dois alvos: (a) tracks recém-geradas para aquela família e (b)
tracks copiadas do MIDI de origem que estão nomeadas em `plan.edits`, mapeando `profile` para
família (`bass`→`bass`, `drums`→`drums`, `keys`→`keys`). `profile: generic` não tem família e não
recebe técnica; é documentado, não é erro. Track de origem que não está em `plan.edits` continua
saindo nota a nota idêntica. Sobre a track editada, toda nota vinda do MIDI de origem é estrutural
por definição — o nível `technique` só pode acrescentar ornamento sobre ela. Ordem inviolável do
pipeline: primeiro `apply_edits` (humanização por profile), depois o motor de técnicas de estilo
sobre as tracks editadas, depois o render por elemento (que também roda o motor sobre as tracks que
acabou de gerar), e por último os carimbos de plugin/preset. O motor tem dois níveis com contratos
centrais: `humanize` pode alterar timing, velocity e duração sem mudar contagem, pitches ou ordem
de notas; `technique` pode acrescentar ornamentos, CC e pitch bend, mas preserva pitch e posição
das notas estruturais. Nota estrutural é o material de entrada ou de gerador; nota ornamental é a
nota adicionada pela técnica.

A humanização por profile (`plan.edits[].profile` + `intensity`) que roda dentro de `apply_edits`
antes do motor de técnicas é parametrizada por `tools.style_profile.StyleProfile`: dataclass
imutável (`frozen=True`, mapas `MappingProxyType`) que carrega `velocity_ranges`, `gate_ratios` e
`timing_jitter_ms` na mesma forma dos dicionários de `tools/constants.py`. `StyleProfile.default()`
reproduz esses três dicionários byte a byte — `constants.py` continua sendo a fonte declarada do
default e não é apagado nem movido. `tools/humanize.py` (base_velocity, VelocityEngine,
MicrotimingEngine, DurationEngine) e `tools/edits.py` (apply_edits, apply_edit) aceitam o profile
como keyword-only opcional no final da assinatura; toda chamada antiga continua válida e
byte-idêntica porque cai em `StyleProfile.default()`. HOJE só `gate_ratios` chega efetivamente a
`apply_edits` — `timing_jitter_ms` do perfil ainda não substitui o `sigma_ms` fixo por profile em
`tools/edits.py::ProfileParams`; a monotonicidade desse dicionário está provada isoladamente em
`MicrotimingEngine`, não ponta a ponta pelo pipeline de edits. Fechar essa propagação é trabalho
futuro, não bug desta rodada. Perfil muda a FAIXA de sorteio, nunca a
fórmula. O construtor valida sanidade física (velocity em [0,127], gate em [0,1], jitter em
[0,250] ms) e levanta `ValueError` antes de qualquer render.

A idempotência também fica no despacho central: ao reaplicar uma técnica, ornamentos com a mesma
assinatura de track/canal/pitch/início/fim já presentes são descartados antes da validação do
contrato, assim como CC e pitch bend com a mesma assinatura de track/canal/tick/valor. Depois que as
técnicas rodam, o render reconstrói as notas renderizadas a partir do MIDI final dessas tracks, para
que harmonia, placement, artificialidade e persona validem também os ornamentos.

Inventário atual de técnicas de bateria que o motor executa:

- `drums.accent_hierarchy` (`humanize`)
- `drums.accented_roll` (`humanize`)
- `drums.articulation_diff` (`technique`, troca articulação da mesma peça)
- `drums.buzz_roll` (`technique`)
- `drums.cymbal_choke` (`technique`)
- `drums.flam` (`technique`)
- `drums.ghost_notes` (`technique`)
- `drums.microtiming` (`humanize`)

`drums.accent_hierarchy` foi reintroduzida na issue #50 depois da remoção que motivou a lição de
"nunca inverter a intenção da origem". A implementação nova combina duas peças: (a) detecção
determinística de virada em `tools/techniques/_fill_detection.py`, que reusa o padrão de
`roll_sequences` de `drums.accented_roll` (agrupamento por gap máximo, filtragem de hi-hat
contínuo, critérios de densidade/variedade/backbeat) e classifica cada trecho como virada ou
groove estável; e (b) invariante de pressão em duas camadas dentro do aplicador — piso
`soft_ceiling+1` impede que nota escrita no topo caia para ghost/soft, e piso
`original - pressure_max_drop` (default 15, `source: CONVENÇÃO` no manual) limita quanto uma nota
pode ser rebaixada. Dentro de janela de virada, tom/caixa/prato vão para `accent_ceiling` (não
para ghost/soft); fora de virada, a lógica de posição métrica (quantização ao 16-avo, mapa GM de
peças) diferencia backbeat/downbeat das notas de fundo. Plano que declara técnica documentada mas
não suportada continua falhando na validação em vez de ser aceito como no-op.

Exemplo mínimo válido:

```json
{
  "style": {
    "drums": {
      "reference": "baterista pesquisado para esta musica",
      "researched_at": "2026-08-24",
      "sources": ["https://example.test/drums-technique"],
      "confidence": "medium",
      "techniques": [
        {
          "name": "drums.ghost_notes",
          "density": 0.25,
          "rationale": "A referencia usa ghost notes como articulacao de dinamica, sem copiar levada."
        }
      ],
      "parameters": {
        "velocity": [20, 45]
      }
    }
  }
}
```

### Sugestão vs autorização de técnica

O que a pesquisa levanta e o que o motor aplica são coisas separadas — o mesmo padrão de
`suggested_plugin` vs `plugin`. A separação existe em três camadas:

1. **No brief.** Cada `style.<família>` carrega dois campos independentes além de `techniques[]`:
   - `suggested_techniques`: mesma forma de `techniques[]` (nome + parâmetros + `rationale`), onde
     a pesquisa registra o que levantou. Sugerir não autoriza.
   - `authorized_techniques`: array de nomes canônicos. É o que o usuário marcou depois de ver a
     lista. Default é `[]` — o caminho seguro é NÃO autorizar nada.
   - `techniques[]`: continua sendo a lista que vai para o plano, e é validada como **subconjunto**
     de `authorized_techniques`. Nome em `techniques[]` fora de `authorized_techniques` é erro de
     schema. `authorized_techniques` ausente ou `[]` com `techniques[]` não vazio também é erro —
     não pode passar por omissão. `authorized_techniques: []` com `techniques: []` é o default
     seguro.

2. **No plano.** `plan.brief_ref` aponta para o brief real e carrega `path` mais `sha256`.
   `tools/plan.py::validate` lê o brief apontado por `path` e confere o `sha256` contra
   `tools.brief_ref.brief_sha256()` do arquivo antes de confiar no conteúdo — autorização pode ter
   sido editada depois de aprovada, e o hash é o que trava essa janela. Divergência é erro no path
   `brief_ref.sha256`, brief inexistente é erro no path `brief_ref.path`. Técnica em
   `plan.style.<família>.techniques[]` fora de `authorized_techniques` da mesma família no brief é
   `PlanValidationError` citando família, técnica e a lista autorizada. Plano sem `brief_ref` com
   qualquer `style.<família>.techniques[]` não vazio é erro: sem brief não há como saber o que foi
   autorizado. Plano sem `brief_ref` e sem técnica em família nenhuma continua válido — é o caminho
   de quem só usa `plan.edits`.

3. **No render.** `tools/render.py` repete a mesma barreira antes de aplicar qualquer técnica, para
   plano construído em memória sem passar por `plan.load`. Violação vira `RenderError` explícito
   citando família e técnica — nunca aplica parcialmente, nunca ignora em silêncio.

A skill `midi-brief` fecha o ciclo: lista as técnicas disponíveis por família com `techniques.list`
e o resumo de cada uma, apresenta ao usuário, e preenche `authorized_techniques` só com o que ele
marcou. Silêncio ou dúvida não autoriza.

### Inventário de técnicas do motor

Manual e motor são coisas separadas. `knowledge/tecnicas/*.md` documenta técnicas;
`tools/techniques/engine.py` executa as que têm aplicador real. `SUPPORTED_TECHNIQUES` é
derivado do registro e é a única lista que o `plan.validate` aceita — técnica só documentada
recebe `PlanValidationError` com `not implemented by the engine`.

Estado atual do motor:

| Família | Executadas pelo motor | Documentadas, ainda sem aplicador |
|---|---|---|
| `drums` | `drums.accent_hierarchy`, `drums.accented_roll`, `drums.articulation_diff`, `drums.buzz_roll`, `drums.cymbal_choke`, `drums.flam`, `drums.ghost_notes`, `drums.microtiming` | — |
| `bass` | `bass.attack_style`, `bass.ghost_notes`, `bass.hammer_pull`, `bass.let_ring`, `bass.palm_mute`, `bass.string_selection`, `bass.velocity_contour` | `bass.slide`, `bass.vibrato`, `bass.harmonic` |
| `keys` | `keys.damper_pedal`, `keys.expression`, `keys.modulation`, `keys.pitch_bend` | `keys.melody_lead`, `keys.hand_asynchrony`, `keys.bass_anticipation`, `keys.voice_dynamics`, `keys.rolled_chord`, `keys.syncopated_pedal`, `keys.vibrato`, `keys.rhodes_touch`, `keys.hammond_dynamics`, `keys.human_articulation` |
| `guitar` | — | tudo documentado |

O teste `test_supported_techniques_is_derived_from_the_registry` em
`tests/test_techniques_engine.py` afirma a tupla exata para que registro fantasma
(aplicador stub, `_identity_apply`) quebre o build. O teste
`test_keys_engine_inventory_matches_the_issue_14_contract` no mesmo arquivo trava
o inventário da família `keys` e afirma que as dez técnicas restantes
documentadas continuam fora do motor.

As quatro técnicas de teclas implementadas são todas nível `technique` e só
acrescentam CC/pitch bend — nunca mudam pitch/posição/duração da nota
estrutural. `filtro` (CC74) e `portamento` (CC5/CC65), citados na issue #14
original, **não** têm bloco de técnica próprio em `knowledge/tecnicas/tecnicas_teclas_midi.md`
(só aparecem discutidos em prosa na §7.4). São pesquisa futura, não bug desta
rodada — inventar bloco de técnica novo em cima deles é escopo novo.

### Preset real em vez de nome inventado

`plan.validate` exige `instrument.plugin`/`instrument.preset` não vazios (`tools/plan.py`), mas não
exige que o preset exista de verdade — o valor é texto livre carimbado na track (§ abaixo). Isso
abre espaço para o harness inventar um nome plausível e marcar `verified: false`: tecnicamente
honesto, mas inútil na prática — o usuário procura o preset na própria biblioteca e não acha nada
com aquele nome.

A regra é a mesma que já rejeitou `_identity_apply` e a atribuição falsa a `cmuse.org`: **nunca
apresentar chute como fato, mesmo marcado como chute**. Concretamente:

- `presets.scan` (`tools/presets.py`) primeiro descobre automaticamente roots de libraries a partir
  dos locais canônicos e de ponteiros locais (por exemplo, o symlink `Spectrasonics/STEAM` deixado
  pelo instalador para uma library em volume externo), depois varre os presets reais. A resposta
  expõe `searched_roots`, `discovered_roots` com proveniência e `unresolved_roots` para volume
  desmontado/permissão. Hoje só macOS; caminhos Windows ficam para depois. Só roda numa sessão local
  com acesso ao filesystem do usuário, nunca em sessão remota/sandbox.
- O usuário não configura path nem variável de ambiente no fluxo normal. Depois de rodar
  `plugins.scan` e `presets.scan` sem overrides, o agente compara os inventários. Plugin instalado
  sem preset encontrado aciona diagnóstico read-only: o agente inspeciona configs, symlinks e aliases
  locais e repete `presets.scan` passando o root descoberto em `extra_roots`. Só pede intervenção ao
  usuário quando a máquina comprova que o destino está inacessível. Overrides/envs são escape hatch
  de diagnóstico e retrocompatibilidade, não requisito de instalação.
- Na Spectrasonics, os `.db` de factory começam com um manifesto `FileSystem` que lista nomes reais
  e offsets antes do payload concatenado. A tool lê somente esse manifesto e para em
  `</FileSystem>`; isso permite verificar nomes de Omnisphere/Trilian/Keyscape sem interpretar o
  payload. A busca na STEAM fica restrita a `<Produto>/Settings Library/{Patches,Multis}`, nunca
  atravessa `Soundsources`, samples ou wavetables. Isso é diferente das DBs Toontrack realmente
  opacas, que continuam aparecendo somente em `opaque_libraries`.
- Preset encontrado no disco é o **único** tipo de sugestão que pode virar nome exato em
  `instrument.preset`, com `verified: true` de verdade.
- Sem preset real para o plugin desejado (base binária fechada, library realmente ausente ou
  inacessível), a sugestão cai para a
  **categoria** do instrumento (ex.: "Synth Piano — escolha o preset na sua biblioteca"), nunca um
  nome de preset inventado — mesmo com `verified: false`.
- `plugins.scan` continua respondendo só pelo inventário de plugins (formato, fabricante, papel
  sugerido); `presets.scan` é a tool separada para o inventário de presets. Use as duas juntas antes
  de sugerir instrumento.

### Carimbo de plugin/preset em toda track tocada pelo arranjador

Toda track de saída que o arranjador criou ou editou carrega, além do nome
(`meta 0x03 track_name`), um **carimbo** em `meta 0x01 text` no tick 0. O
carimbo é ASCII puro (o meta-evento SMF de texto não carrega encoding) e usa
`|` como separador entre `chave=valor`:

```
midi-arranger v1|role=drums|plugin=Superior Drummer|preset=Metal Kit|verified=true|techniques=[drums.ghost_notes]
```

Campos, sempre nessa ordem quando presentes:

- `role`: role do elemento (para elemento gerado) ou `profile` da edit
  (para track de `plan.edits`).
- `plugin`, `preset`, `verified`: instrumento do elemento gerado.
- `techniques`: canônicos das técnicas de `style.<familia>` aplicadas.
- `suggested_plugin`, `suggested_preset`, `suggested_verified`: sugestão
  declarada em `plan.edits[].suggested_instrument` para uma track que já
  existia no MIDI de origem. É metadado puro — nunca altera nota alguma.

Regras:

- Track de origem **não** declarada em `plan.edits` sai byte-idêntica: sem
  carimbo. O carimbo aparece apenas em tracks que o arranjador tocou.
- O carimbo nunca substitui o nome da track — os dois coexistem.
- A sugestão passa pelas mesmas regras de `tools/tracks.py`: plugin em
  `FORBIDDEN_PLUGINS` (Trigger_2, Addictive Trigger) é recusado; plugin
  default por FR-24 é respeitado; Serum só pode aparecer em roles do FR-14.
- O formato é determinístico: mesmo plano, mesma origem, mesma seed → mesmos
  bytes.

### Conclusão

O agente emite a sentinela de conclusão no stdout quando o arranjo está pronto e validado. Ela é
formada por `<promise>` + `COMPLETE` + `</promise>`. O harness procura essa sentinela e encerra.
Sem ela, itera até o limite.

O `arrangement-plan.json` é a fonte de verdade da conclusão; `progress.txt` é só log.

---

## 5. Estrutura de diretórios

```
midi.arranger.cli/
├── bin/midi-arranger            o harness
├── install.sh
├── prompts/                     driver prompt por ferramenta
│   ├── BRIEF.md                 fase interativa
│   ├── CLAUDE.md  CODEX.md  OPENCODE.md
│   ├── AGY.md  CURSOR.md  AMP.md  GEMINI.md
├── tools/                       o maquinário determinístico, Python
│   ├── cli.py                   entrypoint: midi-arranger-tool <nome>
│   ├── analyze.py  sections.py  plan.py  render.py  learn.py
│   ├── humanize.py  voicing.py  constants.py  style_profile.py  tracks.py
│   ├── techniques/              motor de técnicas por família
│   ├── palette/                 geradores
│   ├── validators/              harmônico, placement, persona, artificialidade, não-cópia, colisão
│   ├── plugins.py  presets.py
│   └── primitives.py            análise musical de base
├── knowledge/
│   ├── persona/
│   └── tecnicas/                bateria, baixo, teclas, guitarra
├── tests/fixtures/
├── docs/
└── AGENTS.md
```

---

## 6. O contrato de tool

Toda tool obedece ao mesmo contrato. É o que permite trocar de agente sem reescrever nada e testar
o maquinário inteiro sem modelo.

1. **Entrada e saída em JSON**, com JSON Schema declarado.
2. **Determinística.** Sem relógio, sem aleatoriedade sem seed, sem rede. A seed vem da entrada.
3. **Sem efeito colateral fora do declarado.**
4. **Nunca sobrescreve o MIDI de origem.**
5. **Erro é dado, não exceção** — resultado estruturado com código, mensagem acionável e caminho do
   campo. O agente precisa poder agir sobre o erro.
6. **Chamável por CLI e por Python**, com comportamento idêntico.

Envelope de sucesso e de falha:

```json
{ "ok": true, "data": { }, "warnings": [ {"code": "...", "message": "...", "path": "..."} ] }
```
```json
{ "ok": false, "error": {
    "code": "PLAN_INVALID_TECHNIQUE",
    "message": "tecnica 'inexistente' nao existe no manual de bateria",
    "path": "style.drums.techniques[2].name",
    "hint": "disponiveis: ghost_notes, flam, ..." } }
```

`warnings` não impedem sucesso. Erro é o que invalida o resultado; aviso é o que o usuário precisa
saber mas não invalida. Densidade estranha é aviso. Nota fora do acorde é erro.

### Tools previstas

| Tool | Faz |
|---|---|
| `analyze` | Seções, tom, acordes por compasso, densidade, ocupação de registro, âncoras rítmicas |
| `techniques.list` | Técnicas documentadas por família, com parâmetros e faixas |
| `techniques.describe` | Receita completa de uma técnica, por ferramenta-alvo |
| `plan.skeleton` | Esqueleto de plano a partir de um MIDI analisado |
| `plan.validate` | Valida o plano contra schema e índice de técnicas |
| `render` | Plano + MIDI → MIDI final, com validadores e relatório |
| `validate` | Roda validadores sobre um MIDI já renderizado |
| `learn` | Mede um corpus e devolve um perfil de estilo |
| `plugins.scan` | Inventário de plugins AU/VST/VST3 instalados, com papel sugerido |
| `presets.scan` | Inventário de presets/patches reais em disco, por plugin suportado |

---

## 7. Detecção de afinação no `analyze`

O MIDI não carrega metadado de afinação. Ele carrega a corda de outra forma: **um canal por corda**,
convenção de export do Guitar Pro e do Songsterr. A tool `analyze` reconstrói a afinação a partir
da distribuição de notas por canal, dentro de cada SMF track.

O relatório aparece em dois campos no output de `analyze`:

- `channel_distribution` — por track, um objeto por canal com `channel`, `note_count`, `pitch_min`,
  `pitch_max`, `span` e `percentage`. Ordenação estável por número de canal. Track sem nota é omitida.
- `tuning_inference` — por track: `is_stringed`, `stringed_source`, `discard_reason`, `gm_programs`,
  `candidate_channels`, `discarded_channels` (com o motivo de cada descarte), `tuning_intervals`,
  `tuning_class`, `tuning_name`, `lowest_string_pitch`, `confidence`, `string_concentrations` e
  `low_strings_top3_percentage`.

### As três travas que impedem inventar afinação

O detector inventa afinação de linha de voz se não travar a inferência. Três travas obrigatórias
em `tools/tuning.py`; o motivo de cada canal descartado aparece no relatório, nunca em silêncio.

1. **TRAVA 1 — instrumento de corda.** Decidido por `instrument_name` da track, por patch General
   MIDI (`GM_GUITAR_PROGRAMS=24..31`, `GM_BASS_PROGRAMS=32..39`), ou por declaração explícita do
   usuário via input `declared_stringed_tracks` de `analyze`. Precedência: declaração > patch > nome.
   Sem nenhuma das três evidências, `is_stringed=false`, `discard_reason=not_stringed` e não infere.
   O casamento por nome usa fronteira de palavra (`\b`), nunca substring solta: `Bassoon` e `Brass`
   não passam por conterem `bass`/`brass` dentro de outra palavra. Quando o nome sugere corda mas o
   patch General MIDI contradiz, o patch vence e o conflito aparece no relatório em
   `name_patch_conflict`. O patch General MIDI só autoriza os canais que **têm notas** na track:
   `program_change` num canal vazio não licencia inferência sobre notas de outro canal. Quando
   canais diferentes carregam patches diferentes, só os canais com patch de corda entram na
   inferência; os demais aparecem em `discarded_channels` com motivo `non_stringed_channel_patch`.
2. **TRAVA 2 — contagem mínima por canal.** Só entra na inferência canal com
   `note_count >= MIN_NOTES_PER_CHANNEL_FOR_INFERENCE` (=8). Canal com meia dúzia de notas tem
   mínimo que é nota casada, não corda solta. Descarte aparece como `low_note_count`.
3. **TRAVA 3 — span como sanidade.** Corda solta vive numa janela estreita; span >
   `MAX_STRING_SPAN_SEMITONES` (=24) desmente a hipótese canal-igual-corda. Descarte aparece como
   `span_too_wide`.

A tabela de afinações vem do manual `guitar.drop_tuning` em `knowledge/tecnicas/`, lida pelo índice
de técnicas e convertida em intervalos entre cordas adjacentes — **não hardcodada**. Classificação
é por prefix-match, mas exige um mínimo de intervalos declarado em `MIN_INTERVALS_FOR_CLASSIFICATION`
(=3) — dois intervalos soltos são ambíguos e não nomeiam. Exceção estrutural: a assinatura
`DROP_SIGNATURE_INTERVAL=7` na base já classifica drop a partir de 2 intervalos, porque `7` não
aparece em afinação padrão. Prefixo que casa DROP **e** STANDARD ao mesmo tempo resulta em
`unknown` sem nome — a ambiguidade é travada por construção.

### Vocabulário de confiança

Fechado, três valores, populado por `_classify_confidence(tuning_class, candidate_count)`:

| `confidence` | Quando |
|---|---|
| `high` | Classe reconhecida (`drop` ou `standard`) e `>= MIN_CANDIDATES_FOR_HIGH_CONFIDENCE` (=4) canais candidatos |
| `low`  | Classe reconhecida mas poucos canais candidatos |
| `unknown` | Classe `unknown` — intervalos que não batem com padrão nenhum, canal único, ou track não-stringed |

Regra estrutural: **afinação `unknown` nunca vem com nome**. `_tuning_name` retorna `None` para
`tuning_class == unknown` e `_classify_confidence` retorna `unknown` no mesmo predicado — a
conjunção garante que o par `(tuning_name != null, confidence == unknown)` é impossível por
construção, não por convenção do chamador.

Segunda regra estrutural: **descarte de canal rebaixa confiança**. `_classify_confidence` recebe
`has_discards` e nunca devolve `high` quando qualquer canal foi descartado, mesmo com contagem
suficiente de candidatos. O relatório expõe `inference_incomplete=true` no mesmo caso, para o
consumidor auditar sem re-executar o detector.

O relatório sempre expõe `candidate_channels` e `discarded_channels` com quantidade e motivo —
o consumidor pode auditar por que uma track ficou `unknown` sem re-executar o detector.

### O que está fora de escopo desta rodada

Declarar corda no plano e emitir keyswitch de forçar corda ficam para rodada seguinte, porque tocam
os mesmos arquivos da issue #10. Esta rodada **não** altera `tools/render.py`, `tools/plan.py` nem
`tools/techniques/`.

### Configuração de instrumento declarada no brief (issue #44)

A detecção acima é automática e roda sem entrevista. Mas dois casos ela não resolve sozinha: export
achatado (um canal por track, não por corda — as três travas não têm o que separar) e afinação sem
assinatura ambígua o bastante para desambiguar por prefixo. Os dois só se resolvem perguntando —
**e configuração de instrumento é por música**, nunca conhecimento de repositório.

`arrangement-brief.json` carrega `instruments.<familia>` (`guitar`, `bass` — as duas famílias de
corda dedilhada; bateria e teclas não têm afinação a declarar), estruturado por
`tools/brief_schema.py`:

```
instruments: {
  guitar: { known, strings, tuning: { name, notes } | null },
  bass: {
    known, strings, tuning: { name, notes } | null,
    playing_style: "finger" | "pick" | "slap" | null,
    notation: "written" | "sounding" | null,
  },
}
```

- `known: false` é ausência DECLARADA ("o usuário não sabe") — os demais campos ficam `null`; nunca
  um palpite maquiado de dado. `known: true` sem `tuning` é erro — se a configuração é conhecida, a
  afinação faz parte dela.
- `tuning.name` (`"Drop C"`, `"Drop G#"`, `"E padrão"`) resolve contra o MESMO manual
  `guitar.drop_tuning` que `tools.tuning` usa (`tools.tuning.resolve_tuning_name`, nunca uma tabela
  paralela) — por `(número de cordas, nome canônico)`, porque `Drop A` de 6 cordas e `Drop A` de 7
  cordas são instrumentos diferentes com pisos diferentes. O manual só documenta guitarra (6/7/8
  cordas); baixo nunca resolve por nome hoje — pede `tuning.notes` sempre, o que é o comportamento
  correto (nunca inventar "guitarra menos uma oitava" sem fonte).
- Nome sem entrada no manual para aquele número de cordas — como `Drop G#` de 7 cordas, o caso real
  que abriu a issue #44 — **não é aceito em silêncio**: `brief.validate` recusa com
  `E_BRIEF_TUNING_NAME_UNKNOWN` e pede `tuning.notes` explícito (MIDI das cordas soltas,
  grave→agudo). `tuning.notes` tem que ter o mesmo tamanho de `strings` e vir em ordem estritamente
  ascendente; nome e notas declarados juntos têm que concordar (`E_BRIEF_TUNING_NAME_MISMATCH`
  quando não concordam).
- `bass.notation` existe porque baixo é instrumento TRANSPOSITOR — soa uma oitava abaixo do que
  está escrito na convenção padrão (`written`). Medindo o caso real: 82% dos ataques do baixo
  estavam em uníssono ESCRITO com a nota mais grave da guitarra, que soa uma oitava abaixo dela.
  Ler a track como altura soante (`sounding`) quando na verdade é escrita faz o arranjador
  escrever a linha uma oitava no lugar errado e o baixo deixa de sustentar a guitarra.
- **A declaração do usuário vence a detecção automática.** `tools.tuning` continua rodando —
  confirma ou contradiz a declaração; contradição vira aviso no relatório (mostrando os dois
  valores e qual está sendo usado), nunca erro que trava o brief.
- A entrevista que preenche isso vive em `skills/midi-brief/SKILL.md` — pergunta só pela família
  presente no MIDI de origem (lida a partir do `tuning_inference` que o `analyze` do passo 1 já
  devolveu), na mesma linha da pergunta de estilo/referência daquela família.

---

## 8. Prompts driver

Um por ferramenta, no mesmo espírito do Ralph. O conteúdo é quase idêntico entre elas; a diferença
existe porque cada CLI tem convenções próprias de ferramenta e de permissão.

Todo driver contém: quem o agente é; o fluxo dos 10 passos; as tools disponíveis e quando usar cada
uma; a obrigação de ler `knowledge/` antes de decidir; a regra de nunca extrair conteúdo musical da
pesquisa; e a instrução de emitir a sentinela só quando o arranjo passar em todos os validadores.

`AGENTS.md` na raiz existe em paralelo, para quem quiser trabalhar no repositório com um agente
qualquer sem passar pelo harness.

---

## 9. Regras invioláveis

- Nunca sobrescrever o MIDI de origem.
- Track não declarada para edição sai nota a nota idêntica.
- Mesmo plano, mesma origem, mesma seed: byte-idêntico.
- Nenhum parâmetro sorteado sem origem declarada; o componente aleatório nunca supera a soma das
  intenções determinísticas.
- Perfil de artista pesquisado nunca vira base de conhecimento — vive no plano daquela música.
- A pesquisa levanta técnica e comportamento, **nunca conteúdo musical**.
- Nenhum teste artificial para inflar cobertura.
- Número sem fonte é marcado como não verificado e jamais apresentado como fato.
- Nada em `tools/` importa de `bin/` ou depende de LLM.
