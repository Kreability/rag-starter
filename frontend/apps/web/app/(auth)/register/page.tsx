import { RegisterForm } from '@/components/forms/register-form'

export const metadata = { title: 'Register · RAG System' }

export default function RegisterPage() {
  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-medium tracking-tight text-gray-900">
          Create an account
        </h1>
        <p className="mt-1 text-xs text-gray-600">
          Each account gets its own private knowledge base.
        </p>
      </header>

      <RegisterForm />
    </div>
  )
}
