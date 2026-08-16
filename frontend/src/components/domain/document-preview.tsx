import * as React from 'react'
import { FileImage, ZoomIn } from 'lucide-react'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'

interface DocumentThumbnailProps {
  label: string
  url: string | null
}

function DocumentThumbnail({ label, url }: DocumentThumbnailProps) {
  const [open, setOpen] = React.useState(false)

  if (!url) {
    return (
      <div className="border-border bg-surface-raised text-text-subtle flex aspect-[4/3] flex-col items-center justify-center gap-2 rounded-lg border border-dashed">
        <FileImage className="h-6 w-6" aria-hidden="true" />
        <p className="text-xs">{label} not available</p>
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="group border-border relative aspect-[4/3] overflow-hidden rounded-lg border focus-visible:outline-2 focus-visible:outline-[var(--color-brand)]"
      >
        <img src={url} alt={label} className="h-full w-full object-cover" />
        <span className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition-opacity group-hover:bg-black/30 group-hover:opacity-100">
          <ZoomIn className="h-6 w-6 text-white" aria-hidden="true" />
        </span>
      </button>
      <DialogContent className="max-w-2xl">
        <DialogTitle>{label}</DialogTitle>
        <img src={url} alt={label} className="mt-2 w-full rounded-md" />
      </DialogContent>
    </Dialog>
  )
}

export function DocumentPreview({
  idDocumentUrl,
  selfieUrl,
}: {
  idDocumentUrl: string | null
  selfieUrl: string | null
}) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <div className="space-y-1.5">
        <p className="text-text-subtle text-xs font-medium">ID document</p>
        <DocumentThumbnail label="ID document" url={idDocumentUrl} />
      </div>
      <div className="space-y-1.5">
        <p className="text-text-subtle text-xs font-medium">Selfie</p>
        <DocumentThumbnail label="Selfie" url={selfieUrl} />
      </div>
    </div>
  )
}
