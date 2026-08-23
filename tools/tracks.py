"""Convencao de nome de track MIDI (US-013).

Formato: `<Elemento> - <Plugin> / <Preset> <marca>` onde marca e `*`
para preset verificado em disco e `?` para sugestao nao verificada.

Todo o vocabulario e ASCII por decisao explicita — ver o bloco de
constantes abaixo. O meta-evento 0x03 do SMF nao carrega encoding, entao
qualquer byte >127 fica a merce do decoder do DAW.

Duas fontes normativas do spec:

- **FR-24 (roteamento por sample):** `perc_elec` -> Addictive Drums 2;
  `impact`, `snare_bomb`, `sub_drop`, `vox_chop` -> Logic Sampler. Nesses
  papeis o campo `<Preset>` deve ser o arquivo de sample. Trigger_2 e
  Addictive Trigger NUNCA sao sugeridos.
- **FR-14 (Serum):** Serum so pode ser sugerido para pluck, arp_gated,
  riser, growl_bass e lead_agressivo. Nunca como default de pad ou textura.

Nenhuma track sai sem sugestao — `name_for_element` bloqueia campos
vazios, plugin proibido, mismatch de FR-24 e uso de Serum fora do escopo
de FR-14. O truncamento preserva prefixo (`Elemento - Plugin / `) e marca
(` *|?`), cortando so o preset com reticencia.
"""

from __future__ import annotations

# --- constantes -------------------------------------------------------------

MIDI_TRACK_NAME_MAX_LEN: int = 64
"""Limite pratico do meta-event Track Name para exibicao no Logic e outros DAWs.

O SMF permite ate 255 chars em meta 0x03, mas display real trunca antes.
64 caracteres cobrem os exemplos do spec (`Pad Atmos - Omnisphere / Desert Wind *`)
com folga e ainda cabem na strip de mixer da maioria dos DAWs.
"""

# --- vocabulario ASCII-only -------------------------------------------------
#
# Meta-evento 0x03 do SMF nao tem campo de encoding: a spec MIDI 1.0 trata o
# texto como ASCII e cada DAW escolhe seu proprio decoder para bytes >127.
# Gravar UTF-8 e apostar em comportamento indefinido — se o leitor assumir
# latin-1, "—" (E2 80 94) vira "â\x80\x94" e "✓" (E2 9C 93) vira "â\x9c\x93",
# destruindo justamente a marca que diz ao usuario se o preset existe no disco
# dele. Como a marca e a informacao mais importante do nome, ela nao pode
# depender do decoder do DAW. Tudo aqui e ASCII (<0x80), que todo leitor
# decodifica igual.
VERIFIED_MARK: str = "*"
UNVERIFIED_MARK: str = "?"
SEPARATOR: str = " - "
PRESET_JOINER: str = " / "
TRUNCATION_MARK: str = "..."

#: Todo caractere emitido em nome de track precisa passar por este teste.
def is_ascii_safe(text: str) -> bool:
    """True quando `text` sobrevive a qualquer decoder de meta-evento SMF."""
    return all(ord(c) < 0x80 for c in text)


# --- FR-24: roteamento de sample -------------------------------------------

SAMPLER_ROUTING: dict[str, str] = {
    "perc_elec": "Addictive Drums 2",
    "impact": "Logic Sampler",
    "snare_bomb": "Logic Sampler",
    "sub_drop": "Logic Sampler",
    "vox_chop": "Logic Sampler",
}
"""Roles cujo plugin default e fixado pela tabela de FR-24."""

SAMPLER_ROLES: frozenset[str] = frozenset(SAMPLER_ROUTING.keys())
"""Roles que disparam sample — o campo `<Preset>` deve ser o arquivo de sample."""

FORBIDDEN_PLUGINS: frozenset[str] = frozenset({
    "Trigger 2",
    "Trigger_2",
    "Addictive Trigger",
})
"""Plugins que nunca podem ser sugeridos (FR-24, ultima linha)."""


# --- FR-14: escopo do Serum -------------------------------------------------

SERUM_PLUGIN_NAME: str = "Serum"

SERUM_ALLOWED_ROLES: frozenset[str] = frozenset({
    "pluck",
    "arp_gated",
    "riser",
    "growl_bass",
    "lead_agressivo",
})
"""Unicos roles em que Serum pode aparecer como sugestao."""


# --- excecao ---------------------------------------------------------------

class TrackNameError(ValueError):
    """Violacao da convencao de nome de track."""


# --- helpers publicos ------------------------------------------------------

def default_plugin_for_role(role: str) -> str | None:
    """Plugin default do role segundo FR-24; `None` quando escolha e livre."""
    return SAMPLER_ROUTING.get(role)


def is_sampler_role(role: str) -> bool:
    """`True` quando o role dispara sampler (preset e nome de arquivo de sample)."""
    return role in SAMPLER_ROLES


def is_forbidden_plugin(plugin: str) -> bool:
    """`True` para Trigger_2 e Addictive Trigger."""
    return plugin in FORBIDDEN_PLUGINS


def is_serum_allowed_for_role(role: str) -> bool:
    """`True` quando o role esta no escopo de FR-14."""
    return role in SERUM_ALLOWED_ROLES


# --- formatacao ------------------------------------------------------------

def format_track_name(
    element: str,
    plugin: str,
    preset: str,
    verified: bool,
    max_len: int = MIDI_TRACK_NAME_MAX_LEN,
) -> str:
    """Formata `<Elemento> - <Plugin> / <Preset> <marca>` com truncamento seguro.

    Nao aplica regras de roteamento — para isso, use `name_for_element`.
    Erros: `TrackNameError` se qualquer campo textual for vazio ou se
    `max_len` nao acomodar sequer prefixo+marca+1 char de preset.
    """
    if not isinstance(element, str) or not element.strip():
        raise TrackNameError("element must be non-empty string")
    if not isinstance(plugin, str) or not plugin.strip():
        raise TrackNameError("plugin must be non-empty string")
    if not isinstance(preset, str) or not preset.strip():
        raise TrackNameError("preset must be non-empty string")
    if not isinstance(max_len, int) or isinstance(max_len, bool) or max_len < 1:
        raise TrackNameError(f"max_len must be positive int, got {max_len!r}")

    mark = VERIFIED_MARK if verified else UNVERIFIED_MARK
    prefix = f"{element}{SEPARATOR}{plugin}{PRESET_JOINER}"
    suffix = f" {mark}"
    full = f"{prefix}{preset}{suffix}"
    if len(full) <= max_len:
        return full

    room = max_len - len(prefix) - len(suffix) - len(TRUNCATION_MARK)
    if room < 1:
        raise TrackNameError(
            f"max_len={max_len} too small to fit prefix {prefix!r} + mark {suffix!r}"
        )
    return f"{prefix}{preset[:room]}{TRUNCATION_MARK}{suffix}"


def name_for_element(
    element: str,
    role: str,
    plugin: str,
    preset: str,
    verified: bool,
    max_len: int = MIDI_TRACK_NAME_MAX_LEN,
) -> str:
    """Formata o nome aplicando FR-14 e FR-24.

    Bloqueia:
    - plugin em `FORBIDDEN_PLUGINS`;
    - role em `SAMPLER_ROUTING` com plugin diferente do default da tabela;
    - `plugin == "Serum"` com role fora de `SERUM_ALLOWED_ROLES`.
    """
    if is_forbidden_plugin(plugin):
        raise TrackNameError(
            f"plugin {plugin!r} is forbidden by FR-24 (Trigger_2/Addictive Trigger nunca sao sugeridos)"
        )
    default = default_plugin_for_role(role)
    if default is not None and plugin != default:
        raise TrackNameError(
            f"role {role!r} must use {default!r} per FR-24, got {plugin!r}"
        )
    if plugin == SERUM_PLUGIN_NAME and not is_serum_allowed_for_role(role):
        raise TrackNameError(
            f"Serum is not allowed for role {role!r} per FR-14 "
            f"(allowed: {sorted(SERUM_ALLOWED_ROLES)})"
        )
    return format_track_name(element, plugin, preset, verified, max_len)
