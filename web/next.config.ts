import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin the workspace root to this package: the monorepo has other
  // lockfiles (ingest/, and sibling projects) that Next.js would otherwise
  // try to infer as the root.
  turbopack: {
    root: path.join(__dirname),
  },
};

export default nextConfig;
