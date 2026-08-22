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

Em construção. Veja as issues e `docs/arquitetura.md`.
