"use client";

import { useState, type FormEvent } from "react";

import {
  useInvestHoldings,
  useCreateHoldingSimple,
  useAdjustHolding,
  useRemoveHolding,
} from "@/lib/hooks";
import type {
  InvestHolding,
  InvestHoldingCreateSimple,
  InvestHoldingAdjust,
} from "@/lib/types";

import {
  Card,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
  DialogClose,
} from "@/components/ui/dialog";
import { ErrorState } from "@/components/ErrorState";
import { EmptyState } from "@/components/EmptyState";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtPrice(v: number | null): string {
  if (v == null) return "-";
  return v.toFixed(2);
}

function fmtMoney(v: number): string {
  return v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pnlColor(val: number): string {
  if (val > 0) return "text-red-500";
  if (val < 0) return "text-emerald-500";
  return "text-muted-foreground";
}

function fmtPct(val: number): string {
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(2)}%`;
}

function fmtSignedMoney(val: number): string {
  const sign = val > 0 ? "+" : "";
  return `${sign}${fmtMoney(val)}`;
}

// ---------------------------------------------------------------------------
// Inner page
// ---------------------------------------------------------------------------

function HoldingsContent() {
  const { data: holdings, isLoading, error, refetch } = useInvestHoldings();
  const createHolding = useCreateHoldingSimple();
  const adjustHolding = useAdjustHolding();
  const removeHolding = useRemoveHolding();

  const [mutationError, setMutationError] = useState<string | null>(null);
  const clearError = () => setMutationError(null);

  const [addOpen, setAddOpen] = useState(false);
  const [adjustDialog, setAdjustDialog] = useState<{
    open: boolean;
    id: number;
    code: string;
    type: "buy" | "sell";
  }>({ open: false, id: 0, code: "", type: "buy" });
  const [deleteConfirm, setDeleteConfirm] = useState<{
    open: boolean;
    id: number;
  }>({ open: false, id: 0 });

  // ---------------------------------------------------------------------------
  // Loading / Error / Empty
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return (
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <ErrorState onRetry={() => refetch()} error={error} />
      </div>
    );
  }

  if (!holdings || holdings.length === 0) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold tracking-tight">持仓管理</h1>
          <Button onClick={() => setAddOpen(true)}>新增持仓</Button>
        </div>
        <EmptyState
          className="mt-12"
          message="暂无持仓"
          description="点击上方按钮添加您的第一条持仓记录"
        />
        <AddHoldingDialog
          open={addOpen}
          onOpenChange={setAddOpen}
          onSubmit={(data) => {
            createHolding.mutate(data, {
              onSuccess: () => { setAddOpen(false); clearError(); },
              onError: (err: Error) => { setMutationError(err.message || "操作失败"); },
            });
          }}
          pending={createHolding.isPending}
        />
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Data view
  // ---------------------------------------------------------------------------

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">持仓管理</h1>
        <Button onClick={() => setAddOpen(true)}>新增持仓</Button>
      </div>

      {mutationError && (
        <div className="flex items-center justify-between rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
          <span>{mutationError}</span>
          <button onClick={clearError} className="ml-4 text-red-500 hover:text-red-700">✕</button>
        </div>
      )}

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>代码</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>板块</TableHead>
                <TableHead className="text-right">持仓数量</TableHead>
                <TableHead className="text-right">成本价</TableHead>
                <TableHead className="text-right">现价</TableHead>
                <TableHead className="text-right">持仓市值</TableHead>
                <TableHead className="text-right">盈亏</TableHead>
                <TableHead className="text-right">盈亏%</TableHead>
                <TableHead className="text-right">止损价</TableHead>
                <TableHead className="text-right">目标价</TableHead>
                <TableHead className="text-right">仓位%</TableHead>
                <TableHead>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map((h) => {
                const qty = h.quantity ?? 0;
                const avgCost = h.avg_cost ?? h.entry_price;
                const currentPrice = h.current_price;
                const marketValue = qty * (currentPrice ?? 0);
                const pnl = avgCost != null && currentPrice != null
                  ? (currentPrice - avgCost) * qty
                  : null;
                const pnlPct = avgCost != null && currentPrice != null && avgCost > 0
                  ? ((currentPrice - avgCost) / avgCost) * 100
                  : null;

                return (
                  <TableRow key={h.id}>
                    <TableCell className="font-mono text-xs">
                      {h.code}
                    </TableCell>
                    <TableCell>{h.name ?? "-"}</TableCell>
                    <TableCell>{h.sector ?? "-"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {qty.toLocaleString("zh-CN")}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(avgCost)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(currentPrice)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {currentPrice != null ? fmtMoney(marketValue) : "-"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {pnl != null ? (
                        <span className={pnlColor(pnl)}>{fmtSignedMoney(pnl)}</span>
                      ) : "-"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {pnlPct != null ? (
                        <span className={pnlColor(pnlPct)}>{fmtPct(pnlPct)}</span>
                      ) : "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(h.stop_loss_hard)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(h.target_price)}
                    </TableCell>
                    <TableCell className="text-right">
                      {h.position_pct != null
                        ? `${h.position_pct.toFixed(1)}%`
                        : "-"}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            setAdjustDialog({ open: true, id: h.id, code: h.code, type: "buy" })
                          }
                        >
                          加仓
                        </Button>
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            setAdjustDialog({ open: true, id: h.id, code: h.code, type: "sell" })
                          }
                        >
                          减仓
                        </Button>
                        <Button
                          variant="destructive"
                          size="xs"
                          onClick={() =>
                            setDeleteConfirm({ open: true, id: h.id })
                          }
                        >
                          清仓
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <AddHoldingDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onSubmit={(data) => {
          createHolding.mutate(data, {
            onSuccess: () => { setAddOpen(false); clearError(); },
            onError: (err: Error) => { setMutationError(err.message || "操作失败"); },
          });
        }}
        pending={createHolding.isPending}
      />

      <AdjustDialog
        open={adjustDialog.open}
        onOpenChange={(open) =>
          setAdjustDialog({ ...adjustDialog, open })
        }
        code={adjustDialog.code}
        adjustType={adjustDialog.type}
        onSubmit={(data) => {
          adjustHolding.mutate(
            { id: adjustDialog.id, data },
            {
              onSuccess: () => {
                setAdjustDialog({ ...adjustDialog, open: false });
                clearError();
              },
              onError: (err: Error) => { setMutationError(err.message || "操作失败"); },
            }
          );
        }}
        pending={adjustHolding.isPending}
      />

      <Dialog
        open={deleteConfirm.open}
        onOpenChange={(open) => setDeleteConfirm({ open, id: deleteConfirm.id })}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>确认清仓</DialogTitle>
            <DialogDescription>
              确定要清仓此持仓吗？此操作不可撤销。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              取消
            </DialogClose>
            <Button
              variant="destructive"
              disabled={removeHolding.isPending}
              onClick={() => {
                removeHolding.mutate(deleteConfirm.id, {
                  onSuccess: () => {
                    setDeleteConfirm({ open: false, id: deleteConfirm.id });
                    clearError();
                  },
                  onError: (err: Error) => { setMutationError(err.message || "操作失败"); },
                });
              }}
            >
              {removeHolding.isPending ? "清仓中…" : "确认清仓"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add Holding Dialog (simplified — 3 fields)
// ---------------------------------------------------------------------------

function AddHoldingDialog({
  open,
  onOpenChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: InvestHoldingCreateSimple) => void;
  pending: boolean;
}) {
  const [code, setCode] = useState("");
  const [quantity, setQuantity] = useState("");
  const [entryPrice, setEntryPrice] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      code,
      quantity: parseInt(quantity, 10),
      ...(entryPrice ? { entry_price: parseFloat(entryPrice) } : {}),
    });
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setCode("");
      setQuantity("");
      setEntryPrice("");
    }
    onOpenChange(nextOpen);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新增持仓</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid gap-3">
          <div className="grid gap-1">
            <label className="text-sm font-medium">股票代码 *</label>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="如 600519"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">持仓数量 *</label>
            <Input
              type="number"
              step="1"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="如 1000"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">入场价</label>
            <Input
              type="number"
              step="0.01"
              value={entryPrice}
              onChange={(e) => setEntryPrice(e.target.value)}
              placeholder="可选，不填则用当天收盘价"
            />
          </div>
          <DialogFooter showCloseButton>
            <Button type="submit" disabled={pending}>
              {pending ? "提交中…" : "确认添加"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Adjust Dialog (buy / sell)
// ---------------------------------------------------------------------------

function AdjustDialog({
  open,
  onOpenChange,
  code,
  adjustType,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  code: string;
  adjustType: "buy" | "sell";
  onSubmit: (data: InvestHoldingAdjust) => void;
  pending: boolean;
}) {
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [notes, setNotes] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      type: adjustType,
      quantity: parseInt(quantity, 10),
      price: parseFloat(price),
      ...(notes ? { notes } : {}),
    });
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setQuantity("");
      setPrice("");
      setNotes("");
    }
    onOpenChange(nextOpen);
  }

  const isBuy = adjustType === "buy";

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {isBuy ? "加仓" : "减仓"} — {code}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid gap-3">
          <div className="grid gap-1">
            <label className="text-sm font-medium">数量 *</label>
            <Input
              type="number"
              step="1"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="如 1000"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">价格 *</label>
            <Input
              type="number"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="0.00"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">备注</label>
            <Input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="可选"
            />
          </div>
          <DialogFooter showCloseButton>
            <Button type="submit" disabled={pending}>
              {pending ? "提交中…" : "确认"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function HoldingsPage() {
  return <HoldingsContent />;
}
