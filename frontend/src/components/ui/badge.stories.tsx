import type { Meta, StoryObj } from '@storybook/react-vite'
import { Badge } from './badge'

const meta: Meta<typeof Badge> = {
  title: 'UI/Badge',
  component: Badge,
  args: { children: 'Badge' },
}
export default meta

type Story = StoryObj<typeof Badge>

export const Neutral: Story = { args: { variant: 'neutral' } }
export const Brand: Story = { args: { variant: 'brand' } }
export const Success: Story = { args: { variant: 'success', children: 'Active' } }
export const Warning: Story = { args: { variant: 'warning', children: 'Needs review' } }
export const Danger: Story = { args: { variant: 'danger', children: 'Revoked' } }

export const AllVariants: Story = {
  render: () => (
    <div className="flex gap-2">
      <Badge variant="neutral">Neutral</Badge>
      <Badge variant="brand">Brand</Badge>
      <Badge variant="success">Success</Badge>
      <Badge variant="warning">Warning</Badge>
      <Badge variant="danger">Danger</Badge>
    </div>
  ),
}
