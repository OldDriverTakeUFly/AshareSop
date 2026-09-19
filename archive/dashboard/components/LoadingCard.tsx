"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface LoadingCardProps {
  className?: string;
}

export function LoadingCard({ className }: LoadingCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-6",
        className
      )}
    >
      <Skeleton className="mb-4 h-4 w-24" />
      <Skeleton className="h-8 w-20" />
    </div>
  );
}

export function PageSkeleton({
  cards = 0,
  rows = 0,
  charts = 0,
}: {
  cards?: number;
  rows?: number;
  charts?: number;
}) {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-4 w-32" />
      {cards > 0 && (
        <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${Math.min(cards, 3)}, 1fr)` }}>
          {Array.from({ length: cards }, (_, i) => (
            <Skeleton key={i} className="h-28 rounded-xl" />
          ))}
        </div>
      )}
      {Array.from({ length: charts }, (_, i) => (
        <Skeleton key={`chart-${i}`} className="h-[300px] rounded-xl" />
      ))}
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={`row-${i}`} className="h-48 rounded-xl" />
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-3">
      <Skeleton className="h-10 w-full rounded-md" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full rounded-md" />
      ))}
    </div>
  );
}
