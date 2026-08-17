import * as React from 'react'
import { AppShell } from '@/components/domain/app-shell'
import { WebhooksTable, DeliveryLogTable } from '@/components/domain/admin-tables'
import { WebhookForm } from '@/components/domain/webhook-form'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
import { useToast } from '@/components/ui/toast'
import { useWebhookDeliveries, useWebhooks } from '@/hooks/use-api'
import type { Webhook } from '@/types/api'

export function AdminWebhooksPage() {
  const { data: webhooks, isLoading, create, disable } = useWebhooks()
  const { toast } = useToast()
  const [inspecting, setInspecting] = React.useState<Webhook | null>(null)
  const { data: deliveries, isLoading: deliveriesLoading } = useWebhookDeliveries(inspecting?.id)

  const handleDisable = (webhook: Webhook) => {
    disable(webhook.id)
    toast({ title: 'Webhook disabled' })
  }

  return (
    <AppShell persona="admin">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-text text-xl font-semibold">Webhooks</h1>
            <p className="text-text-subtle text-sm">
              Get notified the moment a case is decided, instead of polling.
            </p>
          </div>
          <WebhookForm onCreate={create} />
        </div>
        <WebhooksTable
          webhooks={webhooks}
          isLoading={isLoading}
          onDisable={handleDisable}
          onViewDeliveries={setInspecting}
        />
      </div>

      <Dialog open={inspecting !== null} onOpenChange={(open) => !open && setInspecting(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Delivery log</DialogTitle>
            <DialogDescription className="font-mono text-xs">{inspecting?.url}</DialogDescription>
          </DialogHeader>
          <DeliveryLogTable deliveries={deliveries} isLoading={deliveriesLoading} />
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}
