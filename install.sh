#!/bin/sh
# No platform-specific agent or Python package is needed to enter the installer.
set -eu
INSTALL_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
if [ "${JYUT_OCR_BOOTSTRAP_PYTHON+x}" = x ]; then
    INSTALL_PYTHON=$JYUT_OCR_BOOTSTRAP_PYTHON
    if command -v "$INSTALL_PYTHON" >/dev/null 2>&1 && "$INSTALL_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
        exec "$INSTALL_PYTHON" "$INSTALL_ROOT/scripts/install.py" "$@"
    fi
    printf '%s\n' 'JYUT_OCR_BOOTSTRAP_PYTHON must name an executable Python 3.10 or newer.' 'The explicit override was not replaced; correct it or unset it and rerun.' >&2
    exit 2
fi
INSTALL_FALLBACK_PYTHON=
for INSTALL_PYTHON in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$INSTALL_PYTHON" >/dev/null 2>&1 && "$INSTALL_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
        if [ -z "$INSTALL_FALLBACK_PYTHON" ]; then
            INSTALL_FALLBACK_PYTHON=$INSTALL_PYTHON
        fi
        # Distribution Python packages can include venv but omit ensurepip.
        # Prefer an installed interpreter that can initialize the core venv.
        if "$INSTALL_PYTHON" -c 'import venv, ensurepip' 2>/dev/null; then
            exec "$INSTALL_PYTHON" "$INSTALL_ROOT/scripts/install.py" "$@"
        fi
    fi
done
# Still enter the installer when possible: it reports the missing dependency,
# and read-only checks or skill-only installation do not need venv creation.
if [ -n "$INSTALL_FALLBACK_PYTHON" ]; then
    exec "$INSTALL_FALLBACK_PYTHON" "$INSTALL_ROOT/scripts/install.py" "$@"
fi
cat >&2 <<'EOF_HELP'
Python 3.10 or newer is required before setup can run.
Ubuntu/Debian/WSL: sudo apt-get install python3 python3-venv
Fedora: sudo dnf install python3
macOS with Homebrew: brew install python
Then run ./install.sh again, or set JYUT_OCR_BOOTSTRAP_PYTHON to its executable.
EOF_HELP
exit 2
