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

O bloco é estruturalmente anticópia: chaves ou formas que carreguem notas, tempos, riffs, grooves,
frases, melodias, motivos ou sequências musicais são erro. Quando um parâmetro casa com uma técnica
citada e o manual declara `range`, valor fora da faixa é erro, nunca clamp silencioso.

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
│   ├── humanize.py  voicing.py  constants.py  tracks.py
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
| `plugins.scan` | Inventário de plugins e presets instalados |

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
2. **TRAVA 2 — contagem mínima por canal.** Só entra na inferência canal com
   `note_count >= MIN_NOTES_PER_CHANNEL_FOR_INFERENCE` (=8). Canal com meia dúzia de notas tem
   mínimo que é nota casada, não corda solta. Descarte aparece como `low_note_count`.
3. **TRAVA 3 — span como sanidade.** Corda solta vive numa janela estreita; span >
   `MAX_STRING_SPAN_SEMITONES` (=24) desmente a hipótese canal-igual-corda. Descarte aparece como
   `span_too_wide`.

A tabela de afinações vem do manual `guitar.drop_tuning` em `knowledge/tecnicas/`, lida pelo índice
de técnicas e convertida em intervalos entre cordas adjacentes — **não hardcodada**. Classificação
é por prefix-match: 3 cordas graves com intervalos `[7,5]` já classificam como drop, porque o riff
pode não usar as agudas. O `7` na base é a assinatura do drop.

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

O relatório sempre expõe `candidate_channels` e `discarded_channels` com quantidade e motivo —
o consumidor pode auditar por que uma track ficou `unknown` sem re-executar o detector.

### O que está fora de escopo desta rodada

Declarar corda no plano e emitir keyswitch de forçar corda ficam para rodada seguinte, porque tocam
os mesmos arquivos da issue #10. Esta rodada **não** altera `tools/render.py`, `tools/plan.py` nem
`tools/techniques/`.

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
