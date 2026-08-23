#!/usr/bin/env bash
# Grava em docs/fluxo-harness.md o hash atual de bin/midi-arranger.
#
# Rode DEPOIS de atualizar o texto do documento, nunca antes: o hash so registra
# que alguem passou por la e olhou. Ele nao verifica se o texto ficou correto.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
harness="$root/bin/midi-arranger"
doc="$root/docs/fluxo-harness.md"

[[ -f "$harness" ]] || { printf 'Error: harness nao encontrado em %s\n' "$harness" >&2; exit 1; }
[[ -f "$doc" ]] || { printf 'Error: documento nao encontrado em %s\n' "$doc" >&2; exit 1; }

sha="$(shasum -a 256 "$harness" | awk '{print $1}')"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed "s|^<!-- harness-sha256: .* -->$|<!-- harness-sha256: ${sha} -->|" "$doc" > "$tmp"

if ! grep -q "^<!-- harness-sha256: ${sha} -->$" "$tmp"; then
  printf 'Error: marcador harness-sha256 nao encontrado em %s\n' "$doc" >&2
  printf '       o documento precisa terminar com uma linha no formato:\n' >&2
  printf '       <!-- harness-sha256: ... -->\n' >&2
  exit 1
fi

mv "$tmp" "$doc"
trap - EXIT
printf 'hash atualizado: %s\n' "$sha"
