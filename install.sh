#!/usr/bin/env bash
# Install JARVIS into a virtual environment and register the desktop entry.
# Usage: ./install.sh [venv-path]   (default: ~/.local/share/jarvis/venv)
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv_dir="${1:-$HOME/.local/share/jarvis/venv}"
bin_dir="$HOME/.local/bin"
desktop_dir="$HOME/.local/share/applications"
icon_dir="$HOME/.local/share/icons/hicolor/scalable/apps"

python3 -m venv "$venv_dir"
"$venv_dir/bin/pip" install --upgrade pip >/dev/null
"$venv_dir/bin/pip" install "$repo_dir"

mkdir -p "$bin_dir" "$desktop_dir" "$icon_dir"
ln -sf "$venv_dir/bin/jarvis" "$bin_dir/jarvis"
install -m 0644 "$repo_dir/packaging/jarvis.svg" "$icon_dir/jarvis.svg"
sed "s|^Exec=jarvis|Exec=$bin_dir/jarvis|" "$repo_dir/packaging/jarvis.desktop" \
    > "$desktop_dir/jarvis.desktop"
chmod 0644 "$desktop_dir/jarvis.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_dir" || true
fi

echo "Installed. 'jarvis' is at $bin_dir/jarvis (ensure $bin_dir is on PATH)."
"$venv_dir/bin/jarvis" doctor || true
