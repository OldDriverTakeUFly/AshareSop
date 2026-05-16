"use client";

import { useState, useEffect, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import { setCredentials } from "@/lib/api";
import { useHealth } from "@/lib/hooks";
import { Separator } from "@/components/ui/separator";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const NAV_ITEMS = [
  { label: "首页", href: "/" },
  { label: "涨停板", href: "/limit-up" },
  { label: "龙虎榜", href: "/dragon-tiger" },
  { label: "资金流向", href: "/fund-flow" },
  { label: "风险提示", href: "/risk-alert" },
  { label: "历史对比", href: "/compare" },
  { label: "投资SOP", href: "/invest-sop" },
  { label: "持仓管理", href: "/invest-sop/holdings" },
  { label: "历史图表", href: "/invest-sop/charts" },
  { label: "报告查看", href: "/invest-sop/reports" },
] as const;

function Sidebar() {
  const pathname = usePathname();
  const { data: health } = useHealth();

  const latestDate =
    health?.latest_dates?.limit_up_pool ??
    health?.latest_dates?.dragon_tiger_detail ??
    "—";

  return (
    <aside className="flex h-screen w-52 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2 px-5 py-5">
        <h1 className="text-lg font-bold tracking-tight text-sidebar-primary">
          StockHot
        </h1>
      </div>

      <Separator />

      <nav className="flex-1 overflow-y-auto px-3 py-3">
        <ul className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);

            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={`flex items-center rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
                  }`}
                >
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <Separator />

      <div className="px-5 py-4 text-xs text-sidebar-foreground/50">
        <span className="block">最新数据</span>
        <span className="block mt-0.5 font-mono">{latestDate}</span>
      </div>
    </aside>
  );
}

export default function RootLayout({
  children,
}: Readonly<{
  children: ReactNode;
}>) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 5 * 60 * 1000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      })
  );

  useEffect(() => {
    setCredentials({ username: "stockhot", password: "stockhot" });
  }, []);

  return (
    <html
      lang="zh-CN"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="h-full">
        <QueryClientProvider client={queryClient}>
          <div className="flex h-full">
            <Sidebar />
            <main className="flex-1 overflow-y-auto">
              <div className="p-6">{children}</div>
            </main>
          </div>
        </QueryClientProvider>
      </body>
    </html>
  );
}
