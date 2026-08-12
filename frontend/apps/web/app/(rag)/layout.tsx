import Link from 'next/link'

/**
 * Shell for the RAG pages.
 *
 * Auth is intentionally absent for now: the server-side API layer signs in as a
 * fixed dev user (see `lib/token.ts`). To restore per-user access, reinstate a
 * session check here — every page under this segment inherits it.
 */
export default function RagLayout({
  children
}: {
  children: React.ReactNode
}) {
  return (
    <div className="space-y-6">
      <nav className="flex items-center gap-1 border-b border-gray-200 pb-3">
        <NavLink href="/chat">Chat</NavLink>
        <NavLink href="/documents">Documents</NavLink>
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
