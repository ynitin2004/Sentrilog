import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FolderOpen } from 'lucide-react'
import { expectNoA11yViolations } from '@/test/a11y'
import { DataTable, type DataTableColumn } from './data-table'

// jsdom has no real layout engine -- every element reports a 0px size, which makes
// @tanstack/react-virtual compute an empty visible range and render zero rows. Real browsers
// (and the manual Playwright verification this virtualization was checked against) don't have
// this problem; this stub exists purely so the virtualizer has a non-zero viewport to measure
// against in tests.
beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    value: 300,
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    value: 300,
  })
})

interface Row {
  id: string
  label: string
}

const columns: DataTableColumn<Row>[] = [{ key: 'label', header: 'Label', render: (r) => r.label }]

function makeRows(count: number): Row[] {
  return Array.from({ length: count }, (_, i) => ({ id: `row-${i}`, label: `Row ${i}` }))
}

describe('DataTable (plain, non-virtualized)', () => {
  it('renders every row when not virtualized', () => {
    render(
      <DataTable
        columns={columns}
        rows={makeRows(10)}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="empty"
      />,
    )
    expect(screen.getAllByRole('row')).toHaveLength(11) // 10 body rows + 1 header row
  })

  it('shows the empty state when there are no rows', () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="Nothing here"
      />,
    )
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
  })

  it('has no accessibility violations', async () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={makeRows(5)}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="empty"
        onRowClick={() => {}}
      />,
    )
    await expectNoA11yViolations(container)
  })
})

describe('DataTable (virtualized)', () => {
  it('renders far fewer DOM rows than the full row count for a large list', () => {
    render(
      <DataTable
        columns={columns}
        rows={makeRows(500)}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="empty"
        virtualized
        rowHeight={44}
        maxHeight={300}
      />,
    )
    // jsdom has no real layout engine, so the scroll container reports a 0px viewport -- the
    // virtualizer still only renders its overscan window around that, which is the real thing
    // being asserted here: far fewer than 500 row elements exist in the DOM at once.
    const rows = screen.getAllByRole('row')
    expect(rows.length).toBeGreaterThan(0)
    expect(rows.length).toBeLessThan(500)
  })

  it('still fires onRowClick for a rendered virtualized row', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <DataTable
        columns={columns}
        rows={makeRows(200)}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="empty"
        virtualized
        onRowClick={onSelect}
      />,
    )
    const firstBodyRow = screen.getByRole('row', { name: 'Row 0' })
    await user.click(firstBodyRow)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'row-0' }))
  })

  it('preserves table/row/cell ARIA semantics despite the div-based markup', () => {
    render(
      <DataTable
        columns={columns}
        rows={makeRows(50)}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="empty"
        virtualized
      />,
    )
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Label' })).toBeInTheDocument()
    expect(screen.getAllByRole('cell').length).toBeGreaterThan(0)
  })

  it('has no accessibility violations', async () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={makeRows(100)}
        getRowKey={(r) => r.id}
        emptyIcon={FolderOpen}
        emptyTitle="empty"
        virtualized
        onRowClick={() => {}}
      />,
    )
    await expectNoA11yViolations(container)
  })
})
