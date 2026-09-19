"use client";

import { useState, useMemo, type ReactNode } from "react";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Column definition
// ---------------------------------------------------------------------------

export interface RiskColumnDef<T> {
  key: keyof T;
  label: string;
  render?: (value: T[keyof T], row: T) => ReactNode;
  sortable?: boolean;
  numeric?: boolean;
  mono?: boolean;
}

// ---------------------------------------------------------------------------
// Risk severity
// ---------------------------------------------------------------------------

export type RiskLevel = "high" | "medium" | "info";

const rowColors: Record<RiskLevel, string> = {
  high: "bg-red-500/10 dark:bg-red-500/15",
  medium: "bg-orange-500/10 dark:bg-orange-500/15",
  info: "bg-muted/30",
};

// ---------------------------------------------------------------------------
// Sort helpers
// ---------------------------------------------------------------------------

type SortDir = "asc" | "desc";

function toggleSort(current: SortDir): SortDir {
  return current === "asc" ? "desc" : "asc";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface RiskTableProps<T> {
  data: T[];
  columns: RiskColumnDef<T>[];
  /** Maps each row to its risk level for row coloring. */
  getRiskLevel: (row: T, index: number) => RiskLevel;
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
}

export function RiskTable<T extends Record<string, unknown>>({
  data,
  columns,
  getRiskLevel,
  onRowClick,
  emptyMessage = "暂无风险数据",
}: RiskTableProps<T>) {
  const [sortKey, setSortKey] = useState<keyof T | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const sorted = useMemo(() => {
    if (!sortKey) return data;
    return [...data].sort((a, b) => {
      const va = a[sortKey];
      const vb = b[sortKey];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") {
        return sortDir === "asc" ? va - vb : vb - va;
      }
      const sa = String(va);
      const sb = String(vb);
      return sortDir === "asc"
        ? sa.localeCompare(sb)
        : sb.localeCompare(sa);
    });
  }, [data, sortKey, sortDir]);

  function handleSort(key: keyof T) {
    if (sortKey === key) {
      setSortDir(toggleSort(sortDir));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[72px]">风险</TableHead>
          {columns.map((col) => (
            <TableHead
              key={String(col.key)}
              className={[
                col.numeric ? "text-right" : "",
                col.sortable ? "cursor-pointer select-none hover:bg-muted/60" : "",
              ].join(" ")}
              onClick={col.sortable ? () => handleSort(col.key) : undefined}
            >
              <span className="inline-flex items-center gap-1">
                {col.label}
                {col.sortable && sortKey === col.key && (
                  <span className="text-[10px] text-muted-foreground">
                    {sortDir === "asc" ? "▲" : "▼"}
                  </span>
                )}
              </span>
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.length === 0 ? (
          <TableRow>
            <TableCell
              colSpan={columns.length + 1}
              className="h-24 text-center text-muted-foreground"
            >
              {emptyMessage}
            </TableCell>
          </TableRow>
        ) : (
          sorted.map((row, idx) => {
            const level = getRiskLevel(row, idx);
            return (
              <TableRow
                key={idx}
                className={cn(
                  onRowClick ? "cursor-pointer" : "",
                  rowColors[level]
                )}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
              >
                <TableCell className="text-center">
                  <span
                    className={cn(
                      "inline-block rounded px-1.5 py-0.5 text-xs font-medium",
                      level === "high" &&
                        "bg-red-500/20 text-red-700 dark:text-red-400",
                      level === "medium" &&
                        "bg-orange-500/20 text-orange-700 dark:text-orange-400",
                      level === "info" &&
                        "bg-muted text-muted-foreground"
                    )}
                  >
                    {level === "high" ? "高" : level === "medium" ? "中" : "低"}
                  </span>
                </TableCell>
                {columns.map((col) => {
                  const val = row[col.key];
                  return (
                    <TableCell
                      key={String(col.key)}
                      className={[
                        col.numeric ? "text-right font-mono tabular-nums" : "",
                        col.mono ? "font-mono" : "",
                      ].join(" ")}
                    >
                      {col.render
                        ? col.render(val, row)
                        : val != null
                          ? String(val)
                          : "—"}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}
