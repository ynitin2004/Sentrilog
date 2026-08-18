import * as React from 'react'
import { useNavigate } from 'react-router-dom'
import { ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { useAuth, type Persona } from '@/lib/auth-context'

export function ConnectPage() {
  const { connect } = useAuth()
  const navigate = useNavigate()
  const [apiBase, setApiBase] = React.useState('http://localhost:8000')
  const [token, setToken] = React.useState('')
  const [persona, setPersona] = React.useState<Persona>('reviewer')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!apiBase.trim() || !token.trim()) return
    connect({ apiBase: apiBase.trim().replace(/\/$/, ''), token: token.trim(), persona })
    navigate(persona === 'reviewer' ? '/reviewer/queue' : '/admin/overview')
  }

  return (
    <main className="bg-bg flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="flex-col items-start gap-1">
          <div className="flex items-center gap-2">
            <ShieldCheck className="text-brand h-5 w-5" aria-hidden="true" />
            <CardTitle>Sentrilog</CardTitle>
          </div>
          <CardDescription>Connect with an API key or reviewer token.</CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-4">
            <fieldset className="space-y-1.5">
              <legend className="text-text text-sm font-medium">I am a</legend>
              <div className="flex gap-2">
                {(['reviewer', 'admin'] as const).map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPersona(p)}
                    aria-pressed={persona === p}
                    className={
                      'flex-1 rounded-md border px-3 py-2 text-sm font-medium capitalize transition-colors ' +
                      (persona === p
                        ? 'border-brand bg-brand-bg text-brand-text'
                        : 'border-border text-text-muted hover:border-border-strong')
                    }
                  >
                    {p}
                  </button>
                ))}
              </div>
            </fieldset>
            <div className="space-y-1.5">
              <Label htmlFor="api-base">API base URL</Label>
              <Input
                id="api-base"
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
                placeholder="http://localhost:8000"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="token">{persona === 'reviewer' ? 'Reviewer token' : 'API key'}</Label>
              <Input
                id="token"
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="••••••••••••••••"
              />
            </div>
          </CardContent>
          <CardContent className="pt-0">
            <Button type="submit" className="w-full" disabled={!apiBase.trim() || !token.trim()}>
              Connect
            </Button>
          </CardContent>
        </form>
      </Card>
    </main>
  )
}
