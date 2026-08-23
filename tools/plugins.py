"""Inventario de plugins instalados na maquina (US-011).

Fonte: secao 8 do spec (`tasks/midi-arranger-spec.md`) e US-011 do PRD.

O scanner varre os diretorios canonicos do macOS para AU/VST/VST3, extrai
metadados basicos do bundle (`Info.plist`) quando possivel e classifica cada
plugin em papeis da paleta (`pad`, `arp`, `piano`, `strings`, `bass`, `sub`,
`fx`, `drums`, `sampler`, `amp`).

Instrumentos stock do Logic (Alchemy, ES2, Retro Synth, Sampler, Studio
Strings, Vintage Keys, Drum Machine Designer) sao sempre incluidos, com ou sem
arquivo de plugin, porque vivem embutidos no Logic. Plugin ausente do disco
nunca aparece — o consumidor pode confiar no inventario como fonte de verdade
do que existe agora.

O cache em disco (`cache_path`) armazena o inventario junto com as mtimes dos
diretorios varridos. Se qualquer mtime muda (ou uma nova pasta aparece), o
cache e invalidado e um novo scan roda. Diretorios inexistentes nao geram
erro: entram como `None` no dicionario de mtimes, e a comparacao vale o mesmo
para varreduras futuras.
"""

from __future__ import annotations

import json
import plistlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROLES = (
    "pad", "arp", "piano", "strings", "bass", "sub",
    "fx", "drums", "sampler", "amp",
)

FORMAT_BY_EXT = {
    ".component": "AU",
    ".vst": "VST",
    ".vst3": "VST3",
}

DEFAULT_PLUGIN_DIRS: tuple[Path, ...] = (
    Path("/Library/Audio/Plug-Ins/Components"),
    Path("/Library/Audio/Plug-Ins/VST"),
    Path("/Library/Audio/Plug-Ins/VST3"),
    Path("~/Library/Audio/Plug-Ins/Components").expanduser(),
    Path("~/Library/Audio/Plug-Ins/VST").expanduser(),
    Path("~/Library/Audio/Plug-Ins/VST3").expanduser(),
)


# --- classificacao ----------------------------------------------------------

# Mapa explicito de plugins conhecidos -> papeis. Nome normalizado (lower,
# sem sufixos como "2"/"3"/"AU"). Bate como substring dentro do nome do
# bundle apos normalizacao. Ordem NAO importa (dedup no fim).
KNOWN_PLUGIN_ROLES: dict[str, tuple[str, ...]] = {
    "omnisphere":        ("pad", "arp", "strings", "fx", "sampler"),
    "alchemy":           ("pad", "arp", "fx", "sampler"),
    "es2":               ("arp", "sub", "bass", "pad"),
    "retro synth":       ("arp", "sub", "bass"),
    "sampler":           ("sampler",),
    "exs24":             ("sampler",),
    "studio strings":    ("strings",),
    "vintage keys":      ("piano",),
    "drum machine":      ("drums",),
    "kontakt":           ("sampler",),
    "serum":             ("arp", "fx"),
    "vital":             ("arp", "fx"),
    "addictive drums":   ("drums",),
    "addictive trigger": ("drums",),
    "trigger":           ("drums",),
    "modo bass":         ("bass", "sub"),
    "frozenplain":       ("pad",),
    "mirage":            ("pad",),
    "nexus":             ("arp", "pad"),
    "nord piano":        ("piano",),
    "nord keyboard":     ("piano",),
    "guitar rig":        ("amp",),
    "amplitube":         ("amp",),
    "neural dsp":        ("amp",),
    "helix":             ("amp",),
}

# Fallback por palavra-chave quando o plugin nao esta no mapa conhecido.
# Testado em ORDEM: o primeiro keyword que aparece no nome define os papeis.
GENERIC_KEYWORD_ROLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("piano",    ("piano",)),
    ("rhodes",   ("piano",)),
    ("wurli",    ("piano",)),
    ("keys",     ("piano",)),
    ("string",   ("strings",)),
    ("choir",    ("strings",)),
    ("orchestr", ("strings",)),
    ("violin",   ("strings",)),
    ("cello",    ("strings",)),
    ("sub",      ("sub",)),
    ("bass",     ("bass",)),
    ("drum",     ("drums",)),
    ("kick",     ("drums",)),
    ("snare",    ("drums",)),
    ("beat",     ("drums",)),
    ("pad",      ("pad",)),
    ("atmos",    ("pad",)),
    ("texture",  ("pad",)),
    ("arp",      ("arp",)),
    ("pluck",    ("arp",)),
    ("lead",     ("arp",)),
    ("fx",       ("fx",)),
    ("riser",    ("fx",)),
    ("impact",   ("fx",)),
    ("sample",   ("sampler",)),
    ("amp",      ("amp",)),
)


LOGIC_STOCK: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Alchemy",              ("pad", "arp", "fx", "sampler")),
    ("ES2",                  ("arp", "sub", "bass", "pad")),
    ("Retro Synth",          ("arp", "sub", "bass")),
    ("Sampler",              ("sampler",)),
    ("Studio Strings",       ("strings",)),
    ("Vintage Keys",         ("piano",)),
    ("Drum Machine Designer", ("drums",)),
)


def classify(name: str) -> tuple[str, ...]:
    """Devolve papeis de um plugin pelo nome.

    Prioriza `KNOWN_PLUGIN_ROLES` (match por substring apos normalizacao).
    Se nada casar, tenta `GENERIC_KEYWORD_ROLES`. Se ainda assim nada,
    devolve tupla vazia — plugin fica no inventario sem papel atribuido.
    """
    lo = name.lower()
    matched: list[str] = []
    for key, roles in KNOWN_PLUGIN_ROLES.items():
        if key in lo:
            for r in roles:
                if r not in matched:
                    matched.append(r)
    if matched:
        return tuple(matched)
    for key, roles in GENERIC_KEYWORD_ROLES:
        if key in lo:
            for r in roles:
                if r not in matched:
                    matched.append(r)
            break
    return tuple(matched)


# --- dataclass --------------------------------------------------------------

@dataclass(frozen=True)
class Plugin:
    name: str
    manufacturer: str | None
    format: str  # "AU" | "VST" | "VST3" | "stock"
    path: str | None
    roles: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["roles"] = list(self.roles)
        return d

    @staticmethod
    def from_dict(data: dict) -> Plugin:
        return Plugin(
            name=data["name"],
            manufacturer=data.get("manufacturer"),
            format=data["format"],
            path=data.get("path"),
            roles=tuple(data.get("roles", [])),
        )


# --- leitura de bundle ------------------------------------------------------

def _read_info_plist(bundle: Path) -> dict:
    """Le `Contents/Info.plist` de um bundle. Devolve dict vazio se ausente/corrupto."""
    plist_path = bundle / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return {}
    try:
        with plist_path.open("rb") as fp:
            return plistlib.load(fp) or {}
    except (plistlib.InvalidFileException, OSError, ValueError):
        return {}


def _manufacturer_from_bundle_id(bundle_id: str) -> str | None:
    """Extrai fabricante do CFBundleIdentifier no padrao reverso-DNS.

    Ex.: `com.spectrasonics.Omnisphere2` -> `Spectrasonics`.
    Retorna `None` se o formato nao for reconhecido.
    """
    parts = bundle_id.split(".")
    if len(parts) < 3:
        return None
    return parts[1].capitalize() if parts[1] else None


def _plugin_from_bundle(bundle: Path) -> Plugin | None:
    """Constroi `Plugin` a partir de um bundle .component/.vst/.vst3.

    Retorna `None` se a extensao nao for reconhecida.
    """
    fmt = FORMAT_BY_EXT.get(bundle.suffix.lower())
    if fmt is None:
        return None
    info = _read_info_plist(bundle)
    name = info.get("CFBundleName") or bundle.stem
    manufacturer: str | None = None
    bundle_id = info.get("CFBundleIdentifier")
    if isinstance(bundle_id, str):
        manufacturer = _manufacturer_from_bundle_id(bundle_id)
    return Plugin(
        name=name,
        manufacturer=manufacturer,
        format=fmt,
        path=str(bundle),
        roles=classify(name),
    )


# --- varredura --------------------------------------------------------------

def _iter_bundles(directory: Path) -> Iterable[Path]:
    """Itera bundles com extensao de plugin dentro de `directory` (nao recursivo)."""
    if not directory.is_dir():
        return
    for entry in sorted(directory.iterdir()):
        if entry.suffix.lower() in FORMAT_BY_EXT:
            yield entry


def _stock_plugins(existing_names: set[str]) -> list[Plugin]:
    """Devolve plugins stock do Logic que ainda nao apareceram no scan."""
    out: list[Plugin] = []
    for name, roles in LOGIC_STOCK:
        if name.lower() in existing_names:
            continue
        out.append(Plugin(
            name=name,
            manufacturer="Apple",
            format="stock",
            path=None,
            roles=roles,
        ))
    return out


def scan(dirs: Iterable[Path] = DEFAULT_PLUGIN_DIRS) -> list[Plugin]:
    """Varre `dirs` e devolve inventario de plugins.

    Diretorio inexistente e pulado sem erro. Duplicatas (mesmo bundle em
    formatos distintos, ex.: Omnisphere.component e Omnisphere.vst3) sao
    preservadas — o consumidor decide como agrupar. Stock do Logic e
    anexado no fim, so incluindo nomes que ainda nao apareceram.
    """
    plugins: list[Plugin] = []
    seen_lower_names: set[str] = set()
    for d in dirs:
        d = Path(d)
        for bundle in _iter_bundles(d):
            p = _plugin_from_bundle(bundle)
            if p is None:
                continue
            plugins.append(p)
            seen_lower_names.add(p.name.lower())
    plugins.extend(_stock_plugins(seen_lower_names))
    return plugins


# --- cache ------------------------------------------------------------------

def _dir_mtimes(dirs: Iterable[Path]) -> dict[str, float | None]:
    """Coleta mtimes dos diretorios. Ausente -> None (chave preservada)."""
    out: dict[str, float | None] = {}
    for d in dirs:
        d = Path(d)
        try:
            out[str(d)] = d.stat().st_mtime
        except FileNotFoundError:
            out[str(d)] = None
    return out


def _load_cache(cache_path: Path) -> dict | None:
    if not cache_path.is_file():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(cache_path: Path, mtimes: dict[str, float | None], plugins: list[Plugin]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mtimes": mtimes,
        "plugins": [p.to_dict() for p in plugins],
    }
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_or_scan(
    cache_path: str | Path,
    dirs: Iterable[Path] = DEFAULT_PLUGIN_DIRS,
) -> list[Plugin]:
    """Carrega inventario do cache ou refaz o scan se algum mtime mudou.

    Regra: cache invalido se as chaves de `mtimes` nao baterem com `dirs`
    OU se qualquer mtime divergir. Cache ausente/corrupto tambem forca
    scan novo. Um scan bem-sucedido reescreve o cache.
    """
    dirs = tuple(Path(d) for d in dirs)
    cache_path = Path(cache_path)
    current = _dir_mtimes(dirs)
    cached = _load_cache(cache_path)
    if cached is not None and cached.get("mtimes") == current:
        try:
            return [Plugin.from_dict(p) for p in cached["plugins"]]
        except (KeyError, TypeError):
            pass  # cache corrompido: cai no scan
    plugins = scan(dirs)
    _write_cache(cache_path, current, plugins)
    return plugins
