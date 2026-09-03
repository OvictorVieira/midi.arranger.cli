"""Scanner de presets instalados no computador do usuario (US-012).

Fonte: secao 8 do spec (`tasks/midi-arranger-spec.md`).

Modelo: **descoberta + sweep generico**, nao scanner-por-plugin hardcoded.
Roda em qualquer Mac; nada assume o filesystem do proprio autor.

Locais varridos por padrao (macOS):

- `~/Library/Audio/Presets`              (AU user presets)
- `/Library/Audio/Presets`               (AU system-wide, ex.: reFX Nexus library)
- `~/Music/Audio Music Apps/Plug-In Settings`  (Logic AU settings)
- `~/Music/Audio Music Apps/Sampler Instruments`
- `~/Library/Application Support/<Vendor>`
- `~/Documents/<Vendor>`
- `/Users/Shared/<Vendor>`               (STEAM da Spectrasonics costuma ficar aqui)

Antes do sweep, o scanner procura ponteiros de filesystem (symlinks) dentro
desses locais. O instalador da Spectrasonics, por exemplo, costuma deixar
`~/Library/Application Support/Spectrasonics/STEAM` apontando para a library
real, inclusive em volume externo. O destino e descoberto e varrido
automaticamente; o usuario nao precisa configurar caminho.

Vendor dirs de terceiros sao filtrados por whitelist `VENDOR_DIRS`: so descemos
em pastas de vendors conhecidos, pra nao varrer Documentos inteiros do usuario.
Extensoes de preset (`PRESET_EXTS`) sao whitelisted para evitar sample de audio
(`.wav`, `.aif`, `.mp3`), imagem, log, backup, etc. Diretorios de sample /
cache / recordings (`SKIP_DIR_NAMES`) sao podados na descida.

Preset achado carrega `verified=True` e e o UNICO tipo que pode virar nome
exato num `instrument.preset`. Sugestao vinda de conhecimento do modelo cria
`Preset` via `unverified(plugin, name)` com `verified=False`.

Overrides de diagnostico/compatibilidade (nao fazem parte do fluxo normal):

- `PresetRoots(extra_roots=[Path, ...])` adiciona roots customizados.
- Env `MIDI_ARRANGER_PRESET_ROOTS` (colon-separated) tem o mesmo efeito.
- Env `SPECTRASONICS_STEAM_ROOT` continua aceito como escape hatch legado.

DBs opacos (Toontrack Superior3) NAO viram preset: sao registrados em
`OpaqueLibrary` com motivo, pra o harness saber que existe conteudo, mas nunca
inventar nome de preset a partir dele. DBs Spectrasonics sao diferentes: seu
inicio contem manifesto `FileSystem` legivel com os nomes reais; o scanner le
somente esse manifesto e nao interpreta o payload proprietario.
"""

from __future__ import annotations

import json
import os
import re
from html import unescape
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- constantes de descoberta ------------------------------------------------

DEFAULT_ROOTS: tuple[str, ...] = (
    "~/Library/Audio/Presets",
    "/Library/Audio/Presets",
    "~/Music/Audio Music Apps/Plug-In Settings",
    "~/Music/Audio Music Apps/Sampler Instruments",
    "~/Library/Application Support",
    "~/Documents",
    "/Users/Shared",
)

# Vendors reconhecidos dentro de `~/Library/Application Support`, `~/Documents`
# e `/Users/Shared`. Fora dessa lista, NAO descemos — evita varrer projetos
# pessoais do usuario. Ampliar quando aparecer plugin novo no `plugins.scan`.
VENDOR_DIRS: frozenset[str] = frozenset({
    "Ample Sound",
    "Antares",
    "Arturia",
    "Celemony",
    "FabFilter",
    "IK Multimedia",
    "Image-Line",
    "Modartt",
    "Native Instruments",
    "Neural DSP",
    "Output",
    "Peavey Electronics",
    "Plogue",
    "PreSonus",
    "Slate Digital",
    "Sonnox",
    "Spectrasonics",
    "Steven Slate Audio",
    "Steven Slate Audio Center",
    "Steven Slate Drums",
    "Steinberg",
    "Synapse Audio",
    "Toontrack",
    "u-he",
    "UVI",
    "Valhalla DSP",
    "Valhalla DSP, LLC",
    "Vienna Symphonic Library",
    "Waves Audio",
    "XLN Audio",
    "Xfer",
    "Xfer Records",
    "iZotope",
    "reFX",
})

# Extensoes cujo arquivo E preset. NAO inclui `.wav`, `.aif`, `.mp3`, `.png`,
# `.pdf`, `.txt`, `.log`, `.bak`, `.ds_store`.
PRESET_EXTS: frozenset[str] = frozenset({
    # Apple / Logic
    ".aupreset", ".pst", ".exs", ".acp", ".patch",
    # AU/VST/VST3 padrao
    ".fxp", ".fxb", ".vstpreset",
    # Native Instruments
    ".nki", ".nkm", ".nkr", ".nksn", ".nkss", ".nksf",
    # u-he
    ".h2p",
    # Xfer / Vital
    ".serumpreset", ".vital",
    # FabFilter
    ".ffp",
    # XLN Addictive Drums 2
    ".adpak", ".adkit", ".adg", ".adbeat", ".adm",
    # IK Multimedia (AmpliTube / SampleTank / T-Racks)
    ".at4p", ".at5p", ".txp", ".txm", ".at4m", ".mbp",
    # reFX Nexus (exportado como VST fxp) + .nxp
    ".nxp",
    # Spectrasonics Omnisphere/Trilian/Keyscape (patches)
    ".prt_a", ".prt_b", ".prt_c",
    # Steven Slate
    ".sslp",
    # Arturia
    ".arturiax",
    # Waves (partial coverage — Waves also uses proprietary format em App Support)
    ".xps",
})

# Diretorios que nao interessam durante a descida.
SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git", ".cache", "__pycache__",
    "Samples", "Sample Library", "Samples Library",
    "sample", "samples", "wav", "Wav", "WAV",
    "Audio", "Recordings", "recordings",
    "Cache", "cache", "Logs", "logs",
    "Backups", "backups", "Backup", "backup",
    "Temp", "temp", "tmp",
})

# DBs binarios opacos: caminho relativo dentro do root -> (plugin, motivo).
# Sao registrados como `OpaqueLibrary`, nunca como preset navegavel.
OPAQUE_DBS: tuple[tuple[str, str], ...] = (
    ("Toontrack/Superior3/SoundDB", "Superior Drummer 3"),
    ("Toontrack/EZdrummer/SoundDB", "EZdrummer"),
    ("Toontrack/EZBass/SoundDB", "EZbass"),
)

# Marcadores para dispatch especial durante a varredura.
_NEURAL_DSP_MARKER = "Neural DSP"
_LOGIC_SETTINGS_MARKER = "Plug-In Settings"

# Nomes canonicos dos plugins Logic AU vistos como sub-diretorios do
# `Plug-In Settings`. `supported_plugins` de `scan_all()` garante que essas
# chaves aparecam mesmo sem preset em disco — comportamento pedido pelos
# testes de contrato.
LOGIC_PLUGINS: tuple[str, ...] = ("Alchemy", "ES2", "Sampler", "Retro Synth")

# Plugins que sempre aparecem em `supported_plugins` para o harness ver a
# lista completa mesmo quando o usuario nao tem preset nenhum instalado.
_ALWAYS_LISTED_PLUGINS: tuple[str, ...] = (
    "Omnisphere",
    *LOGIC_PLUGINS,
    "Kontakt",
    "Serum",
    "Vital",
    "Addictive Drums 2",
    "Nexus",
)

# Mapeamento de nome de diretorio real → nome canonico do plugin.
_PLUGIN_ALIASES: dict[str, str] = {
    "NEXUS library": "Nexus",
    "NEXUS Library": "Nexus",
    "Nexus 4 library": "Nexus",
    "Nexus4Library": "Nexus",
    "Addictive Drums 2": "Addictive Drums 2",
    "AD2": "Addictive Drums 2",
    "STEAM": "Omnisphere",
}

# Vendor → plugin default (quando o preset sai de `~/Library/Audio/Presets/
# <Vendor>/*.aupreset` sem subdir de plugin).
_VENDOR_DEFAULT_PLUGIN: dict[str, str] = {
    "Spectrasonics": "Omnisphere",
    "Xfer Records": "Serum",
    "Xfer": "Serum",
}

NEXUS_PLUGIN_NAME = "Nexus"


# --- dataclasses -------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """Preset de plugin. `verified=True` = arquivo real visto no disco."""

    name: str
    plugin: str
    format: str
    path: str | None
    verified: bool
    vendor: str | None = None

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
            vendor=data.get("vendor"),
        )


@dataclass(frozen=True)
class OpaqueLibrary:
    """Library com presets em DB binario proprietario (nao navegavel)."""

    plugin: str
    vendor: str
    root: str
    reason: str  # 'proprietary_db'


@dataclass(frozen=True)
class DiscoveredRoot:
    """Root de library descoberto a partir de configuracao local."""

    path: str
    source: str
    method: str  # 'symlink'


@dataclass(frozen=True)
class UnresolvedRoot:
    """Ponteiro local encontrado cujo destino nao esta acessivel."""

    source: str
    target: str
    reason: str  # 'target_unavailable' ou 'permission_denied'


@dataclass(frozen=True)
class PresetRoots:
    """Overrides de diretorios de scan.

    Cada campo dedicado (omnisphere/logic/kontakt/serum/vital/addictive) mantem
    compatibilidade com testes e chamadores antigos; quando definido, e usado
    em VEZ do sweep generico daquele plugin. `extra_roots` acrescenta caminhos
    ao sweep. `disable_defaults=True` desliga `DEFAULT_ROOTS` e usa SOMENTE os
    campos dedicados + `extra_roots` (usado em teste).
    """

    omnisphere: Path | None = None
    logic: Path | None = None
    kontakt: Path | None = None
    serum: Path | None = None
    vital: Path | None = None
    addictive: tuple[Path, ...] | None = None
    extra_roots: tuple[Path, ...] = field(default_factory=tuple)
    disable_defaults: bool = False


def unverified(plugin: str, name: str) -> Preset:
    """Sugestao vinda de conhecimento do modelo (nao verificada em disco)."""
    return Preset(
        name=name,
        plugin=plugin,
        format="suggested",
        path=None,
        verified=False,
        vendor=None,
    )


# --- sweep primitives --------------------------------------------------------

def _expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def _walk(root: Path, *, filter_vendor: bool = False) -> Iterable[Path]:
    """Desce em `root` de forma deterministica pulando `SKIP_DIR_NAMES`.

    Quando `filter_vendor=True`, o primeiro nivel abaixo do root e filtrado
    pela whitelist `VENDOR_DIRS` — evita varrer `~/Documents` inteiro do
    usuario, so entra em subpastas de vendors conhecidos.
    """
    if not root.is_dir():
        return
    if filter_vendor:
        try:
            entries = sorted(root.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if entry.is_dir() and entry.name in VENDOR_DIRS:
                yield from _walk_all(entry)
    else:
        yield from _walk_all(root)


def _walk_all(root: Path) -> Iterable[Path]:
    """os.walk com pruning de `SKIP_DIR_NAMES` e ordenacao estavel."""
    try:
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIR_NAMES)
            for name in sorted(files):
                yield Path(current) / name
    except PermissionError:
        return


def _walk_steam(root: Path) -> Iterable[Path]:
    """Varre somente manifests/patches da STEAM, nunca samples/soundsources.

    Uma STEAM pode ter centenas de GB. Presets navegaveis ficam em
    `<Produto>/Settings Library/{Patches,Multis}`; descer Wavetables e
    Soundsources seria lento e nao encontraria presets selecionaveis.
    """
    try:
        products = sorted(root.iterdir())
    except (OSError, PermissionError):
        return
    for product in products:
        if not product.is_dir():
            continue
        settings = product / "Settings Library"
        for name in ("Patches", "Multis"):
            location = settings / name
            if location.is_dir():
                yield from _walk_all(location)


def _is_preset_file(path: Path) -> bool:
    # AppleDouble (`._name.ext`) e metadata do macOS em filesystems non-HFS;
    # nao e preset — filtra antes de qualquer coisa.
    if path.name.startswith("._"):
        return False
    ext = path.suffix.lower()
    if ext in PRESET_EXTS:
        return True
    # Neural DSP archetypes salvam presets como `.xml` dentro da propria arvore.
    if ext == ".xml" and _NEURAL_DSP_MARKER in path.parts:
        return True
    return False


# Pastas cujo nome NAO e nome de plugin (containers genericos). Quando
# aparecem no lugar do plugin, o parser desce um nivel pra achar o nome real.
_GENERIC_DIR_NAMES: frozenset[str] = frozenset({
    "Presets", "presets",
    "Libraries", "libraries",
    "Instruments", "instruments",
    "Patches", "patches",
    "Programs", "programs",
    "Banks", "banks",
    "User", "user", "Factory", "factory",
    "Bank", "Sound Sets", "Sound Packs",
})

# Segmento que marca uma library de instrumento Kontakt/Kontakt Player: o
# nome da PASTA (ex.: "Nord Piano 3") nao e o plugin que a toca — o host e
# sempre Kontakt. Detectado no caminho ABSOLUTO (nao so relativo ao root),
# porque `extra_roots` pode apontar direto pra dentro de uma library.
_NI_VENDOR_DIR = "Native Instruments"
_NI_LIBRARIES_DIR = "Libraries"


def _is_native_instruments_library_path(path: Path) -> bool:
    parts = path.parts
    for i in range(len(parts) - 1):
        if parts[i] == _NI_VENDOR_DIR and parts[i + 1] == _NI_LIBRARIES_DIR:
            return True
    return False


def _classify(path: Path, root: Path) -> tuple[str | None, str]:
    """Deduz `(vendor, plugin)` a partir do caminho relativo ao root.

    Regras:
      - `<root>/<Vendor>/<Plugin>/.../file.ext`  → vendor=<Vendor>, plugin=<Plugin>
      - `<root>/<Vendor>/file.ext`               → vendor=<Vendor>, plugin=default(<Vendor>) ou <Vendor>
      - `<root>/<Plugin>/.../file.ext` quando root e `Plug-In Settings` → plugin=<Plugin>
      - Quando o "plugin" cai numa pasta generica (Presets / Libraries /
        Instruments / Patches / Programs / Banks / User / Factory), o parser
        desce para o proximo nivel real. Cobre padrao FabFilter
        (`~/Documents/FabFilter/Presets/<Plugin>/*.ffp`).
      - `.../Native Instruments/Libraries/<Library>/...` (o nome da library,
        ex.: "Nord Piano 3", NAO e um plugin — quem toca esse `.nki` e
        sempre Kontakt/Kontakt Player) → vendor="Native Instruments",
        plugin="Kontakt", verificado no caminho ABSOLUTO pra cobrir tanto o
        sweep generico quanto `extra_roots` apontando direto pra library.
    Nome de plugin passa por `_PLUGIN_ALIASES` (ex.: 'NEXUS library' → 'Nexus').
    """
    if _is_native_instruments_library_path(path):
        return _NI_VENDOR_DIR, "Kontakt"
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None, path.parent.name
    parts = rel.parts
    is_logic = root.name == _LOGIC_SETTINGS_MARKER

    def _alias(name: str) -> str:
        return _PLUGIN_ALIASES.get(name, name)

    def _pick_plugin(idx_start: int) -> str:
        """A partir de `parts[idx_start]`, escolhe o primeiro segmento que
        nao seja pasta generica nem o proprio arquivo."""
        for i in range(idx_start, len(parts) - 1):
            candidate = parts[i]
            if candidate not in _GENERIC_DIR_NAMES:
                return candidate
        # Fallback: ultima pasta antes do arquivo.
        return parts[-2] if len(parts) >= 2 else "Unknown"

    if is_logic:
        # Estrutura: <Plug-In Settings>/<Plugin>/[<subdir>]/<file>
        if len(parts) >= 2:
            plugin_raw = _pick_plugin(0)
        else:
            plugin_raw = "Logic"
        return None, _alias(plugin_raw)

    if root.name.upper() == "STEAM" and len(parts) >= 2:
        # O root descoberto pelo ponteiro da Spectrasonics ja e a propria
        # STEAM. Seu primeiro nivel e o produto (Omnisphere/Trilian/Keyscape),
        # nao o vendor.
        return "Spectrasonics", _alias(parts[0])

    if len(parts) >= 3:
        vendor_raw = parts[0]
        plugin_raw = _pick_plugin(1)
        return vendor_raw, _alias(plugin_raw)

    if len(parts) == 2:
        vendor_raw = parts[0]
        plugin = _VENDOR_DEFAULT_PLUGIN.get(vendor_raw, vendor_raw)
        return vendor_raw, _alias(plugin)

    # Arquivo direto no root — plugin desconhecido.
    return None, "Unknown"


def _preset_from_file(path: Path, root: Path) -> Preset:
    vendor, plugin = _classify(path, root)
    return Preset(
        name=path.stem,
        plugin=plugin,
        format=path.suffix.lstrip(".").lower() or "file",
        path=str(path),
        verified=True,
        vendor=vendor,
    )


# --- sweep de alto nivel -----------------------------------------------------

def _configured_roots(pr: PresetRoots) -> list[Path]:
    """Resolve a lista final de roots a varrer, respeitando overrides.

    Regras:
      - Se `disable_defaults`, comeca com [], senao usa `DEFAULT_ROOTS`.
      - Acrescenta `extra_roots`.
      - Acrescenta env `MIDI_ARRANGER_PRESET_ROOTS` (colon-separated).
      - Acrescenta env `SPECTRASONICS_STEAM_ROOT` como root generico.
      - Acrescenta campos dedicados nao-nulos (omnisphere/logic/kontakt/...).
      - Deduplica preservando ordem.
    """
    roots: list[Path] = []
    if not pr.disable_defaults:
        for r in DEFAULT_ROOTS:
            roots.append(_expand(r))
    for r in pr.extra_roots:
        roots.append(_expand(r))
    env_extra = os.environ.get("MIDI_ARRANGER_PRESET_ROOTS", "")
    if env_extra:
        for item in env_extra.split(":"):
            item = item.strip()
            if item:
                roots.append(_expand(item))
    steam = os.environ.get("SPECTRASONICS_STEAM_ROOT", "")
    if steam:
        roots.append(_expand(steam))
    for dedicated in (pr.omnisphere, pr.logic, pr.kontakt, pr.serum, pr.vital):
        if dedicated is not None:
            roots.append(_expand(dedicated))
    if pr.addictive:
        for r in pr.addictive:
            roots.append(_expand(r))

    seen: set[str] = set()
    deduped: list[Path] = []
    for r in roots:
        key = str(r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


_DISCOVERY_MAX_DEPTH = 4
_LIBRARY_POINTER_HINTS: tuple[str, ...] = (
    "steam", "library", "libraries", "content", "sounddb", "sounds", "packs",
)


def _iter_discovery_dirs(
    root: Path, *, filter_vendor: bool, denied: list[Path] | None = None,
) -> Iterable[Path]:
    """Itera diretorios para descoberta sem seguir symlinks.

    A busca e limitada em profundidade: ponteiros de instalacao vivem perto da
    raiz do vendor; descer uma library inteira so aumentaria custo e ruido.

    Quando `denied` e passado, todo diretorio que existe mas nao pode ser
    listado por `PermissionError` (o root canonico em si, uma vendor dir
    whitelisted, ou algo mais fundo na descoberta) e acrescentado a ele — o
    chamador usa isso pra relatar `permission_denied` em vez de tratar
    silenciosamente como biblioteca ausente/vazia.
    """
    if not root.is_dir():
        return
    try:
        first_level = sorted(root.iterdir())
    except PermissionError:
        if denied is not None:
            denied.append(root)
        return
    except OSError:
        return

    stack: list[tuple[Path, int]] = []
    for entry in reversed(first_level):
        if filter_vendor and entry.name not in VENDOR_DIRS:
            continue
        stack.append((entry, 1))

    while stack:
        current, depth = stack.pop()
        if current.name in SKIP_DIR_NAMES:
            continue
        yield current
        if depth >= _DISCOVERY_MAX_DEPTH or current.is_symlink():
            continue
        try:
            children = sorted(current.iterdir(), reverse=True)
        except PermissionError:
            if denied is not None:
                denied.append(current)
            continue
        except OSError:
            continue
        for child in children:
            try:
                if child.is_dir() or child.is_symlink():
                    stack.append((child, depth + 1))
            except OSError:
                continue


def discover_roots(
    pr: PresetRoots | None = None,
) -> tuple[list[Path], list[DiscoveredRoot], list[UnresolvedRoot]]:
    """Resolve roots canonicos e ponteiros locais de library.

    A descoberta e deterministica e somente leitura. Symlink de diretorio e
    tratado como configuracao de instalacao: seu destino entra na fila de
    busca, inclusive quando fica em volume externo. Ponteiro quebrado e
    relatado para o harness diagnosticar volume desmontado/permissao, nunca
    vira pedido automatico para o usuario configurar env var.

    Um root canonico ou vendor dir whitelisted que EXISTE mas nao pode ser
    listado (`PermissionError`) tambem e relatado em `unresolved` com
    `reason="permission_denied"` — sem isso o harness nao distingue "biblioteca
    vazia" de "bloqueado por permissao, precisa de acao do usuario".
    """
    seeds = _configured_roots(pr or PresetRoots())
    roots: list[Path] = []
    discoveries: list[DiscoveredRoot] = []
    unresolved: list[UnresolvedRoot] = []
    denied: list[Path] = []
    queued: list[Path] = list(seeds)
    seen_roots: set[str] = set()
    seen_sources: set[str] = set()

    while queued:
        root = queued.pop(0)
        root_key = os.path.realpath(str(root)) if root.exists() else os.path.abspath(str(root))
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        roots.append(root)

        for candidate in _iter_discovery_dirs(
            root, filter_vendor=_needs_vendor_filter(root), denied=denied,
        ):
            if not candidate.is_symlink():
                continue
            source = str(candidate)
            if source in seen_sources:
                continue
            seen_sources.add(source)
            try:
                raw_target = os.readlink(candidate)
            except OSError:
                continue
            target = Path(raw_target)
            if not target.is_absolute():
                target = candidate.parent / target
            target = Path(os.path.abspath(str(target)))
            if target.is_dir():
                discoveries.append(DiscoveredRoot(
                    path=str(target), source=source, method="symlink",
                ))
                queued.append(target)
            elif any(hint in candidate.name.lower() for hint in _LIBRARY_POINTER_HINTS):
                unresolved.append(UnresolvedRoot(
                    source=source,
                    target=str(target),
                    reason="target_unavailable",
                ))

    seen_denied: set[str] = set()
    for path in denied:
        key = str(path)
        if key in seen_denied:
            continue
        seen_denied.add(key)
        unresolved.append(UnresolvedRoot(
            source=key, target=key, reason="permission_denied",
        ))

    return roots, discoveries, unresolved


def _needs_vendor_filter(root: Path) -> bool:
    """`~/Library/Application Support`, `~/Documents` e `/Users/Shared`
    varrem so subpastas de vendors conhecidos; demais roots descem tudo."""
    name = root.name
    if name in ("Application Support", "Documents", "Shared"):
        return True
    return False


_SPECTRASONICS_FILE_RE = re.compile(rb'<FILE\s+name="([^"]+)"')
_SPECTRASONICS_DIR_RE = re.compile(rb'<DIR\s+name="([^"]+)"')
_SPECTRASONICS_PATCH_RE = re.compile(r"\.(?:prt|mlt)_[a-z0-9]+$", re.IGNORECASE)


def _spectrasonics_presets_from_db(db_path: Path, steam_root: Path) -> list[Preset]:
    """Le o manifesto XML inicial de um DB Spectrasonics.

    O `.db` real nao e um banco SQL opaco: comeca com um `<FileSystem>` que
    lista nomes/categorias e offsets, seguido pelo payload concatenado. So o
    manifesto ate `</FileSystem>` e lido. Assim o nome retornado e observado no
    disco, nao inferido, sem tentar interpretar o payload proprietario.
    """
    try:
        rel = db_path.relative_to(steam_root)
    except ValueError:
        return []
    if len(rel.parts) < 2:
        return []
    plugin = rel.parts[0]
    if plugin not in {"Omnisphere", "Trilian", "Keyscape"}:
        return []

    out: list[Preset] = []
    dirs: list[str] = []
    try:
        with db_path.open("rb") as fp:
            for line in fp:
                dir_match = _SPECTRASONICS_DIR_RE.search(line)
                if dir_match:
                    dirs.append(unescape(dir_match.group(1).decode("utf-8", "replace")))
                file_match = _SPECTRASONICS_FILE_RE.search(line)
                if file_match:
                    filename = unescape(file_match.group(1).decode("utf-8", "replace"))
                    if _SPECTRASONICS_PATCH_RE.search(filename):
                        virtual = "/".join([*dirs, filename])
                        suffix = filename.rsplit(".", 1)[-1].lower()
                        out.append(Preset(
                            name=filename.rsplit(".", 1)[0],
                            plugin=plugin,
                            format=f"{suffix}_db",
                            path=f"{db_path}#{virtual}",
                            verified=True,
                            vendor="Spectrasonics",
                        ))
                for _ in range(line.count(b"</DIR>")):
                    if dirs:
                        dirs.pop()
                if b"</FileSystem>" in line:
                    break
    except OSError:
        return []
    return out


def _sweep_root(root: Path) -> tuple[list[Preset], list[OpaqueLibrary]]:
    """Retorna presets encontrados em `root` + libraries opacas."""
    presets_out: list[Preset] = []
    opaque: list[OpaqueLibrary] = []
    if not root.is_dir():
        return presets_out, opaque

    # Detecta DBs opacos ANTES de descer.
    for rel, plugin in OPAQUE_DBS:
        candidate = root / rel
        if candidate.exists():
            vendor = rel.split("/", 1)[0]
            opaque.append(OpaqueLibrary(
                plugin=plugin,
                vendor=vendor,
                root=str(candidate),
                reason="proprietary_db",
            ))

    files = (
        _walk_steam(root)
        if root.name.upper() == "STEAM"
        else _walk(root, filter_vendor=_needs_vendor_filter(root))
    )
    for f in files:
        if not f.is_file():
            continue
        if f.suffix.lower() == ".db" and root.name.upper() == "STEAM":
            presets_out.extend(_spectrasonics_presets_from_db(f, root))
            continue
        if not _is_preset_file(f):
            continue
        # Vital: valida JSON antes de aceitar como preset (skip corrompido).
        if f.suffix.lower() == ".vital":
            try:
                with f.open("r", encoding="utf-8") as fp:
                    json.load(fp)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
        presets_out.append(_preset_from_file(f, root))
    return presets_out, opaque


def sweep_with_locations(
    pr: PresetRoots | None = None,
) -> tuple[
    list[Preset],
    list[OpaqueLibrary],
    list[Path],
    list[DiscoveredRoot],
    list[UnresolvedRoot],
]:
    """Sweep completo com proveniencia dos roots pesquisados."""
    all_presets: list[Preset] = []
    all_opaque: list[OpaqueLibrary] = []
    seen_paths: set[str] = set()
    roots, discoveries, unresolved = discover_roots(pr)
    for root in roots:
        presets_here, opaque_here = _sweep_root(root)
        for p in presets_here:
            if p.path in seen_paths:
                continue
            seen_paths.add(p.path or "")
            all_presets.append(p)
        for op in opaque_here:
            if op not in all_opaque:
                all_opaque.append(op)
    return all_presets, all_opaque, roots, discoveries, unresolved


def sweep(pr: PresetRoots | None = None) -> tuple[list[Preset], list[OpaqueLibrary]]:
    """Sweep completo: devolve todos presets achados + libraries opacas.

    Determinismo: mesma entrada de filesystem → mesma saida (arquivos ordenados
    dentro de cada root, roots percorridos na ordem de `_configured_roots`).
    """
    presets_out, opaque, _roots, _discoveries, _unresolved = sweep_with_locations(pr)
    return presets_out, opaque


# --- compat: API antiga (scanners por plugin) --------------------------------
#
# Delega ao sweep e filtra por plugin. Preserva os testes e chamadores que
# criam roots sinteticos por plugin.

def _scan_specific(root: Path | None, plugin_label: str, exts: tuple[str, ...]) -> list[Preset]:
    """Varre `root` (isolado) devolvendo Presets marcados como `plugin_label`."""
    if root is None:
        return []
    root = _expand(root)
    if not root.is_dir():
        return []
    exts_lower = tuple(e.lower() for e in exts)
    out: list[Preset] = []
    for f in _walk_all(root):
        if not f.is_file():
            continue
        if f.suffix.lower() not in exts_lower:
            continue
        out.append(Preset(
            name=f.stem,
            plugin=plugin_label,
            format=f.suffix.lstrip(".").lower(),
            path=str(f),
            verified=True,
            vendor=None,
        ))
    return sorted(out, key=lambda p: p.path or "")


OMNISPHERE_EXTS: tuple[str, ...] = (".prt_a", ".prt_b", ".prt_c")
LOGIC_EXTS: tuple[str, ...] = (".aupreset", ".patch", ".pst", ".exs", ".acp")
KONTAKT_EXTS: tuple[str, ...] = (".nki",)
SERUM_EXTS: tuple[str, ...] = (".fxp",)
VITAL_EXTS: tuple[str, ...] = (".vital",)
ADDICTIVE_EXTS: tuple[str, ...] = (".adpak", ".adkit", ".adg", ".adbeat", ".adm")

DEFAULT_OMNISPHERE: Path = _expand("~/Library/Audio/Presets/Spectrasonics")
DEFAULT_LOGIC: Path = _expand("~/Music/Audio Music Apps/Plug-In Settings")
DEFAULT_KONTAKT: Path = _expand("~/Documents/Native Instruments")
DEFAULT_SERUM: Path = _expand("~/Documents/Xfer/Serum Presets")
DEFAULT_VITAL: Path = _expand("~/Music/Vital")
DEFAULT_ADDICTIVE: tuple[Path, ...] = (
    _expand("~/Library/Application Support/XLN Audio/Addictive Drums 2"),
    _expand("~/Library/Audio/Presets/XLN Audio"),
)


def scan_omnisphere(root: Path | None = None) -> list[Preset]:
    root = _expand(root) if root is not None else DEFAULT_OMNISPHERE
    return _scan_specific(root, "Omnisphere", OMNISPHERE_EXTS)


def scan_logic(root: Path | None = None) -> list[Preset]:
    """Escaneia `LOGIC_PLUGINS` dentro de `root`. Sub-plugin desconhecido e ignorado."""
    root = _expand(root) if root is not None else DEFAULT_LOGIC
    if not root.is_dir():
        return []
    out: list[Preset] = []
    for name in LOGIC_PLUGINS:
        out.extend(_scan_specific(root / name, name, LOGIC_EXTS))
    return out


def scan_kontakt(root: Path | None = None) -> list[Preset]:
    root = _expand(root) if root is not None else DEFAULT_KONTAKT
    return _scan_specific(root, "Kontakt", KONTAKT_EXTS)


def scan_serum(root: Path | None = None) -> list[Preset]:
    root = _expand(root) if root is not None else DEFAULT_SERUM
    return _scan_specific(root, "Serum", SERUM_EXTS)


def scan_vital(root: Path | None = None) -> list[Preset]:
    """Valida JSON: `.vital` corrompido e ignorado."""
    root = _expand(root) if root is not None else DEFAULT_VITAL
    if not root.is_dir():
        return []
    out: list[Preset] = []
    for f in _walk_all(root):
        if not (f.is_file() and f.suffix.lower() == ".vital"):
            continue
        try:
            with f.open("r", encoding="utf-8") as fp:
                json.load(fp)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        out.append(Preset(
            name=f.stem, plugin="Vital", format="vital",
            path=str(f), verified=True, vendor=None,
        ))
    return sorted(out, key=lambda p: p.path or "")


def scan_addictive_drums(roots: Iterable[Path] | None = None) -> list[Preset]:
    if roots is None:
        roots = DEFAULT_ADDICTIVE
    out: list[Preset] = []
    for r in roots:
        out.extend(_scan_specific(Path(r), "Addictive Drums 2", ADDICTIVE_EXTS))
    return out


# --- fachada `scan_all` ------------------------------------------------------

def _fallback_default_scanners() -> dict[str, list[Preset]]:
    """Roda scanners dedicados nos DEFAULT_* — cobre casos que o sweep generico
    perderia por caminho fora de whitelist (raro; garantia de compat)."""
    out: dict[str, list[Preset]] = {}
    out["Omnisphere"] = scan_omnisphere()
    logic = scan_logic()
    for name in LOGIC_PLUGINS:
        out[name] = [p for p in logic if p.plugin == name]
    out["Kontakt"] = scan_kontakt()
    out["Serum"] = scan_serum()
    out["Vital"] = scan_vital()
    out["Addictive Drums 2"] = scan_addictive_drums()
    return out


def scan_all(roots: PresetRoots | None = None) -> dict[str, list[Preset]]:
    """Devolve dict `plugin -> [Preset]`.

    - Se `roots` traz overrides dedicados (omnisphere/logic/kontakt/...), esses
      caminhos sao varridos pelos scanners dedicados e MAPEADOS no dict pelas
      chaves classicas (Omnisphere / Alchemy / ES2 / ... / Nexus).
    - Se `roots` e None (rodada real), roda o sweep generico com `DEFAULT_ROOTS`.
      Plugins descobertos entram como chaves dinamicas; chaves em
      `_ALWAYS_LISTED_PLUGINS` aparecem mesmo vazias.
    - Nexus deixou de ser hardcoded vazio: agora aparece com presets `.fxp`
      quando o usuario tem a NEXUS library instalada em `/Library/Audio/Presets/reFX/`.
    """
    r = roots or PresetRoots()
    grouped: dict[str, list[Preset]] = {}

    dedicated_used = any([
        r.omnisphere is not None, r.logic is not None, r.kontakt is not None,
        r.serum is not None, r.vital is not None, r.addictive is not None,
    ])

    if dedicated_used:
        # Modo compat (usado por testes com dir sintetico). Campos nao
        # informados NAO caem em DEFAULT_* quando `disable_defaults=True` —
        # senao teste com root sintetico varreria o Mac real do usuario.
        def _or_none(root: Path | None) -> Path | None:
            if root is not None:
                return root
            return None if r.disable_defaults else root  # None em ambos os casos

        grouped["Omnisphere"] = (
            scan_omnisphere(r.omnisphere) if r.omnisphere is not None
            else ([] if r.disable_defaults else scan_omnisphere())
        )
        logic_list = (
            scan_logic(r.logic) if r.logic is not None
            else ([] if r.disable_defaults else scan_logic())
        )
        for name in LOGIC_PLUGINS:
            grouped[name] = [p for p in logic_list if p.plugin == name]
        grouped["Kontakt"] = (
            scan_kontakt(r.kontakt) if r.kontakt is not None
            else ([] if r.disable_defaults else scan_kontakt())
        )
        grouped["Serum"] = (
            scan_serum(r.serum) if r.serum is not None
            else ([] if r.disable_defaults else scan_serum())
        )
        grouped["Vital"] = (
            scan_vital(r.vital) if r.vital is not None
            else ([] if r.disable_defaults else scan_vital())
        )
        grouped["Addictive Drums 2"] = (
            scan_addictive_drums(r.addictive) if r.addictive is not None
            else ([] if r.disable_defaults else scan_addictive_drums())
        )
        grouped[NEXUS_PLUGIN_NAME] = []
        # Sweep adicional em extra_roots (sempre isolado dos DEFAULT_ROOTS).
        if r.extra_roots:
            extra_pr = PresetRoots(extra_roots=r.extra_roots, disable_defaults=True)
            extra_presets, _ = sweep(extra_pr)
            for p in extra_presets:
                grouped.setdefault(p.plugin, []).append(p)
        return grouped

    # Rodada real: sweep generico.
    presets_found, _opaque = sweep(r)
    for p in presets_found:
        grouped.setdefault(p.plugin, []).append(p)

    # Rede de seguranca: se algum DEFAULT_* dedicado encontrou algo que o
    # sweep perdeu (root fora de whitelist), acrescenta sem duplicar. So
    # roda quando os defaults estao ativos.
    if not r.disable_defaults:
        seen_paths = {p.path for lst in grouped.values() for p in lst}
        for plugin, plist in _fallback_default_scanners().items():
            for p in plist:
                if p.path in seen_paths:
                    continue
                grouped.setdefault(plugin, []).append(p)
                seen_paths.add(p.path)

    # Garante chaves sempre listadas (`supported_plugins`).
    for name in _ALWAYS_LISTED_PLUGINS:
        grouped.setdefault(name, [])
    return grouped


def scan_all_with_opaque(
    roots: PresetRoots | None = None,
) -> tuple[dict[str, list[Preset]], list[OpaqueLibrary]]:
    """Como `scan_all`, mas tambem devolve DBs opacos detectados."""
    grouped = scan_all(roots)
    _, opaque = sweep(roots or PresetRoots())
    return grouped, opaque
