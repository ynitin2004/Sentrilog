import type { ReactNode } from 'react'
import type React from 'react'
import type { LucideIcon } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { TableRowSkeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

export interface DataTableColumn<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
  className?: string
  headerClassName?: string
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[]
  rows: T[]
  getRowKey: (row: T) => string
  isLoading?: boolean
  emptyIcon: LucideIcon
  emptyTitle: string
  emptyDescription?: string
  onRowClick?: (row: T) => void
  loadingRowCount?: number
}

/** Generic, unopinionated-about-domain table -- every list screen (queue, cases, api keys,
 * webhooks, reviewers, delivery log) renders through this rather than five separate
 * hand-rolled <table> markups, so sorting/keyboard-nav/loading/empty behavior only needs to be
 * built and tested once. */
export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  isLoading = false,
  emptyIcon,
  emptyTitle,
  emptyDescription,
  onRowClick,
  loadingRowCount = 5,
}: DataTableProps<T>) {
  if (!isLoading && rows.length === 0) {
    return <EmptyState icon={emptyIcon} title={emptyTitle} description={emptyDescription} />
  }

  return (
    <div className="border-border overflow-x-auto rounded-lg border">
      <table className="w-full min-w-max text-left text-sm">
        <thead>
          <tr className="border-border bg-surface-raised border-b">
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={cn(
                  'text-text-subtle px-4 py-2.5 text-xs font-medium tracking-wide uppercase',
                  col.headerClassName,
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-border divide-y">
          {isLoading
            ? Array.from({ length: loadingRowCount }).map((_, i) => (
                <TableRowSkeleton key={i} columns={columns.length} />
              ))
            : rows.map((row) => (
                <tr
                  key={getRowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(
                    'transition-colors',
                    onRowClick && 'hover:bg-surface-raised cursor-pointer',
                  )}
                  tabIndex={onRowClick ? 0 : undefined}
                  role={onRowClick ? 'button' : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e: React.KeyboardEvent) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            onRowClick(row)
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((col) => (
                    <td key={col.key} className={cn('text-text px-4 py-3', col.className)}>
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              ))}
        </tbody>
      </table>
    </div>
  )
}
