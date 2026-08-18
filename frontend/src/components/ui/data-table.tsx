import { useRef } from 'react'
import type { ReactNode } from 'react'
import type React from 'react'
import type { LucideIcon } from 'lucide-react'
import { useVirtualizer } from '@tanstack/react-virtual'
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
  /** Renders only the rows near the viewport instead of every row in the DOM at once -- for
   * lists that can grow unbounded (all of a tenant's cases, an append-only delivery log), not
   * the small/bounded ones (a tenant's handful of API keys or reviewers), which stay on the
   * plain path below since virtualizing them would add complexity with no real benefit. */
  virtualized?: boolean
  /** Row height in px -- required by the virtualizer's size estimate, and used as this table's
   * fixed row height (virtualization needs a stable height to position absolutely, unlike the
   * plain path where rows can size to content). */
  rowHeight?: number
  /** Height of the scrollable viewport in px. */
  maxHeight?: number
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
  virtualized = false,
  rowHeight = 44,
  maxHeight = 480,
}: DataTableProps<T>) {
  if (!isLoading && rows.length === 0) {
    return <EmptyState icon={emptyIcon} title={emptyTitle} description={emptyDescription} />
  }

  if (virtualized && !isLoading) {
    return (
      <VirtualizedTableBody
        columns={columns}
        rows={rows}
        getRowKey={getRowKey}
        onRowClick={onRowClick}
        rowHeight={rowHeight}
        maxHeight={maxHeight}
      />
    )
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

interface VirtualizedTableBodyProps<T> {
  columns: DataTableColumn<T>[]
  rows: T[]
  getRowKey: (row: T) => string
  onRowClick?: (row: T) => void
  rowHeight: number
  maxHeight: number
}

/** A <table> can't be virtualized directly -- a virtualizer needs to absolutely position only
 * the rows near the viewport, which native table layout doesn't allow. This switches the table
 * itself to CSS grid/flex (the standard pattern for virtualizing tabular data) while keeping
 * explicit ARIA table/row/cell roles, since the browser's automatic table accessibility tree
 * only comes from real table/tr/td elements -- losing it here would be a real regression for
 * screen-reader users, not a cosmetic one. */
function VirtualizedTableBody<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  rowHeight,
  maxHeight,
}: VirtualizedTableBodyProps<T>) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })

  return (
    <div className="border-border overflow-hidden rounded-lg border">
      <div
        ref={scrollRef}
        role="table"
        style={{ maxHeight }}
        className="w-full overflow-auto text-left text-sm"
      >
        <div role="rowgroup" className="bg-surface-raised border-border sticky top-0 z-10 border-b">
          <div role="row" className="flex">
            {columns.map((col) => (
              <div
                key={col.key}
                role="columnheader"
                className={cn(
                  'text-text-subtle flex-1 px-4 py-2.5 text-xs font-medium tracking-wide uppercase',
                  col.headerClassName,
                )}
              >
                {col.header}
              </div>
            ))}
          </div>
        </div>
        <div
          role="rowgroup"
          className="divide-border relative divide-y"
          style={{ height: virtualizer.getTotalSize() }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = rows[virtualRow.index]
            if (!row) return null
            const key = getRowKey(row)
            return (
              <div
                key={key}
                role="row"
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(
                  'absolute top-0 left-0 flex w-full items-center transition-colors',
                  onRowClick && 'hover:bg-surface-raised cursor-pointer',
                )}
                style={{ height: virtualRow.size, transform: `translateY(${virtualRow.start}px)` }}
                tabIndex={onRowClick ? 0 : undefined}
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
                  <div
                    key={col.key}
                    role="cell"
                    className={cn('text-text flex-1 truncate px-4 py-3', col.className)}
                  >
                    {col.render(row)}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
