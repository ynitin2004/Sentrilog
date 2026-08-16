import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { expectNoA11yViolations } from '@/test/a11y'
import { StatusBadge, DecisionBadge, DeliveryStatusBadge } from './status-badge'
import type { CaseStatus, ReviewDecision, WebhookDeliveryStatus } from '@/types/api'

describe('StatusBadge', () => {
  const cases: [CaseStatus, string][] = [
    ['pending', 'Pending'],
    ['processing', 'Processing'],
    ['needs_review', 'Needs Review'],
    ['approved', 'Approved'],
    ['rejected', 'Rejected'],
  ]

  it.each(cases)('renders the correct label for %s', (status, label) => {
    render(<StatusBadge status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { container } = render(<StatusBadge status="needs_review" />)
    await expectNoA11yViolations(container)
  })
})

describe('DecisionBadge', () => {
  const cases: [ReviewDecision, string][] = [
    ['approved', 'Approved'],
    ['rejected', 'Rejected'],
    ['escalated', 'Escalated'],
  ]

  it.each(cases)('renders the correct label for %s', (decision, label) => {
    render(<DecisionBadge decision={decision} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })
})

describe('DeliveryStatusBadge', () => {
  const cases: WebhookDeliveryStatus[] = ['pending', 'delivered', 'failed']

  it.each(cases)('renders %s', (status) => {
    render(<DeliveryStatusBadge status={status} />)
    expect(screen.getByText(status)).toBeInTheDocument()
  })
})
