#!/usr/bin/env bash
# midi.arranger.cli - instalador da skill midi-brief.
#
# Cria um symlink de skills/midi-brief/ em cada diretorio de provider
# presente no alvo (default: $HOME). Provider ausente nao quebra a
# instalacao; so nao recebe symlink. Rodar duas vezes nao duplica nada.
#
# Uso:
#   ./install.sh              # instala em $HOME
#   ./install.sh /caminho/x   # instala em outro root (usado nos testes)

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_source="${script_dir}/skills/midi-brief"
target_root="${1:-${HOME}}"

if [[ ! -d "${skill_source}" ]]; then
    echo "erro: fonte da skill nao encontrada em ${skill_source}" >&2
    exit 1
fi

if [[ ! -d "${target_root}" ]]; then
    echo "erro: diretorio de instalacao inexistente: ${target_root}" >&2
    exit 1
fi

providers=(".claude" ".opencode" ".agents")

installed=()
skipped=()
missing=()

for provider in "${providers[@]}"; do
    provider_root="${target_root}/${provider}"
    if [[ ! -d "${provider_root}" ]]; then
        missing+=("${provider}")
        continue
    fi
    skills_dir="${provider_root}/skills"
    mkdir -p "${skills_dir}"
    link="${skills_dir}/midi-brief"
    if [[ -L "${link}" ]]; then
        current="$(readlink "${link}")"
        if [[ "${current}" == "${skill_source}" ]]; then
            skipped+=("${provider}")
            continue
        fi
        rm "${link}"
    elif [[ -e "${link}" ]]; then
        echo "erro: ${link} existe e nao e symlink; remova manualmente" >&2
        exit 1
    fi
    ln -s "${skill_source}" "${link}"
    installed+=("${provider}")
done

echo "midi-brief: skill em ${skill_source}"
echo "  alvo: ${target_root}"
if [[ ${#installed[@]} -gt 0 ]]; then
    echo "  instalado em: ${installed[*]}"
fi
if [[ ${#skipped[@]} -gt 0 ]]; then
    echo "  ja instalado (skip): ${skipped[*]}"
fi
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "  provider ausente: ${missing[*]}"
fi
