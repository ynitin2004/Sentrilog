import type { Meta, StoryObj } from '@storybook/react-vite'
import { StatusBadge, DecisionBadge, DeliveryStatusBadge } from './status-badge'

const meta: Meta<typeof StatusBadge> = {
  title: 'Domain/StatusBadge',
  component: StatusBadge,
}
export default meta

type Story = StoryObj<typeof StatusBadge>

export const AllCaseStatuses: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <StatusBadge status="pending" />
      <StatusBadge status="processing" />
      <StatusBadge status="needs_review" />
      <StatusBadge status="approved" />
      <StatusBadge status="rejected" />
    </div>
  ),
}

export const AllDecisions: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <DecisionBadge decision="approved" />
      <DecisionBadge decision="rejected" />
      <DecisionBadge decision="escalated" />
    </div>
  ),
}

export const AllDeliveryStatuses: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <DeliveryStatusBadge status="pending" />
      <DeliveryStatusBadge status="delivered" />
      <DeliveryStatusBadge status="failed" />
    </div>
  ),
}
