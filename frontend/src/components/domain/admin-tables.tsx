import { KeyRound, Send, Users, Webhook as WebhookIcon } from 'lucide-react'
import { DataTable, type DataTableColumn } from '@/components/ui/data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { DeliveryStatusBadge } from '@/components/domain/status-badge'
import { RoleBadge } from '@/components/domain/role-badge'
import { formatDate, formatDateTime } from '@/lib/utils'
import type { ApiKey, Reviewer, Webhook, WebhookDelivery } from '@/types/api'

function RevokedIndicator({ revokedAt }: { revokedAt: string | null }) {
  return revokedAt ? (
    <Badge variant="danger">Revoked</Badge>
  ) : (
    <Badge variant="success">Active</Badge>
  )
}

export interface ApiKeysTableProps {
  keys: ApiKey[]
  isLoading?: boolean
  onRevoke: (key: ApiKey) => void
}

export function ApiKeysTable({ keys, isLoading = false, onRevoke }: ApiKeysTableProps) {
  const columns: DataTableColumn<ApiKey>[] = [
    { key: 'name', header: 'Name', render: (k) => <span className="font-medium">{k.name}</span> },
    {
      key: 'status',
      header: 'Status',
      render: (k) => <RevokedIndicator revokedAt={k.revoked_at} />,
    },
    { key: 'created', header: 'Created', render: (k) => formatDate(k.created_at) },
    {
      key: 'actions',
      header: '',
      headerClassName: 'w-24',
      render: (k) =>
        !k.revoked_at && (
          <Button variant="ghost" size="sm" onClick={() => onRevoke(k)}>
            Revoke
          </Button>
        ),
    },
  ]
  return (
    <DataTable
      columns={columns}
      rows={keys}
      getRowKey={(k) => k.id}
      isLoading={isLoading}
      emptyIcon={KeyRound}
      emptyTitle="No API keys yet"
      emptyDescription="Create a key to let your integration start submitting cases."
    />
  )
}

export interface ReviewersTableProps {
  reviewers: Reviewer[]
  isLoading?: boolean
  onRevoke: (reviewer: Reviewer) => void
}

export function ReviewersTable({ reviewers, isLoading = false, onRevoke }: ReviewersTableProps) {
  const columns: DataTableColumn<Reviewer>[] = [
    {
      key: 'email',
      header: 'Email',
      render: (r) => <span className="font-medium">{r.email}</span>,
    },
    { key: 'role', header: 'Role', render: (r) => <RoleBadge role={r.role} /> },
    {
      key: 'status',
      header: 'Status',
      render: (r) => <RevokedIndicator revokedAt={r.revoked_at} />,
    },
    { key: 'created', header: 'Added', render: (r) => formatDate(r.created_at) },
    {
      key: 'actions',
      header: '',
      headerClassName: 'w-24',
      render: (r) =>
        !r.revoked_at && (
          <Button variant="ghost" size="sm" onClick={() => onRevoke(r)}>
            Revoke
          </Button>
        ),
    },
  ]
  return (
    <DataTable
      columns={columns}
      rows={reviewers}
      getRowKey={(r) => r.id}
      isLoading={isLoading}
      emptyIcon={Users}
      emptyTitle="No reviewers yet"
      emptyDescription="Add a reviewer so someone can work the review queue."
    />
  )
}

export interface WebhooksTableProps {
  webhooks: Webhook[]
  isLoading?: boolean
  onDisable: (webhook: Webhook) => void
  onViewDeliveries: (webhook: Webhook) => void
}

export function WebhooksTable({
  webhooks,
  isLoading = false,
  onDisable,
  onViewDeliveries,
}: WebhooksTableProps) {
  const columns: DataTableColumn<Webhook>[] = [
    {
      key: 'url',
      header: 'Endpoint',
      render: (w) => <span className="font-mono text-xs">{w.url}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      render: (w) => <RevokedIndicator revokedAt={w.disabled_at} />,
    },
    { key: 'created', header: 'Registered', render: (w) => formatDate(w.created_at) },
    {
      key: 'actions',
      header: '',
      headerClassName: 'w-56',
      render: (w) => (
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={() => onViewDeliveries(w)}>
            View deliveries
          </Button>
          {!w.disabled_at && (
            <Button variant="ghost" size="sm" onClick={() => onDisable(w)}>
              Disable
            </Button>
          )}
        </div>
      ),
    },
  ]
  return (
    <DataTable
      columns={columns}
      rows={webhooks}
      getRowKey={(w) => w.id}
      isLoading={isLoading}
      emptyIcon={WebhookIcon}
      emptyTitle="No webhooks registered"
      emptyDescription="Register an endpoint to get notified the moment a case is decided, instead of polling."
    />
  )
}

export interface DeliveryLogTableProps {
  deliveries: WebhookDelivery[]
  isLoading?: boolean
}

export function DeliveryLogTable({ deliveries, isLoading = false }: DeliveryLogTableProps) {
  const columns: DataTableColumn<WebhookDelivery>[] = [
    {
      key: 'event',
      header: 'Event',
      render: (d) => <span className="font-mono text-xs">{d.event_type}</span>,
    },
    {
      key: 'case',
      header: 'Case',
      render: (d) => <span className="font-mono text-xs">{d.case_id.slice(0, 8)}…</span>,
    },
    { key: 'status', header: 'Status', render: (d) => <DeliveryStatusBadge status={d.status} /> },
    { key: 'attempts', header: 'Attempts', render: (d) => d.attempt_count },
    {
      key: 'last_attempted',
      header: 'Last attempted',
      render: (d) => (d.last_attempted_at ? formatDateTime(d.last_attempted_at) : '—'),
    },
  ]
  return (
    <DataTable
      columns={columns}
      rows={deliveries}
      getRowKey={(d) => d.id}
      isLoading={isLoading}
      emptyIcon={Send}
      emptyTitle="No deliveries yet"
      emptyDescription="Deliveries appear here after the first case decision fires this webhook."
    />
  )
}
