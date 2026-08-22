import type { Metadata } from 'next'
import { Geist, Inter } from 'next/font/google'

import { AuthProvider } from '@/providers/auth-provider'

import '@frontend/ui/styles/globals.css'

const geist = Geist({
  subsets: ['latin'],
  variable: '--font-geist'
})

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter'
})

export const metadata: Metadata = {
  title: 'RAG System — Django & Next.js',
  description:
    'Retrieval-augmented generation over your own documents, with citations.'
}

export default function RootLayout({
  children
}: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`dark ${geist.variable} ${inter.variable}`}>
      <body className="antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
