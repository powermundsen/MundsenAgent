#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
version="${1:-0.3.0}"
output_dir="${2:-$repo_root/dist}"

case "$version" in
    *[!0-9A-Za-z.-]*|"") printf 'Invalid version\n' >&2; exit 1 ;;
esac

package_version="$(
    sed -n 's/^__version__ = "\([^"]*\)"/\1/p' \
        "$repo_root/src/mundsen_agent/__init__.py"
)"
[ "$package_version" = "$version" ] || {
    printf 'Requested version does not match package version %s\n' \
        "$package_version" >&2
    exit 1
}

mkdir -p "$output_dir"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/mundsen-agent-release.XXXXXX")"
cleanup() {
    rm -rf "$temporary_dir"
}
trap cleanup EXIT INT TERM

stage="$temporary_dir/mundsen-agent-$version"
mkdir -p "$stage"
(
    cd "$repo_root"
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

archive_name="mundsen-agent-$version.tar.gz"
archive="$output_dir/$archive_name"
tar -czf "$archive" -C "$temporary_dir" "mundsen-agent-$version"

if command -v shasum >/dev/null 2>&1; then
    checksum="$(shasum -a 256 "$archive" | awk '{print $1}')"
else
    checksum="$(sha256sum "$archive" | awk '{print $1}')"
fi

manifest="$output_dir/release-manifest.json"
printf '%s\n' \
    '{' \
    '  "manifest_version": 1,' \
    "  \"version\": \"$version\"," \
    '  "minimum_instance_schema": 1,' \
    '  "maximum_instance_schema": 1,' \
    '  "files": [' \
    '    {' \
    "      \"path\": \"$archive_name\"," \
    "      \"sha256\": \"$checksum\"" \
    '    }' \
    '  ],' \
    '  "modules_changed": ["telegram", "router", "onboarding", "context", "reminders", "installer", "updater", "skills", "local-health", "diagnostics"],' \
    '  "personal_data_impact": "none",' \
    '  "migrations": [],' \
    '  "rollback_supported": true' \
    '}' \
    > "$manifest"

bundle="$output_dir/MundsenAgent-$version-installer.sh"
cp "$repo_root/installer/install.sh" "$bundle"
cat "$archive" >> "$bundle"
chmod 700 "$bundle"
if command -v shasum >/dev/null 2>&1; then
    bundle_checksum="$(shasum -a 256 "$bundle" | awk '{print $1}')"
else
    bundle_checksum="$(sha256sum "$bundle" | awk '{print $1}')"
fi
printf '%s  %s\n' "$bundle_checksum" "$(basename "$bundle")" \
    > "$bundle.sha256"
printf '%s  %s\n%s  %s\n' \
    "$checksum" "$archive_name" \
    "$bundle_checksum" "$(basename "$bundle")" \
    > "$output_dir/SHA256SUMS"

printf '%s\n' \
    "Built $archive" \
    "Built $manifest" \
    "Built $bundle" \
    "Built $bundle.sha256" \
    "Built $output_dir/SHA256SUMS"
