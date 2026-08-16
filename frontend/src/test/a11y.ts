import { expect } from 'vitest'
import axe from 'axe-core'

/** Runs a real axe-core audit against rendered DOM and fails with the actual violation
 * messages (not just a boolean) if anything is found -- calling axe-core directly rather than
 * through a custom-matcher wrapper package, since the available wrapper's type augmentation
 * targets an older Vitest type structure than the version this project pins. */
export async function expectNoA11yViolations(container: Element): Promise<void> {
  const results = await axe.run(container)
  const summary = results.violations.map(
    (v) => `${v.id}: ${v.description} (${v.nodes.length} node(s))`,
  )
  expect(summary, summary.join('\n')).toHaveLength(0)
}
