import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/components/Providers";
import Link from "next/link";

export const metadata: Metadata = {
  title: "戴维斯双击估值分析器",
  description: "Davis Double Play Valuation Analyzer WebUI",
};

const navItems = [
  { href: "/screening", label: "筛选结果", icon: "📊" },
  { href: "/distress", label: "困境热力图", icon: "🔥" },
  { href: "/research", label: "深度调研", icon: "🔬" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="bg-zinc-950 text-zinc-100 min-h-screen">
        <Providers>
          <div className="flex min-h-screen">
            <nav className="w-56 border-r border-zinc-800 p-4 space-y-2 fixed h-full">
              <h1 className="text-lg font-bold mb-6 text-blue-400">戴维斯双击</h1>
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="block px-3 py-2 rounded hover:bg-zinc-800 transition-colors text-sm"
                >
                  <span className="mr-2">{item.icon}</span>
                  {item.label}
                </Link>
              ))}
            </nav>
            <main className="flex-1 ml-56 p-8">{children}</main>
          </div>
        </Providers>
      </body>
    </html>
  );
}
