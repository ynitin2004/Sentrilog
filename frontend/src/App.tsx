import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthGuard } from '@/components/domain/auth-guard'
import { PageLoadingFallback } from '@/components/domain/page-loading-fallback'
import { ConnectPage } from '@/pages/connect'

// Every screen behind AuthGuard is its own chunk -- a reviewer's first load doesn't need to
// download the admin console's code (API keys, webhooks, reviewer management), and vice versa.
// Only /connect stays eager: it's the one screen every session hits before we even know which
// persona's chunks are worth fetching.
const ReviewerQueuePage = lazy(() =>
  import('@/pages/reviewer/queue').then((m) => ({ default: m.ReviewerQueuePage })),
)
const ReviewerCaseDetailPage = lazy(() =>
  import('@/pages/reviewer/case-detail').then((m) => ({ default: m.ReviewerCaseDetailPage })),
)
const AdminOverviewPage = lazy(() =>
  import('@/pages/admin/overview').then((m) => ({ default: m.AdminOverviewPage })),
)
const AdminCasesPage = lazy(() =>
  import('@/pages/admin/cases').then((m) => ({ default: m.AdminCasesPage })),
)
const AdminApiKeysPage = lazy(() =>
  import('@/pages/admin/api-keys').then((m) => ({ default: m.AdminApiKeysPage })),
)
const AdminWebhooksPage = lazy(() =>
  import('@/pages/admin/webhooks').then((m) => ({ default: m.AdminWebhooksPage })),
)
const AdminReviewersPage = lazy(() =>
  import('@/pages/admin/reviewers').then((m) => ({ default: m.AdminReviewersPage })),
)

export function App() {
  return (
    <Suspense fallback={<PageLoadingFallback />}>
      <Routes>
        <Route path="/" element={<Navigate to="/connect" replace />} />
        <Route path="/connect" element={<ConnectPage />} />

        <Route
          path="/reviewer/queue"
          element={
            <AuthGuard persona="reviewer">
              <ReviewerQueuePage />
            </AuthGuard>
          }
        />
        <Route
          path="/reviewer/cases/:caseId"
          element={
            <AuthGuard persona="reviewer">
              <ReviewerCaseDetailPage />
            </AuthGuard>
          }
        />

        <Route
          path="/admin/overview"
          element={
            <AuthGuard persona="admin">
              <AdminOverviewPage />
            </AuthGuard>
          }
        />
        <Route
          path="/admin/cases"
          element={
            <AuthGuard persona="admin">
              <AdminCasesPage />
            </AuthGuard>
          }
        />
        <Route
          path="/admin/api-keys"
          element={
            <AuthGuard persona="admin">
              <AdminApiKeysPage />
            </AuthGuard>
          }
        />
        <Route
          path="/admin/webhooks"
          element={
            <AuthGuard persona="admin">
              <AdminWebhooksPage />
            </AuthGuard>
          }
        />
        <Route
          path="/admin/reviewers"
          element={
            <AuthGuard persona="admin">
              <AdminReviewersPage />
            </AuthGuard>
          }
        />

        <Route path="*" element={<Navigate to="/connect" replace />} />
      </Routes>
    </Suspense>
  )
}
