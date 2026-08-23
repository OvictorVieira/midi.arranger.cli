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
- Número sem fonte é marcado `[NÃO VERIFICADO]` e **jamais** apresentado como fato.
- Determinismo nas tools: sem relógio, sem `random` sem seed, sem rede.
- Ao adicionar leitura de `element.pattern` em `tools/render.py`, atualize o conjunto
  `*_PATTERN_FIELDS` do role correspondente para evitar aviso falso de campo ignorado.
- Em strings/choir com `pattern.tutti=true`, `element.layers` é limitado por
  `STRINGS_TUTTI_MAX_VOICES`; dimensione buckets/tracks pelo número efetivo e avise quando reduzir.
- `tools.render.render()` valida `ArrangementPlan` em memória com `plan.validate()` antes de carregar
  MIDI ou rodar validadores; plano inválido deve falhar como `PlanValidationError`, não como erro
  interno do pipeline.

## Qualidade

- Todo módulo com teste. `python -m pytest` da raiz.
- Teste de regressão obrigatório para todo bug corrigido.
- **Nunca** criar teste artificial para inflar cobertura. Todo teste cobre cenário real com asserção
  de comportamento observável.
- **Nunca** editar `quality-baseline.json` à mão para o gate passar.
- Dependências das tools: só `mido` e `pretty_midi`. Nenhuma nova sem perguntar.
- Python ≥ 3.11.

## Commits

Formato `type: descrição concisa` — conventional commits, **sem scope, sem Co-Authored-By**.
Tipos: `feat` `fix` `test` `chore` `docs` `refactor`.
Nunca `--no-verify`. Nunca amend em commit publicado. Nunca force-push em `main`.

## Worktrees

`.worktrees/<numero-da-issue>-<slug>/`, sempre dentro do repositório, sempre a partir de
`origin/main` atualizado.
