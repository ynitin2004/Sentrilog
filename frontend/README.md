# Sentrilog frontend

React + TypeScript operator console for Sentrilog: a reviewer queue (list, claim, decide) and a
tenant/admin console (case dashboard, API keys, webhooks + delivery logs, reviewers). Replaces
[`webui/reviewer.html`](../webui/reviewer.html)'s deliberately minimal Phase 7 single-file
console. See [`PLAN.md`](../PLAN.md) (Phases 8-10) for the full delivery plan and
[`STUDY.md`](../STUDY.md) for concept write-ups.

**Status:** wired to the real intake API (Phase 9) with real-time updates over Server-Sent Events
(Phase 10). The API URL is entered at runtime on the `/connect` screen.

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
  hooks/       # TanStack Query data hooks
  lib/         # Auth session context, utilities
  mocks/       # Fixture data for Phase 8's mock hooks
  types/       # Types mirroring the backend's Pydantic schemas
```
