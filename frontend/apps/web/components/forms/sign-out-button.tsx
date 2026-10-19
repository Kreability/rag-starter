'use client'

import { signOut } from 'next-auth/react'

import { Button } from '@frontend/ui/components/button'

export function SignOutButton() {
  return (
    <Button type="button" variant="outline" size="sm" onClick={() => signOut({ callbackUrl: '/login' })}>
      Sign out
    </Button>
  )
}
