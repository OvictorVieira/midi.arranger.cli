"""Fixtures compartilhados entre modulos de teste.

`synthetic_contract_midi` existe para a issue #34: a maioria dos testes de
CONTRATO (envelope, codigo de erro, schema, aviso) nao precisa do arquivo
ancora real (`tests/fixtures/ancora_arranjo_atual.mid`, 86 KB / 27 tracks /
163 compassos) — um MIDI sintetico minimo percorre exatamente os mesmos
caminhos de codigo por uma fracao do tempo de render/analise.

Escopo de sessao porque e usado so como ENTRADA somente-leitura: nenhum
teste de contrato escreve nele, so le e grava saida em `tmp_path`. Isso
tambem satisfaz o pedido de reuso de fixture quando varios testes so
inspecionam o resultado do mesmo insumo, em vez de reconstrui-lo por teste.

Testes que dependem de COMPORTAMENTO MUSICAL REAL do arquivo ancora — valores
congelados (contagem de secoes/compassos, tom), determinismo byte a byte e
preservacao das tracks originais — continuam usando o helper
`_require_fixture()` de cada modulo de teste, que aponta para o ancora. Esta
fixture NAO os substitui.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.test_render import _build_synthetic_source

_SYNTHETIC_CONTRACT_MIDI_PATH: str | None = None


def _build_synthetic_contract_midi() -> str:
    """Variante sem injecao de fixture de `synthetic_contract_midi`, para
    helpers de modulo que nao recebem parametros de teste (ex.:
    `_valid_plan_from_skeleton()` em `test_contract_analyze_plan.py`).
    Memoiza em nivel de processo — mesmo papel de uma fixture de escopo de
    sessao, so que chamavel fora do ciclo de vida de uma fixture pytest."""
    global _SYNTHETIC_CONTRACT_MIDI_PATH
    if _SYNTHETIC_CONTRACT_MIDI_PATH is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="midi_arranger_contract_synth_"))
        _SYNTHETIC_CONTRACT_MIDI_PATH = str(_build_synthetic_source(tmp_dir))
    return _SYNTHETIC_CONTRACT_MIDI_PATH


@pytest.fixture(scope="session")
def synthetic_contract_midi() -> str:
    """MIDI sintetico minimo (8 compassos, 2 tracks) para testes de contrato
    que so exercitam envelope/schema/erro — nunca comportamento musical
    real. Reusa `tests.test_render._build_synthetic_source`, ja testado e
    calibrado para o estimador de tempo/compasso do pretty_midi."""
    return _build_synthetic_contract_midi()
