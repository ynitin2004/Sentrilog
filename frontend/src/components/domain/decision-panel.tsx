import * as React from 'react'
import { AlertTriangle, Check, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { ReviewDecision } from '@/types/api'

export interface DecisionPanelProps {
  onSubmit: (decision: ReviewDecision, justification: string) => void | Promise<void>
  disabled?: boolean
}

/** approved/rejected/escalated -- matches services/intake/schemas.py::ReviewDecisionRequest
 * exactly (including the requirement that justification is non-empty, enforced client-side
 * here as a fast-feedback mirror of what the backend's Pydantic model will reject anyway). */
export function DecisionPanel({ onSubmit, disabled = false }: DecisionPanelProps) {
  const [justification, setJustification] = React.useState('')
  const [pending, setPending] = React.useState<ReviewDecision | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const handleDecide = async (decision: ReviewDecision) => {
    if (!justification.trim()) {
      setError('A justification is required before submitting a decision.')
      return
    }
    setError(null)
    setPending(decision)
    try {
      await onSubmit(decision, justification.trim())
    } finally {
      setPending(null)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Decision</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="justification">Justification</Label>
          <Textarea
            id="justification"
            value={justification}
            onChange={(e) => {
              setJustification(e.target.value)
              if (error) setError(null)
            }}
            placeholder="Explain the reasoning behind this decision -- this becomes part of the permanent record."
            disabled={disabled || pending !== null}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'justification-error' : undefined}
          />
          {error && (
            <p id="justification-error" className="text-danger text-xs">
              {error}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            className="bg-status-approved hover:bg-status-approved"
            onClick={() => handleDecide('approved')}
            loading={pending === 'approved'}
            disabled={disabled || pending !== null}
          >
            <Check className="h-4 w-4" /> Approve
          </Button>
          <Button
            variant="danger"
            onClick={() => handleDecide('rejected')}
            loading={pending === 'rejected'}
            disabled={disabled || pending !== null}
          >
            <X className="h-4 w-4" /> Reject
          </Button>
          <Button
            variant="secondary"
            onClick={() => handleDecide('escalated')}
            loading={pending === 'escalated'}
            disabled={disabled || pending !== null}
          >
            <AlertTriangle className="h-4 w-4" /> Escalate
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
