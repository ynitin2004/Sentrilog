import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expectNoA11yViolations } from '@/test/a11y'
import { DecisionPanel } from './decision-panel'

describe('DecisionPanel', () => {
  it('blocks submission and shows an error when justification is empty', async () => {
    const onSubmit = vi.fn()
    render(<DecisionPanel onSubmit={onSubmit} />)

    await userEvent.click(screen.getByRole('button', { name: /approve/i }))

    expect(screen.getByText(/justification is required/i)).toBeInTheDocument()
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('submits the correct decision and justification once filled in', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    render(<DecisionPanel onSubmit={onSubmit} />)

    await userEvent.type(screen.getByLabelText(/justification/i), 'Looks legitimate.')
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))

    expect(onSubmit).toHaveBeenCalledWith('rejected', 'Looks legitimate.')
  })

  it('clears the error once the reviewer starts typing', async () => {
    render(<DecisionPanel onSubmit={vi.fn()} />)

    await userEvent.click(screen.getByRole('button', { name: /approve/i }))
    expect(screen.getByText(/justification is required/i)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/justification/i), 'a')
    expect(screen.queryByText(/justification is required/i)).not.toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { container } = render(<DecisionPanel onSubmit={vi.fn()} />)
    await expectNoA11yViolations(container)
  })
})
