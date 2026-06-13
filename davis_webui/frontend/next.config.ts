import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  outputFileTracingRoot: __dirname,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8322/api/:path*",
      },
    ];
  },
};

export default nextConfig;
