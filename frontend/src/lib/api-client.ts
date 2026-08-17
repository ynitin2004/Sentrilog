import type { Session } from '@/lib/auth-context'
import type { ApiError } from '@/types/api'

export class ApiRequestError extends Error {
  status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiRequestError'
    this.status = status
  }
}

/** Thrown specifically for 401s -- callers (the query client's global error handler) use this
 * to distinguish "the session itself is no longer valid" from every other error, which needs a
 * different response (disconnect + redirect to /connect) than a normal error toast. */
export class UnauthorizedError extends ApiRequestError {
  constructor(detail: string) {
    super(401, detail)
    this.name = 'UnauthorizedError'
  }
}

export interface ApiClient {
  get<T>(path: string): Promise<T>
  post<T>(path: string, body?: unknown): Promise<T>
}

/** A thin fetch wrapper, not a generated client -- the generated types (api-generated.ts)
 * cover the request/response shapes; this covers the one thing codegen doesn't: turning a
 * non-2xx response into a typed error carrying the backend's actual `detail` message, so a
 * toast can show "Case is not awaiting review (status: approved)" instead of a generic
 * "Something went wrong." */
export function createApiClient(session: Session): ApiClient {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${session.apiBase}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${session.token}`,
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })

    if (!response.ok) {
      let detail = response.statusText
      try {
        const body = (await response.json()) as ApiError | { detail: unknown }
        detail =
          typeof body.detail === 'string'
            ? body.detail
            : Array.isArray(body.detail)
              ? body.detail
                  .map((e) => (typeof e === 'object' && e && 'msg' in e ? e.msg : e))
                  .join('; ')
              : detail
      } catch {
        // Response body wasn't JSON (or was empty) -- fall back to statusText.
      }
      if (response.status === 401) throw new UnauthorizedError(detail)
      throw new ApiRequestError(response.status, detail)
    }

    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  return {
    get: (path) => request(path),
    post: (path, body) =>
      request(path, {
        method: 'POST',
        body: body !== undefined ? JSON.stringify(body) : undefined,
      }),
  }
}
