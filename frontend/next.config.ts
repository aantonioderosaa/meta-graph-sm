import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
    NEXT_PUBLIC_USE_MOCK_EVENTS:
      process.env.NEXT_PUBLIC_USE_MOCK_EVENTS ?? "false",
  },
  transpilePackages: [
    "@neo4j-nvl/react",
    "@neo4j-nvl/base",
    "@neo4j-nvl/interaction-handlers",
    "@neo4j-nvl/layout-workers",
  ],
};

export default nextConfig;
