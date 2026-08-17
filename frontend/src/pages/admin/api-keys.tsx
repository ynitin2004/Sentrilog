import { AppShell } from '@/components/domain/app-shell'
import { ApiKeysTable } from '@/components/domain/admin-tables'
import { CreateKeyModal } from '@/components/domain/create-key-modal'
import { useToast } from '@/components/ui/toast'
import { useApiKeys } from '@/hooks/use-api'
import type { ApiKey } from '@/types/api'

export function AdminApiKeysPage() {
  const { data: keys, isLoading, create, revoke } = useApiKeys()
  const { toast } = useToast()

  const handleRevoke = (key: ApiKey) => {
    revoke(key.id)
    toast({ title: `Revoked "${key.name}"` })
  }

  return (
    <AppShell persona="admin">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-text text-xl font-semibold">API keys</h1>
            <p className="text-text-subtle text-sm">Used by your integration to submit cases.</p>
          </div>
          <CreateKeyModal onCreate={create} />
        </div>
        <ApiKeysTable keys={keys} isLoading={isLoading} onRevoke={handleRevoke} />
      </div>
    </AppShell>
  )
}
