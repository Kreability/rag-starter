'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Citation } from '@/lib/rag'
import { CitationList } from './citation-list'
import { Markdown } from './markdown'

type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  error?: boolean
}

type StreamEvent =
  | { type: 'citations'; citations: Citation[] }
  | { type: 'token'; token: string }
  | { type: 'done'; finish_reason: string }
  | { type: 'error'; message: string }

export function ChatPanel({ documentId }: { documentId?: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const conversationId = useRef<string | null>(null)
  const scrollAnchor = useRef<HTMLDivElement>(null)
  const abortController = useRef<AbortController | null>(null)

  useEffect(() => {
    scrollAnchor.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Abort an in-flight stream if the component unmounts mid-answer.
  useEffect(() => () => abortController.current?.abort(), [])

  const send = useCallback(async () => {
    const question = input.trim()
    if (!question || isStreaming) return

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: question,
      citations: []
    }
    const assistantId = crypto.randomUUID()

    setMessages((current) => [
      ...current,
      userMessage,
      { id: assistantId, role: 'assistant', content: '', citations: [] }
    ])
    setInput('')
    setIsStreaming(true)

    const controller = new AbortController()
    abortController.current = controller

    const patchAssistant = (patch: Partial<ChatMessage>) =>
      setMessages((current) =>
        current.map((message) =>
          message.id === assistantId ? { ...message, ...patch } : message
        )
      )

    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          message: question,
          conversation_id: conversationId.current,
          document_id: documentId ?? null
        })
      })

      if (!response.ok || !response.body) {
        const body = await response.json().catch(() => ({}))
        throw new Error(body.error ?? 'The assistant is unavailable.')
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let answer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        // SSE frames are separated by a blank line; the tail may be partial.
        const frames = buffer.split('\n\n')
        buffer = frames.pop() ?? ''

        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '))
          if (!line) continue

          let event: StreamEvent
          try {
            event = JSON.parse(line.slice(6))
          } catch {
            continue
          }

          if (event.type === 'token') {
            answer += event.token
            patchAssistant({ content: answer })
          } else if (event.type === 'citations') {
            patchAssistant({ citations: event.citations })
          } else if (event.type === 'error') {
            patchAssistant({ content: event.message, error: true })
          }
        }
      }
    } catch (error) {
      if ((error as Error).name === 'AbortError') return
      patchAssistant({
        content: (error as Error).message || 'Something went wrong.',
        error: true
      })
    } finally {
      setIsStreaming(false)
      abortController.current = null
    }
  }, [input, isStreaming, documentId])

  const reset = () => {
    abortController.current?.abort()
    conversationId.current = null
    setMessages([])
  }

  return (
    <div className="flex h-[calc(100vh-16rem)] flex-col rounded-xl border border-gray-200 bg-white">
      <header className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
        <div>
          <h2 className="text-sm font-medium text-gray-900">Ask your documents</h2>
          <p className="text-xs text-gray-500">
            {documentId ? 'Scoped to one document' : 'Searching your whole knowledge base'}
          </p>
        </div>
        {messages.length > 0 && (
          <button
            type="button"
            onClick={reset}
            className="rounded-lg px-3 py-1.5 text-xs text-gray-600 transition hover:bg-gray-100"
          >
            New chat
          </button>
        )}
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
        {messages.length === 0 && <EmptyState />}
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            message={message}
            isStreaming={
              isStreaming &&
              message.role === 'assistant' &&
              message.id === messages.at(-1)?.id
            }
          />
        ))}
        <div ref={scrollAnchor} />
      </div>

      <footer className="border-t border-gray-200 p-4">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends; Shift+Enter inserts a newline.
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void send()
              }
            }}
            rows={1}
            placeholder="Ask a question about your documents…"
            aria-label="Your question"
            disabled={isStreaming}
            className="max-h-40 flex-1 resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none transition focus:border-purple-500 focus:ring-1 focus:ring-purple-500 disabled:bg-gray-50"
          />
          <button
            type="button"
            onClick={() => void send()}
            disabled={isStreaming || !input.trim()}
            className="rounded-lg bg-purple-600 px-4 py-2 text-sm text-white transition hover:bg-purple-700 disabled:cursor-not-allowed disabled:bg-gray-300"
          >
            {isStreaming ? 'Thinking…' : 'Send'}
          </button>
        </div>
      </footer>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center text-center">
      <p className="text-sm text-gray-500">No messages yet.</p>
      <p className="mt-1 max-w-sm text-xs text-gray-400">
        Upload a document first, then ask anything about it. Answers cite the exact
        passages they came from.
      </p>
    </div>
  )
}

function MessageBubble({
  message,
  isStreaming
}: {
  message: ChatMessage
  isStreaming: boolean
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-purple-600 px-4 py-2.5 text-sm text-white">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] space-y-3">
        <div
          className={`rounded-2xl rounded-bl-sm px-4 py-2.5 text-sm ${
            message.error ? 'bg-red-50 text-red-700' : 'bg-gray-100 text-gray-800'
          }`}
        >
          {message.content ? (
            <Markdown content={message.content} />
          ) : (
            <TypingIndicator />
          )}
          {isStreaming && message.content && (
            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-gray-400 align-middle" />
          )}
        </div>
        {message.citations.length > 0 && <CitationList citations={message.citations} />}
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <span className="flex gap-1" aria-label="Assistant is typing">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  )
}
