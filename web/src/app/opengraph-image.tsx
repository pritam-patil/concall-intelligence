import { ImageResponse } from "next/og";

export const alt =
  "Concall Intelligence — ask questions across Indian companies' annual reports and earnings calls, with page-level citations";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

// Static (no request-time APIs), so Next caches the rendered PNG. Uses the
// built-in default font — the OG card needs no brand face, and bundling one
// would add a fetch to every cold render.
export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "#0a0a0a",
          color: "#fafafa",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 10,
              background: "#fafafa",
              color: "#0a0a0a",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 30,
              fontWeight: 700,
              lineHeight: 1,
            }}
          >
            ”
          </div>
          <div style={{ fontSize: 28, letterSpacing: 4, color: "#a1a1aa" }}>
            CONCALL INTELLIGENCE
          </div>
        </div>
        <div
          style={{
            fontSize: 60,
            fontWeight: 600,
            lineHeight: 1.15,
            letterSpacing: -1,
            maxWidth: 1000,
          }}
        >
          Ask questions across Indian companies’ annual reports and earnings calls —
          with page-level citations.
        </div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 24,
            color: "#a1a1aa",
          }}
        >
          <span>NSE filings · refreshed nightly</span>
          <span>Informational only — not investment advice</span>
        </div>
      </div>
    ),
    size,
  );
}
