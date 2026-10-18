# NoX Web UI

Single-page application for the NoX Autonomous AI Agent, built with React, TypeScript, Vite, Tailwind CSS, and Lucide icons.

## Design Philosophy

- **Dark by default:** Near-black background (`#0a0a0b`), surface (`#111113`), border (`#222226`), and blue accent (`#7c9cff`).
- **Calm, precise, high-end look:** No gratuitous animations; 8-px spacing grid, 10-px border radius, Inter for UI, JetBrains Mono for code and tool outputs.
- **Privacy & Safety transparency:** Visible indicators for Local vs. Cloud model execution, local-only memory guarantees, and inline action approval cards with countdown timer.
- **Autonomous Task Plan DAG:** Signature live progress tree for multi-step agent workflows.

## Development

Prerequisites: Node.js (v20+) and npm.

```bash
# Install dependencies
npm install

# Run Vite development server with proxy to daemon (http://127.0.0.1:7777)
npm run dev

# Run TypeScript typecheck and build production bundle into ui/dist
npm run build

# Preview production build locally
npm run preview
```

## Production Distribution

The production build is written to `ui/dist/` and served automatically by `noxd` at `http://127.0.0.1:7777`.
All `/api/*` and `/ws` endpoints are protected by `HttpOnly`, `SameSite=Strict` session cookies established via the `nx open` token exchange flow (`/?t=<token>`).
State-changing API calls automatically include the `X-Nox-Client: web` CSRF defense header.
