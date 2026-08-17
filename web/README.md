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

Hybrid search over chunks by default — embeds `query` server-side (the
same `EmbeddingsProvider` `ingest/` used to embed the chunks — see
`src/lib/providers/embeddings.ts`), then fuses vector similarity with
Postgres full-text search via reciprocal rank fusion (the
`match_chunks_hybrid` RPC — `supabase/migrations/`), and returns ranked
chunks with their source metadata. No generation step — that's `/api/ask`.

```jsonc
// Request
{
  "query": "management commentary on margins",
  "symbol": "TCS",           // optional
  "doc_type": "concall",     // optional: annual_report | concall | announcement
  "period": "FY2025-26",     // optional
  "top_k": 10,                // optional, default HYBRID_TOP_K env (else 10)
  "mode": "hybrid"            // optional: "hybrid" (default) | "vector"
}
// Response
{ "query": "...", "mode": "hybrid", "results": [
  { "content": "...", "symbol": "TCS", "doc_type": "concall", "period": null,
    "page": 6, "source_url": "https://...", "score": 0.0164,
    "vector_rank": 3, "text_rank": 1 }
] }
```

`mode: "vector"` calls the earlier vector-only RPC (`match_chunks_filtered`)
instead, for comparison — `vector_rank`/`text_rank` are only present in
`hybrid` mode, and are `null` when a result came from only one of the two
channels. The fusion balance between the two channels
(`HYBRID_FUSION_WEIGHT`) is an env-only default, not a request field —
see `.env.example` and `ingest/NOTES.md`'s "Hybrid retrieval" section for
why a numbers-heavy query like "dividend per share" is exactly the case
hybrid mode was built for (vector embeddings tend to blur exact figures
together; full-text search doesn't).

`node scripts/test-search.mjs` hits it with five sample queries (one
unfiltered, one symbol-filtered, one doc_type+period-filtered, and the
same numbers-heavy query run under both `mode`s back to back) against a
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
