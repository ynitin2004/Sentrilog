import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expectNoA11yViolations } from '@/test/a11y'
import { Button } from './button'

describe('Button', () => {
  it('renders its children and responds to clicks', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Approve</Button>)

    const button = screen.getByRole('button', { name: 'Approve' })
    await userEvent.click(button)

    expect(onClick).toHaveBeenCalledOnce()
  })

  it('is disabled and unclickable while loading', async () => {
    const onClick = vi.fn()
    render(
      <Button onClick={onClick} loading>
        Approve
      </Button>,
    )

    const button = screen.getByRole('button', { name: 'Approve' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')

    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('respects an explicit disabled prop independent of loading', () => {
    render(<Button disabled>Approve</Button>)
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
  })

  it('has no accessibility violations', async () => {
    const { container } = render(<Button>Approve</Button>)
    await expectNoA11yViolations(container)
  })
})
