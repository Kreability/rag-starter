'use client'

import { signOut } from 'next-auth/react'

export function SignOutButton() {
  return (
    <button
      type="button"
      onClick={() => signOut({ callbackUrl: '/login' })}
      className="rounded-lg px-2 py-1 text-xs text-gray-600 transition hover:bg-gray-100 hover:text-gray-900"
    >
      Sign out
    </button>
  )
}
