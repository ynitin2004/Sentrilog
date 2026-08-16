import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthGuard } from '@/components/domain/auth-guard'
import { ConnectPage } from '@/pages/connect'
import { ReviewerQueuePage } from '@/pages/reviewer/queue'
import { ReviewerCaseDetailPage } from '@/pages/reviewer/case-detail'
import { AdminOverviewPage } from '@/pages/admin/overview'
import { AdminCasesPage } from '@/pages/admin/cases'
import { AdminApiKeysPage } from '@/pages/admin/api-keys'
import { AdminWebhooksPage } from '@/pages/admin/webhooks'
import { AdminReviewersPage } from '@/pages/admin/reviewers'

export function App() {
  return (
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
  )
}
