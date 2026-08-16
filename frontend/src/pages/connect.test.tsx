import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expectNoA11yViolations } from '@/test/a11y'
import { renderWithProviders } from '@/test/test-utils'
import { ConnectPage } from './connect'

describe('ConnectPage', () => {
  it('disables Connect until both fields are filled', async () => {
    renderWithProviders(<ConnectPage />)

    // API base URL comes pre-filled with a sensible local-dev default; the token field starts
    // empty, so Connect is disabled until a token is entered too.
    const connectButton = screen.getByRole('button', { name: 'Connect' })
    expect(connectButton).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/reviewer token/i), 'a-token')
    expect(connectButton).toBeEnabled()

    await userEvent.clear(screen.getByLabelText(/api base url/i))
    expect(connectButton).toBeDisabled()
  })

  it('switches the token label when the admin persona is selected', async () => {
    renderWithProviders(<ConnectPage />)

    expect(screen.getByLabelText(/reviewer token/i)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /admin/i }))

    expect(screen.getByLabelText(/api key/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/reviewer token/i)).not.toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { container } = renderWithProviders(<ConnectPage />)
    await expectNoA11yViolations(container)
  })
})
