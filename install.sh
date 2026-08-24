#!/usr/bin/env bash
# midi.arranger.cli - instalador.
#
# Instala o harness e a skill de brief no modelo do Ralph:
#
#   $XDG_BIN_DIR/midi-arranger          shim executavel, entra no PATH
#   $MIDI_ARRANGER_HOME/                corpo: bin, prompts, tools, knowledge,
#                                       skills, AGENTS.md, requirements.txt
#   <provider>/skills/midi-brief        symlink para o corpo instalado
#
# Nada e escrito fora desses tres lugares. O instalador nao mexe em nenhuma
# outra configuracao de provider e imprime no fim exatamente o que fez.
#
# Variaveis de ambiente:
#   MIDI_ARRANGER_HOME  onde fica o corpo    (default <root>/.local/share/midi-arranger)
#   XDG_BIN_DIR         onde fica o shim     (default <root>/.local/bin)
#
# Uso:
#   ./install.sh              # instala com <root> = $HOME
#   ./install.sh /caminho/x   # instala com <root> = /caminho/x (usado nos testes)

set -euo pipefail

readonly EX_UNAVAILABLE=69
readonly MIN_PYTHON_MAJOR=3
readonly MIN_PYTHON_MINOR=11

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
target_root="${1:-${HOME}}"

die() {
    echo "erro: $1" >&2
    exit "${2:-1}"
}

if [[ ! -d "${target_root}" ]]; then
    die "diretorio de instalacao inexistente: ${target_root}"
fi
target_root="$(cd "${target_root}" && pwd -P)"

arranger_home="${MIDI_ARRANGER_HOME:-${target_root}/.local/share/midi-arranger}"
bin_dir="${XDG_BIN_DIR:-${target_root}/.local/bin}"

# --- pre-requisitos -------------------------------------------------------

# Uma unica invocacao do interpretador responde as duas perguntas: qual a
# versao, e quais modulos das tools faltam. Sondamos com `find_spec` em vez de
# `import` de proposito — importar pretty_midi carrega numpy e scipy e custa
# segundos, e a pergunta aqui e "esta instalado?", nao "funciona?".
readonly PYTHON_PROBE='
import importlib.util, sys
missing = [m for m in ("mido", "pretty_midi") if importlib.util.find_spec(m) is None]
print("%d.%d.%d|%s" % (sys.version_info[:3] + (" ".join(missing),)))
'

python_version=""
python_missing=""

probe_python() {
    local python_bin probe
    python_bin="$(command -v python3 || true)"
    if [[ -z "${python_bin}" ]]; then
        die "python3 nao encontrado no PATH. As tools exigem Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}." \
            "${EX_UNAVAILABLE}"
    fi
    if ! probe="$("${python_bin}" -c "${PYTHON_PROBE}" 2>/dev/null)"; then
        die "nao consegui interrogar ${python_bin}; as tools exigem Python >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}." \
            "${EX_UNAVAILABLE}"
    fi
    python_version="${probe%%|*}"
    python_missing="${probe#*|}"

    local major="${python_version%%.*}"
    local rest="${python_version#*.}"
    local minor="${rest%%.*}"
    if (( major < MIN_PYTHON_MAJOR )) ||
       (( major == MIN_PYTHON_MAJOR && minor < MIN_PYTHON_MINOR )); then
        die "Python ${python_version} em ${python_bin}; as tools exigem >= ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}." \
            "${EX_UNAVAILABLE}"
    fi
    python_path="${python_bin}"
}

# Nao instalamos pacote nenhum: pip mexeria fora dos diretorios declarados.
# Reportamos exatamente o que falta e o comando exato para resolver.
report_dependencies() {
    if [[ -z "${python_missing}" ]]; then
        echo "  dependencias python: ok (mido, pretty_midi) em Python ${python_version}"
        return 0
    fi
    echo "  dependencias python FALTANDO: ${python_missing}"
    echo "    instale com: ${python_path} -m pip install -r ${arranger_home}/requirements.txt"
}

# --- corpo ----------------------------------------------------------------

readonly BODY_DIRS=(bin prompts tools knowledge skills)
readonly BODY_FILES=(AGENTS.md requirements.txt)

install_body() {
    local item
    for item in "${BODY_DIRS[@]}"; do
        [[ -d "${script_dir}/${item}" ]] || die "fonte ausente: ${script_dir}/${item}"
    done
    for item in "${BODY_FILES[@]}"; do
        [[ -f "${script_dir}/${item}" ]] || die "fonte ausente: ${script_dir}/${item}"
    done

    mkdir -p "${arranger_home}"

    # Reinstalar tem que remover o que sumiu da origem, senao um arquivo
    # apagado no repo sobrevive para sempre na instalacao. Limpamos so os
    # diretorios que sao nossos, nunca o $arranger_home inteiro.
    for item in "${BODY_DIRS[@]}"; do
        rm -rf "${arranger_home:?}/${item}"
        cp -R "${script_dir}/${item}" "${arranger_home}/${item}"
    done
    find "${arranger_home}" -name '__pycache__' -type d -prune -exec rm -rf {} +

    for item in "${BODY_FILES[@]}"; do
        install -m 644 "${script_dir}/${item}" "${arranger_home}/${item}"
    done
    chmod 755 "${arranger_home}/bin/midi-arranger"
}

install_shim() {
    mkdir -p "${bin_dir}"
    local shim="${bin_dir}/midi-arranger"
    cat >"${shim}" <<EOF
#!/usr/bin/env bash
# Gerado por install.sh do midi.arranger.cli. Nao edite: reinstalar sobrescreve.
set -euo pipefail

MIDI_ARRANGER_HOME="\${MIDI_ARRANGER_HOME:-${arranger_home}}"
body="\$MIDI_ARRANGER_HOME/bin/midi-arranger"

if [[ ! -x "\$body" ]]; then
  echo "erro: midi-arranger nao esta instalado em \$body" >&2
  echo "Rode ./install.sh do repositorio, ou aponte MIDI_ARRANGER_HOME." >&2
  exit ${EX_UNAVAILABLE}
fi

exec "\$body" "\$@"
EOF
    chmod 755 "${shim}"
}

# --- skill ----------------------------------------------------------------

readonly PROVIDERS=(.claude .opencode .agents)

install_skill_links() {
    # O symlink aponta para o corpo instalado, nao para o checkout: harness e
    # skill tem que ser sempre a mesma versao. Depois de um git pull, rode
    # ./install.sh de novo.
    local skill_source="${arranger_home}/skills/midi-brief"
    local provider provider_root skills_dir link current

    for provider in "${PROVIDERS[@]}"; do
        provider_root="${target_root}/${provider}"
        if [[ ! -d "${provider_root}" ]]; then
            skill_missing+=("${provider}")
            continue
        fi
        skills_dir="${provider_root}/skills"
        mkdir -p "${skills_dir}"
        link="${skills_dir}/midi-brief"
        if [[ -L "${link}" ]]; then
            current="$(readlink "${link}")"
            if [[ "${current}" == "${skill_source}" ]]; then
                skill_skipped+=("${provider}")
                continue
            fi
            rm "${link}"
        elif [[ -e "${link}" ]]; then
            die "${link} existe e nao e symlink; remova manualmente"
        fi
        ln -s "${skill_source}" "${link}"
        skill_installed+=("${provider}")
    done
}

# --- execucao -------------------------------------------------------------

python_path=""
probe_python

install_body
install_shim

skill_installed=()
skill_skipped=()
skill_missing=()
install_skill_links

echo "midi-arranger instalado."
echo "  binario: ${bin_dir}/midi-arranger"
echo "  corpo:   ${arranger_home}"
report_dependencies
if [[ ${#skill_installed[@]} -gt 0 ]]; then
    echo "  skill midi-brief instalada em: ${skill_installed[*]}"
fi
if [[ ${#skill_skipped[@]} -gt 0 ]]; then
    echo "  skill midi-brief ja instalada (skip): ${skill_skipped[*]}"
fi
if [[ ${#skill_missing[@]} -gt 0 ]]; then
    echo "  provider ausente: ${skill_missing[*]}"
fi
case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *) echo "  aviso: ${bin_dir} nao esta no PATH" ;;
esac
