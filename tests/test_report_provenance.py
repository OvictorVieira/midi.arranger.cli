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
from tools.plan import ArrangementPlan, BriefRef, StyleTechnique
from tools.registry import call
from tools.render import render
from tools.report import ValidatorRun, build_report, read_stamps

GHOST = "drums.ghost_notes"


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


def _build_via_tool(
    src: Path,
    out: Path,
    plan: ArrangementPlan,
    *,
    brief_path: Path | None,
    influence: InfluenceProfile | None,
    out_path: Path | None = None,
) -> dict:
    from tools.influence import to_dict as influence_to_dict
    from tools.plan import to_dict as plan_to_dict

    payload: dict = {
        "midi_path": str(src),
        "rendered_path": str(out),
        "plan": plan_to_dict(plan),
    }
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
    """O caminho feliz inteiro, com todos os artefatos presentes."""
    src, out, brief_path, plan = _rendered_project(tmp_path)
    report = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=_influence_profile(),
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
    assert [t["track_name"] for t in targets] == ["Drums"]
    assert targets[0]["kind"] == "edit"

    # elo 6: metrica medida no MIDI final + veredito de validador
    metrics = targets[0]["metrics"]
    assert metrics["note_on_count"] > 0
    assert metrics["velocity_min"] is not None
    evidence = targets[0]["validator_evidence"]
    assert evidence["veredito"] == "limpo"
    assert "harmonia" in evidence["validadores"]

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

    primeiro = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=profile,
        out_path=tmp_path / "arrangement-report-1.json",
    )
    segundo = _build_via_tool(
        src, out, plan, brief_path=brief_path, influence=profile,
        out_path=tmp_path / "arrangement-report-2.json",
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
