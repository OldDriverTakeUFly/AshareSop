"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  message?: string;
  error?: Error | { message?: string } | null;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  message = "数据加载失败",
  error,
  onRetry,
  className,
}: ErrorStateProps) {
  const detail =
    error instanceof Error
      ? error.message
      : error && "message" in error
        ? error.message
        : null;

  return (
    <div
      className={cn(
        "rounded-xl border border-destructive/30 bg-destructive/5 p-6 text-center",
        className
      )}
    >
      <svg
        xmlns="http://www.w3.org/2000/svg"
        className="mx-auto mb-3 h-10 w-10 text-destructive/60"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={1.5}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
        />
      </svg>
      <p className="text-lg font-medium text-destructive">{message}</p>
      {detail && (
        <p className="mt-1 text-sm text-muted-foreground">{detail}</p>
      )}
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry} className="mt-4">
          重试
        </Button>
      )}
    </div>
  );
}
