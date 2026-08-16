import type { Meta, StoryObj } from '@storybook/react-vite'
import { Check, KeyRound } from 'lucide-react'
import { Button } from './button'

const meta: Meta<typeof Button> = {
  title: 'UI/Button',
  component: Button,
  args: { children: 'Approve' },
}
export default meta

type Story = StoryObj<typeof Button>

export const Primary: Story = { args: { variant: 'primary' } }
export const Secondary: Story = { args: { variant: 'secondary' } }
export const Ghost: Story = { args: { variant: 'ghost' } }
export const Danger: Story = { args: { variant: 'danger', children: 'Reject' } }
export const Link: Story = { args: { variant: 'link' } }

export const WithIcon: Story = {
  args: {
    children: (
      <>
        <Check className="h-4 w-4" /> Approve
      </>
    ),
  },
}

export const KeyIcon: Story = {
  args: {
    variant: 'secondary',
    children: (
      <>
        <KeyRound className="h-4 w-4" /> New API key
      </>
    ),
  },
}

export const Loading: Story = { args: { loading: true } }
export const Disabled: Story = { args: { disabled: true } }

export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Button size="sm">Small</Button>
      <Button size="md">Medium</Button>
      <Button size="lg">Large</Button>
    </div>
  ),
}
