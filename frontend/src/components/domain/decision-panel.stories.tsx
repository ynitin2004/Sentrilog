import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { DecisionPanel } from './decision-panel'

const meta: Meta<typeof DecisionPanel> = {
  title: 'Domain/DecisionPanel',
  component: DecisionPanel,
  args: { onSubmit: fn() },
}
export default meta

type Story = StoryObj<typeof DecisionPanel>

export const Default: Story = {}
export const Disabled: Story = { args: { disabled: true } }
