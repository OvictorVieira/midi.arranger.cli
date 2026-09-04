# Fixture default de `test-drive`

`ENTRE NÓS.mid` aqui é uma cópia (byte-idêntica) de
`tests/fixtures/corpus_drums/ENTRE NÓS.mid` — descrita em `docs/objetivo.md` §4
como "o fixture mais valioso do conjunto".

A cópia existe porque `install.sh` só copia `bin`, `prompts`, `tools`,
`knowledge`, `skills`, `AGENTS.md` e `requirements.txt` para o corpo instalado
(`AGENTS.md` — "Instalação"); `tests/` não é instalado. `tools/test_drive.py`
usa este arquivo, dentro de `tools/`, como fixture default para funcionar
também a partir do comando instalado (`midi-arranger test-drive`, sem
`--fixture`), não só de um checkout de desenvolvimento.

Não edite este arquivo separadamente do original em `tests/fixtures/`. Se o
original mudar, recopie aqui.
