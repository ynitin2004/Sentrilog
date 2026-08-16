import { AppShell } from '@/components/domain/app-shell'
import { CasesTable } from '@/components/domain/cases-table'
import { useCases } from '@/hooks/use-mock-data'

export function AdminCasesPage() {
  const { data: cases, isLoading } = useCases()

  return (
    <AppShell persona="admin">
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-text text-xl font-semibold">Cases</h1>
          <p className="text-text-subtle text-sm">Every case submitted through your API keys.</p>
        </div>
        <CasesTable cases={cases} isLoading={isLoading} />
      </div>
    </AppShell>
  )
}
