'use client'

import Link from 'next/link'
import { useState, useTransition } from 'react'

import { registerAction } from '@/actions/register-action'

export function RegisterForm() {
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState(false)
  const [isPending, startTransition] = useTransition()

  const onSubmit = (formData: FormData) => {
    setError(null)
    startTransition(async () => {
      const result = await registerAction(formData)
      if (result.ok) setCreated(true)
      else setError(result.error)
    })
  }

  // Django creates accounts inactive, so there is nothing to sign in to yet.
  if (created) {
    return (
      <div className="space-y-3">
        <p className="rounded-lg bg-green-50 px-4 py-3 text-sm text-green-800">
          Account created. It still needs to be activated before you can sign in.
        </p>
        <p className="text-xs leading-relaxed text-gray-600">
          New accounts start deactivated. An administrator has to open the Django admin
          at <code className="text-gray-800">/admin/</code>, find your user and tick{' '}
          <span className="text-gray-800">Active</span>. Running this template locally?
          You are the administrator — sign in with your superuser account and activate
          it yourself.
        </p>
        <Link
          href="/login"
          className="inline-block rounded-lg bg-purple-600 px-4 py-2 text-sm text-white transition hover:bg-purple-700"
        >
          Go to sign in
        </Link>
      </div>
    )
  }

  return (
    <form action={onSubmit} className="space-y-3">
      {error && (
        <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <p className="rounded-lg bg-amber-50 px-4 py-3 text-xs leading-relaxed text-amber-800">
        New accounts are created deactivated and must be activated by an administrator
        in the Django admin before they can sign in.
      </p>

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
          minLength={8}
          autoComplete="new-password"
          className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
      </div>

      <div>
        <label htmlFor="password_retype" className="block text-xs text-gray-600">
          Repeat password
        </label>
        <input
          id="password_retype"
          name="password_retype"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
          className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
      </div>

      <button
        type="submit"
        disabled={isPending}
        className="w-full rounded-lg bg-purple-600 px-4 py-2 text-sm text-white transition hover:bg-purple-700 disabled:bg-gray-300"
      >
        {isPending ? 'Creating account…' : 'Create account'}
      </button>

      <p className="text-center text-xs text-gray-500">
        Already have an account?{' '}
        <Link href="/login" className="text-purple-700 underline">
          Sign in
        </Link>
      </p>
    </form>
  )
}
