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
| `influence-profile.json` | A pesquisa desta música: `sources[]` e `findings[]` do `InfluenceProfile` (`tools/influence.py`) | fase `brief` |
| `arrangement-plan.json` | O que será construído: seções, elementos, `style`, `edits`, `rationale` por elemento | fase `run` |
| `arrangement-report.json` | Relatório de proveniência: a cadeia da influência ao resultado MIDI | tool `report.build`, depois do `render` |
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

### A skill `midi-brief` como coordenadora (issue #76)

A entrevista deixou de ser só um formulário: `skills/midi-brief/SKILL.md` coordena o MVP
*reference-driven* inteiro. A divisão é a mesma de sempre — **a IA do usuário pesquisa e decide; o
maquinário determinístico valida e executa** —, agora com os dez passos explícitos na skill:

1. `analyze` → 2. entrevista → 3. pesquisa das referências citadas → 4. registro de fontes e
achados no `InfluenceProfile` → 5. validação do perfil → 6. `influence.compile` → 7. apresentação
dos mapeamentos e dos achados **não suportados** → 8. autorização explícita → 9. brief +
`influence-profile.json` gravados → 10. `midi-arranger run` renderiza, valida, corrige e entrega.

Regras que a skill carrega e que o maquinário faz valer:

- **Linguagem do produto.** Arranjo *influenciado por características de performance*. Nunca
  "clone", "cópia" ou "reprodução exata" — posicionamento legal, não preferência estética.
- **Nada de número inventado a partir de prosa.** O perfil registra comportamento em vocabulário
  fechado; número de MIDI e parâmetro de execução vêm do manual (`techniques.describe`) ou de
  `influence.compile`. `tools.influence._validate_free_string` recusa nota e sequência MIDI dentro
  de `semantic_value`/`summary`.
- **Só se oferece o que o motor executa.** As sugestões saem restritas a `SUPPORTED_TECHNIQUES`;
  técnica apenas documentada (`implemented: false`) não pode ser oferecida nem autorizada —
  `brief.validate` recusa em `E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED`.
- **Autorização em uma ação, lista canônica completa.** O usuário pode autorizar o conjunto
  recomendado de uma vez, mas o brief grava nome por nome em `authorized_techniques[]`; não existe
  marcador de "todas". Silêncio ou dúvida não autoriza nada.
- **Achado não suportado permanece visível.** `unmapped_findings` é apresentado ao usuário e
  declarado em `assumptions`; nunca é descartado nem vira sugestão genérica.
- **Ausência de pesquisa é lacuna, não decisão.** "A referência não usa isso" só existe com fonte
  (`intensity: off`, que sai em `not_recommended`).
- **Sem acesso à web, três saídas.** Fornecer as fontes manualmente, usar a persona default (com a
  ausência declarada em `assumptions`), ou cancelar aquela referência. Memória do modelo não é
  fonte e não entra em `sources[]`.
- **Veto manda mais que sugestão.** Antirreferência e veto do usuário derrubam a sugestão
  compilada; veto de família inteira vira `excluded_families[]`.
- **A skill não renderiza.** Render é o passo 10, na fase `run`, e só depois da autorização
  gravada.

O perfil vive em `influence-profile.json`, no projeto daquela música, ao lado do brief — nunca em
`knowledge/`, nunca como persona reutilizável. É ele que a fase `run` passa a `report.build` para
fechar a cadeia de proveniência.

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
- `parameters`: apenas número escalar ou par `[min, max]` — contrato LEGADO, compartilhado por
  todas as técnicas da família (ver `techniques[].parameters` abaixo para o contrato atual,
  por técnica).
- `techniques[].style`: única exceção não numérica ao "número ou par" do bloco `style`. Seleção
  fechada de técnica de execução (dedo/palheta/slap, ex. `bass.attack_style`), validada contra
  `STYLE_TECHNIQUE_STYLE_VALUES` — nunca string livre. Existe porque a escolha não tem como ser
  número: o manual da técnica declara a categoria, não uma faixa.
- `techniques[].parameters` (issue #72): mesma forma restrita de `parameters` de família (número
  escalar ou par `[min, max]`), mas pertence à TÉCNICA que os consome, não à família inteira.
  Validado contra a receita da PRÓPRIA técnica resolvida (`tools.techniques.build_index()`), nunca
  contra todas as técnicas da família — duas técnicas da mesma família podem declarar um parâmetro
  de mesmo nome (ex. `velocity`) com faixas diferentes sem colidir. Quando o mesmo nome aparece nos
  dois níveis para a mesma técnica, o nível da técnica MANDA sobre o legado `parameters` de família
  — mesma lógica de "parâmetros do plano > receita da tool > range do manual" já documentada acima
  — e `plan.validate()` emite warning de conflito. `tools.render._run_style_pipeline` funde os dois
  canais (legado como base, `parameters` da técnica por cima) antes de despachar, então o aplicador
  recebe só os parâmetros relevantes para ELE.
- `techniques[].intensity` (issue #72): intensidade semântica explícita, `0.0`-`1.0`, mesma escala
  de `PlanEdit.intensity`. Quando `techniques[].density` está ausente, `intensity` assume o papel
  de `density` no despacho (liga/desliga a técnica e entra em `context.parameters["density"]`) —
  sempre exposta também em `context.parameters["intensity"]`. `density`, quando declarado, continua
  tendo precedência (retrocompatibilidade: plano v1 nunca declara `intensity`).
- `techniques[].evidence_refs` (issue #72): lista de ids de achados (`InfluenceFinding.id`,
  `tools/influence.py`) que justificaram a técnica — rastreabilidade pura, validada só
  estruturalmente (strings não vazias); sem cruzamento contra um `InfluenceProfile` carregado
  (fora do escopo da issue #72).

O bloco é estruturalmente anticópia: chaves ou formas que carreguem notas, tempos, riffs, grooves,
frases, melodias, motivos ou sequências musicais são erro — a mesma barreira vale para `parameters`
de família e para `techniques[].parameters`. Quando um parâmetro casa com uma técnica citada e o
manual declara `range`, valor fora da faixa é erro, nunca clamp silencioso.

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

### Decisão de criar família ausente e veto do usuário (issue #17)

A criação de bateria/baixo/elementos harmônicos do zero já existia antes da issue #17: qualquer
`plan.elements[]` gera conteúdo novo independentemente de o MIDI de origem ter uma track daquela
família — o mecanismo é `role`/`_style_family_for_role` mais o dispatch de `_ROLE_RENDERERS`, o
mesmo caminho de qualquer elemento do plano. O que faltava era a **camada de decisão**: a IA
declara *por quê* está criando (AC-03) e o usuário pode vetar a criação de uma família inteira
mesmo que a IA julgue que ela falta (AC-04 e a "restrição do brief manda").

1. **Justificativa (AC-03).** Todo `plan.elements[]` já carrega `rationale` obrigatório e não vazio
   (regra pré-existente). Quando o elemento preenche uma lacuna do MIDI de origem, o `rationale`
   descreve isso em prosa — não há campo estrutural adicional além do já exigido, porque a
   justificativa textual É o mecanismo de auditoria (o mesmo papel que `rationale` cumpre para
   qualquer outro elemento do plano).

2. **Veto (AC-04 / "não quero guitarra gerada").** `brief.excluded_families` (`tools/brief_schema.py`)
   é um array de vocabulário fechado (`STYLE_FAMILIES`: `bass`, `drums`, `guitar`, `keys`) — nunca
   texto livre parseado de `restricoes`, que continua sendo prosa documental sem maquinário atrás.
   Campo opcional: brief antigo sem `excluded_families` não veta nada, preservando o comportamento
   de criação já entregue. Duplicata na lista é `E_BRIEF_EXCLUDED_FAMILIES_DUPLICATE`.

   O veto se aplica só a `plan.elements[]` (conteúdo **gerado**) — `plan.edits[]` fica de fora porque
   edita uma track que já existe no MIDI de origem, nunca cria família nova.

   Mesmo padrão de três camadas de `authorized_techniques` (seção acima):
   - **No plano.** `tools/plan.py::validate` lê o brief apontado por `plan.brief_ref`, confere o
     `sha256` (mesma função `_read_and_verify_brief` que `_load_brief_authorized_techniques` usa) e
     recusa qualquer `plan.elements[i].role` cuja família (`_style_family_for_role`) esteja em
     `brief.excluded_families` — `PlanValidationError` no path `elements[i].role`, mesmo que o
     `rationale` do elemento diga explicitamente que a IA julgou a família ausente. Plano sem
     `brief_ref` não tem veto nenhum (não há como saber o que o usuário vetou), mesmo default seguro
     de "sem brief, sem restrição adicional" — a criação sem veto já era o comportamento entregue
     antes da issue #17.
   - **No render.** `tools/render.py::_reject_excluded_family_elements` repete a barreira antes de
     `validate_plan`, para plano construído em memória sem passar por `plan.load` — violação vira
     `RenderError` explícito citando `role`, família e o `rationale` do elemento, nunca aplica
     parcial nem ignora em silêncio.

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
| `keys` | `keys.damper_pedal`, `keys.expression`, `keys.human_articulation`, `keys.modulation`, `keys.pitch_bend`, `keys.rolled_chord`, `keys.voice_dynamics` | `keys.melody_lead`, `keys.hand_asynchrony`, `keys.bass_anticipation`, `keys.syncopated_pedal`, `keys.vibrato`, `keys.rhodes_touch`, `keys.hammond_dynamics` |
| `guitar` | `guitar.bend`, `guitar.dead_notes`, `guitar.double_tracking`, `guitar.hammer_pull`, `guitar.palm_mute`, `guitar.pinch_harmonic`, `guitar.vibrato` | `guitar.chord_voicing`, `guitar.dive_bomb`, `guitar.drop_tuning`, `guitar.natural_harmonics`, `guitar.picking_direction`, `guitar.pick_scrape`, `guitar.power_chord`, `guitar.rake`, `guitar.slide`, `guitar.track_offset`, `guitar.tremolo_picking` |

Inventário de guitarra (issue #19): as cinco técnicas implementadas cobrem palm
mute/chug (`guitar.palm_mute`, profundidade por velocity + gate curto, keyswitch
opcional quando a receita da ferramenta declara um), dead notes entre chugs
(`guitar.dead_notes`, ornamento de baixa velocity herdando o pitch da nota
anterior), pinch harmonic (`guitar.pinch_harmonic`, sobe a velocity de notas
selecionadas para 127 — o gatilho real documentado da Ample —, e falha
explícito fora de `tool: ample`/`ample_metal` em vez de recorrer à única
receita `generic` do manual, que exigiria transpor a nota estrutural),
hammer-on/pull-off (`guitar.hammer_pull`, reduz a velocity da nota ligada e
sobrepõe o note-off da primeira sobre o ataque da segunda, restrito a pares
que alcançam a MESMA corda na afinação declarada) e double tracking real
(`guitar.double_tracking`, cria uma SEGUNDA track com offset de timing e de
velocity sorteados por nota e um detune de canal constante via pitch bend —
nunca duplica a track 1‑para‑1; idempotente por um marcador
`meta text guitar_double_tracking_of=<índice da track de origem>` na track
duplicada, não pelo dedup central de notas). A rodada seguinte fechou a dívida com mais duas: `guitar.bend`
(pré-bend no alvo ANTES do note_on e rampa monotônica de volta ao centro
depois do ataque, exatamente a receita `generic` do manual, com o range
declarado por RPN 0 e fechado com RPN Null) e `guitar.vibrato` (oscilação
senoidal de pitch bend que só entra DEPOIS do atraso de início — o estágio
"Start" que a Ample documenta para impedir que nota rápida seja vibrada — e
termina em ciclo inteiro, no centro, antes do note_off). As duas só entram em
nota que soa SOZINHA no canal: o manual é categórico que bend dentro de acorde
em canal único é impossível e que vibrato de canal em power chord está errado,
porque o guitarrista vibra UMA corda. "Sozinha no canal" é avaliado no
`MidiFile` INTEIRO (`isolated_notes_by_file`), não track a track: canal não
pertence a uma track — `_render_guitar_element` dá o mesmo `GUITAR_CHANNEL` a
todas as layers e `_apply_style_techniques_to_edit_tracks` junta num só
`MidiFile` todas as tracks físicas com o mesmo nome de DAW, então avaliar por
track deixava passar exatamente o power chord proibido, com dois fluxos de
pitch bend conflitantes no mesmo canal. O `guitar.vibrato` também fecha SEMPRE
no centro: o evento de fase 1.0 é o único que vale 0 e, quando o arredondamento
o punha em cima do `note_off`, ele era descartado e o bend ficava pendurado
desafinando a nota seguinte. E o sorteio de `delay_ms`/`rate_hz` acontece antes
de qualquer escrita: nota que não fecha um ciclo inteiro depois do sorteio sai
byte-idêntica, sem RPN órfão. Nenhum número novo entrou no manual de
guitarra para isso — os dois blocos já tinham todos os parâmetros com fonte.

As onze técnicas restantes do
manual (`guitar.chord_voicing`, `guitar.dive_bomb`,
`guitar.drop_tuning`, `guitar.natural_harmonics`, `guitar.picking_direction`,
`guitar.pick_scrape`, `guitar.power_chord`, `guitar.rake`, `guitar.slide`,
`guitar.track_offset`, `guitar.tremolo_picking`) continuam
fora de `SUPPORTED_TECHNIQUES`, cada uma por um motivo concreto — nenhuma por
falta de tempo:

- `guitar.natural_harmonics` e `guitar.slide`: mesmo motivo estrutural de
  `bass.harmonic`/`bass.slide` (exigiriam mudar pitch estrutural, exceção
  reservada à articulação de bateria).
- `guitar.chord_voicing` e `guitar.power_chord`: são regras de CONSTRUÇÃO do
  voicing (reusadas diretamente por `tools/palette/guitar.py` via
  `tools.techniques.physical.guitar_voicing_is_playable`), não ornamentos
  toggleáveis por `style.guitar.techniques[]`.
- `guitar.dive_bomb`: o próprio manual recusa declarar profundidade e duração —
  inventar um número aqui contradiria a seção "Lacunas".
- `guitar.picking_direction`: os TRÊS parâmetros do bloco são `source: null`, e
  o próprio manual diz que em lib sem Picking Mode "o padrão de velocity que
  expressa down-picking vs alternate NÃO TEM FONTE". O que o bloco recomenda é
  escolher um MODO do plugin, que não é evento MIDI.
- `guitar.tremolo_picking`: a única coisa que o motor poderia fazer —
  sequenciar 32avos no lugar da nota longa — é exatamente o que o manual manda
  NÃO fazer ("quando a lib tem articulação de tremolo real, dispare UMA NOTA
  LONGA"), e encurtar a nota estrutural para caber a sequência é proibido.
- `guitar.rake`: os dois números de que o aplicador precisaria
  (`delay_entre_rake_e_nota_ms` e `velocity_das_cordas_abafadas`) são
  `source: null`, e a receita `generic` não define QUAIS alturas as cordas
  abafadas soam — escolhê-las seria inventar conteúdo, não parametrizar
  técnica.
- `guitar.pick_scrape`: `duracao_ms` e `velocity` são `source: null` e o bloco
  descreve um evento de transição de SEÇÃO — o motor de técnicas não recebe o
  mapa de seções, então onde colocar o scrape seria decisão inventada.
- `guitar.drop_tuning`: é restrição de GERAÇÃO ("nunca gerar nota abaixo da
  corda solta mais grave"), já garantida por `tools/techniques/physical.py`.
  Como técnica só poderia transpor nota estrutural (proibido) ou não fazer
  nada.
- `guitar.track_offset`: o offset é NEGATIVO por definição (20 a 30 ms para
  trás) e MIDI não tem tempo negativo. Material que começa no tick 0 — o caso
  normal — não tem para onde recuar, e recuar a guitarra empurrando as outras
  tracks para a frente violaria a regra de que track não declarada sai nota a
  nota idêntica. É ajuste de track delay do DAW, não dado de nota.

`_validate_strings` (`tools/techniques/physical.py`) recebeu uma exceção
dedicada a `*.hammer_pull` (`bass.hammer_pull` e `guitar.hammer_pull`,
identificada pelo sufixo do nome canônico): a sobreposição que essas duas
técnicas criam de propósito (para disparar legato no instrumento sampleado)
é sempre explicável por UMA corda tocando as duas alturas em sequência — uma
corda é fisicamente monofônica, então a sobreposição de ticks não representa
dois dedos simultâneos. A exceção só vale quando existe uma corda que alcança
TODAS as alturas ativas; para qualquer outra técnica (ornamentos
independentes como `guitar.dead_notes`/`bass.ghost_notes`), sobreposição
exigindo a mesma corda continua sendo erro de plausibilidade física — duas
notas independentes não podem soar juntas de uma corda só.

O teste `test_supported_techniques_is_derived_from_the_registry` em
`tests/test_techniques_engine.py` afirma a tupla exata para que registro fantasma
(aplicador stub, `_identity_apply`) quebre o build. O teste
`test_keys_engine_inventory_matches_the_issue_14_contract` no mesmo arquivo trava
o inventário da família `keys` e afirma que as sete técnicas restantes
documentadas continuam fora do motor.

Nas três de nível `humanize`, a seleção por `density` é decidida CANDIDATO A
CANDIDATO, a partir da seed do contexto e da identidade do alvo (canal, tick,
altura/alturas), pelo helper `select_by_stable_density`. Sortear um subconjunto
do pool (`select_by_density`) só é idempotente enquanto o pool não muda entre
passadas, e ele muda justamente porque a técnica aplicada tira o alvo da lista
de candidatos — acorde rolado deixa de ser simultâneo, nota já articulada não
tem mais o que encurtar. Em `keys.rolled_chord` o espalhamento sorteado também
sai da identidade do acorde, senão o valor de cada acorde dependeria de quantos
acordes foram selecionados antes dele.

O limite conhecido dessa idempotência está medido e não escondido: reaplicar
`keys.rolled_chord` sobre a SAÍDA dela pode rolar um acorde a mais, quando o
rolo do acorde anterior liberou a folga que faltava — as vozes de baixo dele
passam a terminar antes. É convergente (a partir daí não anda mais), mas não é
idempotência plena, e nenhuma escolha de seed conserta: a grandeza medida
mudou de verdade. `render` sobre a mesma origem continua byte-idêntico, porque
a origem é a mesma; o teste
`test_a_chord_freed_by_the_neighbours_roll_only_settles_on_the_next_pass`
trava exatamente esse comportamento.

As quatro técnicas de teclas de EXPRESSÃO CONTÍNUA (`keys.damper_pedal`,
`keys.expression`, `keys.modulation`, `keys.pitch_bend`) são nível `technique`
e só acrescentam CC/pitch bend — nunca mudam pitch/posição/duração da nota
estrutural. As três acrescentadas na rodada seguinte são nível `humanize`,
porque mexem em execução (velocity, timing, duração) e não escrevem evento
nenhum novo:

- `keys.voice_dynamics`: sobe a voz de cima do acorde até ficar `delta` acima
  da mais forte das outras. O `delta` em unidades MIDI é DERIVADO no manual por
  aritmética de dois parâmetros já sourced do mesmo bloco (`fhv_melodia_normal`
  e `fhv_melodia_enfatizada`, Goebl 2001) pela conversão logarítmica medida de
  Goebl & Bresin 2003 — nada de converter m/s linearmente para 0–127, que é o
  erro contra o qual o próprio manual avisa. Rebaixar só acontece quando o topo
  já bateu 127, e aí o piso é `127 - delta`: nenhuma voz cai mais que `delta`
  pontos porque a queda máxima possível é `127 - (127 - delta)`. É a invariante
  que impede a inversão de intenção que `drums.accent_hierarchy` cometeu em
  DEIXE IR — e ela é ARITMÉTICA, não um guard que descarta acorde. O PR #120
  trazia um `if` que prometia jogar fora "o acorde que exigiria uma queda
  maior"; ele era inalcançável (só rodava com o topo em 127, onde a condição
  vira `velocity > 127`) e saiu na revisão: proteção morta vendida como
  proteção ativa é o mesmo vício de `_identity_apply`.
- `keys.rolled_chord`: espalha o acorde com intervalos DECRESCENTES do grave
  para o agudo (o achado que dá nome ao bloco) e deixa a nota de topo no tempo,
  como manda a receita. Só entra acorde com folga real antes do tempo e escrito
  em ordem ascendente — rolar um acorde escrito fora dessa ordem exigiria
  reordenar os `note_on`, que é justamente o que o contrato `humanize` proíbe.
  A folga se mede até o FIM da nota anterior, não até o onset dela: medindo até
  o onset, uma nota terminando cinco ticks antes do tempo passava na guarda e o
  `note_on` da fundamental do acorde nascia ANTES do `note_off` dela — o
  sintetizador cortava a fundamental. Acorde cujo `note_off` deslocado cruzaria
  o `note_off` de uma nota de fora também fica de fora, porque `note_pairs`
  congela a ordem dos `note_off` da track inteira.
- `keys.human_articulation`: aplica a razão de articulação medida (0,75) ao
  tell nº 2 do manual, a nota colada com 100% da duração nominal. A razão é
  medida contra o INTERVALO ATÉ O PRÓXIMO ATAQUE, não contra a duração escrita:
  é a forma da regra Overall articulation. Medir contra a duração encurtaria a
  cada passada, empilhando ornamento sobre ornamento. Medir contra o IOI é
  NECESSÁRIO para a idempotência, mas não basta — a afirmação de que era "a
  única" coisa que fazia a técnica ser idempotente estava errada: a nota já
  articulada some do pool de candidatos, e com sorteio sobre o pool a passada
  seguinte resorteava o resto intocado, de modo que `density` fracionária
  convergia para 1,0 a cada reaplicação.

As sete restantes continuam fora do motor, também com motivo concreto:

- `keys.melody_lead`, `keys.hand_asynchrony` e `keys.bass_anticipation`: as
  três exigem que uma voz soe ANTES de notas escritas no mesmo tick. Realizar
  isso muda a ordem dos `note_on` na track, que o contrato `humanize` congela
  (`_MidiContentSnapshot.note_on_sequence`), e o nível `technique` não pode
  mover nota estrutural nenhuma. Não é falta de número — os números são os
  melhores do manual inteiro (Goebl 2001; Goebl/Flossmann/Widmer 2010) — é
  contrato.
- `keys.vibrato`: o próprio manual (§7.6) diz que o onset não tem padrão medido
  ("não escreva 'vibrato entra após N ms' — é invenção") e recomenda, para
  resultado reprodutível entre engines, desenhar rampa de CC1 — que é
  exatamente o que `keys.modulation` já executa. Registrar seria duplicar
  aplicador com outro nome.
- `keys.syncopated_pedal`: o gesto (soltar antes da harmonia nova, repisar
  depois que ela soa) já é o que `keys.damper_pedal` escreve; a diferença é a
  constante de atraso. Dois aplicadores registrados escrevendo CC64 na mesma
  track brigariam entre si — o atraso medido de 50–150 ms é candidato a
  parâmetro de `keys.damper_pedal`, não a técnica nova.
- `keys.rhodes_touch`: o bloco declara explicitamente que NÃO existe curva de
  velocity canônica do Rhodes, e que nenhum estudo de performance foi
  publicado. Os parâmetros com fonte são geometria da ação em milímetros, sem
  ponte para nota MIDI.
- `keys.hammond_dynamics`: a regra central (velocity é ignorada, dinâmica é
  CC11) é fato oficial, mas realizá-la exige um mapeamento velocity→CC11 que
  nenhuma fonte publica; inventá-lo seria mais um número órfão.

`filtro` (CC74) e `portamento` (CC5/CC65), citados na issue #14
original, **não** têm bloco de técnica próprio em `knowledge/tecnicas/tecnicas_teclas_midi.md`
(só aparecem discutidos em prosa na §7.4). São pesquisa futura, não bug desta
rodada — inventar bloco de técnica novo em cima deles é escopo novo.

Os dois únicos números acrescentados ao manual nesta rodada estão em
`tecnicas_teclas_midi.md`: `keys.voice_dynamics.delta_midi_melodia_vs_acompanhamento`
(DERIVADO, aritmética entre dois parâmetros já sourced) e
`keys.rolled_chord.razao_entre_intervalos_sucessivos` (CONVENÇÃO com razão
declarada — a fonte publica o perfil e o total, nunca a razão entre um
intervalo e o seguinte). O segundo derruba `verified` de `keys.rolled_chord`
para `false`, como manda o parser, e isso é o sinal correto.

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

### Relatório de proveniência (issue #77)

`tools/report.py` monta `arrangement-report.json` **depois** do render. Ele não pesquisa, não
mapeia, não aplica e não valida nada por conta própria: só liga artefatos que já existem.

| Elo | De onde vem |
|---|---|
| `source` | `InfluenceProfile.sources[]` (`tools/influence.py`) |
| `finding` | `InfluenceProfile.findings[]` |
| `mapping` | `compile_influence()` — `MAPPING_RULES` e `INFLUENCE_MAPPING_VERSION` (`tools/influence_compile.py`) |
| `technique` | `plan.style.<familia>.techniques[]` + `authorized_techniques[]`/`suggested_techniques[]` do brief |
| `track` | carimbo `meta 0x01 text` no tick 0 (`techniques=[...]`), escrito por `tools/render.py` |
| `section` | `plan.elements[].sections` do elemento que gerou a track |
| `metric` | medição direta do MIDI final (`tools.report.track_metrics`) + veredito dos validadores |

Regra de negócio central: **"aplicada com sucesso" só aparece com evidência objetiva de
validador.** Por isso `ValidatorRun` carrega `covered_tracks` — a lista do que o validador
realmente recebeu. Ausência de issue não prova que ele olhou; só a cobertura prova. Sem cobertura,
o status é `aplicada_nao_verificavel`, nunca "ok".

Vocabulário de status por técnica: `aplicada_verificada`, `aplicada_com_erro`,
`aplicada_nao_verificavel`, `autorizada_nao_aplicada`, `sugerida_nao_autorizada`,
`nao_recomendada`, `nao_suportada`. O bloco `techniques` resume nas cinco listas que a issue pede
(sugeridas, autorizadas, aplicadas, ignoradas, não suportadas).

Todo elo que falta vira entrada em `missing_links` com código estável (`source`, `finding`,
`mapping`, `technique`, `track`, `metric`), caminho e motivo — o relatório nunca preenche elo
ausente com suposição, nem trata ausência de informação como aprovação.

**Escopo dos validadores no relatório (issue #124).** `report.build` audita um MIDI já escrito, e
`tools.contract._rendered_tracks_from_midi` reconstrói por eliminação toda track do arquivo que não
casa com elemento do plano como `RenderedTrack` sintética `source:<nome>`. Essa lista completa serve
a `validate_transitions` (fronteira também vem de track de origem) e a `compliance` (compara origem
com renderizado), mas **não** ao anti-cópia. Quem decide o alcance de cada validador é
`_report_validator_runs`, do lado da fachada — nenhuma assinatura de validador muda:

| Validador | Escopo | O que percorre |
|---|---|---|
| `harmonia`, `placement`, `artificialidade` | `per_track` | só track de elemento — eles mesmos pulam o resto por `elements_by_id.get(...) is None` |
| `anticopia` | `per_track` | só track de elemento — é o único que percorreria qualquer lista que recebesse, então o recorte vem da fachada |
| `persona`, `colisao`, `conformidade` | `global` | track nenhuma; erro deles não carrega campo `track` e rebaixa todo alvo |

O recorte do anti-cópia é o mesmo que `tools.render.render()` sempre usou: lá o `rendered_tracks` do
momento da chamada só tem track de elemento, e as tracks de origem/edição entram depois, só para
`validate_transitions`. A razão é musical, não de implementação: track não declarada em `plan.edits`
sai byte-idêntica por contrato, e mesmo a declarada tem as notas ESTRUTURAIS do usuário — o nível
`technique` só acrescenta ornamento sobre elas. A janela que casa com o corpus de referência não foi
escrita pelo arranjador, e acusá-la é acusar o usuário de copiar o próprio material. Consequência
aceita: track de `plan.edits` não recebe cobertura de validador POR TRACK nenhum, e a técnica
aplicada nela sai `aplicada_nao_verificavel` — `nao_verificavel` é resposta legítima, e é melhor que
veredito errado.

Anticópia: `InfluenceFinding.summary` **nunca** é copiado (só `summary_present`/`summary_chars`);
`semantic_value` é citado apenas até `MAX_QUOTE_CHARS` e passa pela mesma barreira de conteúdo
musical do perfil (`tools.influence._validate_free_string`) — string recusada vira `null` com nota
de omissão. Da fonte, só metadado de citação (id, url, título, data).

Determinismo: sem relógio, sem rede, sem `random`; toda lista sai ordenada por chave estável, e
duas execuções da mesma entrada produzem o mesmo arquivo byte a byte.

### Paleta de transições (issue #23)

Os eventos que costuram uma seção na seguinte — `riser`, `downer`, `impact` e `reverse` (meia-lua) —
são roles gerados do zero em `tools/palette/transitions.py`, no mesmo molde de `sub`/`sub_drop`/
`hat_elec` (issue #22): cada elemento aponta a seção seguinte em `element.sections`, e o gerador
ancora o evento no início (downbeat) do primeiro compasso daquela seção. `riser`/`downer` terminam
ANTES do downbeat (a curva de CC74/CC11 só sobe/desce, nunca chega lá); `impact` ataca EXATAMENTE no
downbeat, em camadas com caudas divergentes e três intensidades (soft/medium/hard) que ciclam por
`occurrence_index`, nunca sorteio sem origem; `reverse` RESOLVE exatamente no downbeat, com CC7/CC74
em formato de meia-lua (sobe e desce) — essa é a diferença estrutural entre riser e meia-lua que não
pode colapsar numa implementação só. Todo número vem de
`knowledge/tecnicas/tecnicas_transicoes_midi.md` (`transitions.riser`, `transitions.impact`,
`transitions.reverse` — `downer` reaproveita os parâmetros de `riser`, mecânica invertida), lido via
`tools.techniques.index.build_index()`, nunca hardcoded; a issue não trouxe medição, então todo
parâmetro é `CONVENCAO` declarada, não `verified: true`. `impact` já cai na tabela FR-24 existente de
`tools/tracks.py::SAMPLER_ROUTING` (plugin default `Logic Sampler`) e `riser` já estava na whitelist
FR-14 de `SERUM_ALLOWED_ROLES` — os dois roles previstos pela issue #23 já tinham lugar reservado
nessas tabelas antes desta rodada.

`false_downbeat`, `subdivision_flip` e `half_time_magnifier` (a última seção da issue) são funções
puras e testadas no mesmo módulo (`false_downbeat_delay_s`, `generate_subdivision_flip`,
`half_time_drum_pattern`) — cobrem a mecânica descrita, mas NÃO ganharam role/campo de plano próprio
nesta rodada (mesmo corte de escopo que `electronic.py` já documenta para `perc_elec`/`vox_chop`).

### Prova ponta a ponta com influência mockada (issue #79)

`tests/test_e2e_influencias.py` exercita o produto inteiro sem web e sem modelo: um
`InfluenceProfile` fixo, escrito no próprio arquivo de teste, faz o papel da pesquisa; daí para baixo
tudo é real e passa pelo mesmo `tools.registry.call` do CLI —
`analyze` → `tools.influence.validate` → `influence.compile` → `brief.validate` → `plan.validate` →
`render` → `validate` (+ `compliance.validate` e `report.build`).

**Divergência declarada:** a issue pede `influence.validate` como fachada. Ela não existe no
registry — a validação do perfil é função de módulo (`tools.influence.validate`), como esta seção já
documenta. O teste `test_influence_validate_nao_e_fachada_do_registry` fixa a divergência para que
ela seja decisão visível, não esquecimento.

As duas origens são fixtures reais: `tests/fixtures/ancora_arranjo_atual.mid` (arranjo feito à mão,
com `Bass`, `Drums`, guitarras, teclas e marcadores de seção) e
`tests/fixtures/corpus_drums/ENTRE NÓS.mid` (a bateria mais chapada do acervo — 1037 notas todas em
velocity 127). Cinco cenários: remodelar bateria e baixo existentes; criar a família baixo ausente;
aplicar `keys.expression`; receber um achado de guitarra que o dicionário de `influence.compile` não
mapeia e degradar em `unmapped_findings`; e respeitar `brief.excluded_families`.

**Quatro achados do motor**, todos com repro concreto. Os de número 1, 2 e 4 seguem com
`xfail(strict=True)` no arquivo — cada marcador quebra o build no dia em que o defeito sair. O de
número 3 foi corrigido na issue #124 e seu teste hoje é regressão comum:

1. `drums.microtiming` não roda em take de bateria real com releases sobrepostos: o contrato
   `humanize` congela a ORDEM GLOBAL dos `note_off` (`_MidiContentSnapshot.note_pairs`), e deslocar
   o hi-hat alguns ms troca a ordem do release dele com o de outra peça. `ancora_arranjo_atual.mid`
   tem 16 re-ataques de 42/46 com a nota anterior ainda soando e falha; os MIDIs de `corpus_drums`
   não têm nenhum e passam.
2. `render` e `validate` divergem sobre o MESMO arquivo e o MESMO plano: o render que gerou a linha
   de baixo declara zero erro harmônico e o `validate` sobre o arquivo que ele acabou de escrever
   acusa sete, em notas a poucos milissegundos da borda de compasso.
3. **CORRIGIDO (issue #124).** O anti-cópia de `report.build` julgava as tracks que o arranjador
   NÃO escreveu (`_rendered_tracks_from_midi` reconstrói cada track de origem como `source:<nome>`),
   enquanto o de `render` só olha as tracks de elemento. Com o mesmo corpus: zero contra dezenove, e
   esses erros rebaixavam o status de TODA técnica do relatório para `aplicada_com_erro`. Hoje a
   fachada entrega ao `validate_anticopy` o mesmo conjunto que o `render` lhe entrega — ver
   "Escopo dos validadores no relatório", acima.
4. `StyleTechnique.intensity` sequestra o canal de `density` e desliga a densidade por seção da
   issue #45. Como `influence.compile` sempre emite `intensity`, todo plano montado pelo caminho real
   de pesquisa perde o eixo `plan.sections[].energy.densidade`: trocar 9 por 1 entre as duas metades
   do arquivo devolve MIDI byte-idêntico.

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
| `report.build` | Relatório de proveniência: fonte → achado → mapeamento → técnica → track/seção → métrica |

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
