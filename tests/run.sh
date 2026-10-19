#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
while IFS= read -r -d '' file; do bash -n "$file"; done < <(find . -name '*.sh' -print0)
if command -v shellcheck >/dev/null; then
    shellcheck -x nginx-manager.sh lib/*.sh tests/run.sh
else
    printf 'SKIP: ShellCheck is not installed.\n'
fi
python3 -m unittest discover -s tests -v
