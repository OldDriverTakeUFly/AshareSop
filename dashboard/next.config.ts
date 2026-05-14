import path from "path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(__dirname, ".."),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8321/api/:path*",
      },
    ];
  },
};

export default nextConfig;
