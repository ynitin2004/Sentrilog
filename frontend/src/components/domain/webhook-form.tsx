import * as React from 'react'
import { Webhook } from 'lucide-react'
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

export interface WebhookFormProps {
  onCreate: (url: string) => Promise<void>
}

function isValidHttpsUrl(value: string): boolean {
  try {
    return new URL(value).protocol === 'https:'
  } catch {
    return false
  }
}

export function WebhookForm({ onCreate }: WebhookFormProps) {
  const [open, setOpen] = React.useState(false)
  const [url, setUrl] = React.useState('')
  const [creating, setCreating] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const handleSubmit = async () => {
    if (!isValidHttpsUrl(url)) {
      setError(
        'Enter a valid https:// URL -- webhook payloads carry case decisions and must not travel over plain HTTP.',
      )
      return
    }
    setError(null)
    setCreating(true)
    try {
      await onCreate(url)
      setOpen(false)
      setUrl('')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Webhook className="h-4 w-4" /> Register webhook
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Register a webhook</DialogTitle>
          <DialogDescription>
            Sentrilog POSTs an HMAC-signed <code className="font-mono">case.decided</code> event to
            this URL whenever a case is finalized.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="webhook-url">Endpoint URL</Label>
          <Input
            id="webhook-url"
            type="url"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              if (error) setError(null)
            }}
            placeholder="https://your-service.example.com/webhooks/sentrilog"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? 'webhook-url-error' : undefined}
            autoFocus
          />
          {error && (
            <p id="webhook-url-error" className="text-danger text-xs">
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button onClick={handleSubmit} loading={creating} disabled={!url}>
            Register
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
