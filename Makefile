# NSO GameCube Controller Bridge - Makefile
# Requires: Python 3.7+, pip. Uses venv if present.

VENV_PYTHON := $(shell if [ -f venv/bin/python3 ]; then echo venv/bin/python3; else echo python3; fi)

.PHONY: run build build-alias clean install open run-app icon release release-publish release-replace help

VERSION ?= $(shell git describe --tags --always 2>/dev/null || echo "dev")

# Default target
help:
	@echo "NSO GameCube Controller Bridge"
	@echo ""
	@echo "Targets:"
	@echo "  make run          - Run the launcher UI"
	@echo "  make icon         - Convert assets/NSO_GC_BRIDGE.png to .icns (requires Pillow)"
	@echo "  make build        - Build .app with py2app"
	@echo "  make build-alias  - Build dev .app (alias mode, uses source in-place)"
	@echo "  make clean        - Remove build artifacts"
	@echo "  make install      - Install Python dependencies"
	@echo "  make open         - Open the built app (after make build)"
	@echo "  make run-app      - Run app from terminal (shows errors if it crashes)"
	@echo "  make release      - Create release zip in release/"
	@echo "  make release-publish VERSION=1.0.0 - Create GitHub release and upload (requires gh)"
	@echo "  make release-replace VERSION=1.0.1 - Replace assets of existing release (requires gh)"
	@echo ""
	@echo "  Or double-click run.command to start the launcher (no .app needed)"

run:
	$(VENV_PYTHON) launcher.py

install:
	$(VENV_PYTHON) -m pip install "setuptools<71"  # py2app has Errno 17 with setuptools>=71
	$(VENV_PYTHON) -m pip install -r requirements.txt
	$(VENV_PYTHON) -m pip install py2app

icon:
	@$(VENV_PYTHON) -c "from PIL import Image; img = Image.open('assets/NSO_GC_BRIDGE.png').convert('RGBA'); img.save('assets/NSO_GC_BRIDGE.icns', format='ICNS'); print('Created assets/NSO_GC_BRIDGE.icns')" || (echo "Run: pip install Pillow" && exit 1)

build: install
	rm -rf build dist
	VERSION="$(VERSION)" $(VENV_PYTHON) setup.py py2app
	@if [ -d dist/launcher.app ]; then mv dist/launcher.app "dist/NSO GC Bridge.app"; fi
	@# py2app ignores plist overrides; patch Info.plist for version and copyright
	@APP="dist/NSO GC Bridge.app"; \
	if [ -d "$$APP" ]; then \
		/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $(VERSION)" "$$APP/Contents/Info.plist"; \
		/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $(VERSION)" "$$APP/Contents/Info.plist"; \
		/usr/libexec/PlistBuddy -c "Set :NSHumanReadableCopyright 'Copyright © 2026 Isaac Smith'" "$$APP/Contents/Info.plist"; \
	fi
	@# py2app wrongly creates hid.py (binary) alongside lib-dynload/hid.so; remove it so Python loads the .so
	@rm -f "dist/NSO GC Bridge.app/Contents/Resources/lib/python3.13/hid.py" 2>/dev/null || true

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

# Release packaging (no gh needed) - builds .app first so macOS zip is included
release: build
	@mkdir -p release
	@rm -f release/nso-gc-bridge-*.zip release/NSO-GC-Bridge-*-macOS.zip
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
	@gh release create "v$(VERSION)" release/nso-gc-bridge-$(VERSION).zip release/NSO-GC-Bridge-$(VERSION)-macOS.zip --title "v$(VERSION)" --generate-notes
	@echo "Done. See: https://github.com/$$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases"

# Replace existing release assets (build + package + upload with --clobber)
release-replace: build release
	@which gh >/dev/null || (echo "Install gh: brew install gh"; echo "Then: gh auth login"; exit 1)
	@gh release view "v$(VERSION)" >/dev/null 2>&1 || (echo "Release v$(VERSION) does not exist. Use: make release-publish VERSION=$(VERSION)"; exit 1)
	@echo "Replacing assets for v$(VERSION)..."
	@gh release upload "v$(VERSION)" release/nso-gc-bridge-$(VERSION).zip release/NSO-GC-Bridge-$(VERSION)-macOS.zip --clobber
	@echo "Done. See: https://github.com/$$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/v$(VERSION)"
