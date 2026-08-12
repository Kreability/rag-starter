import { getServerSession } from 'next-auth'
import Link from 'next/link'
import { redirect } from 'next/navigation'

import { authOptions } from '@/lib/auth'
import { assertDevModeAllowed, DEV_MODE } from '@/lib/token'
import { SignOutButton } from '@/components/forms/sign-out-button'

/**
 * Shell for the RAG pages, and the single place per-user access is enforced —
 * every page under this segment inherits the check.
 *
 * DEV_MODE skips it because the server signs in as a fixed account instead
 * (see `lib/token.ts`).
 */
export default async function RagLayout({
  children
}: {
  children: React.ReactNode
}) {
  // Fails loudly if DEV_MODE is on in production, so the bypass below can
  // never silently skip the login requirement on a real deployment.
  assertDevModeAllowed()

  const session = DEV_MODE ? null : await getServerSession(authOptions)

  if (!DEV_MODE && (!session || session.error)) {
    redirect('/login')
  }

  return (
    <div className="space-y-6">
      <nav className="flex items-center gap-1 border-b border-gray-200 pb-3">
        <NavLink href="/chat">Chat</NavLink>
        <NavLink href="/documents">Documents</NavLink>
        <div className="ml-auto flex items-center gap-3">
          {DEV_MODE ? (
            <span
              className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800"
              title="DEV_MODE is on: everyone shares one account. Unset DEV_MODE for per-user logins."
            >
              Dev mode
            </span>
          ) : (
            <>
              <span className="text-xs text-gray-500">{session?.user.username}</span>
              <SignOutButton />
            </>
          )}
        </div>
      </nav>
      {children}
    </div>
  )
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="rounded-lg px-3 py-1.5 text-sm text-gray-600 transition hover:bg-gray-100 hover:text-gray-900"
    >
      {children}
    </Link>
  )
}
