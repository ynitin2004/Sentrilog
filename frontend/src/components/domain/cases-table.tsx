import { FolderOpen } from 'lucide-react'
import { DataTable, type DataTableColumn } from '@/components/ui/data-table'
import { StatusBadge } from '@/components/domain/status-badge'
import { RiskScoreBar } from '@/components/domain/risk-score-gauge'
import { formatDate } from '@/lib/utils'
import type { Case } from '@/types/api'

export interface CasesTableProps {
  cases: Case[]
  isLoading?: boolean
  onSelect?: (caseItem: Case) => void
}

const columns: DataTableColumn<Case>[] = [
  {
    key: 'subject',
    header: 'Subject',
    render: (c) => <span className="font-medium">{c.subject_name}</span>,
  },
  { key: 'status', header: 'Status', render: (c) => <StatusBadge status={c.status} /> },
  { key: 'risk', header: 'Risk score', render: (c) => <RiskScoreBar score={c.risk_score} /> },
  { key: 'created', header: 'Created', render: (c) => formatDate(c.created_at) },
]

export function CasesTable({ cases, isLoading = false, onSelect }: CasesTableProps) {
  return (
    <DataTable
      columns={columns}
      rows={cases}
      getRowKey={(c) => c.case_id}
      isLoading={isLoading}
      emptyIcon={FolderOpen}
      emptyTitle="No cases yet"
      emptyDescription="Cases created through the intake API will appear here."
      onRowClick={onSelect}
      virtualized={cases.length > 50}
      rowHeight={52}
      maxHeight={560}
    />
  )
}
