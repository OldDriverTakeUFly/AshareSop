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

// ---------------------------------------------------------------------------
// Column definition
// ---------------------------------------------------------------------------

export interface ColumnDef<T> {
  key: keyof T;
  label: string;
  render?: (value: T[keyof T], row: T) => ReactNode;
  sortable?: boolean;
  /** Right-align the column (numbers, amounts). */
  numeric?: boolean;
  /** Apply monospace font (stock codes). */
  mono?: boolean;
}

// ---------------------------------------------------------------------------
// Sort direction
// ---------------------------------------------------------------------------

type SortDir = "asc" | "desc";

function toggleSort(current: SortDir): SortDir {
  return current === "asc" ? "desc" : "asc";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface StockTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  onRowClick?: (row: T) => void;
  /** Empty-state message. */
  emptyMessage?: string;
}

export function StockTable<T extends Record<string, unknown>>({
  data,
  columns,
  onRowClick,
  emptyMessage = "暂无数据",
}: StockTableProps<T>) {
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
              colSpan={columns.length}
              className="h-24 text-center text-muted-foreground"
            >
              {emptyMessage}
            </TableCell>
          </TableRow>
        ) : (
          sorted.map((row, idx) => (
            <TableRow
              key={idx}
              className={onRowClick ? "cursor-pointer" : ""}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
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
          ))
        )}
      </TableBody>
    </Table>
  );
}
