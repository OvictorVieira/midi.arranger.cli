"""US-001: detectar virada de forma deterministica.

Ver PRD e AGENTS.md. `classify_window` e `fill_windows` alimentam o
reregistro de `drums.accent_hierarchy` — sem essa distincao, contratempo
de virada cai em ghost, exatamente o defeito que fechou a issue #50.
"""

from __future__ import annotations

from tools.techniques._fill_detection import (
    FILL_MIN_DENSITY_PER_BEAT,
    FILL_MIN_NOTES,
    FILL_MIN_PIECE_VARIETY,
    classify_window,
    fill_windows,
    is_fill_run,
    piece_family,
)

TPB = 480  # ticks por semi (semininma); semicolcheia = TPB // 4 = 120


def _note(pitch: int, start: int, *, velocity: int = 100, channel: int = 9) -> dict:
    return {
        "channel": channel,
        "pitch": pitch,
        "start": start,
        "end": start + 60,
        "duration": 60,
        "velocity": velocity,
    }


def _steady_backbeat_bar() -> list[dict]:
    """Groove classico: kick em 1/3, snare em 2/4, hihat em cada 16."""

    notes: list[dict] = []
    for step in range(16):
        tick = step * (TPB // 4)
        notes.append(_note(42, tick, velocity=70))  # hi-hat closed em toda 16
    for beat in (0, 2):
        notes.append(_note(36, beat * TPB, velocity=110))  # kick 1 e 3
    for beat in (1, 3):
        notes.append(_note(38, beat * TPB, velocity=115))  # snare 2 e 4
    return notes


def _dense_fill_bar() -> list[dict]:
    """Virada: 16 notas em semicolcheias variando por caixa e toms."""

    sequence = [38, 45, 47, 48, 38, 45, 47, 48, 38, 43, 45, 47, 38, 43, 45, 47]
    return [
        _note(pitch, step * (TPB // 4), velocity=112)
        for step, pitch in enumerate(sequence)
    ]


def test_groove_estavel_com_backbeat_regular_nao_e_virada():
    groove = _steady_backbeat_bar()
    assert classify_window(groove, ticks_per_beat=TPB) == "groove"
    assert fill_windows(groove, ticks_per_beat=TPB) == ()


def test_rufo_denso_com_pecas_variadas_e_virada():
    fill = _dense_fill_bar()
    assert classify_window(fill, ticks_per_beat=TPB) == "fill"
    detected = fill_windows(fill, ticks_per_beat=TPB)
    assert len(detected) == 1
    start, end = detected[0]
    assert start == 0
    assert end == 15 * (TPB // 4)


def test_fill_windows_isola_o_trecho_dentro_de_um_arranjo_maior():
    bar_ticks = TPB * 4
    notes: list[dict] = []
    notes.extend(_steady_backbeat_bar())
    fill = _dense_fill_bar()
    for note in fill:
        deslocado = dict(note)
        deslocado["start"] += bar_ticks
        deslocado["end"] += bar_ticks
        notes.append(deslocado)
    notes.extend(
        {**n, "start": n["start"] + 2 * bar_ticks, "end": n["end"] + 2 * bar_ticks}
        for n in _steady_backbeat_bar()
    )
    detected = fill_windows(notes, ticks_per_beat=TPB)
    assert len(detected) == 1
    start, end = detected[0]
    assert start == bar_ticks
    # Fim pode se estender ate o proximo downbeat (kick de resolucao); o que
    # nao pode e vazar para dentro do terceiro compasso, que e groove estavel.
    assert end <= 2 * bar_ticks


def test_hihat_ostinato_denso_sozinho_nao_conta_como_virada():
    """Alto e denso, mas so uma familia — variedade falta, e groove."""

    hihat_only = [_note(42, step * (TPB // 4), velocity=80) for step in range(16)]
    assert classify_window(hihat_only, ticks_per_beat=TPB) == "groove"


def test_run_curto_nao_conta_como_virada():
    curto = [_note(38, step * (TPB // 4)) for step in range(FILL_MIN_NOTES - 1)]
    assert is_fill_run(curto, ticks_per_beat=TPB) is False


def test_densidade_baixa_nao_conta_como_virada():
    """Notas variadas mas espacadas demais — nao passa o piso de densidade."""

    esparso = [
        _note(38, 0),
        _note(45, TPB * 2),
        _note(47, TPB * 4),
        _note(48, TPB * 6),
    ]
    assert len(esparso) >= FILL_MIN_NOTES
    familias = {"snare", "tom"}
    assert len(familias) >= FILL_MIN_PIECE_VARIETY
    assert is_fill_run(esparso, ticks_per_beat=TPB) is False


def test_notas_fora_do_canal_de_bateria_sao_ignoradas():
    baixo = [_note(45, step * (TPB // 4), channel=1) for step in range(FILL_MIN_NOTES)]
    assert classify_window(baixo, ticks_per_beat=TPB) == "groove"
    assert fill_windows(baixo, ticks_per_beat=TPB) == ()


def test_run_com_uma_unica_familia_nao_e_virada():
    """Run rapido, denso e longo, mas so uma familia — nao vale como virada.

    Guarda o piso `fill_min_piece_variety`: ostinato de caixa sozinha e
    exercicio de rudimento, nao virada de arranjo.
    """

    so_caixa = [
        _note(38, step * (TPB // 4), velocity=100)
        for step in range(FILL_MIN_NOTES + 2)
    ]
    assert is_fill_run(so_caixa, ticks_per_beat=TPB) is False


def test_fill_windows_com_ticks_por_beat_invalido_retorna_vazio():
    """Guarda de sanidade: MIDI corrompido nao trava a deteccao."""

    notes = _dense_fill_bar()
    assert fill_windows(notes, ticks_per_beat=0) == ()
    assert fill_windows(notes, ticks_per_beat=-1) == ()


def test_convencoes_sao_lidas_do_manual():
    """As convencoes do modulo casam com o bloco `accent_hierarchy` do manual.

    Se alguem mexer no manual sem mexer no modulo (ou vice-versa) o teste
    quebra — essa e a promessa da secao 11.
    """

    from tools.techniques.index import build_index

    technique = build_index().get("drums.accent_hierarchy")
    assert technique is not None
    parametros = {p.name: p for p in technique.parameters}
    assert parametros["fill_max_gap_beats"].value == 0.25
    assert parametros["fill_min_notes"].value == FILL_MIN_NOTES
    assert parametros["fill_min_density_per_beat"].value == FILL_MIN_DENSITY_PER_BEAT
    assert parametros["fill_min_piece_variety"].value == FILL_MIN_PIECE_VARIETY
    for nome in (
        "fill_max_gap_beats",
        "fill_min_notes",
        "fill_min_density_per_beat",
        "fill_min_piece_variety",
    ):
        source = parametros[nome].source or ""
        assert source.startswith("CONVENCAO"), (
            f"{nome} precisa declarar CONVENCAO com a razao no campo `source`, "
            f"nao so em prosa (regra do AGENTS.md)"
        )


# --- Regressao: achados do Codex no PR #59 (review r3882332323/333) --------


def test_fill_windows_aceita_limiares_sobrescritos():
    """Achado do Codex: um plano que sobrescreva `fill_min_piece_variety` (ou
    os outros tres limiares) tinha que mudar o resultado — antes desta
    correcao `fill_windows` sempre usava as constantes do modulo, entao a
    sobrescrita do plano era aceita na validacao e ignorada no render.

    O trecho denso abaixo bate variedade 2 (tom + caixa) por default e
    portanto conta como virada; pedindo `min_piece_variety=3` explicitamente
    tem que derrubar essa mesma corrida para groove.
    """

    fill = _dense_fill_bar()
    assert fill_windows(fill, ticks_per_beat=TPB) != ()
    assert (
        fill_windows(fill, ticks_per_beat=TPB, min_piece_variety=3) == ()
    )


def test_fill_windows_aceita_min_notes_e_min_density_sobrescritos():
    """Mesmo achado, cobrindo os outros dois limiares numericos."""

    fill = _dense_fill_bar()
    assert fill_windows(fill, ticks_per_beat=TPB, min_notes=len(fill) + 1) == ()
    assert (
        fill_windows(fill, ticks_per_beat=TPB, min_density_per_beat=999.0) == ()
    )


def test_fill_windows_aceita_max_gap_beats_sobrescrito():
    """`max_gap_beats` menor que o gap real do trecho corta o agrupamento em
    runs curtos demais, que caem abaixo de `FILL_MIN_NOTES` e desaparecem."""

    fill = _dense_fill_bar()
    assert fill_windows(fill, ticks_per_beat=TPB, max_gap_beats=0.0) == ()


def test_piece_family_reconhece_aliases_do_kit_real():
    """Achado do Codex: o kit real do usuario (knowledge/tecnicas/
    tecnicas_bateria_midi.md secao 5, `alias kickR 36 34 35` e
    `alias snareR 38 6 33 39 66 68 69 70 125`) tinha 33/34 sem familia
    nenhuma e 66/68 caindo em tom por engano."""

    assert piece_family(33) == "snare"
    assert piece_family(34) == "kick"
    assert piece_family(66) == "snare"
    assert piece_family(68) == "snare"
    # 65 e 67 continuam tom puro (SD3 Core, secao 1.2) — so os quatro
    # numeros citados pelo Codex mudam de familia.
    assert piece_family(65) == "tom"
    assert piece_family(67) == "tom"
