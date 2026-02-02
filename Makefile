# NSO GameCube Controller Bridge - Makefile
# Requires: Python 3.7+, pip. Uses venv if present.

VENV_PYTHON := $(shell if [ -f venv/bin/python3 ]; then echo venv/bin/python3; else echo python3; fi)

.PHONY: run build build-alias clean install open run-app release release-publish help

VERSION ?= $(shell git describe --tags --always 2>/dev/null || echo "dev")

# Default target
help:
	@echo "NSO GameCube Controller Bridge"
	@echo ""
	@echo "Targets:"
	@echo "  make run          - Run the launcher UI"
	@echo "  make build        - Build .app with py2app"
	@echo "  make build-alias  - Build dev .app (alias mode, uses source in-place)"
	@echo "  make clean        - Remove build artifacts"
	@echo "  make install      - Install Python dependencies"
	@echo "  make open         - Open the built app (after make build)"
	@echo "  make run-app      - Run app from terminal (shows errors if it crashes)"
	@echo "  make release      - Create release zip in release/"
	@echo "  make release-publish VERSION=1.0.0 - Create GitHub release and upload (requires gh)"
	@echo ""
	@echo "  Or double-click run.command to start the launcher (no .app needed)"

run:
	$(VENV_PYTHON) launcher.py

install:
	$(VENV_PYTHON) -m pip install "setuptools<71"  # py2app has Errno 17 with setuptools>=71
	$(VENV_PYTHON) -m pip install -r requirements.txt
	$(VENV_PYTHON) -m pip install py2app

build: install
	rm -rf build dist
	$(VENV_PYTHON) setup.py py2app
	@if [ -d dist/launcher.app ]; then mv dist/launcher.app "dist/NSO GC Bridge.app"; fi

build-alias:
	rm -rf build dist
	$(VENV_PYTHON) setup.py py2app -A
	@if [ -d dist/launcher.app ]; then mv dist/launcher.app "dist/NSO GC Bridge.app"; fi

clean:
	rm -rf build dist *.egg-info

open:
	@APP="$$(pwd)/dist/NSO GC Bridge.app"; \
	if [ -d "$$APP" ]; then open "$$APP"; else echo "App not found. Run 'make build' first."; exit 1; fi

run-app:
	@EXE="$$(pwd)/dist/NSO GC Bridge.app/Contents/MacOS/NSO GC Bridge"; \
	ALT="$$(pwd)/dist/NSO GC Bridge"; \
	if [ -f "$$EXE" ]; then "$$EXE" 2>&1; \
	elif [ -f "$$ALT" ]; then "$$ALT" 2>&1; \
	else echo "App not found. Run 'make build' first."; exit 1; fi

# Release packaging (no gh needed)
release:
	@mkdir -p release
	@echo "Creating nso-gc-bridge-$(VERSION).zip..."
	@git archive --format=zip --prefix=nso-gc-bridge-$(VERSION)/ HEAD -o release/nso-gc-bridge-$(VERSION).zip
	@if [ -d "dist/NSO GC Bridge.app" ]; then \
		echo "Adding NSO GC Bridge.app to release..."; \
		cd dist && zip -r "../release/NSO-GC-Bridge-$(VERSION)-macOS.zip" "NSO GC Bridge.app" && cd ..; \
	fi
	@echo "Done. Artifacts in release/:"
	@ls -la release/

# Create GitHub release and upload (requires: brew install gh && gh auth login)
release-publish: release
	@which gh >/dev/null || (echo "Install gh: brew install gh"; echo "Then: gh auth login"; exit 1)
	@echo "Creating release v$(VERSION)..."
	@gh release create "v$(VERSION)" release/*.zip --title "v$(VERSION)" --generate-notes
	@echo "Done. See: https://github.com/$$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases"
