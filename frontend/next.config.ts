import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Lean production image for the Dockerfile: a self-contained
  // .next/standalone build instead of shipping full node_modules.
  output: "standalone",
};

export default nextConfig;
