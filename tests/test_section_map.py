"""Testes de format_section_map (US-003)."""

from tools.sections import Section, format_section_map


def _mk(label: str, kind: str, sb: int, eb: int, source: str) -> Section:
    return Section(
        label=label,
        kind=kind,
        start_tick=0,
        end_tick=0,
        start_bar=sb,
        end_bar=eb,
        source=source,
    )


def test_all_markers_says_no_confirmation_needed():
    sections = [
        _mk("Intro", "intro", 0, 4, "marker"),
        _mk("Verse 1", "verse", 4, 12, "marker"),
        _mk("Chorus", "chorus", 12, 20, "marker"),
    ]
    out = format_section_map(sections)

    # Cabecalho e colunas presentes.
    assert "Secao" in out
    assert "Compasso inicial" in out
    assert "Compasso final" in out
    assert "Duracao (compassos)" in out
    assert "Origem" in out

    # Cada label aparece.
    for label in ("Intro", "Verse 1", "Chorus"):
        assert label in out

    # Compassos em 1-based: primeira secao comeca no compasso 1.
    assert "1" in out.split("\n")[2]

    # Duracoes corretas: 4, 8, 8 compassos.
    assert "4" in out
    assert "8" in out

    # Sem aviso, com mensagem de que nao precisa confirmar.
    assert "AVISO" not in out
    assert "nao ha necessidade de confirmacao" in out


def test_any_inferred_triggers_warning():
    sections = [
        _mk("Intro", "intro", 0, 4, "marker"),
        _mk("verse", "verse", 4, 12, "inferred"),
        _mk("Chorus", "chorus", 12, 20, "marker"),
    ]
    out = format_section_map(sections)

    assert "AVISO" in out
    assert "inferidas por heuristica" in out
    assert "Confirme o mapa" in out
    assert "nao ha necessidade de confirmacao" not in out


def test_all_inferred_triggers_warning():
    sections = [
        _mk("intro", "intro", 0, 4, "inferred"),
        _mk("verse", "verse", 4, 12, "inferred"),
    ]
    out = format_section_map(sections)
    assert "AVISO" in out


def test_empty_sections_returns_short_message():
    assert format_section_map([]) == "Nenhuma secao detectada."


def test_duration_uses_end_minus_start_bar():
    sections = [_mk("Bridge", "bridge", 20, 28, "marker")]
    out = format_section_map(sections)
    # 8 compassos de duracao.
    row = [line for line in out.splitlines() if "Bridge" in line][0]
    parts = row.split()
    # colunas: Bridge  21  28  8  marker
    assert parts[0] == "Bridge"
    assert parts[1] == "21"
    assert parts[2] == "28"
    assert parts[3] == "8"
    assert parts[4] == "marker"
