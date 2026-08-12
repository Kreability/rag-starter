'use client'

import { useState } from 'react'
import type { Citation } from '@/lib/rag'

/**
 * Sources behind an answer. Collapsed by default so long answers stay readable,
 * with the exact retrieved passage available on expand — that is what makes an
 * answer auditable rather than merely plausible.
 */
export function CitationList({ citations }: { citations: Citation[] }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/70">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-3 py-2 text-xs text-gray-600 transition hover:text-gray-900"
      >
        <span>
          {citations.length} source{citations.length === 1 ? '' : 's'}
        </span>
        <span aria-hidden className="text-gray-400">
          {expanded ? '−' : '+'}
        </span>
      </button>

      {expanded && (
        <ul className="space-y-2 border-t border-gray-200 p-3">
          {citations.map((citation, index) => (
            <li
              key={`${citation.document_id}-${index}`}
              className="rounded-md border border-gray-200 bg-white p-2.5"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-gray-900">
                    [{index + 1}] {citation.document_name || 'Untitled'}
                  </p>
                  <p className="mt-0.5 text-[11px] text-gray-500">
                    {citation.page && `Page ${citation.page} · `}
                    {citation.type}
                    {typeof citation.score === 'number' &&
                      ` · ${citation.score.toFixed(3)}`}
                  </p>
                </div>
                {citation.document_url && (
                  <a
                    href={citation.document_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 text-[11px] text-purple-600 underline"
                  >
                    Open
                  </a>
                )}
              </div>
              <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-[11px] leading-relaxed text-gray-600">
                {citation.page_content}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
