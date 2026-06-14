import Link from "next/link";

const navItems = [
  { href: "/screening", label: "筛选结果", icon: "📊" },
  { href: "/distress", label: "困境热力图", icon: "🔥" },
  { href: "/research", label: "深度调研", icon: "🔬" },
  { href: "/history", label: "历史记录", icon: "📜" },
];

export default function DavisLayout({ children }: { children: React.ReactNode }) {
  return (
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
  );
}
