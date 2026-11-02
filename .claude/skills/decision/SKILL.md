---
name: decision
description: Record a Sentrilog architecture or product decision with its reason, alternatives, revisit trigger and guard. Use when choosing between technologies or approaches, or before any major change as defined in CLAUDE.md.
---

# Decision

1. **Check it is not already settled.** Search `docs/DECISIONS.md` and `improvements.md` §10. If it is, say so and cite it. Reopen it only if its revisit trigger fired or the user asks.
2. **Run the Quiz Rule** if the change is major: 3–5 short questions on what it affects, what breaks if it is wrong, how to roll it back and what it costs. Wait for the answers.
3. **Add the entry** to `docs/DECISIONS.md`, using the next `D` number:

   ```markdown
   ### D<n> — <the decision, stated as a choice over its alternative>
   <Why, with measured evidence where there is any. The alternatives and why they lost.>
   **Revisit:** <the concrete condition that reopens this>.
   ```

4. **Add the guard** to `docs/DECISION-TRACES.md`: the test or script that fails if this is broken. If none exists, write one or mark it ⚠. Do not imply a guard that does not exist.
5. **Update** `docs/RULES.md` if it changes a rule, and `docs/FLOW.md` if it touches an execution path or a model's influence on a case.

Separate what was measured from what is estimated, and say which is which.
