This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

Needs `.env.local` (copy from `.env.example`) — Supabase URL/service-role
key, and Cloudflare account ID/API token for the embeddings provider. See
`../ARCHITECTURE.md` for the overall design.

## API routes

### `POST /api/search`

Plain semantic search over chunks — embeds `query` server-side (the same
`EmbeddingsProvider` `ingest/` used to embed the chunks — see
`src/lib/providers/embeddings.ts`), runs cosine top-k via the
`match_chunks_filtered` RPC (`supabase/migrations/`), and returns ranked
chunks with their source metadata. No generation step — that's `/api/ask`.

```jsonc
// Request
{
  "query": "management commentary on margins",
  "symbol": "TCS",           // optional
  "doc_type": "concall",     // optional: annual_report | concall | announcement
  "period": "FY2025-26",     // optional
  "top_k": 10                // optional, default 10
}
// Response
{ "query": "...", "results": [
  { "content": "...", "symbol": "TCS", "doc_type": "concall", "period": null,
    "page": 6, "source_url": "https://...", "score": 0.7246 }
] }
```

`node scripts/test-search.mjs` hits it with three sample queries (one
unfiltered, one symbol-filtered, one doc_type+period-filtered) against a
running dev server — see that file for exactly what it checks, and
`ingest/NOTES.md` for a real, populated dataset this has actually been
run against.

### `POST /api/ask`

Source-cited Q&A — the same retrieval, plus a generation step that
answers strictly from the retrieved chunks. See `src/app/api/ask/route.ts`.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
