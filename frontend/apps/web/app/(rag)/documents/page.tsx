import { DocumentManager } from '@/components/documents/document-manager'
import { listDocuments, type RagDocument } from '@/lib/rag'

export const metadata = { title: 'Documents · RAG System' }

// Ingestion status changes out-of-band, so always render fresh.
export const dynamic = 'force-dynamic'

export default async function DocumentsPage() {
  let documents: RagDocument[] = []
  let error: string | null = null

  try {
    documents = await listDocuments()
  } catch (caught) {
    error = caught instanceof Error ? caught.message : 'Could not load documents.'
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-medium tracking-tight text-gray-900">Documents</h1>
        <p className="mt-1 text-sm text-gray-600">
          Everything here is indexed into your private knowledge base.
        </p>
      </header>

      {error ? (
        <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      ) : (
        <DocumentManager documents={documents} />
      )}
    </div>
  )
}
