import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

interface Fixture {
  tenant_id: string
  case_id: string
  subject_name: string
  api_key: string
  reviewer_token: string
}

const API_BASE = process.env.E2E_API_BASE ?? 'http://localhost:8000'
const fixture: Fixture = JSON.parse(
  readFileSync(process.env.E2E_FIXTURE ?? 'e2e/.fixture.json', 'utf-8'),
)

// connect -> queue -> claim -> decide, then check the decision reached the workflow: the
// case only becomes "approved" when finalize_case_activity runs, which happens after Temporal
// delivers the reviewer's signal -- so this proves the signal path, not just a table write.
test('a reviewer approves a parked case and the workflow finalizes it', async ({
  page,
  request,
}) => {
  await page.goto('/connect')
  await page.getByLabel('API base URL').fill(API_BASE)
  await page.getByLabel('Reviewer token').fill(fixture.reviewer_token)
  await page.getByRole('button', { name: 'Connect' }).click()

  await expect(page).toHaveURL(/\/reviewer\/queue$/)
  await page.getByRole('button', { name: new RegExp(fixture.subject_name) }).click()

  await expect(page).toHaveURL(new RegExp(`/reviewer/cases/${fixture.case_id}$`))
  await page.getByRole('button', { name: 'Claim case' }).click()
  await page.getByLabel('Justification').fill('E2E: documents verified against the record.')
  await page.getByRole('button', { name: 'Approve' }).click()

  await expect(page).toHaveURL(/\/reviewer\/queue$/)

  await expect
    .poll(
      async () => {
        const res = await request.get(`${API_BASE}/cases/${fixture.case_id}`, {
          headers: { Authorization: `Bearer ${fixture.api_key}` },
        })
        const body = await res.json()
        return `${body.status}/${body.decision}`
      },
      { timeout: 30_000 },
    )
    .toBe('approved/approved')
})
