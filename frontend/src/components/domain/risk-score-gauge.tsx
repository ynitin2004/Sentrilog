import { cn, formatRiskScore } from '@/lib/utils'

/** Thresholds are visual bands only, not a decision boundary -- the actual auto-clear cutoff
 * lives in the backend's risk_scoring.py (combined confidence >= 0.85, i.e. risk_score <=
 * ~0.15). Kept close to that so "low" roughly lines up with "would auto-clear," without this
 * component pretending to encode the exact backend formula. */
function band(score: number): 'low' | 'medium' | 'high' {
  if (score <= 0.15) return 'low'
  if (score <= 0.5) return 'medium'
  return 'high'
}

const BAND_COLOR: Record<'low' | 'medium' | 'high', string> = {
  low: 'var(--color-risk-low)',
  medium: 'var(--color-risk-medium)',
  high: 'var(--color-risk-high)',
}

const BAND_LABEL: Record<'low' | 'medium' | 'high', string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
}

/** Compact horizontal meter -- used in table cells (queue, cases list) where a full radial
 * gauge would be too tall. */
export function RiskScoreBar({ score, className }: { score: number | null; className?: string }) {
  if (score === null) {
    return <span className="text-text-subtle text-xs">Not yet scored</span>
  }
  const b = band(score)
  const pct = Math.round(score * 100)
  return (
    <div className={cn('flex w-28 items-center gap-2', className)}>
      <div
        className="bg-surface-raised h-1.5 flex-1 overflow-hidden rounded-full"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Risk score ${formatRiskScore(score)}, ${BAND_LABEL[b]}`}
      >
        <div
          className="h-full rounded-full transition-[width]"
          style={{ width: `${pct}%`, backgroundColor: BAND_COLOR[b] }}
        />
      </div>
      <span className="text-text-muted w-9 shrink-0 text-right font-mono text-xs">
        {formatRiskScore(score)}
      </span>
    </div>
  )
}

/** Full radial gauge with a breakdown label -- the case-detail explainability view. Shows the
 * score as an arc rather than just a number, since "0.42" alone doesn't communicate where that
 * sits relative to the auto-clear threshold nearly as fast as a colored position on a dial does. */
export function RiskScoreGauge({ score }: { score: number | null }) {
  if (score === null) {
    return (
      <div className="flex flex-col items-center gap-2 py-4 text-center">
        <p className="text-text-subtle text-sm">Risk score not yet computed</p>
      </div>
    )
  }

  const b = band(score)
  const clampedPct = Math.min(Math.max(score, 0), 1)
  // Half-circle gauge, 180deg sweep: 0 -> left, 1 -> right.
  const angle = 180 * clampedPct
  const radius = 70
  const cx = 80
  const cy = 80
  const needleX = cx + radius * Math.cos(Math.PI - (angle * Math.PI) / 180)
  const needleY = cy - radius * Math.sin(Math.PI - (angle * Math.PI) / 180)

  return (
    <div
      className="flex flex-col items-center gap-1"
      role="img"
      aria-label={`Risk score ${formatRiskScore(score)}, ${BAND_LABEL[b]}`}
    >
      <svg width="160" height="96" viewBox="0 0 160 96" aria-hidden="true">
        <path
          d="M 10 80 A 70 70 0 0 1 150 80"
          fill="none"
          stroke="var(--color-surface-raised)"
          strokeWidth="12"
          strokeLinecap="round"
        />
        <path
          d="M 10 80 A 70 70 0 0 1 150 80"
          fill="none"
          stroke={BAND_COLOR[b]}
          strokeWidth="12"
          strokeLinecap="round"
          strokeDasharray={`${clampedPct * 220} 220`}
        />
        <circle cx={needleX} cy={needleY} r="5" fill={BAND_COLOR[b]} />
      </svg>
      <p className="text-text text-2xl font-semibold">{formatRiskScore(score)}</p>
      <p className="text-xs font-medium" style={{ color: BAND_COLOR[b] }}>
        {BAND_LABEL[b]}
      </p>
    </div>
  )
}
