"""Invariante de pressao de `drums.accent_hierarchy` sobre corpus real.

Cobre a US-003 da issue #50. A versao antiga da tecnica destruia virada e
rebaixava a mediana global de bateria em dezenas de pontos; a reimplementacao
usa `_fill_detection` mais um piso de rebaixamento por nota (o parametro
`pressure_max_drop`, documentado como CONVENCAO no manual). Os testes aqui
medem SEMPRE por arquivo (nunca pool) e por peca (nunca so overall), porque
a mediana agregada ja mascarou essa inversao uma vez.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median

import mido
import pytest

from tools.techniques import apply_technique
from tools.techniques._fill_detection import piece_family

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "corpus_drums"

# Corpus real com bateria no canal 9. Precisamos de mais de um arquivo (AC-3.1)
# para garantir que a invariante nao esta amarrada a um caso especifico.
CORPUS_FILES = ("DEIXE IR.mid", "MARÉ DRUMS.mid", "TEMPESTADE.mid")

# AC-3.1: nenhuma nota com origem >= 110 pode sair <= 45 depois da tecnica.
LOUD_SOURCE_THRESHOLD = 110
GHOST_MAX_VELOCITY = 45

# AC-3.2 e AC-3.3: mediana (por peca ou global) nao cai mais que 15 pts,
# medida SOBRE A MESMA PECA. Coincide com o `pressure_max_drop` do manual.
MEDIAN_DROP_TOLERANCE = 15


def _drum_notes(mid: mido.MidiFile) -> list[tuple[int, int, int]]:
    """(pitch, velocity, tick) de todas as `note_on` no canal 9."""

    out: list[tuple[int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if (
                msg.type == "note_on"
                and msg.velocity > 0
                and getattr(msg, "channel", -1) == 9
            ):
                out.append((msg.note, msg.velocity, tick))
    return out


def _apply(path: Path) -> mido.MidiFile:
    return apply_technique(
        "drums.accent_hierarchy",
        mido.MidiFile(str(path)),
        seed=1,
    )


def _velocities_of_family(
    notes: list[tuple[int, int, int]],
    family: str,
) -> list[int]:
    return [vel for pitch, vel, _ in notes if piece_family(pitch) == family]


@pytest.mark.parametrize("filename", CORPUS_FILES)
def test_nota_forte_da_origem_nunca_vira_ghost(filename):
    """AC-3.1: origem >= 110 sempre sai > 45.

    Foi essa a inversao que tirou a tecnica do motor: 63 das 65 caixas de
    `DEIXE IR` com origem >= 110 saiam <= 45 na versao antiga. Aqui a
    invariante de pressao garante que nunca mais acontece.
    """
    src_path = CORPUS_DIR / filename
    src_notes = _drum_notes(mido.MidiFile(str(src_path)))
    out_notes = _drum_notes(_apply(src_path))

    assert len(src_notes) == len(out_notes), (
        f"{filename}: contagem de notas mudou "
        f"({len(src_notes)} -> {len(out_notes)})"
    )
    inversions: list[tuple[int, int, int]] = []
    for (sp, sv, st), (op, ov, ot) in zip(src_notes, out_notes, strict=True):
        assert (sp, st) == (op, ot), (
            f"{filename}: estrutura mudou em tick {st} "
            f"(pitch/tick {sp}/{st} -> {op}/{ot})"
        )
        if sv >= LOUD_SOURCE_THRESHOLD and ov <= GHOST_MAX_VELOCITY:
            inversions.append((sv, ov, st))

    assert not inversions, (
        f"{filename}: {len(inversions)} nota(s) forte(s) caiu/cairam para "
        f"ghost — invariante de pressao quebrada. Amostra: {inversions[:5]}"
    )


@pytest.mark.parametrize("filename", CORPUS_FILES)
def test_mediana_de_toms_nao_cai_mais_de_15_pts_por_arquivo(filename):
    """AC-3.2: mediana de tom por ARQUIVO nao cai mais que 15 pts.

    Medir em pool de arquivos ja mascarou essa inversao uma vez.
    """
    src_path = CORPUS_DIR / filename
    src_toms = _velocities_of_family(
        _drum_notes(mido.MidiFile(str(src_path))),
        "tom",
    )
    if not src_toms:
        pytest.skip(f"{filename} nao tem tom no canal 9")
    out_toms = _velocities_of_family(_drum_notes(_apply(src_path)), "tom")

    src_median = median(src_toms)
    out_median = median(out_toms)
    drop = src_median - out_median
    assert drop <= MEDIAN_DROP_TOLERANCE, (
        f"{filename}: mediana de tom caiu {drop} pts "
        f"({src_median} -> {out_median})"
    )


@pytest.mark.parametrize("filename", CORPUS_FILES)
def test_mediana_global_por_arquivo_nao_cai_mais_de_15_pts(filename):
    """AC-3.3: mediana global de velocity por ARQUIVO nao cai mais que 15."""

    src_path = CORPUS_DIR / filename
    src_notes = _drum_notes(mido.MidiFile(str(src_path)))
    out_notes = _drum_notes(_apply(src_path))

    src_median = median(v for _, v, _ in src_notes)
    out_median = median(v for _, v, _ in out_notes)
    drop = src_median - out_median
    assert drop <= MEDIAN_DROP_TOLERANCE, (
        f"{filename}: mediana global caiu {drop} pts "
        f"({src_median} -> {out_median})"
    )


@pytest.mark.parametrize("filename", CORPUS_FILES)
@pytest.mark.parametrize(
    "family", ["kick", "snare", "tom", "hihat", "cymbal"]
)
def test_mediana_por_peca_por_arquivo(filename, family):
    """AC-3.4: cada peca (kick/snare/tom/hihat/crash) medida separadamente.

    Overall mean/stddev ja mascarou a inversao uma vez nesta base
    (`_apply_drums_accent_hierarchy` do commit 502d440). Aqui olhamos peca
    por peca dentro do MESMO arquivo — se qualquer familia cair mais de 15
    pts, e sinal de que a tecnica esta invertendo intencao naquela peca.
    """
    src_path = CORPUS_DIR / filename
    src_family = _velocities_of_family(
        _drum_notes(mido.MidiFile(str(src_path))),
        family,
    )
    if not src_family:
        pytest.skip(f"{filename} nao tem {family} no canal 9")
    out_family = _velocities_of_family(_drum_notes(_apply(src_path)), family)

    src_median = median(src_family)
    out_median = median(out_family)
    drop = src_median - out_median
    assert drop <= MEDIAN_DROP_TOLERANCE, (
        f"{filename}: mediana de {family} caiu {drop} pts "
        f"({src_median} -> {out_median})"
    )


def _fill_fixture_all_at(velocity: int) -> mido.MidiFile:
    """Trecho de virada: 8 semicolcheias em toms + caixa, todos na velocidade dada.

    Densidade 4.57 notas/tempo, kick+tom+snare atendem `fill_min_piece_variety`,
    todos os quatro criterios de `_fill_detection.is_fill_run` casam. Serve
    para o teste de regressao de virada — origem loud, saida ainda loud.
    """
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    step = 120  # semicolcheia com tpb=480

    pitches = [41, 43, 45, 47, 41, 43, 45, 38]  # 3 toms + snare
    for i, pitch in enumerate(pitches):
        track.append(
            mido.Message(
                "note_on",
                channel=9,
                note=pitch,
                velocity=velocity,
                time=0 if i == 0 else step - 60,
            )
        )
        track.append(
            mido.Message(
                "note_off",
                channel=9,
                note=pitch,
                velocity=0,
                time=60,
            )
        )
    return mid


def test_virada_com_origem_alta_saida_permanece_alta():
    """AC-3.5: fixture de virada com origem 100 sai >= 90 depois da tecnica.

    Regressao explicita do defeito historico: contratempo dentro de virada
    rebaixado para ghost/soft. Todos os oito hits estao na mesma janela de
    virada; nenhum pode sair abaixo de 90.
    """
    from tools.techniques._fill_detection import fill_windows

    src = _fill_fixture_all_at(100)
    src_notes = _drum_notes(src)
    assert len(src_notes) == 8

    from tools.techniques._helpers import iter_note_dicts

    drums = [n for n in iter_note_dicts(src.tracks[0]) if n["channel"] == 9]
    windows = fill_windows(drums, ticks_per_beat=src.ticks_per_beat)
    assert windows, (
        "a fixture precisa ser classificada como virada, senao o teste nao "
        "esta exercitando o caminho de virada"
    )

    out = apply_technique("drums.accent_hierarchy", src, seed=1)
    out_notes = _drum_notes(out)

    assert len(out_notes) == len(src_notes)
    low = [(pitch, vel, tick) for pitch, vel, tick in out_notes if vel < 90]
    assert not low, (
        f"virada com origem 100 rebaixada: {low}. A tecnica nao pode inverter "
        "a intencao da origem dentro de janela classificada como virada."
    )
