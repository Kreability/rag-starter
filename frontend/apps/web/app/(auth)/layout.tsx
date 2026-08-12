import Link from 'next/link'

/**
 * Narrow, centred shell for the sign-in and registration pages.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-sm">
      <Link href="/" className="block text-center text-sm text-gray-500 hover:text-gray-900">
        RAG System
      </Link>
      <div className="mt-4 rounded-xl border border-gray-200 bg-white p-6">{children}</div>
    </div>
  )
}
