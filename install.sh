#!/bin/sh
# Install baton:  curl -fsSL https://raw.githubusercontent.com/NikhilDhaliya/baton/main/install.sh | sh
set -eu
for need in python3 git curl; do
  command -v "$need" >/dev/null 2>&1 || { echo "baton needs $need" >&2; exit 1; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' || { echo "baton needs Python 3.8 or newer" >&2; exit 1; }
dir="${BATON_BIN:-$HOME/.local/bin}"
mkdir -p "$dir"
curl -fsSL https://raw.githubusercontent.com/NikhilDhaliya/baton/main/baton -o "$dir/baton.tmp"
chmod +x "$dir/baton.tmp"
mv "$dir/baton.tmp" "$dir/baton"
echo "Installed $dir/baton"
case ":$PATH:" in
  *":$dir:"*) ;;
  *) echo "Add it to your PATH:  export PATH=\"$dir:\$PATH\"" ;;
esac
echo "Next:  baton init --github my-claude   (or --remote URL, or nothing for this computer only)"
