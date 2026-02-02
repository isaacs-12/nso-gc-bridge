#!/bin/bash
# Double-click to run the NSO GC Bridge launcher (no .app needed)
cd "$(dirname "$0")"

# Try venv first
if [ -f venv/bin/python3 ]; then
    venv/bin/python3 launcher.py
    exitcode=$?
else
    python3 launcher.py
    exitcode=$?
fi

# Fallback: venv Python often lacks tkinter on macOS (Homebrew). Use system Python + venv packages.
if [ $exitcode -ne 0 ] && [ -d venv/lib ]; then
    SITE=$(echo venv/lib/python*/site-packages)
    if [ -d "$SITE" ]; then
        echo "Trying system Python with venv packages..."
        PYTHONPATH="$SITE" /usr/bin/python3 launcher.py
        exitcode=$?
    fi
fi

if [ $exitcode -ne 0 ]; then
    echo ""
    echo "If tkinter is missing: brew install python-tk@3.13  (match your Python version)"
    echo "Press Enter to close..."
    read
fi
exit $exitcode
