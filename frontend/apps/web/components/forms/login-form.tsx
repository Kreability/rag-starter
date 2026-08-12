'use client'

import { signIn } from 'next-auth/react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useState, useTransition } from 'react'

export function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

  // Preserve the page the user was trying to reach before being bounced here.
  const callbackUrl = searchParams.get('callbackUrl') ?? '/chat'

  const onSubmit = (formData: FormData) => {
    setError(null)
    startTransition(async () => {
      const result = await signIn('credentials', {
        username: String(formData.get('username') ?? ''),
        password: String(formData.get('password') ?? ''),
        redirect: false
      })

      if (!result?.ok) {
        setError(
          'Incorrect username or password — or the account has not been activated yet.'
        )
        return
      }

      router.push(callbackUrl)
      // The session cookie is new; refresh so server components see it.
      router.refresh()
    })
  }

  return (
    <form action={onSubmit} className="space-y-3">
      {error && (
        <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div>
        <label htmlFor="username" className="block text-xs text-gray-600">
          Username
        </label>
        <input
          id="username"
          name="username"
          type="text"
          required
          autoComplete="username"
          className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
      </div>

      <div>
        <label htmlFor="password" className="block text-xs text-gray-600">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
          className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
      </div>

      <button
        type="submit"
        disabled={isPending}
        className="w-full rounded-lg bg-purple-600 px-4 py-2 text-sm text-white transition hover:bg-purple-700 disabled:bg-gray-300"
      >
        {isPending ? 'Signing in…' : 'Sign in'}
      </button>

      <p className="text-center text-xs text-gray-500">
        No account?{' '}
        <Link href="/register" className="text-purple-700 underline">
          Register
        </Link>
      </p>
    </form>
  )
}
