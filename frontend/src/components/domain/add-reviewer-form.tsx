import * as React from 'react'
import { UserPlus } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import type { ReviewerRole } from '@/types/api'

const ROLES: ReviewerRole[] = ['reviewer', 'admin', 'auditor']

export function AddReviewerForm({
  onCreate,
}: {
  onCreate: (email: string, role: ReviewerRole) => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [email, setEmail] = React.useState('')
  const [role, setRole] = React.useState<ReviewerRole>('reviewer')
  const [creating, setCreating] = React.useState(false)

  const handleSubmit = async () => {
    if (!email.trim()) return
    setCreating(true)
    try {
      await onCreate(email.trim(), role)
      setOpen(false)
      setEmail('')
      setRole('reviewer')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <UserPlus className="h-4 w-4" /> Add reviewer
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a reviewer</DialogTitle>
          <DialogDescription>
            Issues a reviewer token, one active token per reviewer -- shown once, same as an API
            key.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="reviewer-email">Email</Label>
            <Input
              id="reviewer-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="reviewer@yourcompany.com"
              autoFocus
            />
          </div>
          <fieldset className="space-y-1.5">
            <legend className="text-text text-sm font-medium">Role</legend>
            <div className="flex gap-2">
              {ROLES.map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRole(r)}
                  aria-pressed={role === r}
                  className={
                    'flex-1 rounded-md border px-3 py-2 text-sm font-medium capitalize transition-colors ' +
                    (role === r
                      ? 'border-brand bg-brand-bg text-brand-text'
                      : 'border-border text-text-muted hover:border-border-strong')
                  }
                >
                  {r}
                </button>
              ))}
            </div>
          </fieldset>
        </div>
        <DialogFooter>
          <Button onClick={handleSubmit} loading={creating} disabled={!email.trim()}>
            Add reviewer
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
