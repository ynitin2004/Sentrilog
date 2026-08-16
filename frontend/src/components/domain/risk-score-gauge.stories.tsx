import type { Meta, StoryObj } from '@storybook/react-vite'
import { RiskScoreBar, RiskScoreGauge } from './risk-score-gauge'

const meta: Meta<typeof RiskScoreGauge> = {
  title: 'Domain/RiskScoreGauge',
  component: RiskScoreGauge,
}
export default meta

type Story = StoryObj<typeof RiskScoreGauge>

export const LowRisk: Story = { args: { score: 0.05 } }
export const MediumRisk: Story = { args: { score: 0.42 } }
export const HighRisk: Story = { args: { score: 0.91 } }
export const NotYetScored: Story = { args: { score: null } }

export const AllBands: Story = {
  render: () => (
    <div className="flex gap-8">
      <RiskScoreGauge score={0.05} />
      <RiskScoreGauge score={0.42} />
      <RiskScoreGauge score={0.91} />
      <RiskScoreGauge score={null} />
    </div>
  ),
}

export const CompactBars: Story = {
  render: () => (
    <div className="flex flex-col gap-3">
      <RiskScoreBar score={0.05} />
      <RiskScoreBar score={0.42} />
      <RiskScoreBar score={0.91} />
      <RiskScoreBar score={null} />
    </div>
  ),
}
