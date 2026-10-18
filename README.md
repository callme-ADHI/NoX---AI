# NoX

**NoX** (pronounced "nocks") is a personal AI agent that runs locally on Adhi's Arch Linux machine. It starts automatically at login, handles daily briefings, reminders, memory, and system tasks — all with a local-first, privacy-first architecture.

---

## Requirements

- Arch Linux, Hyprland (Wayland)
- Python 3.11+ (system has 3.14)
- [Ollama](https://ollama.ai/) installed as a system service
- Node 18+ and npm (for the UI build)
- `libnotify` (`notify-send`) for desktop notifications

---

## Quick Setup (fish shell)

```fish
# 1. Clone and enter the project
git clone <repo-url> ~/nox
cd ~/nox

# 2. Run the install script
bash scripts/install.sh

# 3. Add Hyprland autostart (add this line to ~/.config/hypr/hyprland.conf)
exec-once = ~/nox/scripts/nx-open.sh

# 4. Optional: add NVIDIA API key for cloud-assisted research
echo 'NVIDIA_API_KEY=nvapi-...' >> ~/.config/nox/secrets.env
chmod 600 ~/.config/nox/secrets.env
```

---

## Manual Setup

```fish
# Create venv using uv
uv venv .venv --python 3.14
source .venv/bin/activate.fish

# Install package + dev dependencies
uv pip install -e ".[dev]"

# Pull required Ollama models
ollama pull nomic-embed-text
# qwen2.5:14b and gemma4:e4b should already be present

# Run health checks
nx doctor

# Start the daemon
nx run
```

---

## First Run

1. `nx doctor` — verify all dependencies are present
2. `nx run` — start the daemon (it creates `~/.config/nox/config.toml` if missing)
3. `nx open` — open the web UI (M7+)
4. `nx status` — check daemon health

---

## Adding the NVIDIA API Key

Get a free key at [build.nvidia.com](https://build.nvidia.com/). Then:

```fish
echo 'NVIDIA_API_KEY=nvapi-YOUR_KEY_HERE' > ~/.config/nox/secrets.env
chmod 600 ~/.config/nox/secrets.env
```

The key is used only for the `heavy` model role (cloud-assisted research on
**public data only**). Private data — chat history, memory, files — never leaves
the machine regardless of this setting.

---

## Enabling the Gate (Face Authentication)

Edit `~/.config/nox/config.toml`:

```toml
[gate]
mode = "command"
command = ["facelock", "verify"]   # replace with your FaceLock CLI
```

The UI will only open after exit code 0 from the command. Write-tier tools
also require re-authentication when the gate is in `command` mode.

---

## CLI Commands

| Command | Description |
|---|---|
| `nx run` | Start the daemon |
| `nx status` | Check daemon status |
| `nx doctor` | Run health checks (PASS/FAIL) |
| `nx token` | Show the session token |
| `nx open` | Open the web UI (M7) |
| `nx chat "message"` | One-shot chat (M3) |
| `nx jobs list` | List scheduled jobs (M6) |
| `nx memory search <query>` | Search memory (M4) |
| `nx eval-tools <model>` | Evaluate tool calling (M3) |

---

## Development

```fish
# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Type check
mypy src/noxai/

# Run a single test file
pytest tests/test_taint.py -v
```

---

## Configuration

Config lives at `~/.config/nox/config.toml`. See `config.example.toml` for
all options with comments. **Unknown keys are rejected** — the daemon will
refuse to start and show exactly which key is invalid.

Safety settings (`[safety]`, `[gate]`) are read-only from the UI and chat.
They can only be changed by editing the config file directly.

---

## Data Locations

| Path | Contents |
|---|---|
| `~/.config/nox/config.toml` | Configuration |
| `~/.config/nox/secrets.env` | API keys (mode 0600) |
| `~/.local/share/nox/nox.db` | Database (all data) |
| `~/.local/share/nox/skills/` | Editable skill files |
| `~/.local/share/nox/session_token` | Auth token (mode 0600) |
| `~/.local/state/nox/logs/` | Rotating JSON logs |

---

## Milestone Status

| Milestone | Status | Description |
|---|---|---|
| M0 | ✅ Done | Skeleton, config, DB, logging, `nx doctor` |
| M1 | 🔲 Next | Models, router, taint, privacy enforcement |
| M2 | 🔲 | Tools, safety, SSRF, approvals, audit log |
| M3 | 🔲 | Agent loop, prompt assembly, tool calling |
| M4 | 🔲 | Memory — facts, episodes, embeddings, recall |
| M5 | 🔲 | Planner, workers, synthesizer, DAG execution |
| M6 | 🔲 | Scheduler, jobs, reminders, meta-tools, seeds |
| M7 | 🔲 | REST API, WebSocket, session auth, gate |
| M8 | 🔲 | Web UI (React + TypeScript + Tailwind) |
| M9 | 🔲 | Autostart, install script, full docs |
