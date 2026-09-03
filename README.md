# midi.arranger.cli

Um arranjador de MIDI dirigido por IA. Você passa um MIDI e uma demanda em linguagem natural
— *"bateria no estilo do fulano, teclas tipo aquela banda, põe ghost notes no baixo"* — a
ferramenta te entrevista, pesquisa as referências que você citou, e usa scripts determinísticos
para construir e remodelar as linhas. No fim, um MIDI para importar na DAW que você preferir.

Não gera áudio, não mixa, não escolhe timbre. Produz **notas, CCs e organização de tracks**, com
sugestão de plugin e preset no nome de cada track.

## Como funciona

Duas metades, com fronteira dura entre elas.

| | **Harness** | **Tools** |
|---|---|---|
| O que é | O agente: conversa, decide, pesquisa, orquestra | Os scripts: medem, transformam, validam, escrevem MIDI |
| Natureza | Não-determinístico. É um LLM | **Determinístico.** Mesma entrada, mesma saída, sempre |
| Como roda | Chama a CLI de IA que **você já usa** | Python puro, sem LLM nenhum |

O harness **não implementa loop de agente nem fala com provider de LLM**. Ele invoca a ferramenta
que você já tem instalada — Claude Code, Codex, opencode, Antigravity, Cursor, Amp ou Gemini — e
gerencia o estado entre as iterações. Mesmo modelo do [Ralph](https://github.com/OvictorVieira/ralph).

Como todo agente suportado já tem acesso a shell, as tools são simplesmente comandos. Não há MCP,
não há SDK, não há integração por provider.

## Instalação

```bash
git clone git@github.com:OvictorVieira/midi.arranger.cli.git
cd midi.arranger.cli
./install.sh
```

Escreve em exatamente três lugares:

| Onde | O quê |
|---|---|
| `~/.local/bin/midi-arranger` | O comando. Garanta que esse diretório está no `PATH` |
| `~/.local/share/midi-arranger/` | O corpo: harness, prompts, tools e a base de técnicas |
| `~/.claude/skills/`, `~/.opencode/skills/`, `~/.agents/skills/` | Symlink da skill `midi-brief`, só nos providers que existirem |

Dá para mudar os dois primeiros com `MIDI_ARRANGER_HOME` e `XDG_BIN_DIR`. Nada é escrito fora
deles — o instalador não mexe em nenhuma outra configuração de provider, e diz no fim exatamente o
que fez.

Requer **Python ≥ 3.11**. As dependências das tools são `mido` e `pretty_midi`; se faltarem, o
instalador avisa quais e dá o comando exato, mas não roda `pip` por conta própria.

Rodar de novo é idempotente. Depois de um `git pull`, rode `./install.sh` outra vez — o harness e a
skill vêm do corpo instalado, não do checkout, justamente para nunca ficarem em versões diferentes.

## Primeiro test-drive

Antes de rodar `brief`/`run` de verdade com sua própria IA, valide o ambiente local em quatro
comandos — nenhum deles fala com nenhuma IA:

```bash
git clone git@github.com:OvictorVieira/midi.arranger.cli.git
cd midi.arranger.cli
./bin/midi-arranger doctor --tool claude   # Python, dependências, provider, tools — tudo ok?
./bin/midi-arranger test-drive             # analyze -> plan -> render -> validate, com perfil mockado
```

`doctor` confere Python ≥ 3.11, `mido`/`pretty_midi`, se o registro de tools importa sem erro, o
inventário **atual** de técnicas e roles que o motor realmente executa, se o binário da CLI escolhida
em `--tool` está no `PATH`, e permissão de escrita no projeto. Ele nunca testa acesso à internet — só
declara que a pesquisa ao vivo durante `brief` depende das ferramentas da sua própria CLI de IA.

`test-drive` copia um MIDI de bateria versionado (`tests/fixtures/corpus_drums/`, ou `--fixture
seu.mid`) para um workspace temporário isolado — o original nunca é tocado — e roda o pipeline
determinístico real (`analyze` → `plan.validate` → `render` → `validate`) com um perfil de estilo
**mockado**, sem pesquisa nenhuma. Produz MIDI, plano e relatório no workspace; some ao final a menos
que você passe `--keep`.

Códigos de saída: `0` ambiente saudável / fluxo ok, `1` o `test-drive` rodou mas um validador achou
erro musical, `2` problema de ambiente (dependência faltando, provider ausente, sem permissão de
escrita).

## Os dois comandos

```bash
# 1. Interativo: entrevista, pesquisa as referências, escreve o brief
midi-arranger brief musica.mid

# 2. Autônomo: executa até o MIDI ficar pronto
midi-arranger run --tool claude --effort high 12
```

O `brief` é onde você é questionado. O `run` é onde a coisa é construída, com contexto limpo a cada
iteração e o estado vivendo nos arquivos.

## Estado em disco

| Arquivo | Papel |
|---|---|
| `arrangement-brief.json` | O que você quer. Demanda, respostas da entrevista, perfis de estilo pesquisados com fontes |
| `arrangement-plan.json` | O que será construído. Editável à mão e re-renderizável sem rodar IA de novo |
| `progress.txt` | Log append-only das iterações |
| `.midiarranger/` | Estado interno e arquivo de execuções anteriores |

`arrangement-plan.json` é a fronteira entre o não-determinístico e o determinístico: acima dele é
IA, abaixo é máquina testável.

## Garantias

- O MIDI de origem **nunca** é sobrescrito.
- Track que você não mandou editar sai nota a nota idêntica.
- Mesmo plano, mesma origem, mesma seed: arquivo byte-idêntico.
- Nenhum parâmetro é sorteado sem origem declarada.
- A pesquisa levanta **técnica e comportamento**, nunca conteúdo musical. A ferramenta se inspira,
  não copia — e há validador que reprova cópia, inclusive transposta.

## Estado do projeto

Em construção.

- [Roadmap completo](docs/roadmap.md)
- [Arquitetura](docs/arquitetura.md)
- [Objetivo e critérios de aceite](docs/objetivo.md)
- [M6 — MVP orientado por influências](https://github.com/OvictorVieira/midi.arranger.cli/issues/80)
- [M7 — plataforma comercial MCP](https://github.com/OvictorVieira/midi.arranger.cli/issues/81)
