"""Issue #77: relatorio de proveniencia, da influencia ao resultado MIDI.

O que estes testes provam, cenario real e ponta a ponta (render de verdade,
registro de tecnicas de verdade, validadores de verdade):

- a cadeia `source -> finding -> mapping -> technique -> track/section ->
  metric` fecha quando todos os artefatos existem;
- cada elo AUSENTE e declarado em `missing_links` em vez de preenchido por
  suposicao (o criterio de aceite pede os dois lados);
- "aplicada com sucesso" so aparece com evidencia objetiva de validador —
  sem cobertura, o status e `aplicada_nao_verificavel`;
- o relatorio e deterministico byte a byte;
- o relatorio nao copia texto extenso da fonte nem conteudo musical.
"""

from __future__ import annotations

import json
from pathlib import Path

import mido
import pytest

from tests.test_end_to_end_drums_edit import (
    _build_flat_metal_drums_source,
    _plan_with_full_drum_pipeline,
)
from tools import contract as _contract  # noqa: F401 - popula o registry
from tools import report as report_mod
from tools.brief_ref import brief_sha256
from tools.influence import InfluenceFinding, InfluenceProfile, InfluenceSource
from tools.influence_compile import compile_influence
from tools.plan import ArrangementPlan, BriefRef, Element, StyleTechnique
from tools.registry import call
from tools.render import render
from tools.report import ValidatorRun, build_report, parse_stamp, read_stamps

GHOST = "drums.ghost_notes"


def _neutral_corpus(tmp_path: Path) -> list[str]:
    """Corpus de referencia REAL, porem sem parentesco com o material do
    teste: uma escala de piano.

    Serve para fazer o `anticopia` EXECUTAR sem que ele acuse nada. Desde a
    issue #124 ele julga so as tracks de `plan.elements[]` — as mesmas que o
    `render` julga —, entao track de `plan.edits`/origem nao ganha cobertura
    de validador nenhum e sai `sem_cobertura`, que e a resposta honesta. Com
    o corpus real de bateria (`tests/fixtures/corpus_drums`), a bateria
    chapada da fixture casa de verdade e o veredito de um ELEMENTO de
    bateria viraria `com_erro` — cenario do teste de erro, nao do caminho
    feliz.
    """
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=90.0)
    piano = pretty_midi.Instrument(program=0, name="Ref Piano")
    for i, pitch in enumerate((60, 62, 64, 65, 67, 69, 71, 72)):
        piano.notes.append(pretty_midi.Note(
            velocity=70, pitch=pitch, start=i * 0.5, end=i * 0.5 + 0.45,
        ))
    pm.instruments.append(piano)
    dest = tmp_path / "corpus-neutro.mid"
    pm.write(str(dest))
    return [str(dest)]


def _influence_profile() -> InfluenceProfile:
    """Perfil de pesquisa que o dicionario de mapeamento reconhece.

    `articulation` + "ghost notes" e exatamente a regra
    `drums_articulation_ghost_notes` de `influence_compile.MAPPING_RULES`.
    """
    return InfluenceProfile(
        project_ref="musica-de-teste",
        sources=[InfluenceSource(
            id="src-1",
            url="https://example.test/entrevista-baterista",
            title="Entrevista sobre pegada de bateria",
            retrieved_at="2026-08-26",
        )],
        findings=[InfluenceFinding(
            id="f-ghost",
            family="drums",
            dimension="articulation",
            semantic_value="ghost notes constantes na caixa entre os backbeats",
            intensity="medium",
            confidence="high",
            source_ids=("src-1",),
            summary="O baterista mantem pressao baixa entre os golpes fortes.",
        )],
    )


def _brief_file(tmp_path: Path, *, authorized: list[str], suggested: list[str]) -> Path:
    brief = {
        "style": {
            "drums": {
                "suggested_techniques": [{"name": n} for n in suggested],
                "authorized_techniques": list(authorized),
            },
        },
    }
    path = tmp_path / "arrangement-brief.json"
    path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return path


def _rendered_project(tmp_path: Path) -> tuple[Path, Path, Path, ArrangementPlan]:
    """Render real: bateria chapada da origem + `drums.ghost_notes` autorizada."""
    src = _build_flat_metal_drums_source(tmp_path)
    plan = _plan_with_full_drum_pipeline(src)
    brief_path = _brief_file(
        tmp_path, authorized=[GHOST], suggested=[GHOST, "drums.flam"],
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    plan.style["drums"].techniques = [
        StyleTechnique(name=GHOST, evidence_refs=["f-ghost"]),
    ]
    out = tmp_path / "out.mid"
    render(plan, out)
    return src, out, brief_path, plan


def _rendered_project_com_elemento_de_bateria(
    tmp_path: Path,
) -> tuple[Path, Path, Path, ArrangementPlan]:
    """Render real em que `drums.ghost_notes` cai num ELEMENTO gerado.

    O caminho feliz precisa de um alvo que algum validador POR TRACK
    realmente percorra. Desde a issue #124 esse alvo e sempre track de
    elemento: o `anticopia` — como o `render` sempre fez — nao julga track
    de origem, declarada ou nao em `plan.edits`, porque as notas dela sao do
    usuario. Sem `plan.edits`, a bateria da origem sai byte-identica.
    """
    src = _build_flat_metal_drums_source(tmp_path)
    plan = _plan_with_full_drum_pipeline(src)
    plan.edits = []
    plan.elements.append(Element(
        id="drums_main",
        role="drums",
        sections=["MAIN"],
        register=[35, 59],
        layers=1,
        sync_role="exact_anchor",
        articulation="staccato",
        harmony="percussion",
        dynamics={"shape": "hold"},
        instrument={
            "plugin": "Superior 3", "preset": "Metal Kit", "verified": True,
        },
        rationale="Levada nova de bateria para sustentar o refrao.",
    ))
    brief_path = _brief_file(
        tmp_path, authorized=[GHOST], suggested=[GHOST, "drums.flam"],
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    plan.style["drums"].techniques = [
        StyleTechnique(name=GHOST, evidence_refs=["f-ghost"]),
    ]
    out = tmp_path / "out-elemento.mid"
    render(plan, out)
    return src, out, brief_path, plan


def _build_via_tool(
    src: Path,
    out: Path,
    plan: ArrangementPlan,
    *,
    brief_path: Path | None,
    influence: InfluenceProfile | None,
    out_path: Path | None = None,
    reference_corpus: list[str] | None = None,
) -> dict:
    from tools.influence import to_dict as influence_to_dict
    from tools.plan import to_dict as plan_to_dict

    payload: dict = {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": plan_to_dict(plan),
    }
    if reference_corpus is not None:
        payload["reference_corpus"] = list(reference_corpus)
    if brief_path is not None:
        payload["brief_path"] = str(brief_path)
    if influence is not None:
        payload["influence"] = influence_to_dict(influence)
    if out_path is not None:
        payload["out_path"] = str(out_path)
    envelope = call("report.build", payload)
    assert envelope["ok"], envelope
    return envelope["data"]["report"]


def _link(report: dict, technique: str) -> dict:
    matches = [c for c in report["chain"] if c["technique"] == technique]
    assert matches, f"tecnica {technique} ausente da cadeia"
    return matches[0]


# --- cadeia completa --------------------------------------------------------


def test_cadeia_completa_liga_fonte_achado_mapeamento_tecnica_track_metrica(tmp_path):
    """O caminho feliz inteiro, com todos os artefatos presentes.

    A track alvo e um ELEMENTO de bateria — o unico tipo de alvo que algum
    validador POR TRACK percorre. Track de `plan.edits`/origem nunca chega a
    `aplicada_verificada`: harmonia, placement e artificialidade a pulam por
    `elements_by_id.get(track.element_id) is None`, persona/colisao/
    conformidade sao de escopo global, e o `anticopia` deixou de julga-la na
    issue #124 (as notas sao do usuario, nao do arranjador). O status
    honesto dela e `aplicada_nao_verificavel` — ver
    `test_track_de_edit_sem_validador_por_track_nao_pode_sair_verificada`.
    """
    src, out, brief_path, plan = _rendered_project_com_elemento_de_bateria(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
        reference_corpus=_neutral_corpus(tmp_path),
    )

    link = _link(report, GHOST)

    # elo 1-2: fonte e achado
    assert [s["id"] for s in link["sources"]] == ["src-1"]
    assert link["sources"][0]["url"] == "https://example.test/entrevista-baterista"
    assert [f["id"] for f in link["findings"]] == ["f-ghost"]
    assert link["findings"][0]["dimension"] == "articulation"

    # elo 3: mapeamento, com a versao do dicionario
    assert link["mapping"] is not None
    assert link["mapping"]["mapping_version"] == compile_influence(
        _influence_profile(),
    ).mapping_version

    # elo 4: tecnica sugerida E autorizada pelo usuario
    assert link["suggested"] and link["authorized"]
    assert link["plan_declaration"]["evidence_refs"] == ["f-ghost"]

    # elo 5: track carimbada pelo render
    targets = link["targets"]
    assert [t["track_name"] for t in targets] == [
        "drums_main - Superior 3 / Metal Kit *",
    ]
    assert targets[0]["kind"] == "element"

    # elo 6: metrica medida no MIDI final + veredito de validador
    metrics = targets[0]["metrics"]
    assert metrics["note_on_count"] > 0
    assert metrics["velocity_min"] is not None
    evidence = targets[0]["validator_evidence"]
    assert evidence["veredito"] == "limpo"
    # Os validadores que REALMENTE percorreram esta track. Harmonia nao
    # aparece: ela pula `harmony="percussion"`, e a cobertura declarada em
    # `_report_validator_runs` diz isso.
    assert evidence["validadores"] == ["anticopia", "artificialidade", "placement"]
    assert "harmonia" not in evidence["validadores"]
    assert evidence["erros_globais"] == []

    assert link["status"] == "aplicada_verificada"
    assert link["missing_links"] == []

    # as cinco listas que a issue pede
    assert GHOST in report["techniques"]["aplicadas"]
    assert GHOST in report["techniques"]["autorizadas"]
    assert GHOST in report["techniques"]["sugeridas"]

    # versoes e hashes
    assert report["versions"]["influence_mapping"]
    assert report["versions"]["influence_profile"] == 1
    assert report["hashes"]["brief_sha256"] == brief_sha256(brief_path)
    assert report["hashes"]["source_midi_sha256"] == plan.source_midi.sha256
    assert len(report["hashes"]["plan_sha256"]) == 64
    assert len(report["hashes"]["rendered_sha256"]) == 64

    # resumo legivel por musico
    assert "aplicadas e verificadas por validador" in report["summary_text"]
    assert GHOST in report["summary_text"]


def test_tecnica_sugerida_e_nao_autorizada_nao_aparece_como_aplicada(tmp_path):
    """`drums.flam` foi sugerida no brief e o usuario NAO marcou: o relatorio
    tem que dizer isso, nao omitir a sugestao nem promove-la."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
    )
    link = _link(report, "drums.flam")
    assert link["status"] == "sugerida_nao_autorizada"
    assert link["suggested"] is True
    assert link["authorized"] is False
    assert link["targets"] == []
    # sugestao que o usuario nao marcou nao rendeu track — e isso NAO conta
    # como elo `track` quebrado: o status ja explica a ausencia. O que sobra
    # e a lacuna real de proveniencia (a sugestao do brief nao aponta achado
    # nem fonte).
    assert set(link["missing_links"]) == {"finding", "source"}
    assert "drums.flam" in report["techniques"]["ignoradas"]
    assert "drums.flam" not in report["techniques"]["aplicadas"]


# --- elo ausente ------------------------------------------------------------


def test_elo_ausente_e_declarado_quando_nao_ha_pesquisa_nem_brief(tmp_path):
    """Sem perfil de influencia e sem brief, a tecnica aplicada continua
    rastreada ate a track — mas os elos que faltam saem DECLARADOS, nunca
    preenchidos por suposicao."""
    src, out, _brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(src, out, plan, brief_path=None, influence=None)

    link = _link(report, GHOST)
    assert link["sources"] == []
    assert link["findings"] == []
    assert link["mapping"] is None
    assert set(link["missing_links"]) >= {"source", "finding", "mapping"}

    codes = {m["code"] for m in report["missing_links"]}
    assert {"source", "finding", "technique"} <= codes
    assert report["versions"]["influence_profile"] is None
    assert "elos ausentes na cadeia" in report["summary_text"]

    # a track e a metrica continuam existindo — o que existe nao vira lacuna
    assert [t["track_name"] for t in link["targets"]] == ["Drums"]


def test_achado_citado_que_nao_existe_no_perfil_vira_elo_ausente(tmp_path):
    """`evidence_refs` apontando achado inexistente e elo quebrado: o
    relatorio nomeia o id orfao em vez de silenciar."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    plan.style["drums"].techniques = [
        StyleTechnique(name=GHOST, evidence_refs=["f-que-nao-existe"]),
    ]
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
    )
    link = _link(report, GHOST)
    assert "finding" in link["missing_links"]
    assert any("f-que-nao-existe" in nota for nota in link["notes"])


def test_tecnica_autorizada_mas_nao_aplicada_nao_e_reportada_como_aplicada(tmp_path):
    """Autorizacao nao e aplicacao: sem carimbo em track nenhuma, o status e
    `autorizada_nao_aplicada` e o elo `track` sai como ausente."""
    src, out, _bp, plan = _rendered_project(tmp_path)
    brief_path = _brief_file(
        tmp_path, authorized=[GHOST, "drums.buzz_roll"], suggested=[],
    )
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=None,
    )
    link = _link(report, "drums.buzz_roll")
    assert link["status"] == "autorizada_nao_aplicada"
    assert set(link["missing_links"]) >= {"track", "metric"}
    assert "drums.buzz_roll" in report["techniques"]["ignoradas"]


def test_tecnica_nao_suportada_pelo_motor_e_marcada_como_tal(tmp_path):
    """`bass.slide` esta no manual mas fora de `SUPPORTED_TECHNIQUES`. O
    relatorio nao pode fingir que ela existe no motor."""
    src, out, _bp, plan = _rendered_project(tmp_path)
    brief_path = tmp_path / "brief-bass.json"
    brief_path.write_text(json.dumps({
        "style": {
            "drums": {"authorized_techniques": [GHOST]},
            "bass": {"authorized_techniques": ["bass.slide"]},
        },
    }), encoding="utf-8")
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    report = _build_via_tool(src, out, plan, brief_path=brief_path, influence=None)
    link = _link(report, "bass.slide")
    assert link["status"] == "nao_suportada"
    assert link["supported"] is False
    assert "bass.slide" in report["techniques"]["nao_suportadas"]


# --- a regra central: sem validador, nada e "verificado" --------------------


def test_sem_cobertura_de_validador_o_status_e_nao_verificavel(tmp_path):
    """A regra de negocio da issue #77, isolada: a MESMA track carimbada, a
    mesma metrica, muda de `aplicada_verificada` para
    `aplicada_nao_verificavel` so por faltar validador que a tenha coberto.
    """
    src, out, brief_path, plan = _rendered_project(tmp_path)
    profile = _influence_profile()
    compiled = compile_influence(profile)
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    mid = mido.MidiFile(str(out))

    sem_cobertura = build_report(
        plan=plan, rendered_mid=mid, rendered_midi_path=out,
        influence=profile, compile_result=compiled, brief=brief,
        brief_path=brief_path,
        validators=[ValidatorRun(name="harmonia", executed=True,
                                 covered_tracks=("Outra Track",))],
    )
    assert _link(sem_cobertura, GHOST)["status"] == "aplicada_nao_verificavel"
    assert _link(sem_cobertura, GHOST)["targets"][0][
        "validator_evidence"]["veredito"] == "sem_cobertura"
    assert "NAO verificaveis" in sem_cobertura["summary_text"]

    com_cobertura = build_report(
        plan=plan, rendered_mid=mid, rendered_midi_path=out,
        influence=profile, compile_result=compiled, brief=brief,
        brief_path=brief_path,
        validators=[ValidatorRun(name="harmonia", executed=True,
                                 covered_tracks=("Drums",))],
    )
    assert _link(com_cobertura, GHOST)["status"] == "aplicada_verificada"


def test_validador_nao_executado_nunca_conta_como_aprovacao(tmp_path):
    """Validador que nao rodou aparece como nao executado, com motivo, e o
    relatorio registra a falta de metrica."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
    )
    anticopia = report["validators"]["anticopia"]
    assert anticopia["executado"] is False
    assert "corpus" in (anticopia["motivo"] or "")
    assert any(
        m["code"] == "metric" and "anticopia" in m["path"]
        for m in report["missing_links"]
    )
    assert "validadores que NAO rodaram" in report["summary_text"]


def test_todos_os_sete_validadores_aparecem_no_relatorio(tmp_path):
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(src, out, plan, brief_path=brief_path, influence=None)
    assert set(report_mod.VALIDATOR_NAMES) <= set(report["validators"])


# --- determinismo -----------------------------------------------------------


def test_relatorio_e_deterministico_byte_a_byte(tmp_path):
    src, out, brief_path, plan = _rendered_project(tmp_path)
    profile = _influence_profile()

    corpus = _neutral_corpus(tmp_path)
    primeiro = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=profile,
        out_path=tmp_path / "arrangement-report-1.json",
        reference_corpus=corpus,
    )
    segundo = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=profile,
        out_path=tmp_path / "arrangement-report-2.json",
        reference_corpus=corpus,
    )

    assert report_mod.report_sha256(primeiro) == report_mod.report_sha256(segundo)
    assert (tmp_path / "arrangement-report-1.json").read_bytes() == (
        tmp_path / "arrangement-report-2.json"
    ).read_bytes()


# --- anticopia --------------------------------------------------------------


def test_relatorio_nao_copia_texto_extenso_da_fonte(tmp_path):
    """Prosa longa do achado nunca entra no relatorio — so o registro de que
    existe e o tamanho."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    prosa = (
        "O baterista explica em detalhe, ao longo de varios paragrafos, como "
        "constroi a pressao do braco esquerdo entre os golpes fortes, e essa "
        "explicacao inteira nao pode ser reproduzida no relatorio."
    )
    semantic_longo = "ghost notes " + ("muito constantes " * 12)
    profile = InfluenceProfile(
        sources=[InfluenceSource(
            id="src-1", url="https://example.test/x", title="Entrevista",
            retrieved_at="2026-08-26",
        )],
        findings=[InfluenceFinding(
            id="f-ghost", family="drums", dimension="articulation",
            semantic_value=semantic_longo, intensity="medium",
            confidence="high", source_ids=("src-1",), summary=prosa,
        )],
    )
    report = _build_via_tool(src, out, plan, brief_path=brief_path, influence=profile)
    serializado = json.dumps(report, ensure_ascii=False)

    assert prosa not in serializado
    assert "paragrafos" not in serializado
    assert semantic_longo not in serializado

    finding = _link(report, GHOST)["findings"][0]
    assert finding["semantic_value"] is None
    assert finding["semantic_value_omitido"] == "OMITIDO_LIMITE_CITACAO"
    assert finding["summary_present"] is True
    assert finding["summary_chars"] == len(prosa)


def test_relatorio_nao_carrega_conteudo_musical_da_referencia(tmp_path):
    """Segunda barreira: mesmo que um perfil malformado chegue ate aqui sem
    passar por `influence.validate`, a sequencia de notas nao vaza para o
    relatorio."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    profile = InfluenceProfile(
        sources=[InfluenceSource(
            id="src-1", url="https://example.test/x", title="Entrevista",
            retrieved_at="2026-08-26",
        )],
        findings=[InfluenceFinding(
            id="f-ghost", family="drums", dimension="articulation",
            semantic_value="ghost notes tocando C4 depois D4 depois E4",
            intensity="medium", confidence="high", source_ids=("src-1",),
        )],
    )
    compiled = compile_influence(profile)
    report = build_report(
        plan=plan, rendered_midi_path=out, influence=profile,
        compile_result=compiled, brief_path=brief_path,
    )
    finding = _link(report, GHOST)["findings"][0]
    assert finding["semantic_value"] is None
    assert finding["semantic_value_omitido"] == "OMITIDO_CONTEUDO_MUSICAL"
    assert "C4" not in json.dumps(report, ensure_ascii=False)


# --- leitura do carimbo -----------------------------------------------------


def test_leitura_do_carimbo_reflete_o_que_o_render_gravou(tmp_path):
    """A prova de aplicacao vem do carimbo escrito por `tools/render.py` —
    este teste amarra o leitor ao escritor."""
    _src, out, _bp, _plan = _rendered_project(tmp_path)
    stamps = read_stamps(mido.MidiFile(str(out)))
    por_nome = {s.track_name: s for s in stamps}
    assert GHOST in por_nome["Drums"].techniques
    # track de origem NAO declarada em plan.edits nao recebe carimbo
    assert "Piano" not in por_nome


def test_relatorio_sem_midi_renderizado_declara_falta_de_prova(tmp_path):
    """Sem MIDI de saida nao ha carimbo nem metrica — e o relatorio diz
    exatamente isso, em vez de assumir que a tecnica rodou."""
    _src, _out, brief_path, plan = _rendered_project(tmp_path)
    report = build_report(plan=plan, brief_path=brief_path)
    assert any(
        m["code"] == "track" and m["path"] == "rendered_midi_path"
        for m in report["missing_links"]
    )
    assert _link(report, GHOST)["targets"] == []


@pytest.mark.parametrize("status", report_mod.TECHNIQUE_STATUSES)
def test_todo_status_tem_rotulo_no_resumo(status):
    """Nenhum status pode cair no resumo sem traducao — `format_summary`
    levantaria KeyError."""
    fake = {
        "chain": [{"technique": "x.y", "status": status}],
        "validators": {},
        "missing_links": [],
    }
    assert report_mod.format_summary(fake)


# --- regressoes da revisao adversarial do PR #119 ---------------------------
#
# Cada teste abaixo reproduz um defeito REAL encontrado na revisao e trava a
# correcao. A regra de negocio em jogo e sempre a mesma: "aplicado com
# sucesso" so aparece com evidencia objetiva; caso contrario, nao verificavel.


def test_track_de_edit_sem_validador_por_track_nao_pode_sair_verificada(tmp_path):
    """Achado 1: a fachada dava a TODOS os validadores a mesma lista de
    tracks renderizadas. `Drums` e track de `plan.edits` (`element_id`
    `source:Drums`), e harmonia/placement/artificialidade a pulam por
    construcao, enquanto persona/colisao/conformidade nem sao por track.
    Sem corpus de anticopia ninguem olhou para ela — e o relatorio dizia
    `limpo` e `aplicada_verificada`."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
    )
    link = _link(report, GHOST)
    evidence = link["targets"][0]["validator_evidence"]

    assert link["targets"][0]["kind"] == "edit"
    assert evidence["validadores"] == []
    assert evidence["veredito"] == "sem_cobertura"
    assert link["status"] == "aplicada_nao_verificavel"

    # nenhum validador por track pode reivindicar a track de edicao...
    for name in ("harmonia", "placement", "artificialidade"):
        assert "Drums" not in report["validators"][name]["tracks_cobertas"]
    # ...e validador de escopo global nao cobre track nenhuma.
    for name in ("persona", "colisao", "conformidade"):
        assert report["validators"][name]["escopo"] == "global"
        assert report["validators"][name]["tracks_cobertas"] == []


def test_validador_global_nao_pode_declarar_cobertura_de_track():
    """Achado 1, barreira estrutural: a combinacao que fabricava cobertura
    (`scope="global"` com `covered_tracks` cheio) nao e representavel."""
    with pytest.raises(ValueError, match="escopo global"):
        ValidatorRun(
            name="persona", executed=True, scope="global",
            covered_tracks=("Drums",),
        )


def test_erro_de_validador_sem_campo_track_rebaixa_o_status(tmp_path):
    """Achado 2: `RequisitoVerdict`, `PersonaIssue` e `CollisionWarning` nao
    tem campo `track`. O relatorio so acumulava erro cujo `track` casasse com
    o nome do alvo, entao um requisito NAO ATENDIDO convivia com
    `aplicada_verificada` e com 'aplicadas e verificadas por validador' no
    resumo."""
    src, out, _bp, plan = _rendered_project(tmp_path)
    brief = {
        "style": {"drums": {"authorized_techniques": [GHOST]}},
        "requisitos": [{
            "id": "R1", "familia": "bass", "tipo": "tecnica",
            "alvo": "ghost notes", "descricao": "por ghost notes no baixo",
        }],
    }
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))

    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=None,
        reference_corpus=_neutral_corpus(tmp_path),
    )
    conformidade = report["validators"]["conformidade"]
    assert conformidade["executado"] is True
    assert conformidade["erros_globais"] == 1

    link = _link(report, GHOST)
    evidence = link["targets"][0]["validator_evidence"]
    assert [e["id"] for e in evidence["erros_globais"]] == ["R1"]
    assert evidence["veredito"] == "com_erro"
    assert link["status"] == "aplicada_com_erro"
    assert "aplicadas e verificadas por validador" not in report["summary_text"]


def test_tecnica_carimbada_sem_autorizacao_no_brief_vira_elo_quebrado(tmp_path):
    """Achado 3: `missing_links` tinha a mensagem do elo `technique`, mas
    nenhum ponto do modulo a emitia — codigo morto. Brief presente
    autorizando `[]` + tecnica carimbada saia `aplicada_verificada`, contra
    a regra do AGENTS.md de que ausencia de autorizacao significa NENHUMA
    tecnica."""
    _src, out, _bp, plan = _rendered_project(tmp_path)
    brief = {"style": {"drums": {
        "suggested_techniques": [{"name": GHOST}],
        "authorized_techniques": [],
    }}}
    report = build_report(
        plan=plan, rendered_mid=mido.MidiFile(str(out)), rendered_midi_path=out,
        brief=brief,
        validators=[ValidatorRun(
            name="harmonia", executed=True, covered_tracks=("Drums",),
        )],
    )
    link = _link(report, GHOST)
    assert link["authorized"] is False
    assert link["targets"], "a tecnica esta carimbada na track"
    assert "technique" in link["missing_links"]
    assert link["status"] == "aplicada_sem_autorizacao"
    assert GHOST not in report["techniques"]["aplicadas_verificadas"]
    assert "SEM autorizacao rastreavel" in report["summary_text"]


def _source_with_two_drum_tracks(tmp_path: Path) -> Path:
    """Mesma bateria da fixture, porem repartida em DUAS tracks fisicas com
    o MESMO nome `Drums` — o caso que o AGENTS.md trata como unidade."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=140.0)
    pm.time_signature_changes.append(pretty_midi.TimeSignature(4, 4, 0))
    piano = pretty_midi.Instrument(program=0, name="Piano")
    kit = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    hats = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    bar_len = 60.0 / 140.0 * 4
    beat_len = bar_len / 4
    sixteenth = beat_len / 4
    for bar in range(16):
        start = bar * bar_len
        for pc in (60, 64, 67):
            piano.notes.append(pretty_midi.Note(
                velocity=80, pitch=pc, start=start, end=start + bar_len,
            ))
        for beat in range(4):
            beat_start = start + beat * beat_len
            kit.notes.append(pretty_midi.Note(
                velocity=127, pitch=36 if beat in (0, 2) else 38,
                start=beat_start, end=beat_start + 0.08,
            ))
            for s in range(4):
                hh = beat_start + s * sixteenth
                hats.notes.append(pretty_midi.Note(
                    velocity=127, pitch=42, start=hh, end=hh + 0.04,
                ))
    pm.instruments.extend([piano, kit, hats])
    dest = tmp_path / "dup_drums.mid"
    pm.write(str(dest))
    return dest


def test_metrica_nao_migra_entre_tracks_com_nome_repetido_de_daw(tmp_path):
    """Achado 4: a metrica era indexada por NOME de track. Com duas tracks
    fisicas `Drums`, a medicao da ultima sobrescrevia a da primeira e o
    relatorio publicava o mesmo numero para as duas."""
    import hashlib

    from tools.plan import SourceMidi
    from tools.report import track_metrics

    src = _source_with_two_drum_tracks(tmp_path)
    plan = _plan_with_full_drum_pipeline(src)
    plan.source_midi = SourceMidi(
        path=str(src), sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
    )
    brief_path = _brief_file(tmp_path, authorized=[GHOST], suggested=[])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))
    out = tmp_path / "out_dup.mid"
    render(plan, out)

    mid = mido.MidiFile(str(out))
    stamps = [s for s in read_stamps(mid) if s.track_name == "Drums"]
    assert len(stamps) == 2, "as duas tracks fisicas tem que sair carimbadas"
    medido = {
        s.track_index: track_metrics(mid.tracks[s.track_index])["note_on_count"]
        for s in stamps
    }
    assert len(set(medido.values())) == 2, (
        "as duas tracks tem contagens diferentes — sem isso o teste nao "
        "distingue metrica correta de metrica sobrescrita"
    )

    report = build_report(
        plan=plan, rendered_mid=mid, rendered_midi_path=out,
        brief=json.loads(brief_path.read_text(encoding="utf-8")),
        brief_path=brief_path,
    )
    targets = _link(report, GHOST)["targets"]
    publicado = {
        t["track_index"]: t["metrics"]["note_on_count"]
        for t in targets if t["track_name"] == "Drums"
    }
    assert publicado == medido
    # e o relatorio diz que a evidencia de validador chega por nome, para a
    # unidade inteira — em vez de fingir precisao fisica que nao tem.
    assert all(
        t["validator_evidence"]["nome_ambiguo"] is True
        for t in targets if t["track_name"] == "Drums"
    )


def test_carimbo_de_rodada_anterior_nao_vira_prova_desta_execucao(tmp_path):
    """Achado 5: MIDI ja arranjado reaproveitado como origem carrega o
    carimbo velho. Um plano SEM tecnica nenhuma lia esse carimbo e publicava
    `aplicada_verificada` para uma tecnica que esta execucao nunca aplicou."""
    src, out, _bp, plan = _rendered_project(tmp_path)

    # (a) sem plano nem brief que declarem a tecnica: nada de verificada
    orfao = _plan_with_full_drum_pipeline(src)
    orfao.style["drums"].techniques = []
    orfao.edits = []
    report = build_report(
        plan=orfao, rendered_mid=mido.MidiFile(str(out)), rendered_midi_path=out,
        validators=[ValidatorRun(
            name="harmonia", executed=True, covered_tracks=("Drums",),
        )],
    )
    link = _link(report, GHOST)
    assert link["stale_stamp"] is True
    assert link["targets"][0]["kind"] == "desconhecido"
    assert link["targets"][0]["declarada_neste_plano"] is False
    assert link["status"] != "aplicada_verificada"
    assert {"technique", "track"} <= set(link["missing_links"])

    # (b) autorizada no brief, mas ausente do plano desta execucao: o
    # carimbo continua sem provar aplicacao AGORA
    autorizado = _plan_with_full_drum_pipeline(src)
    autorizado.style["drums"].techniques = []
    brief = {"style": {"drums": {"authorized_techniques": [GHOST]}}}
    report_b = build_report(
        plan=autorizado, rendered_mid=mido.MidiFile(str(out)),
        rendered_midi_path=out, brief=brief,
        validators=[ValidatorRun(
            name="harmonia", executed=True, covered_tracks=("Drums",),
        )],
    )
    link_b = _link(report_b, GHOST)
    assert link_b["authorized"] is True
    assert link_b["stale_stamp"] is True
    assert link_b["status"] == "aplicada_nao_verificavel"
    assert "technique" in link_b["missing_links"]


def test_url_da_fonte_passa_pela_mesma_barreira_anticopia_do_titulo(tmp_path):
    """Achado 6: `title` passava por `_quote`, `url` era copiada verbatim.
    `influence.validate` so exige url nao vazia, entao tablatura e sequencia
    de notas em query string aterrissavam no relatorio."""
    from tools import influence as influence_mod

    src, out, brief_path, plan = _rendered_project(tmp_path)
    url = (
        "https://tabs.test/riff?notes=C4%20D4%20E4%20G4&tab=e|-0-3-5-|B|-1-3-|"
        + "x" * 400
    )
    profile = InfluenceProfile(
        sources=[InfluenceSource(
            id="src-1", url=url, title="Entrevista", retrieved_at="2026-08-26",
        )],
        findings=[InfluenceFinding(
            id="f-ghost", family="drums", dimension="articulation",
            semantic_value="ghost notes", intensity="medium",
            confidence="high", source_ids=("src-1",),
        )],
    )
    # a barreira do perfil deixa passar: por isso a do relatorio precisa
    # existir de verdade, e nao confiar no validador de cima.
    influence_mod.validate(profile)

    report = build_report(
        plan=plan, rendered_mid=mido.MidiFile(str(out)), rendered_midi_path=out,
        influence=profile, compile_result=compile_influence(profile),
        brief_path=brief_path,
    )
    fonte = _link(report, GHOST)["sources"][0]
    assert fonte["url"] is None
    assert fonte["url_omitido"] == "OMITIDO_LIMITE_CITACAO"
    assert url not in json.dumps(report, ensure_ascii=False)
    assert "-0-3-5-" not in json.dumps(report, ensure_ascii=False)


def test_url_curta_com_conteudo_musical_tambem_e_omitida(tmp_path):
    """Mesma barreira, pelo outro lado: url curta o bastante para o limite
    de citacao, mas carregando sequencia de notas."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    profile = InfluenceProfile(
        sources=[InfluenceSource(
            id="src-1", url="https://tabs.test/x?notas=C4 D4 E4 G4 A4",
            title="Entrevista", retrieved_at="2026-08-26",
        )],
        findings=[InfluenceFinding(
            id="f-ghost", family="drums", dimension="articulation",
            semantic_value="ghost notes", intensity="medium",
            confidence="high", source_ids=("src-1",),
        )],
    )
    report = build_report(
        plan=plan, rendered_mid=mido.MidiFile(str(out)), rendered_midi_path=out,
        influence=profile, compile_result=compile_influence(profile),
        brief_path=brief_path,
    )
    fonte = _link(report, GHOST)["sources"][0]
    assert fonte["url"] is None
    assert fonte["url_omitido"] == "OMITIDO_CONTEUDO_MUSICAL"


def test_hash_do_brief_descreve_o_arquivo_que_o_relatorio_publica(tmp_path):
    """Achado 7: o sha vinha do argumento `brief_path` e o caminho publicado
    vinha de `plan.brief_ref.path`. Publicar o hash de um arquivo ao lado do
    caminho de OUTRO inverte a prova de autorizacao."""
    _src, out, brief_path, plan = _rendered_project(tmp_path)
    outro = tmp_path / "outro-brief.json"
    outro.write_text(json.dumps({"style": {}}), encoding="utf-8")

    report = build_report(
        plan=plan, rendered_mid=mido.MidiFile(str(out)), rendered_midi_path=out,
        brief_path=outro,
    )
    hashes = report["hashes"]
    assert hashes["brief_sha256"] == brief_sha256(outro)
    assert hashes["brief_path"] == str(outro)
    assert hashes["brief_path_declarado_no_plano"] == str(brief_path)
    assert hashes["brief_path_divergente"] is True
    assert any(
        m["path"] == "hashes.brief_path" for m in report["missing_links"]
    )

    # sem divergencia, nada de alarme falso
    coerente = build_report(
        plan=plan, rendered_mid=mido.MidiFile(str(out)), rendered_midi_path=out,
        brief_path=brief_path,
    )
    assert coerente["hashes"]["brief_path_divergente"] is False
    assert coerente["hashes"]["brief_path"] == str(brief_path)
    assert not any(
        m["path"] == "hashes.brief_path" for m in coerente["missing_links"]
    )


def test_lista_de_aplicadas_distingue_verificada_de_nao_verificavel(tmp_path):
    """Achado 8: `techniques.aplicadas` misturava verificada, nao
    verificavel e com erro — e e essa lista que os drivers mandam o agente
    consumir."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=None,
    )
    link = _link(report, GHOST)
    assert link["status"] == "aplicada_nao_verificavel"

    tecnicas = report["techniques"]
    assert GHOST in tecnicas["aplicadas"]
    assert GHOST not in tecnicas["aplicadas_verificadas"]
    assert GHOST in tecnicas["aplicadas_sem_evidencia"]
    assert tecnicas["por_status"]["aplicada_nao_verificavel"] == [GHOST]
    assert tecnicas["por_status"]["aplicada_verificada"] == []


def test_carimbo_ilegivel_vira_elo_ausente_em_vez_de_sumir(tmp_path):
    """Achado 9: o parser descartava em silencio segmento sem `=`, chave
    desconhecida e `techniques` sem colchetes, aceitava `midi-arranger v1`
    pelado como carimbo valido, e devolvia `None` para `midi-arranger v2` —
    fazendo a track virar 'sem carimbo' e a tecnica SUMIR do relatorio."""
    assert parse_stamp("nao e carimbo") is None
    assert parse_stamp("midi-arranger v2|role=drums")["problemas"] == (
        "versao_desconhecida:midi-arranger v2",
    )
    assert parse_stamp("midi-arranger v1")["problemas"] == ("carimbo_sem_campos",)
    problemas = parse_stamp(
        "midi-arranger v1|role=drums|lixo|techniques=drums.flam|xpto=1",
    )["problemas"]
    assert "segmento_sem_igual:lixo" in problemas
    assert "techniques_sem_colchetes" in problemas
    assert "chave_desconhecida:xpto" in problemas

    # ponta a ponta: carimbo de versao futura no MIDI renderizado
    _src, out, brief_path, plan = _rendered_project(tmp_path)
    mid = mido.MidiFile(str(out))
    alvo = next(s for s in read_stamps(mid) if s.track_name == "Drums")
    for msg in mid.tracks[alvo.track_index]:
        if msg.is_meta and msg.type == "text" and msg.text.startswith("midi-arranger"):
            msg.text = msg.text.replace("midi-arranger v1", "midi-arranger v2", 1)
            break
    futuro = tmp_path / "out-v2.mid"
    mid.save(str(futuro))

    report = build_report(
        plan=plan, rendered_mid=mido.MidiFile(str(futuro)),
        rendered_midi_path=futuro,
        brief=json.loads(brief_path.read_text(encoding="utf-8")),
        brief_path=brief_path,
    )
    assert any(
        m["code"] == "track" and "ilegivel" in m["message"]
        for m in report["missing_links"]
    ), "carimbo que o leitor nao entende tem que virar elo ausente declarado"
    assert _link(report, GHOST)["status"] != "aplicada_verificada"


# --- regressao da issue #124: escopo do anticopia no relatorio --------------
#
# `_rendered_tracks_from_midi` reconstroi CADA track do MIDI renderizado que
# nao casa com um elemento do plano como `RenderedTrack` sintetica
# `source:<nome>`. O `report.build` entregava essa lista inteira ao
# `validate_anticopy`, enquanto o `render` so lhe entrega as tracks de
# elemento. Resultado sobre o mesmo arquivo e o mesmo corpus: o `render`
# acusava zero copia e o relatorio acusava dezenove — todas em tracks
# copiadas byte a byte do MIDI do proprio usuario —, e como erro de
# validador rebaixa o alvo, TODA tecnica do relatorio virava
# `aplicada_com_erro`.


def _corpus_do_proprio_render(out: Path) -> list[str]:
    """Corpus adversarial: o proprio MIDI renderizado.

    Toda track do arquivo casa consigo mesma, entao o `anticopia` acusa
    exatamente aquilo que ele julga — o que torna a lista de acusadas uma
    fotografia direta do escopo dele, sem depender de parentesco musical.
    """
    return [str(out)]


def test_anticopia_do_relatorio_nao_julga_track_que_o_arranjador_nao_escreveu(
    tmp_path,
):
    """Nem a track de origem intocada (`Piano`, `Bass` — fora de
    `plan.edits`, byte-identicas por contrato), nem a track editada
    (`Drums`, em `plan.edits`, cujas notas ESTRUTURAIS continuam sendo as do
    usuario) podem ser acusadas de copia. O elemento gerado (`pad_main`),
    esse sim escrito pelo arranjador, continua sendo julgado."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
        reference_corpus=_corpus_do_proprio_render(out),
    )
    anticopia = report["validators"]["anticopia"]
    assert anticopia["executado"] is True

    acusadas = {i["track"] for i in anticopia["issues"]}
    assert acusadas == {"pad_main - Omnisphere / Desert Wind *"}, (
        f"tracks acusadas fora do que o arranjador escreveu: {sorted(acusadas)}"
    )
    # nenhuma track sintetica de origem/edicao entra na conta...
    assert not [
        i for i in anticopia["issues"]
        if any(e.startswith("source:") for e in i["element_ids"])
    ]
    # ...e a cobertura declarada nao pode afirmar que elas foram olhadas.
    assert anticopia["tracks_cobertas"] == ["pad_main - Omnisphere / Desert Wind *"]
    for nome in ("Piano", "Bass", "Drums"):
        assert nome not in anticopia["tracks_cobertas"]


def test_anticopia_do_relatorio_continua_julgando_track_de_elemento(tmp_path):
    """A metade que NAO pode ser perdida junto com a correcao: quando a copia
    esta numa track que o arranjador escreveu de fato, o relatorio acusa."""
    src, out, brief_path, plan = _rendered_project_com_elemento_de_bateria(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
        reference_corpus=_corpus_do_proprio_render(out),
    )
    anticopia = report["validators"]["anticopia"]
    acusadas = {i["track"] for i in anticopia["issues"]}
    assert "drums_main - Superior 3 / Metal Kit *" in acusadas
    assert anticopia["erros"] >= 1

    link = _link(report, GHOST)
    assert link["targets"][0]["kind"] == "element"
    assert link["status"] == "aplicada_com_erro"


def test_copia_falsa_em_track_de_origem_nao_rebaixa_o_status_da_tecnica(tmp_path):
    """O efeito colateral que motivou a issue: a acusacao falsa em track de
    origem nao carregava so um erro solto — ela rebaixava para
    `aplicada_com_erro` o status de TODA tecnica do relatorio, afirmando
    FALHA sem base. Hoje a tecnica aplicada na track de `plan.edits` sai
    `aplicada_nao_verificavel`: ninguem a percorreu, e isso e o que o
    relatorio diz."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
        reference_corpus=_corpus_do_proprio_render(out),
    )
    link = _link(report, GHOST)
    evidencia = link["targets"][0]["validator_evidence"]

    assert link["targets"][0]["track_name"] == "Drums"
    assert evidencia["erros"] == []
    assert evidencia["erros_globais"] == []
    assert evidencia["veredito"] == "sem_cobertura"
    assert link["status"] == "aplicada_nao_verificavel"
    assert report["techniques"]["por_status"]["aplicada_com_erro"] == []
