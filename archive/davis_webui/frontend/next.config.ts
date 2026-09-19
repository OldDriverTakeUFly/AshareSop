import path from "path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.FASTAPI_URL || "http://localhost:8322"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
