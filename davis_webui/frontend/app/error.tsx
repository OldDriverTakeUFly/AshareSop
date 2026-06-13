"use client";
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-4">
      <h2 className="text-xl font-bold text-zinc-200">页面出错了</h2>
      <p className="text-sm text-zinc-500">{error.message}</p>
      <button
        onClick={reset}
        className="bg-blue-600 hover:bg-blue-500 px-4 py-1.5 rounded text-sm font-medium"
      >
        重试
      </button>
    </div>
  );
}
