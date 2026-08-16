import * as React from 'react'
import { Copy, KeyRound, Check } from 'lucide-react'
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
import { copyToClipboard } from '@/lib/utils'
import type { ApiKeyCreateResponse } from '@/types/api'

export interface CreateKeyModalProps {
  onCreate: (name: string) => Promise<ApiKeyCreateResponse>
}

/** Mirrors the real one-time-reveal semantics scripts/seed_dev_tenant.py already has: the raw
 * key is shown exactly once, in this dialog, and is never retrievable again afterward -- not
 * even by this same UI. Closing the dialog is the point of no return, so the copy affordance
 * is front and center rather than an afterthought. */
export function CreateKeyModal({ onCreate }: CreateKeyModalProps) {
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState('')
  const [creating, setCreating] = React.useState(false)
  const [created, setCreated] = React.useState<ApiKeyCreateResponse | null>(null)
  const [copied, setCopied] = React.useState(false)

  const reset = () => {
    setName('')
    setCreated(null)
    setCopied(false)
  }

  const handleCreate = async () => {
    if (!name.trim()) return
    setCreating(true)
    try {
      const key = await onCreate(name.trim())
      setCreated(key)
    } finally {
      setCreating(false)
    }
  }

  const handleCopy = async () => {
    if (!created) return
    const ok = await copyToClipboard(created.raw_key)
    setCopied(ok)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <KeyRound className="h-4 w-4" /> New API key
        </Button>
      </DialogTrigger>
      <DialogContent>
        {!created ? (
          <>
            <DialogHeader>
              <DialogTitle>Create an API key</DialogTitle>
              <DialogDescription>
                Used by your integration to create cases via the intake API.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="production-intake"
                autoFocus
              />
            </div>
            <DialogFooter>
              <Button onClick={handleCreate} loading={creating} disabled={!name.trim()}>
                Create key
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Key created</DialogTitle>
              <DialogDescription>
                Copy this key now -- it will not be shown again after you close this dialog.
              </DialogDescription>
            </DialogHeader>
            <div className="border-border bg-surface-raised flex items-center gap-2 rounded-md border p-3">
              <code className="text-text flex-1 truncate font-mono text-sm">{created.raw_key}</code>
              <Button variant="secondary" size="icon" onClick={handleCopy} aria-label="Copy key">
                {copied ? (
                  <Check className="text-status-approved h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
            </div>
            <DialogFooter>
              <Button variant="secondary" onClick={() => setOpen(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
