"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useTriggerRefresh } from "@/lib/hooks";

interface TriggerButtonProps {
  date: string;
}

type FeedbackState = "idle" | "success" | "error";

export function TriggerButton({ date }: TriggerButtonProps) {
  const triggerRefresh = useTriggerRefresh();
  const [feedback, setFeedback] = useState<FeedbackState>("idle");

  useEffect(() => {
    if (feedback === "idle") return;
    const timer = setTimeout(() => setFeedback("idle"), 2000);
    return () => clearTimeout(timer);
  }, [feedback]);

  const handleClick = () => {
    triggerRefresh.mutate(date, {
      onSuccess: () => setFeedback("success"),
      onError: () => setFeedback("error"),
    });
  };

  const isPending = triggerRefresh.isPending;

  return (
    <Button
      onClick={handleClick}
      disabled={isPending || !date}
      variant={feedback === "error" ? "destructive" : "default"}
      size="default"
    >
      {isPending ? (
        <span className="inline-flex items-center gap-1.5">
          <svg
            className="size-3.5 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          刷新中…
        </span>
      ) : feedback === "success" ? (
        "✓ 已刷新"
      ) : feedback === "error" ? (
        "刷新失败"
      ) : (
        "刷新数据"
      )}
    </Button>
  );
}
