import { ChatPanel } from '@/components/chat/chat-panel'
import { listDocuments } from '@/lib/rag'

export const metadata = { title: 'Chat · RAG System' }

// Document state changes as ingestion completes, so never cache this page.
export const dynamic = 'force-dynamic'

export default async function ChatPage() {
  let readyCount = 0
  let unavailable = false

  try {
    const documents = await listDocuments()
    readyCount = documents.filter((document) => document.status === 'READY').length
  } catch {
    // The chat still works if this probe fails; only the hint is lost.
    unavailable = true
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-medium tracking-tight text-gray-900">Chat</h1>
        <p className="mt-1 text-sm text-gray-600">
          Answers are grounded in your documents and cite the passages they came from.
        </p>
      </header>

      {!unavailable && readyCount === 0 && (
        <p className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Your knowledge base is empty. Upload a document on the{' '}
          <a href="/documents" className="underline">
            Documents
          </a>{' '}
          page first.
        </p>
      )}

      <ChatPanel />
    </div>
  )
}
