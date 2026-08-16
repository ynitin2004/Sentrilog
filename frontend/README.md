# Sentrilog frontend

React + TypeScript operator console for Sentrilog: a reviewer queue (list, claim, decide) and a
tenant/admin console (case dashboard, API keys, webhooks + delivery logs, reviewers). Replaces
[`webui/reviewer.html`](../webui/reviewer.html)'s deliberately minimal Phase 7 single-file
console. See [`PLAN.md`](../PLAN.md) (Phases 8-10) for the full delivery plan and
[`STUDY.md`](../STUDY.md) for concept write-ups.

**Phase 8 (current):** every screen is built against a mock data layer — no real backend calls
yet. Phase 9 wires it to the real intake API (plus new endpoints this console needs). Phase 10
adds real-time updates via Server-Sent Events.

## Stack

Vite + React 19 + TypeScript (`strict`) + React Router + TanStack Query (Phase 9+) + Tailwind
CSS v4 + Radix primitives (shadcn/ui pattern) + Zod + Vitest/React Testing Library + Storybook.

## Development

```sh
npm install
npm run dev            # dev server, http://localhost:5173
npm run storybook       # component library in isolation, http://localhost:6006
npm test                # Vitest (unit/component tests, incl. real axe-core a11y audits)
npm run typecheck       # tsc -b (app + Storybook config)
npm run lint             # oxlint
npm run format           # prettier --write
npm run build            # production build
```

## Layout

```text
src/
  components/
    ui/        # Design-system primitives (Button, Card, Dialog, Toast, DataTable, ...)
    domain/    # Sentrilog-specific components (StatusBadge, RiskScoreGauge, DecisionPanel, ...)
  pages/
    reviewer/  # Queue, Case Detail
    admin/     # Overview, Cases, API Keys, Webhooks, Reviewers
  hooks/       # Data hooks -- mock-backed in Phase 8, real TanStack Query in Phase 9
  lib/         # Auth session context, utilities
  mocks/       # Fixture data for Phase 8's mock hooks
  types/       # Types mirroring the backend's Pydantic schemas
```
