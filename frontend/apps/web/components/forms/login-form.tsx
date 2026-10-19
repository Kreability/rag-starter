'use client'

import { signIn } from 'next-auth/react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { useState, useTransition } from 'react'

import { Alert, AlertDescription } from '@frontend/ui/components/alert'
import { Button } from '@frontend/ui/components/button'
import { Input } from '@frontend/ui/components/input'
import { Label } from '@frontend/ui/components/label'

export function LoginForm() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

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
        setError('Incorrect username or password — or the account has not been activated yet.')
        return
      }

      router.push(callbackUrl)
      router.refresh()
    })
  }

  return (
    <form action={onSubmit} className="space-y-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="space-y-1.5">
        <Label htmlFor="username">Username</Label>
        <Input
          id="username"
          name="username"
          type="text"
          required
          autoComplete="username"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="password">Password</Label>
        <Input
          id="password"
          name="password"
          type="password"
          required
          autoComplete="current-password"
        />
      </div>

      <Button type="submit" disabled={isPending} className="w-full">
        {isPending ? 'Signing in…' : 'Sign in'}
      </Button>

      <p className="text-center text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
        No account?{' '}
        <Link
          href="/register"
          className="underline"
          style={{ color: 'var(--color-primary)' }}
        >
          Register
        </Link>
      </p>
    </form>
  )
}
