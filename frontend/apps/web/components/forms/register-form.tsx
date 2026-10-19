'use client'

import Link from 'next/link'
import { useState, useTransition } from 'react'
import { registerAction } from '@/actions/register-action'
import { Alert, AlertDescription } from '@frontend/ui/components/alert'
import { Button } from '@frontend/ui/components/button'
import { Input } from '@frontend/ui/components/input'
import { Label } from '@frontend/ui/components/label'

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

  if (created) {
    return (
      <div className="space-y-4">
        <Alert>
          <AlertDescription>Account created. It still needs to be activated before you can sign in.</AlertDescription>
        </Alert>
        <p className="text-xs leading-relaxed" style={{ color: 'var(--color-muted-foreground)' }}>
          New accounts start deactivated. An administrator has to open the Django admin
          at <code style={{ color: 'var(--color-foreground)' }}>/admin/</code>, find
          your user and tick <span style={{ color: 'var(--color-foreground)' }}>Active</span>.
          Running this template locally? You are the administrator — sign in with your
          superuser account and activate it yourself.
        </p>
        <Button asChild>
          <Link href="/login">Go to sign in</Link>
        </Button>
      </div>
    )
  }

  return (
    <form action={onSubmit} className="space-y-4">
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Alert className="border-amber-400/20 bg-amber-400/10 text-xs leading-relaxed text-amber-400">
        <AlertDescription className="text-xs">
          New accounts are created deactivated and must be activated by an administrator
        in the Django admin before they can sign in.
        </AlertDescription>
      </Alert>

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
          minLength={8}
          autoComplete="new-password"
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="password_retype">Repeat password</Label>
        <Input
          id="password_retype"
          name="password_retype"
          type="password"
          required
          minLength={8}
          autoComplete="new-password"
        />
      </div>

      <Button type="submit" disabled={isPending} className="w-full">
        {isPending ? 'Creating account…' : 'Create account'}
      </Button>

      <p className="text-center text-xs" style={{ color: 'var(--color-muted-foreground)' }}>
        Already have an account?{' '}
        <Link
          href="/login"
          className="underline"
          style={{ color: 'var(--color-primary)' }}
        >
          Sign in
        </Link>
      </p>
    </form>
  )
}
