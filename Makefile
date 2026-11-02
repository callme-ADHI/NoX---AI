.PHONY: all build-ui build-app package verify clean test

all: build-ui build-app package verify

build-ui:
	@echo "==> Building UI assets..."
	@if [ ! -d ui/node_modules ]; then (cd ui && npm install --silent); fi
	@cd ui && npm run build --silent

build-app: build-ui
	@echo "==> Building Tauri desktop app..."
	@cd desktop/src-tauri && cargo build --release

package: build-app
	@echo "==> Building Arch Linux packages non-root (makepkg)..."
	@cd packaging/arch && makepkg -f --nodeps

verify:
	@echo "==> Running installation verification..."
	@bash scripts/verify-install.sh

test:
	@echo "==> Running test suite..."
	@uv run pytest tests/ -v
	@cd desktop/src-tauri && cargo test

clean:
	@echo "==> Cleaning packaging artifacts..."
	@rm -rf packaging/arch/src packaging/arch/pkg packaging/arch/*.pkg.tar.zst packaging/arch/icons
