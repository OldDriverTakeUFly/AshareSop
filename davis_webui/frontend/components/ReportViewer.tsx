"use client";
import ReactMarkdown from "react-markdown";

export function ReportViewer({ markdown }: { markdown: string }) {
  return (
    <div className="prose prose-invert prose-sm max-w-none">
      <ReactMarkdown
        components={{
          h1: ({ children }) => (
            <h1 className="text-xl font-bold text-zinc-100 mt-4 mb-2">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-lg font-bold text-zinc-200 mt-4 mb-2">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-base font-semibold text-zinc-300 mt-3 mb-1">
              {children}
            </h3>
          ),
          table: ({ children }) => (
            <table className="border-collapse border border-zinc-600 my-2">
              {children}
            </table>
          ),
          th: ({ children }) => (
            <th className="border border-zinc-600 p-1 text-left text-zinc-300">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-zinc-600 p-1">{children}</td>
          ),
          li: ({ children }) => (
            <li className="text-zinc-300">{children}</li>
          ),
          p: ({ children }) => <p className="text-zinc-300 my-1">{children}</p>,
          strong: ({ children }) => (
            <strong className="text-zinc-100 font-bold">{children}</strong>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
