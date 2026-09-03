"""Testes de `bass.attack_style` — keyswitches do MODO BASS."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

STYLE_KS = {"dedo": 13, "palheta": 15, "slap": 18}
FORCAR_DOWN = 1
FORCAR_UP = 3


def _make_midi(
    velocities: list[int],
    *,
    ticks_per_beat: int = 480,
    duration: int | None = None,
    pitch: int = 40,
    channel: int = 1,
) -> mido.MidiFile:
    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    beat = ticks_per_beat
    dur = beat if duration is None else duration
    for i, vel in enumerate(velocities):
        track.append(mido.Message(
            "note_on", channel=channel, note=pitch, velocity=vel,
            time=beat if i > 0 else beat,
        ))
        track.append(mido.Message(
            "note_off", channel=channel, note=pitch, velocity=0, time=dur,
        ))
    mid.tracks.append(track)
    return mid


def _iter_notes(mid: mido.MidiFile):
    for track in mid.tracks:
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            yield tick, msg


def _structural_note_ons(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    """(tick, channel, pitch, velocity) para note_on estruturais."""

    out: list[tuple[int, int, int, int]] = []
    for tick, msg in _iter_notes(mid):
        if msg.type == "note_on" and msg.velocity > 0 and msg.note >= 20:
            out.append((tick, msg.channel, msg.note, msg.velocity))
    return out


def _keyswitch_events(mid: mido.MidiFile, pitch: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for tick, msg in _iter_notes(mid):
        if msg.type == "note_on" and msg.velocity > 0 and msg.note == pitch:
            out.append((tick, msg.channel))
    return out


def test_bass_attack_style_is_registered_as_supported():
    assert "bass.attack_style" in SUPPORTED_TECHNIQUES
    entry = get_technique("bass.attack_style")
    assert entry.canonical == "bass.attack_style"
    assert entry.level == "technique"


def test_no_style_declared_is_no_op():
    source = _make_midi([80, 90, 100, 110])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
    )

    assert _serialize(out) == original_bytes


def test_generic_tool_without_keyswitch_is_no_op():
    # Receita generic nao tem keyswitch — nao ha o que inserir.
    source = _make_midi([80, 90, 100, 110])
    original = [
        (t, m.channel, m.note, m.velocity)
        for t, m in _iter_notes(source)
        if m.type == "note_on" and m.velocity > 0
    ]

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "dedo"},
    )

    after = [
        (t, m.channel, m.note, m.velocity)
        for t, m in _iter_notes(out)
        if m.type == "note_on" and m.velocity > 0
    ]
    assert after == original


def test_fingered_style_inserts_ks13_and_does_not_change_velocity():
    source = _make_midi([80, 90, 100, 110])
    before = _structural_note_ons(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "dedo"},
    )

    ks = _keyswitch_events(out, STYLE_KS["dedo"])
    assert len(ks) == 1, f"esperava 1 keyswitch de estilo, veio {ks}"
    assert _structural_note_ons(out) == before, (
        "dedo nao deve alterar velocity das notas estruturais"
    )


def test_slap_style_inserts_ks18_and_does_not_change_velocity():
    source = _make_midi([80, 90, 100, 110])
    before = _structural_note_ons(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "slap"},
    )

    ks = _keyswitch_events(out, STYLE_KS["slap"])
    assert len(ks) == 1
    assert _structural_note_ons(out) == before


def test_picked_style_inserts_ks15_and_alternates_downstroke_upstroke():
    # Alternancia deterministica por posicao: par=down, impar=up.
    source = _make_midi([80, 80, 80, 80, 80, 80])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    style_ks = _keyswitch_events(out, STYLE_KS["palheta"])
    assert len(style_ks) == 1

    down_ks = _keyswitch_events(out, FORCAR_DOWN)
    up_ks = _keyswitch_events(out, FORCAR_UP)
    # Seis notas → 3 downstrokes (0, 2, 4) e 3 upstrokes (1, 3, 5).
    assert len(down_ks) == 3
    assert len(up_ks) == 3

    # Velocities alternam picked_downstroke_velocity vs picked_upstroke_velocity.
    structural = _structural_note_ons(out)
    downs = [v for i, (_t, _c, _p, v) in enumerate(structural) if i % 2 == 0]
    ups = [v for i, (_t, _c, _p, v) in enumerate(structural) if i % 2 == 1]
    assert len(set(downs)) == 1
    assert len(set(ups)) == 1
    # Manual: picked_downstroke [85,120] mid=102; picked_upstroke [70,100] mid=85.
    assert downs[0] > ups[0], "downstroke deveria bater mais forte que upstroke"


def test_picked_style_does_not_alter_note_position_or_pitch():
    source = _make_midi([70, 75, 80, 85, 90])
    before = [(t, m.channel, m.note) for t, m in _iter_notes(source)
              if m.type == "note_on" and m.velocity > 0]

    out = apply_technique(
        "bass.attack_style", source, seed=3, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    after = [(t, m.channel, m.note) for t, m in _iter_notes(out)
             if m.type == "note_on" and m.velocity > 0 and m.note >= 20]
    assert after == before


def test_keyswitches_do_not_collide_with_structural_notes():
    # Keyswitch fica em pitches 1, 3, 13, 15, 18 — bem abaixo do baixo (>= 28).
    source = _make_midi([80, 90, 100, 110])
    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    for _t, msg in _iter_notes(out):
        if msg.type == "note_on" and msg.velocity > 0 and msg.note >= 20:
            # nenhuma estrutural mexeu de altura
            assert msg.note == 40


def test_is_deterministic_for_same_seed():
    src_a = _make_midi([80, 90, 100, 110])
    src_b = _make_midi([80, 90, 100, 110])
    out_a = apply_technique(
        "bass.attack_style", src_a, seed=42, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    out_b = apply_technique(
        "bass.attack_style", src_b, seed=42, tool="modo_bass",
        parameters={"style": "palheta"},
    )

    assert _serialize(out_a) == _serialize(out_b)


def test_reapplying_is_idempotent():
    source = _make_midi([80, 90, 100, 110])
    once = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    once_bytes = _serialize(once)
    twice = apply_technique(
        "bass.attack_style", once, seed=1, tool="modo_bass",
        parameters={"style": "palheta"},
    )
    assert _serialize(twice) == once_bytes


def test_generic_picked_alternates_downstroke_upstroke_by_relative_delta():
    # Sem keyswitch (tool=generic): picked ainda diferencia downstroke/upstroke,
    # mas so por delta relativo direto na velocity — sem nota de keyswitch.
    source = _make_midi([80, 80, 80, 80, 80, 80])
    before = _structural_note_ons(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    # Nenhum keyswitch inserido: sem receita para eles no generic.
    for _t, msg in _iter_notes(out):
        assert msg.note not in {1, 3, 13, 15, 18}

    after = _structural_note_ons(out)
    assert len(after) == len(before)
    downs = [v for i, (_t, _c, _p, v) in enumerate(after) if i % 2 == 0]
    ups = [v for i, (_t, _c, _p, v) in enumerate(after) if i % 2 == 1]
    assert len(set(downs)) == 1
    assert len(set(ups)) == 1
    assert downs[0] > ups[0], "downstroke deveria bater mais forte que upstroke"
    # Delta e relativo: origem em 80 vira 80+half_delta / 80-half_delta.
    assert downs[0] > 80 > ups[0]


def test_generic_picked_does_not_alter_note_count_pitch_or_position():
    source = _make_midi([70, 75, 80, 85, 90])
    before = [(t, m.channel, m.note) for t, m in _iter_notes(source)
              if m.type == "note_on" and m.velocity > 0]

    out = apply_technique(
        "bass.attack_style", source, seed=3, tool="generic",
        parameters={"style": "picked"},
    )

    after = [(t, m.channel, m.note) for t, m in _iter_notes(out)
             if m.type == "note_on" and m.velocity > 0]
    assert after == before


def test_generic_picked_preserves_pressure_invariant_never_inverts_origin():
    # Nota que a origem escreveu no topo da faixa nao pode virar a mais fraca
    # da linha so por cair numa posicao de upstroke.
    source = _make_midi([40, 127, 40, 127])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    # Ordem relativa preservada: as notas originalmente mais fortes (127)
    # continuam mais fortes que as originalmente mais fracas (40).
    assert velocities[1] > velocities[0]
    assert velocities[3] > velocities[2]
    assert velocities[1] > velocities[2]
    assert velocities[3] > velocities[0]


def test_generic_picked_reapplication_is_byte_identical():
    source = _make_midi([80, 90, 100, 110, 120])
    once = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )
    once_bytes = _serialize(once)

    twice = apply_technique(
        "bass.attack_style", once, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    assert _serialize(twice) == once_bytes
    # A segunda aplicacao nao deve ter dobrado o deslocamento de velocity.
    assert _structural_note_ons(twice) == _structural_note_ons(once)


def test_generic_picked_upstroke_atraso_ms_is_rejected():
    # Achado do Codex na PR #104, segunda rodada: so corrigir a prosa da
    # receita `generic` no manual nao bastava — `upstroke_atraso_ms`
    # continuava sendo aceito e resolvido (via _range/_midrange) sem nunca
    # afetar o resultado no generic. plan.validate nao pode recusar isso
    # sozinho porque o mesmo style.bass.parameters vale para tracks com
    # tool diferente; a rejeicao acontece aqui, no despacho, quando o tool
    # de fato resolvido e generic.
    from tools.techniques.errors import TechniqueRecipeError

    source = _make_midi([80, 90, 100, 110])

    try:
        apply_technique(
            "bass.attack_style", source, seed=1, tool="generic",
            parameters={"style": "picked", "upstroke_atraso_ms": 8},
        )
    except TechniqueRecipeError as exc:
        assert "upstroke_atraso_ms" in str(exc)
        assert "generic" in str(exc)
    else:
        raise AssertionError("esperava TechniqueRecipeError")


def test_generic_picked_upstroke_atraso_ms_is_accepted_for_modo_bass():
    # O mesmo parametro continua valendo normalmente quando o tool
    # resolvido de fato usa keyswitch (onde o atraso desloca o keyswitch
    # auxiliar, nao a nota estrutural).
    source = _make_midi([80, 90, 100, 110])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="modo_bass",
        parameters={"style": "picked", "upstroke_atraso_ms": 8},
    )
    assert any(
        msg.type == "note_on" and msg.note == STYLE_KS["palheta"]
        for _t, msg in _iter_notes(out)
    )


def test_generic_picked_close_velocities_does_not_invert_dynamics():
    # Achado do Codex na PR #104, segunda rodada: velocities vizinhas
    # proximas ([90, 100], diferenca 10) inverteriam com o shift fixo
    # antigo (half_delta~8 -> [98, 92], a nota mais forte da origem saia
    # mais fraca). O shift agora e limitado pela metade da diferenca com
    # cada vizinho, entao a ordem original nunca inverte (empate e o
    # pior caso aceitavel).
    source = _make_midi([90, 100])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    assert velocities[1] >= velocities[0], (
        "nota que a origem escreveu mais forte (100) nao pode virar mais "
        f"fraca que a vizinha (90): {velocities!r}"
    )


def test_generic_picked_preserves_order_across_non_adjacent_notes():
    # Achado do Codex na PR #104, terceira rodada: limitar so pelo vizinho
    # IMEDIATO nao bastava. Origem [90, 90, 91] — nota 0 e nota 1 empatam
    # (gap=0, sem cap), nota 1 e nota 2 tem gap=1 (cap=0). Um cap por
    # vizinho deixava a nota 0 com magnitude cheia (sem vizinho de gap>0)
    # e a nota 2 travada em 0 — 90+cheio > 91+0, invertendo a nota 0
    # (originalmente igual/menor) acima da nota 2 (originalmente maior),
    # mesmo as duas nao sendo vizinhas. A magnitude agora e UNICA pra toda
    # a track, limitada pelo menor gap entre QUALQUER par de paridade
    # oposta (nao so vizinhos) — nota 0 (down) e nota 2 (down) tem a MESMA
    # paridade e por isso preservam a diferenca original entre si sempre,
    # nao importa a magnitude.
    source = _make_midi([90, 90, 91])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    assert velocities[0] <= velocities[2], (
        "nota 0 (originalmente <= nota 2) nao pode virar mais forte que "
        f"a nota 2: {velocities!r}"
    )


def test_generic_picked_honors_equal_downstroke_upstroke_parameters():
    # Achado do Codex na PR #104, terceira rodada: plano declarando
    # picked_downstroke_velocity == picked_upstroke_velocity (ambos 90,
    # valor valido nas duas faixas do manual) pede contraste ZERO entre
    # downstroke e upstroke — mas o antigo `max(1, ...)` forcava um shift
    # minimo de 1 mesmo assim, entao downstroke e upstroke nunca saiam
    # iguais mesmo com o plano pedindo explicitamente nenhuma diferenca.
    # Dois parametros aceitos e validados que nao comandam o resultado e'
    # parametro mentiroso (AGENTS.md).
    #
    # Nao assume mais byte-identico ao source: desde a sexta rodada
    # (achado "Honor the absolute picked velocity settings"), o NIVEL
    # absoluto pedido (90/90) tambem comanda o resultado via
    # deslocamento uniforme quando difere do baseline do manual — so a
    # diferenca RELATIVA entre downstroke e upstroke fica zerada aqui.
    source = _make_midi([80, 80, 80, 80])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 90,
            "picked_upstroke_velocity": 90,
        },
    )

    velocities = [v for _t, _c, _p, v in _structural_note_ons(out)]
    assert len(set(velocities)) == 1, (
        "contraste zero pedido (downstroke == upstroke) tem que sair com "
        f"toda velocity igual: {velocities!r}"
    )


def test_generic_picked_is_byte_identical_when_level_matches_manual_baseline():
    # Contraste zero E nivel absoluto igual ao baseline do manual (a
    # media dos defaults de fallback, ~94) e o unico caso em que nenhuma
    # das duas fontes de comando (contraste, nivel) pede mudanca — byte
    # identico ao source, cobrindo o mesmo bug historico do `max(1, ...)`
    # (terceira rodada) sem reintroduzir a asserção que a sexta rodada
    # invalidou.
    source = _make_midi([80, 80])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 94,
            "picked_upstroke_velocity": 94,
        },
    )

    assert _serialize(out) == _serialize(source)


def test_generic_picked_honors_inverted_stroke_contrast_sign():
    # Achado do Codex na PR #104, quarta rodada: `abs(downstroke_vel -
    # upstroke_vel)` jogava fora o SINAL do contraste pedido — o codigo
    # sempre fazia "down" mais forte, mesmo com
    # picked_downstroke_velocity=85 < picked_upstroke_velocity=100 (valido
    # nas duas faixas do manual, pedindo o contraste INVERTIDO). Com
    # velocities de origem iguais, o downstroke tinha que sair mais FRACO
    # que o upstroke, nao mais forte.
    source = _make_midi([80, 80, 80, 80])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 85,
            "picked_upstroke_velocity": 100,
        },
    )

    after = _structural_note_ons(out)
    downs = [v for i, (_t, _c, _p, v) in enumerate(after) if i % 2 == 0]
    ups = [v for i, (_t, _c, _p, v) in enumerate(after) if i % 2 == 1]
    assert downs[0] < ups[0], (
        "picked_downstroke_velocity < picked_upstroke_velocity pede "
        f"downstroke mais fraco que upstroke: downs={downs!r} ups={ups!r}"
    )


def test_generic_picked_local_conflict_does_not_disable_whole_track():
    # Achado do Codex na PR #104, quarta rodada: uma magnitude UNICA pra
    # toda a track corrigia inversao, mas um UNICO par de paridade oposta
    # com diferenca pequena (aqui, notas 0/1: 90 e 91) zerava a
    # diferenciacao da track INTEIRA, inclusive notas 2/3 (60, 60) que nao
    # tinham conflito nenhum entre si. A regressao isotonica so poola
    # (agrupa) o que precisa pra resolver o conflito local, deixando o
    # resto da track livre pra diferenciar.
    source = _make_midi([90, 91, 60, 60])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    # Nenhuma inversao em lugar nenhum: nota 0 (orig 90) <= nota 1 (orig 91).
    assert velocities[0] <= velocities[1]
    # O conflito local entre notas 0/1 nao pode zerar a diferenciacao das
    # notas 2/3 — elas nao tinham par de gap pequeno nenhum entre si.
    assert velocities[2] != velocities[3], (
        "notas sem conflito local nao deveriam perder diferenciacao so "
        f"por causa de um par distante em conflito: {velocities!r}"
    )


def test_generic_picked_preserves_one_point_stroke_contrast():
    # Achado do Codex na PR #104, quinta rodada: dividir o contraste
    # pedido numa metade UNICA (`abs(diff) // 2`) descartava o resto pra
    # diferenca IMPAR — picked_downstroke_velocity=86 e
    # picked_upstroke_velocity=85 (diferenca 1) davam half_delta=0, entao
    # um contraste explicitamente pedido (nao-zero) virava shift zero.
    source = _make_midi([80, 80])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 86,
            "picked_upstroke_velocity": 85,
        },
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    assert velocities[0] != velocities[1], (
        "diferenca de 1 ponto entre os parametros pedidos tem que "
        f"produzir contraste nao-zero: {velocities!r}"
    )
    assert velocities[0] > velocities[1], "downstroke ainda mais forte"


def test_generic_picked_enforces_order_across_individual_group_members():
    # Achado do Codex na PR #104, quinta rodada: representar cada grupo de
    # velocity original empatada so pela MEDIA dos alvos dos membros nao
    # garante a ordem entre os MEMBROS individuais. Origem [80, 80, 81, 81]
    # com os alvos default: as medias dos grupos (80 e 81) ja saiam
    # ordenadas (nenhum merge de grupo acontecia), mas a nota 0
    # (originalmente 80) saia mais forte que a nota 3 (originalmente 81) —
    # inversao entre individuos que a comparacao por media nao pegava.
    source = _make_midi([80, 80, 81, 81])

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    after = _structural_note_ons(out)
    velocities = [v for _t, _c, _p, v in after]
    assert velocities[0] <= velocities[3], (
        "nota 0 (origem 80) nao pode ficar mais forte que a nota 3 "
        f"(origem 81): {velocities!r}"
    )
    assert velocities[1] <= velocities[2], (
        "nota 1 (origem 80) nao pode ficar mais forte que a nota 2 "
        f"(origem 81): {velocities!r}"
    )


def test_generic_picked_honors_absolute_velocity_level():
    # Achado do Codex na PR #104, sexta rodada: (85, 70) e (100, 85) tem a
    # MESMA diferenca (15), entao so olhando pra diferenca os dois
    # produziriam alvo identico — o nivel absoluto pedido (dois parametros
    # aceitos e validados independentemente) nunca comandaria nada. O
    # nivel mais alto tem que produzir velocity final mais alta.
    source = _make_midi([80, 80])

    low = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 85,
            "picked_upstroke_velocity": 70,
        },
    )
    high = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 100,
            "picked_upstroke_velocity": 85,
        },
    )

    low_velocities = [v for _t, _c, _p, v in _structural_note_ons(low)]
    high_velocities = [v for _t, _c, _p, v in _structural_note_ons(high)]
    assert low_velocities != high_velocities, (
        "mesma diferenca (15) com nivel absoluto diferente (85/70 vs "
        f"100/85) nao pode produzir o mesmo resultado: {low_velocities!r} "
        f"== {high_velocities!r}"
    )
    assert high_velocities[0] > low_velocities[0]
    assert high_velocities[1] > low_velocities[1]


def test_generic_picked_reapplication_with_different_parameters_is_not_skipped():
    # Achado do Codex na PR #104, sexta rodada: a assinatura de
    # idempotencia so incluia `style`, entao reaplicar com
    # picked_downstroke_velocity/picked_upstroke_velocity DIFERENTES numa
    # track ja marcada pulava a track inteira so porque `style` batia — os
    # parametros novos, aceitos e validados, nunca chegavam a comandar o
    # resultado.
    source = _make_midi([80, 80, 80, 80])

    once = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 90,
            "picked_upstroke_velocity": 70,
        },
    )
    once_velocities = [v for _t, _c, _p, v in _structural_note_ons(once)]

    twice = apply_technique(
        "bass.attack_style", once, seed=1, tool="generic",
        parameters={
            "style": "picked",
            "picked_downstroke_velocity": 70,
            "picked_upstroke_velocity": 90,
        },
    )
    twice_velocities = [v for _t, _c, _p, v in _structural_note_ons(twice)]

    assert twice_velocities != once_velocities, (
        "reaplicar com contraste INVERTIDO nao pode ser tratado como "
        f"'ja aplicado': {once_velocities!r} == {twice_velocities!r}"
    )


def test_generic_picked_saturated_velocity_is_not_reported_as_applied():
    # Origem ja saturada no clamp [1, 127] (127 alternando com 1): o shift
    # relativo nao produz nenhuma mudanca audivel de velocity. Achado do
    # Codex na PR #104 — gravar o marcador de idempotencia mesmo sem
    # nenhuma nota mudar fazia `_midi_bytes` enxergar bytes diferentes (o
    # meta text em si) e o pipeline reportar a tecnica como aplicada ao
    # usuario sem nada ter de fato mudado.
    source = _make_midi([127, 1, 127, 1])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "picked"},
    )

    assert _serialize(out) == original_bytes


def test_generic_fingered_style_remains_no_op():
    source = _make_midi([80, 90, 100, 110])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "fingered"},
    )

    assert _serialize(out) == original_bytes


def test_generic_slap_style_remains_no_op():
    source = _make_midi([80, 90, 100, 110])
    original_bytes = _serialize(source)

    out = apply_technique(
        "bass.attack_style", source, seed=1, tool="generic",
        parameters={"style": "slap"},
    )

    assert _serialize(out) == original_bytes


def _serialize(mid: mido.MidiFile) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    mid.save(file=buffer)
    return buffer.getvalue()
