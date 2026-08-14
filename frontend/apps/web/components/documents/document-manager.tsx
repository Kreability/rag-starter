'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState, useTransition } from 'react'
import {
  deleteDocumentAction,
  ingestSourceAction,
  reindexDocumentAction,
  uploadDocumentAction
} from '@/actions/document-actions'
import type { DocumentStatus, RagDocument } from '@/lib/rag'

const STATUS_STYLES: Record<DocumentStatus, string> = {
  READY: 'bg-green-100 text-green-700',
  PROCESSING: 'bg-amber-100 text-amber-700',
  UPLOADING: 'bg-blue-100 text-blue-700',
  ERROR: 'bg-red-100 text-red-700'
}

export function DocumentManager({ documents }: { documents: RagDocument[] }) {
  const router = useRouter()
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

  // Ingestion is asynchronous, so poll while anything is still in flight.
  const hasPendingWork = documents.some(
    (document) => document.status === 'PROCESSING' || document.status === 'UPLOADING'
  )

  useEffect(() => {
    if (!hasPendingWork) return
    // Poll slow enough to stay far under the API's per-hour list throttle.
    const timer = setInterval(() => router.refresh(), 10000)
    return () => clearInterval(timer)
  }, [hasPendingWork, router])

  const run = (action: () => Promise<{ ok: boolean; error?: string }>) => {
    setError(null)
    startTransition(async () => {
      const result = await action()
      if (!result.ok) setError(result.error ?? 'Request failed.')
      else router.refresh()
    })
  }

  return (
    <div className="space-y-6">
      {error && (
        <p role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <UploadCard disabled={isPending} onSubmit={(formData) => run(() => uploadDocumentAction(formData))} />
        <SourceCard disabled={isPending} onSubmit={(formData) => run(() => ingestSourceAction(formData))} />
      </div>

      <DocumentTable
        documents={documents}
        disabled={isPending}
        onDelete={(id) => run(() => deleteDocumentAction(id))}
        onReindex={(id) => run(() => reindexDocumentAction(id))}
      />
    </div>
  )
}

function UploadCard({
  disabled,
  onSubmit
}: {
  disabled: boolean
  onSubmit: (formData: FormData) => void
}) {
  const formRef = useRef<HTMLFormElement>(null)

  return (
    <form
      ref={formRef}
      action={(formData) => {
        onSubmit(formData)
        formRef.current?.reset()
      }}
      className="rounded-xl border border-gray-200 bg-white p-5"
    >
      <h2 className="text-sm font-medium text-gray-900">Upload a file</h2>
      <p className="mt-1 text-xs text-gray-500">
        PDF, Word, PowerPoint, Excel, CSV, Markdown, HTML, EPUB or images. Up to 50 MB.
      </p>
      <input
        type="file"
        name="file"
        required
        aria-label="File to upload"
        accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md,.html,.htm,.xml,.json,.epub,.png,.jpg,.jpeg"
        className="mt-3 block w-full text-xs file:mr-3 file:rounded-lg file:border-0 file:bg-purple-50 file:px-3 file:py-2 file:text-xs file:text-purple-700 hover:file:bg-purple-100"
      />
      <button
        type="submit"
        disabled={disabled}
        className="mt-3 rounded-lg bg-purple-600 px-4 py-2 text-xs text-white transition hover:bg-purple-700 disabled:bg-gray-300"
      >
        Upload and index
      </button>
    </form>
  )
}

function SourceCard({
  disabled,
  onSubmit
}: {
  disabled: boolean
  onSubmit: (formData: FormData) => void
}) {
  const [sourceType, setSourceType] = useState<'URL' | 'SITEMAP' | 'CONFLUENCE'>('URL')
  const formRef = useRef<HTMLFormElement>(null)

  return (
    <form
      ref={formRef}
      action={(formData) => {
        onSubmit(formData)
        formRef.current?.reset()
      }}
      className="rounded-xl border border-gray-200 bg-white p-5"
    >
      <h2 className="text-sm font-medium text-gray-900">Ingest a source</h2>
      <p className="mt-1 text-xs text-gray-500">
        Crawl a web page, a whole sitemap, or a Confluence space.
      </p>

      <select
        name="source_type"
        value={sourceType}
        onChange={(event) => setSourceType(event.target.value as typeof sourceType)}
        aria-label="Source type"
        className="mt-3 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"
      >
        <option value="URL">Single web page</option>
        <option value="SITEMAP">Sitemap</option>
        <option value="CONFLUENCE">Confluence space</option>
      </select>

      <input
        type="url"
        name="source_uri"
        required
        placeholder="https://example.com/docs"
        aria-label="Source URL"
        className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"
      />

      {sourceType === 'CONFLUENCE' && (
        <input
          type="text"
          name="space_key"
          required
          placeholder="Space key (e.g. ENG)"
          aria-label="Confluence space key"
          className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-xs"
        />
      )}

      <button
        type="submit"
        disabled={disabled}
        className="mt-3 rounded-lg bg-purple-600 px-4 py-2 text-xs text-white transition hover:bg-purple-700 disabled:bg-gray-300"
      >
        Ingest
      </button>
    </form>
  )
}

function DocumentTable({
  documents,
  disabled,
  onDelete,
  onReindex
}: {
  documents: RagDocument[]
  disabled: boolean
  onDelete: (id: string) => void
  onReindex: (id: string) => void
}) {
  if (documents.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-gray-300 p-10 text-center">
        <p className="text-sm text-gray-500">No documents yet.</p>
        <p className="mt-1 text-xs text-gray-400">
          Upload a file or ingest a URL to build your knowledge base.
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      <table className="w-full text-left text-xs">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">Type</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Chunks</th>
            <th className="px-4 py-3 font-medium sr-only">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {documents.map((document) => (
            <tr key={document.id}>
              <td className="max-w-xs px-4 py-3">
                <p className="truncate text-gray-900">{document.name}</p>
                {document.error_message && (
                  <p className="mt-0.5 truncate text-[11px] text-red-600" title={document.error_message}>
                    {document.error_message}
                  </p>
                )}
              </td>
              <td className="px-4 py-3 text-gray-500">{document.source_type}</td>
              <td className="px-4 py-3">
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] ${STATUS_STYLES[document.status]}`}
                >
                  {document.status}
                </span>
              </td>
              <td className="px-4 py-3 text-gray-500">{document.chunk_count}</td>
              <td className="px-4 py-3">
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => onReindex(document.id)}
                    className="rounded px-2 py-1 text-[11px] text-gray-600 transition hover:bg-gray-100 disabled:opacity-50"
                  >
                    Re-index
                  </button>
                  <button
                    type="button"
                    disabled={disabled}
                    onClick={() => {
                      if (confirm(`Delete "${document.name}" and all of its chunks?`)) {
                        onDelete(document.id)
                      }
                    }}
                    className="rounded px-2 py-1 text-[11px] text-red-600 transition hover:bg-red-50 disabled:opacity-50"
                  >
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
