/**
 * Server-side dev authentication.
 *
 * The login UI is removed, but the Django API still requires a JWT and still
 * scopes every document to an owner — so the server transparently signs in as a
 * fixed dev user and caches the token.
 *
 * This module is server-only: the credentials must never reach the browser.
 * To restore real per-user auth, delete this file and point `authHeaders()` in
 * `lib/rag.ts` back at the session.
 */

import 'server-only'

const API_URL = process.env.API_URL ?? 'http://api:8000'
const USERNAME = process.env.DEV_USERNAME ?? 'ragtester'
const PASSWORD = process.env.DEV_PASSWORD ?? ''

type CachedToken = { access: string; refresh: string; expiresAt: number }

// Module-scoped cache: one login per server process, not one per request.
let cached: CachedToken | null = null
let inFlight: Promise<CachedToken> | null = null

function decodeExpiry(accessToken: string): number {
  try {
    const payload = JSON.parse(
      Buffer.from(accessToken.split('.')[1], 'base64').toString('utf8')
    )
    // Refresh a minute early so a token cannot expire mid-request.
    return payload.exp * 1000 - 60_000
  } catch {
    return Date.now() + 4 * 60_000
  }
}

async function login(): Promise<CachedToken> {
  if (!PASSWORD) {
    throw new Error(
      'DEV_PASSWORD is not set in .env.frontend. Create the dev user with ' +
        '`make superuser` and set DEV_USERNAME / DEV_PASSWORD to match.'
    )
  }

  const response = await fetch(`${API_URL}/api/token/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
    cache: 'no-store'
  })

  if (!response.ok) {
    throw new Error(
      `Dev login failed as "${USERNAME}" (${response.status}). ` +
        'Check DEV_USERNAME / DEV_PASSWORD in .env.frontend.'
    )
  }

  const data = (await response.json()) as { access: string; refresh: string }
  return {
    access: data.access,
    refresh: data.refresh,
    expiresAt: decodeExpiry(data.access)
  }
}

async function refresh(token: CachedToken): Promise<CachedToken> {
  const response = await fetch(`${API_URL}/api/token/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh: token.refresh }),
    cache: 'no-store'
  })

  // Refresh tokens rotate and can be blacklisted; fall back to a fresh login.
  if (!response.ok) return login()

  const data = (await response.json()) as { access: string; refresh?: string }
  return {
    access: data.access,
    refresh: data.refresh ?? token.refresh,
    expiresAt: decodeExpiry(data.access)
  }
}

/** A valid access token, logging in or refreshing only when needed. */
export async function getAccessToken(): Promise<string> {
  if (cached && Date.now() < cached.expiresAt) {
    return cached.access
  }

  // Collapse concurrent misses into one request so a page with several
  // server components does not fire N logins.
  if (!inFlight) {
    const previous = cached
    inFlight = (previous ? refresh(previous) : login())
      .then((token) => {
        cached = token
        return token
      })
      .finally(() => {
        inFlight = null
      })
  }

  return (await inFlight).access
}

/** Drop the cached token, forcing a fresh login on the next call. */
export function clearToken(): void {
  cached = null
}
