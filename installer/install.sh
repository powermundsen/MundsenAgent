#!/usr/bin/env bash

set -euo pipefail

MUNDSEN_BUNDLE_VERSION="0.3.0"

usage() {
    printf '%s\n' \
        "Usage: bash MundsenAgent-${MUNDSEN_BUNDLE_VERSION}-installer.sh [DIRECTORY]" \
        "       installer/install.sh [DIRECTORY] [--no-setup]" \
        "" \
        "Installs Mundsen Agent and its private runtime below one selected directory."
}

fail() {
    printf 'Mundsen Agent installer: %s\n' "$1" >&2
    exit 1
}

selected_root=""
run_setup=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --no-setup)
            run_setup=0
            ;;
        --*)
            fail "unknown option: $1"
            ;;
        *)
            if [ -n "$selected_root" ]; then
                fail "only one installation directory may be provided"
            fi
            selected_root="$1"
            ;;
    esac
    shift
done

if [ -z "$selected_root" ]; then
    default_root="$HOME/Mundsen Agent"
    if [ -t 0 ]; then
        printf 'Installation directory [%s]: ' "$default_root"
        IFS= read -r selected_root
    fi
    selected_root="${selected_root:-$default_root}"
fi

if LC_ALL=C printf '%s' "$selected_root" | grep -q '[[:cntrl:]]'; then
    fail "installation directory contains a control character"
fi

if [ "${selected_root#/}" = "$selected_root" ]; then
    selected_root="$PWD/$selected_root"
fi
if [ -L "$selected_root" ]; then
    fail "installation directory must not be a symlink"
fi
mkdir -p "$selected_root"
selected_root="$(cd "$selected_root" && pwd -P)"
resolved_home="$(cd "$HOME" && pwd -P)"
if [ "$selected_root" = "/" ] || [ "$selected_root" = "$resolved_home" ]; then
    fail "choose a dedicated directory below your home or another data volume"
fi

git_probe="$selected_root"
while [ "$git_probe" != "/" ]; do
    if [ -e "$git_probe/.git" ] || [ -L "$git_probe/.git" ]; then
        fail "installation directory must not be inside a Git worktree"
    fi
    git_probe="$(dirname "$git_probe")"
done

root_marker="$selected_root/.mundsen-agent-root"
if [ -L "$root_marker" ] || { [ -e "$root_marker" ] && [ ! -f "$root_marker" ]; }; then
    fail "installation root marker is unsafe"
fi
if [ -f "$root_marker" ]; then
    marker_bytes="$(wc -c < "$root_marker" | tr -d '[:space:]')"
    marker_value="$(LC_ALL=C head -c 64 "$root_marker")"
    if [ "$marker_bytes" != "27" ] || \
       [ "$marker_value" != "mundsen-agent-runtime-root" ]; then
        fail "installation root marker is invalid"
    fi
else
    existing_entry="$(
        find "$selected_root" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null
    )"
    legacy_runtime=0
    if [ -L "$selected_root/current" ] && \
       [ -d "$selected_root/releases" ] && \
       [ -x "$selected_root/bin/mundsen-agent" ]; then
        legacy_runtime=1
    fi
    if [ -n "$existing_entry" ] && [ "$legacy_runtime" -ne 1 ]; then
        fail "installation directory must be empty or an existing Mundsen Agent runtime"
    fi
    printf 'mundsen-agent-runtime-root\n' > "$root_marker"
fi
chmod 600 "$root_marker"
chmod 700 "$selected_root"

case "$(uname -s)" in
    Darwin|Linux) ;;
    *) fail "only macOS and Linux are supported" ;;
esac
case "$(uname -m)" in
    x86_64|amd64|arm64|aarch64) ;;
    *) fail "unsupported CPU architecture" ;;
esac
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
available_kib="$(df -Pk "$selected_root" 2>/dev/null | awk 'NR == 2 { print $4 }')"
case "$available_kib" in
    *[!0-9]*|"") ;;
    *)
        if [ "$available_kib" -lt 1572864 ]; then
            fail "at least 1.5 GiB of free disk space is required"
        fi
        ;;
esac

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/mundsen-agent-install.XXXXXX")"
cleanup() {
    rm -rf "$temporary_dir"
}
trap cleanup EXIT INT TERM

script_path="${BASH_SOURCE[0]}"
source_root="${MUNDSEN_SOURCE_DIR:-}"
if [ -z "$source_root" ] && [ -f "$script_path" ]; then
    marker_line="$(
        awk '/^__MUNDSEN_ARCHIVE_BELOW__$/ { print NR; exit }' "$script_path"
    )"
    if [ -n "$marker_line" ]; then
        payload_line=$((marker_line + 1))
        tail -n +"$payload_line" "$script_path" > "$temporary_dir/payload.tar.gz"
        if [ -s "$temporary_dir/payload.tar.gz" ] && \
           tar -tzf "$temporary_dir/payload.tar.gz" >/dev/null 2>&1; then
            tar -xzf "$temporary_dir/payload.tar.gz" -C "$temporary_dir"
            source_root="$temporary_dir/mundsen-agent-$MUNDSEN_BUNDLE_VERSION"
        fi
    fi
fi
if [ -z "$source_root" ]; then
    source_root="$(cd "$(dirname "$script_path")/.." && pwd -P)"
fi
[ -f "$source_root/src/mundsen_agent/__init__.py" ] || fail "release payload is missing"

mkdir -p \
    "$selected_root/releases" \
    "$selected_root/providers/home" \
    "$selected_root/providers/bin" \
    "$selected_root/providers/codex" \
    "$selected_root/tools/bin" \
    "$selected_root/tools/python" \
    "$selected_root/cache/uv" \
    "$selected_root/bin"
chmod 700 \
    "$selected_root/releases" \
    "$selected_root/providers" \
    "$selected_root/providers/home" \
    "$selected_root/providers/bin" \
    "$selected_root/providers/codex" \
    "$selected_root/tools" \
    "$selected_root/tools/bin" \
    "$selected_root/tools/python" \
    "$selected_root/cache" \
    "$selected_root/cache/uv" \
    "$selected_root/bin"

if [ -n "${MUNDSEN_TEST_PYTHON:-}" ]; then
    ln -sfn "$MUNDSEN_TEST_PYTHON" "$selected_root/tools/bin/python3"
else
    if [ ! -x "$selected_root/tools/bin/uv" ]; then
        curl -LsSf https://astral.sh/uv/install.sh |
            env \
                UV_INSTALL_DIR="$selected_root/tools/bin" \
                UV_NO_MODIFY_PATH=1 \
                sh
    fi
    env \
        UV_PYTHON_INSTALL_DIR="$selected_root/tools/python" \
        UV_PYTHON_BIN_DIR="$selected_root/tools/bin" \
        UV_CACHE_DIR="$selected_root/cache/uv" \
        "$selected_root/tools/bin/uv" python install 3.12 \
            --managed-python \
            --default \
            --no-progress
fi
[ -x "$selected_root/tools/bin/python3" ] || fail "managed Python installation failed"

release_dir="$selected_root/releases/$MUNDSEN_BUNDLE_VERSION"
if [ -L "$release_dir" ]; then
    fail "release directory must not be a symlink"
fi

stage="$temporary_dir/release"
rm -rf "$stage"
mkdir -p "$stage"
(
    cd "$source_root"
    tar -cf - \
        --exclude='*/__pycache__' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='*.egg-info' \
        --exclude='.DS_Store' \
        src \
        pyproject.toml \
        README.md \
        CHANGELOG.md \
        CONTRIBUTING.md \
        LICENSE \
        PRIVACY.md \
        SECURITY.md \
        docs
) | tar -xf - -C "$stage"
chmod -R u+rwX,go-rwx "$stage"

release_created=0
if [ -d "$release_dir" ]; then
    env \
        PYTHONPATH="$stage/src" \
        "$selected_root/tools/bin/python3" \
        -B \
        -c '
import sys
from pathlib import Path
from mundsen_agent.release import RELEASE_METADATA_NAME, payload_matches_release

payload = Path(sys.argv[1])
release = Path(sys.argv[2])
if not (release / RELEASE_METADATA_NAME).exists():
    raise SystemExit("existing release has no integrity metadata")
if not payload_matches_release(payload, release):
    raise SystemExit(
        f"release {release.name} is already installed with different content; "
        "build and install a new version instead of rebuilding this one"
    )
' \
        "$stage" \
        "$release_dir" || fail "installed release does not match this payload"
else
    mv "$stage" "$release_dir"
    release_created=1
fi

current_link="$selected_root/current"
if [ -e "$current_link" ] || [ -L "$current_link" ]; then
    [ -L "$current_link" ] || fail "current release path must be a symlink"
    current_target="$(
        cd "$current_link" 2>/dev/null && pwd -P
    )" || fail "current release link is broken"
    if [ "$current_target" != "$release_dir" ]; then
        fail "a different Mundsen Agent release is active; use mundsen-agent upgrade"
    fi
fi
ln -sfn "$release_dir" "$current_link"

wrapper="$selected_root/bin/mundsen-agent"
if [ -L "$wrapper" ]; then
    fail "command wrapper must not be a symlink"
fi
wrapper_stage="$temporary_dir/mundsen-agent-wrapper"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'set -euo pipefail' \
    'wrapper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"' \
    'mundsen_agent_root="$(cd "$wrapper_dir/.." && pwd -P)"' \
    'export MUNDSEN_HOME="$mundsen_agent_root"' \
    'export PYTHONPATH="$mundsen_agent_root/current/src"' \
    'export UV_PYTHON_INSTALL_DIR="$mundsen_agent_root/tools/python"' \
    'export UV_PYTHON_BIN_DIR="$mundsen_agent_root/tools/bin"' \
    'export UV_CACHE_DIR="$mundsen_agent_root/cache/uv"' \
    'export PYTHONDONTWRITEBYTECODE=1' \
    'exec "$mundsen_agent_root/tools/bin/python3" -m mundsen_agent "$@"' \
    > "$wrapper_stage"
chmod 700 "$wrapper_stage"
mv -f "$wrapper_stage" "$wrapper"

env \
    PYTHONPATH="$release_dir/src" \
    "$selected_root/tools/bin/python3" \
    -B \
    -c '
import sys
from pathlib import Path
from mundsen_agent.release import (
    RELEASE_METADATA_NAME,
    record_installed_release,
    verify_installed_release,
)

release = Path(sys.argv[1])
version = sys.argv[2]
created = sys.argv[3] == "1"
if created:
    record_installed_release(release, version=version)
else:
    if not (release / RELEASE_METADATA_NAME).exists():
        raise SystemExit("existing release has no integrity metadata")
    verify_installed_release(release)
' \
    "$release_dir" \
    "$MUNDSEN_BUNDLE_VERSION" \
    "$release_created"

if [ "${MUNDSEN_SKIP_PROVIDER_INSTALL:-0}" != "1" ]; then
    provider_home="$selected_root/providers/home"
    provider_path="/usr/bin:/bin:/usr/sbin:/sbin"
    provider_temp="$temporary_dir/providers"
    mkdir -p \
        "$provider_temp" \
        "$provider_home/.cache" \
        "$provider_home/.config" \
        "$provider_home/.local/share" \
        "$provider_home/.local/state"
    chmod 700 \
        "$provider_temp" \
        "$provider_home/.cache" \
        "$provider_home/.config" \
        "$provider_home/.local" \
        "$provider_home/.local/share" \
        "$provider_home/.local/state"

    claude_bin="$provider_home/.local/bin/claude"
    if [ ! -x "$claude_bin" ]; then
        printf '%s\n' "Installing Claude Code from Anthropic..."
        curl -fsSL https://claude.ai/install.sh |
            env -i \
                HOME="$provider_home" \
                PATH="$provider_path" \
                SHELL="/bin/sh" \
                TMPDIR="$provider_temp" \
                XDG_CACHE_HOME="$provider_home/.cache" \
                XDG_CONFIG_HOME="$provider_home/.config" \
                XDG_DATA_HOME="$provider_home/.local/share" \
                XDG_STATE_HOME="$provider_home/.local/state" \
                bash -s stable
    fi
    codex_bin="$selected_root/providers/bin/codex"
    if [ ! -x "$codex_bin" ]; then
        printf '%s\n' "Installing Codex CLI from OpenAI..."
        curl -fsSL https://chatgpt.com/codex/install.sh |
            env -i \
                HOME="$provider_home" \
                PATH="$provider_path" \
                SHELL="/bin/sh" \
                TMPDIR="$provider_temp" \
                XDG_CACHE_HOME="$provider_home/.cache" \
                XDG_CONFIG_HOME="$provider_home/.config" \
                XDG_DATA_HOME="$provider_home/.local/share" \
                XDG_STATE_HOME="$provider_home/.local/state" \
                CODEX_HOME="$selected_root/providers/codex" \
                CODEX_INSTALL_DIR="$selected_root/providers/bin" \
                CODEX_NON_INTERACTIVE=1 \
                sh
    fi
fi

printf '\nMundsen Agent %s is installed in %s\n' \
    "$MUNDSEN_BUNDLE_VERSION" "$selected_root"
printf 'Local command: %s\n\n' "$wrapper"

if [ "$run_setup" -eq 1 ]; then
    exec "$wrapper" setup
fi
exit 0

__MUNDSEN_ARCHIVE_BELOW__
