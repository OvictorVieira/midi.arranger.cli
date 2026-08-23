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
- Mesmo plano, mesma origem, mesma seed: arquivo **byte-idêntico**.
- Nenhum parâmetro sorteado sem origem declarada. O componente aleatório nunca supera a soma das
  intenções determinísticas.
- Perfil de artista pesquisado **nunca** vira base de conhecimento — vive no plano daquela música.
- A pesquisa levanta **técnica e comportamento**, jamais conteúdo musical.
- Número sem fonte é marcado `[NÃO VERIFICADO]` e **jamais** apresentado como fato.
- Determinismo nas tools: sem relógio, sem `random` sem seed, sem rede.

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

## Commits

Formato `type: descrição concisa` — conventional commits, **sem scope, sem Co-Authored-By**.
Tipos: `feat` `fix` `test` `chore` `docs` `refactor`.
Nunca `--no-verify`. Nunca amend em commit publicado. Nunca force-push em `main`.

## Worktrees

`.worktrees/<numero-da-issue>-<slug>/`, sempre dentro do repositório, sempre a partir de
`origin/main` atualizado.
