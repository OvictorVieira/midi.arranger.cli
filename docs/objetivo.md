# Objetivo do midi-arranger e critérios de aceite

> **Para que serve este documento.** Ele responde duas perguntas: *o que queremos* e *como saber se
> chegamos lá*. Todo critério aqui é verificável por teste automatizado. Nenhum critério é opinião.
>
> Quando uma rodada terminar, é contra este arquivo que se confere se o que foi feito chegou no que
> queríamos. Critério sem teste é intenção, não aceite — e neste documento não entra.

---

## 1. O objetivo, em uma frase

Uma ferramenta de linha de comando que age como **músico arranjador**: recebe um MIDI, pergunta ao usuário o
estilo e as referências que ele quer por família de instrumento, pesquisa como aquelas referências
tocam, consulta o manual local de técnicas para saber como reproduzir aquilo em MIDI, e usa o maquinário
determinístico para **construir e remodelar** as linhas — devolvendo um único MIDI pronto para o DAW.

A IA é a parte criativa. O Python é o maquinário. Os validadores são a prova de que o maquinário
construiu o que a IA decidiu.

### O fluxo que precisa funcionar de ponta a ponta

```
1. Analisar o MIDI de entrada                     → analyze
2. IA identifica padrões, seções, tom, densidade  → IA lê a saída do analyze
3. IA pergunta estilo e referência por família    → entrevista
4. IA pesquisa a referência ao vivo               → técnica e comportamento, nunca conteúdo
5. IA consulta o manual local de técnicas         → como fazer aquilo em MIDI
6. IA decide o arranjo e escreve o plano          → arrangement-plan.json com style e rationale
7. Maquinário constrói e remodela                 → render
8. Validadores provam que saiu o que foi decidido → relatório
9. IA lê o relatório; se disparou, corrige e refaz
10. Usuário recebe um MIDI para testar
```

---

## 2. O que fica no repo e o que é buscado ao vivo

| | No repo, versionado | Buscado ao vivo, nunca persistido |
|---|---|---|
| Manual de como reproduzir técnica em MIDI | ✅ `knowledge/tecnicas/` | |
| Persona e base de realismo | ✅ `knowledge/` | |
| Corpus de MIDI do usuário (fixtures) | ✅ `tests/fixtures/` | |
| Perfil de artista/banda pesquisado | | ✅ pesquisa a cada pedido |

O perfil pesquisado **não vira arquivo de base de conhecimento**, mas **é registrado no
`arrangement-plan.json` daquela música** — com `sources`, `researched_at` e `confidence`. É isso que
mantém o render determinístico e auditável sem envelhecer informação dentro do repo.

---

## 3. Critérios de aceite

Cada critério tem um identificador `AC-nn`, um enunciado verificável e o teste que o prova.

### Bloco A — Fidelidade ao pedido do usuário

| | Critério | Como se prova |
|---|---|---|
| **AC-01** | Pediu ghost notes no baixo, saem ghost notes no baixo — na faixa de velocity e gate que o manual define para ghost, não notas comuns fracas | Plano com técnica `ghost_notes` no baixo → conta notas em `20 ≤ vel ≤ 45` com gate curto; asserta que apareceram onde antes não havia |
| **AC-02** | Pediu redução de viradas na bateria, a densidade de virada cai e a estrutura permanece | Entrada com N viradas → saída com densidade menor, mesmos compassos de virada, downbeats preservados |
| **AC-03** | Pediu uma família que não existe no MIDI, ela é criada | Entrada sem baixo + plano pedindo baixo → saída tem track de baixo com notas dentro do campo harmônico |
| **AC-04** | Família que o usuário não pediu e a IA não julgou faltar não aparece | Plano sem guitarra → nenhuma track de guitarra na saída |

### Bloco B — Fidelidade ao estilo pesquisado

| | Critério | Como se prova |
|---|---|---|
| **AC-05** | Todo parâmetro declarado no `style` do plano é efetivamente aplicado ao MIDI gerado | Perfil mockado com `timing_bias_ms: -8` → offset médio medido na track fica em −8 ± tolerância |
| **AC-06** | Perfil com densidade alta de uma técnica produz mais ocorrências dela que perfil com densidade baixa | Dois renders, `ghost_note_density` 0.1 vs 0.4, mesma seed → contagem cresce monotonicamente |
| **AC-07** | Técnica declarada no plano que não existe no manual local é rejeitada na validação | Plano com `techniques: [{name: "inexistente"}]` → erro de validação nomeando o campo |
| **AC-08** | Sem referência dada, a skill usa a persona default e **declara** isso | Plano sem `style` → `confidence: "default"` e suposição declarada em `assumptions` |
| **AC-09** | Pesquisa que não achou material suficiente é declarada, não inventada | Perfil com `confidence: "low"` → relatório do render avisa explicitamente |

### Bloco C — Fidelidade musical

| | Critério | Como se prova |
|---|---|---|
| **AC-10** | Toda nota gerada pertence ao campo harmônico do compasso | Validador harmônico (FR-25) — já entregue na rodada 2 |
| **AC-11** | Elemento só soa nas seções declaradas | Validador de placement (FR-26) — já entregue |
| **AC-12** | O arranjo respeita a mentalidade assumida | Validador de persona (FR-27) — já entregue |
| **AC-13** | A saída não dispara anti-padrão de artificialidade | Validador de artificialidade (FR-19) — já entregue |
| **AC-14** | Toda fronteira de seção muda ao menos duas dimensões | Validador de duas dimensões (FR-20) |

### Bloco D — Não cópia

| | Critério | Como se prova |
|---|---|---|
| **AC-15** | O plano nunca carrega trecho musical transcrito do artista referenciado — só parâmetro de técnica | Schema do `style` aceita apenas parâmetros numéricos e nomes de técnica do manual; qualquer campo com sequência de notas é rejeitado |
| **AC-16** | A saída não reproduz melodia ou levada reconhecível de material de referência | Se um corpus de referência for dado, nenhuma sequência de N notas consecutivas da saída coincide com o corpus |

### Bloco E — Garantias mecânicas

| | Critério | Como se prova |
|---|---|---|
| **AC-17** | Mesmo plano + mesma origem + mesma seed = arquivo byte-idêntico | Dois renders comparados byte a byte |
| **AC-18** | Track não declarada em `edits` sai nota a nota idêntica | Comparação nota a nota da entrada com a saída |
| **AC-19** | O MIDI de origem nunca é sobrescrito | Hash do arquivo de entrada antes e depois |
| **AC-20** | Técnica reaplicada é idempotente — não empilha ornamento sobre ornamento | Aplicar o motor duas vezes com o mesmo plano dá o mesmo resultado que aplicar uma |
| **AC-21** | Nenhum parâmetro é sorteado sem origem declarada | Asserção de runtime: todo valor vem do perfil ou do baseline; o componente aleatório nunca supera a soma das intenções |
| **AC-22** | `logicpro/humanize.py` permanece intacto e as três skills vizinhas seguem funcionando | Hash no quality gate + imports de cada skill |

### Bloco F — O loop da IA

| | Critério | Como se prova |
|---|---|---|
| **AC-23** | A SKILL.md instrui a ler as duas bases de conhecimento antes de decidir | Teste de conteúdo: a instrução existe e os arquivos referenciados existem |
| **AC-24** | A entrevista cobre estilo e referência por família | Teste de conteúdo da SKILL.md |
| **AC-25** | Depois do render a IA lê o relatório e corrige antes de entregar | Relatório é legível por máquina (JSON) e carrega severidade por item |
| **AC-26** | Todo elemento do plano carrega `rationale` não vazio | Validação de schema |

---

## 4. Estratégia de teste e o que é mockado

A pesquisa da referência acontece ao vivo, contra a web. Isso **não é testável de forma determinística**
e não deve ser. A fronteira é clara:

| Camada | Testado como |
|---|---|
| Pesquisa da referência (IA + web) | **Mockada.** Testes injetam um `style` fixo no plano. Nunca se testa contra a web |
| Manual de técnicas (`knowledge/tecnicas/`) | **Real.** Os testes leem os arquivos versionados |
| Tradução de técnica em nota MIDI | **Real e determinística.** É o núcleo do que se testa |
| Decisão da IA | **Fora do teste automatizado.** O que se testa é que o plano que ela escreve é válido e que o render obedece ao plano |

Consequência de desenho: **o `style` do plano é a fronteira entre o não-determinístico e o
determinístico.** Tudo acima dele é IA; tudo abaixo é máquina testável. É por isso que o perfil precisa
aterrissar no plano em vez de ser consumido em memória.

### Fixtures

| Fixture | Origem | Para que serve |
|---|---|---|
| `corpus_drums/*.mid` | Dez MIDIs de bateria da banda do usuário | Vocabulário e estrutura; corpus do `learn` |
| `corpus_drums/ENTRE NÓS.mid` | O mais chapado do acervo — 100% em velocity 127, zero ghost, zero desvio de grade | **Prova principal do motor de técnicas:** entrada sem intenção nenhuma; se sair com intenção, o motor funciona |
| `ancora_arranjo_atual.mid` | Arranjo real feito à mão | Calibração dos validadores de placement e persona |
| `ÂNCORA - MIX.mid` | Referência empírica original | Números de humanização da rodada 1 |
| Planos golden | Escritos à mão | Entrada determinística para todo teste de render |

`ENTRE NÓS.mid` é o fixture mais valioso do conjunto justamente por ser o pior MIDI do ponto de vista de
humanização. Ele é o antes; o teste define o depois.

---

## 5. Como saber que chegamos

A skill está pronta quando, num MIDI que o usuário nunca usou antes:

1. `midi-arranger brief` é invocado e faz as perguntas de estilo por família.
2. O usuário responde em linguagem natural — nome de músico, banda ou "no estilo das nossas músicas".
3. A skill pesquisa, mostra o que achou e **declara a confiança** do que achou.
4. Ela apresenta o mapa de seções para confirmação.
5. Ela escreve um plano legível, com `rationale` em cada elemento e `sources` no `style`.
6. O render roda e **todos os validadores passam**.
7. O usuário abre no Logic, as tracks vêm nomeadas com plugin e preset, e a coisa soa como o estilo
   pedido — sem soar como cópia de ninguém.
8. Ele edita o plano à mão, re-renderiza, e o resultado muda de forma previsível.

Os critérios AC-01 a AC-26 são a versão automatizável disso. O passo 7 é o único que só o ouvido resolve.

---

## 6. Fora do objetivo

- Não gera áudio, não mixa, não masteriza, não escolhe timbre final.
- Não transcreve áudio para MIDI.
- Não escreve linha vocal nem letra.
- Não copia melodia, riff, levada ou arranjo reconhecível de nenhum artista — a pesquisa levanta técnica
  e comportamento, jamais conteúdo.
- Não afirma ser o artista referenciado nem sugere endosso.
- Não persiste perfil de artista como base de conhecimento.
- Não modifica `logicpro/humanize.py`.
