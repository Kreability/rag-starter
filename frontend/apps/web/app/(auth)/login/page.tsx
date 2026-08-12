import { Suspense } from 'react'

import { LoginForm } from '@/components/forms/login-form'

export const metadata = { title: 'Sign in · RAG System' }

export default function LoginPage() {
  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-medium tracking-tight text-gray-900">Sign in</h1>
        <p className="mt-1 text-xs text-gray-600">
          Your documents and conversations are private to your account.
        </p>
      </header>

      {/* useSearchParams reads the callbackUrl, which requires a Suspense boundary. */}
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </div>
  )
}
