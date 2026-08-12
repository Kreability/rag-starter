import Link from 'next/link'

export default function Home() {
  return (
    <>
      <h1 className="text-xl font-medium tracking-tight text-gray-900">
        RAG System — Django &amp; Next.js
      </h1>

      <p className="mb-8 mt-2 max-w-3xl text-base leading-relaxed text-gray-600">
        A self-contained retrieval-augmented generation stack: upload documents, and
        ask questions that are answered from their contents with citations back to the
        exact passages. Hybrid vector search, cross-encoder reranking and full LLM
        tracing, all in one Django backend and one Next.js frontend.
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card
          href="/chat"
          title="Chat"
          description="Ask questions across your knowledge base with streamed, cited answers."
        />
        <Card
          href="/documents"
          title="Documents"
          description="Upload files or ingest web pages, sitemaps and Confluence spaces."
        />
      </div>
    </>
  )
}

function Card({
  href,
  title,
  description
}: {
  href: string
  title: string
  description: string
}) {
  return (
    <Link
      href={href}
      className="rounded-xl border border-gray-200 bg-white p-5 transition hover:border-purple-300 hover:shadow-sm"
    >
      <h2 className="text-sm font-medium text-gray-900">{title}</h2>
      <p className="mt-1 text-xs leading-relaxed text-gray-600">{description}</p>
    </Link>
  )
}
