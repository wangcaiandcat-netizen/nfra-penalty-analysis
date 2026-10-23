#!/bin/sh
skill_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 3
for interpreter in python3 python; do
    if command -v "$interpreter" >/dev/null 2>&1 &&
       "$interpreter" -I -B -X utf8 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' >/dev/null 2>&1; then
        exec "$interpreter" -I -B -X utf8 "$skill_dir/scripts/run.py" "$@"
    fi
done
printf '%s\n' '{"error":{"code":"PYTHON_NOT_AVAILABLE","message":"Python 3.10+ is required. Use a suitable interpreter to run scripts/run.py."}}' >&2
exit 3
