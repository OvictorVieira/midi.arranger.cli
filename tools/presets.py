"""Scanner de presets instalados na maquina (US-012).

Fonte: secao 8 do spec (`tasks/midi-arranger-spec.md`).

Cada varredor recebe um root opcional (para teste com diretorios sinteticos)
e cai no diretorio canonico do plugin quando o root nao e passado. Diretorio
inexistente NAO gera erro: retorna lista vazia. Isso vale principalmente para
Serum, que ainda nao esta instalado na maquina do usuario mas cujo caminho
esperado esta declarado.

Preset encontrado em disco carrega `verified=True`. Sugestoes vindas de
conhecimento do modelo devem ser criadas via `unverified(plugin, name)` e
carregam `verified=False`. Nexus e um caso particular: nao ha como escanear
sua base binaria fechada, entao qualquer sugestao para Nexus vem por
`unverified("Nexus", ...)` — nunca aparece na varredura de disco.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

# --- diretorios canonicos ---------------------------------------------------

DEFAULT_OMNISPHERE: Path = Path("~/Library/Audio/Presets/Spectrasonics").expanduser()
DEFAULT_LOGIC: Path = Path("~/Music/Audio Music Apps/Plug-In Settings").expanduser()
DEFAULT_KONTAKT: Path = Path("~/Documents/Native Instruments").expanduser()
DEFAULT_SERUM: Path = Path("~/Documents/Xfer/Serum Presets").expanduser()
DEFAULT_VITAL: Path = Path("~/Music/Vital").expanduser()
DEFAULT_ADDICTIVE: tuple[Path, ...] = (
    Path("~/Library/Application Support/XLN Audio/Addictive Drums 2").expanduser(),
    Path("~/Library/Audio/Presets/XLN Audio").expanduser(),
)

# Sub-plugins do Logic escaneados a partir do root `Plug-In Settings`.
LOGIC_PLUGINS: tuple[str, ...] = ("Alchemy", "ES2", "Sampler", "Retro Synth")

# Extensoes reconhecidas por plugin.
OMNISPHERE_EXTS: tuple[str, ...] = (".prt_a", ".prt_b", ".prt_c")
LOGIC_EXTS: tuple[str, ...] = (".aupreset", ".patch", ".pst", ".exs")
KONTAKT_EXTS: tuple[str, ...] = (".nki",)
SERUM_EXTS: tuple[str, ...] = (".fxp",)
VITAL_EXTS: tuple[str, ...] = (".vital",)
ADDICTIVE_EXTS: tuple[str, ...] = (".adpak", ".adkit", ".adg", ".adbeat", ".adm")

NEXUS_PLUGIN_NAME = "Nexus"


# --- dataclass --------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """Preset de plugin. `verified=True` quando o arquivo foi visto no disco."""

    name: str
    plugin: str
    format: str
    path: str | None
    verified: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> Preset:
        return Preset(
            name=data["name"],
            plugin=data["plugin"],
            format=data["format"],
            path=data.get("path"),
            verified=bool(data["verified"]),
        )


def unverified(plugin: str, name: str) -> Preset:
    """Sugestao de preset vinda de conhecimento do modelo (nao verificada em disco)."""
    return Preset(
        name=name,
        plugin=plugin,
        format="suggested",
        path=None,
        verified=False,
    )


# --- varredura de disco -----------------------------------------------------

def _iter_files_with_ext(root: Path, exts: tuple[str, ...]) -> list[Path]:
    """Devolve, ordenados, arquivos abaixo de `root` cuja extensao esta em `exts`.

    Root inexistente ou nao-diretorio -> lista vazia (sem erro). Extensoes sao
    comparadas em lower-case.
    """
    if root is None:
        return []
    root = Path(root)
    if not root.is_dir():
        return []
    exts_lower = tuple(e.lower() for e in exts)
    out: list[Path] = []
    for f in root.rglob("*"):
        if f.is_file() and f.suffix.lower() in exts_lower:
            out.append(f)
    return sorted(out)


def _preset_from_file(path: Path, plugin: str) -> Preset:
    return Preset(
        name=path.stem,
        plugin=plugin,
        format=path.suffix.lstrip(".").lower() or "file",
        path=str(path),
        verified=True,
    )


def scan_omnisphere(root: Path | None = None) -> list[Preset]:
    """Escaneia patches Omnisphere (.prt_a/.prt_b/.prt_c) sob `root`."""
    root = Path(root) if root is not None else DEFAULT_OMNISPHERE
    return [_preset_from_file(f, "Omnisphere") for f in _iter_files_with_ext(root, OMNISPHERE_EXTS)]


def scan_logic(root: Path | None = None) -> list[Preset]:
    """Escaneia presets Logic sob `~/Music/Audio Music Apps/Plug-In Settings/<Plugin>/`.

    Cada subdiretorio em `LOGIC_PLUGINS` (Alchemy, ES2, Sampler, Retro Synth)
    e varrido separadamente e o preset carrega o nome do sub-plugin, nao 'Logic'.
    """
    root = Path(root) if root is not None else DEFAULT_LOGIC
    if not root.is_dir():
        return []
    out: list[Preset] = []
    for name in LOGIC_PLUGINS:
        sub = root / name
        for f in _iter_files_with_ext(sub, LOGIC_EXTS):
            out.append(_preset_from_file(f, name))
    return out


def scan_kontakt(root: Path | None = None) -> list[Preset]:
    """Escaneia patches Kontakt (.nki) sob `root`."""
    root = Path(root) if root is not None else DEFAULT_KONTAKT
    return [_preset_from_file(f, "Kontakt") for f in _iter_files_with_ext(root, KONTAKT_EXTS)]


def scan_serum(root: Path | None = None) -> list[Preset]:
    """Escaneia presets Serum (.fxp) sob `root`. Ausencia = lista vazia."""
    root = Path(root) if root is not None else DEFAULT_SERUM
    return [_preset_from_file(f, "Serum") for f in _iter_files_with_ext(root, SERUM_EXTS)]


def scan_vital(root: Path | None = None) -> list[Preset]:
    """Escaneia presets Vital (.vital, JSON) sob `root`.

    Vital serializa presets como JSON com extensao `.vital`. Arquivos sem JSON
    valido (corrompidos, escritos por processo externo) sao ignorados.
    """
    root = Path(root) if root is not None else DEFAULT_VITAL
    out: list[Preset] = []
    for f in _iter_files_with_ext(root, VITAL_EXTS):
        try:
            with f.open("r", encoding="utf-8") as fp:
                json.load(fp)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        out.append(_preset_from_file(f, "Vital"))
    return out


def scan_addictive_drums(roots: Iterable[Path] | None = None) -> list[Preset]:
    """Escaneia presets Addictive Drums 2 sob multiplos roots.

    Cada root e varrido separadamente; um mesmo arquivo em dois roots vira
    dois presets (o consumidor decide como agrupar). Roots inexistentes sao
    pulados sem erro.
    """
    if roots is None:
        roots = DEFAULT_ADDICTIVE
    out: list[Preset] = []
    for root in roots:
        for f in _iter_files_with_ext(Path(root), ADDICTIVE_EXTS):
            out.append(_preset_from_file(f, "Addictive Drums 2"))
    return out


# --- fachada ----------------------------------------------------------------

@dataclass(frozen=True)
class PresetRoots:
    """Overrides de diretorios de scan, usado para teste com dir sintetico."""

    omnisphere: Path | None = None
    logic: Path | None = None
    kontakt: Path | None = None
    serum: Path | None = None
    vital: Path | None = None
    addictive: tuple[Path, ...] | None = None


def scan_all(roots: PresetRoots | None = None) -> dict[str, list[Preset]]:
    """Roda todos os scanners e devolve dict `plugin -> presets`.

    Chave 'Nexus' esta sempre presente e sempre vazia — reforca no consumidor
    que Nexus so aceita sugestoes via `unverified`. Serum ausente entra como
    lista vazia (nao levanta), como pede o spec.
    """
    r = roots or PresetRoots()
    logic_presets = scan_logic(r.logic)
    out: dict[str, list[Preset]] = {"Omnisphere": scan_omnisphere(r.omnisphere)}
    for name in LOGIC_PLUGINS:
        out[name] = [p for p in logic_presets if p.plugin == name]
    out["Kontakt"] = scan_kontakt(r.kontakt)
    out["Serum"] = scan_serum(r.serum)
    out["Vital"] = scan_vital(r.vital)
    out["Addictive Drums 2"] = scan_addictive_drums(r.addictive)
    out[NEXUS_PLUGIN_NAME] = []
    return out
