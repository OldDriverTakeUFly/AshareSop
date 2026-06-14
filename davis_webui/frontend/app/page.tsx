import Link from "next/link";

export default function Home() {
  return (
    <div className="min-h-[calc(100vh-4rem)] flex items-center justify-center">
      <div className="w-full max-w-3xl">
        <h1 className="text-3xl font-bold text-center mb-2">投资分析方法论</h1>
        <p className="text-center text-zinc-400 mb-10">选择分析方法</p>
        <div className="grid grid-cols-2 gap-6">
          <Link
            href="/screening"
            className="bg-zinc-900 rounded-lg p-6 border border-zinc-800 hover:border-zinc-700 transition-colors"
          >
            <div className="text-4xl mb-4">📊</div>
            <h2 className="text-xl font-bold">戴维斯双击估值筛选</h2>
            <p className="text-sm text-zinc-400 mt-2">
              基于估值分位、景气度、困境信号的戴维斯双击选股体系
            </p>
          </Link>
          <Link
            href="/prosperity"
            className="bg-zinc-900 rounded-lg p-6 border border-zinc-800 hover:border-zinc-700 transition-colors"
          >
            <div className="text-4xl mb-4">🚀</div>
            <h2 className="text-xl font-bold">景气赛道分析</h2>
            <p className="text-sm text-zinc-400 mt-2">
              基于ΔG二阶导、G+ΔG二次点火、三阶段周期判断的景气赛道排序体系
            </p>
          </Link>
        </div>
      </div>
    </div>
  );
}
