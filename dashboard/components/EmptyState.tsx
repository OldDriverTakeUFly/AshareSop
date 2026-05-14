"use client";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  message?: string;
  description?: string;
  className?: string;
}

export function EmptyState({
  message = "暂无数据",
  description = "非交易日或数据未更新",
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center py-12 text-center",
        className
      )}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className="mb-3 h-10 w-10 text-muted-foreground/40"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M20 13V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7m16 0v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-5m16 0h-2.586a1 1 0 0 0-.707.293l-2.414 2.414a1 1 0 0 1-.707.293h-3.172a1 1 0 0 1-.707-.293l-2.414-2.414A1 1 0 0 0 6.586 13H4"
        />
      </svg>
      <p className="text-lg text-muted-foreground">{message}</p>
      {description && (
        <p className="mt-1 text-sm text-muted-foreground/70">{description}</p>
      )}
    </div>
  );
}
