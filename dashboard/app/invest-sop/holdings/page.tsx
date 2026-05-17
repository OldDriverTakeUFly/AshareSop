"use client";

import { useState, type FormEvent } from "react";

import {
  useInvestHoldings,
  useCreateHolding,
  useUpdateHoldingPrice,
  useUpdateHoldingStoploss,
  useRemoveHolding,
} from "@/lib/hooks";
import type {
  InvestHolding,
  InvestHoldingCreate,
  InvestHoldingUpdatePrice,
  InvestHoldingUpdateStoploss,
} from "@/lib/types";

import {
  Card,
  CardHeader,
  CardContent,
  CardTitle,
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

function pnlPct(entry: number | null, current: number | null): number | null {
  if (entry == null || current == null || entry === 0) return null;
  return ((current - entry) / entry) * 100;
}

function fmtPrice(v: number | null): string {
  if (v == null) return "-";
  return v.toFixed(2);
}

function pnlColor(val: number): string {
  if (val > 0) return "text-red-500";
  if (val < 0) return "text-emerald-500";
  return "text-muted-foreground";
}

function fmtPnl(val: number): string {
  const sign = val > 0 ? "+" : "";
  return `${sign}${val.toFixed(2)}%`;
}

// ---------------------------------------------------------------------------
// Inner page (needs QueryClient context)
// ---------------------------------------------------------------------------

function HoldingsContent() {
  const { data: holdings, isLoading, error, refetch } = useInvestHoldings();
  const createHolding = useCreateHolding();
  const updatePrice = useUpdateHoldingPrice();
  const updateStoploss = useUpdateHoldingStoploss();
  const removeHolding = useRemoveHolding();

  const [mutationError, setMutationError] = useState<string | null>(null);
  const clearError = () => setMutationError(null);

  // Dialog state
  const [addOpen, setAddOpen] = useState(false);
  const [priceDialog, setPriceDialog] = useState<{ open: boolean; id: number }>({
    open: false,
    id: 0,
  });
  const [stoplossDialog, setStoplossDialog] = useState<{
    open: boolean;
    id: number;
  }>({ open: false, id: 0 });
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
                <TableHead className="text-right">买入价</TableHead>
                <TableHead className="text-right">现价</TableHead>
                <TableHead className="text-right">盈亏%</TableHead>
                <TableHead className="text-right">止损(逻辑)</TableHead>
                <TableHead className="text-right">止损(技术)</TableHead>
                <TableHead className="text-right">止损(硬)</TableHead>
                <TableHead className="text-right">目标价</TableHead>
                <TableHead className="text-right">仓位%</TableHead>
                <TableHead>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {holdings.map((h) => {
                const pnl = pnlPct(h.entry_price, h.current_price);
                return (
                  <TableRow key={h.id}>
                    <TableCell className="font-mono text-xs">
                      {h.code}
                    </TableCell>
                    <TableCell>{h.name ?? "-"}</TableCell>
                    <TableCell>{h.sector ?? "-"}</TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(h.entry_price)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(h.current_price)}
                    </TableCell>
                    <TableCell className="text-right">
                      {pnl != null ? (
                        <span className={pnlColor(pnl)}>{fmtPnl(pnl)}</span>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(h.stop_loss_logic)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPrice(h.stop_loss_technical)}
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
                            setPriceDialog({ open: true, id: h.id })
                          }
                        >
                          更新价格
                        </Button>
                        <Button
                          variant="outline"
                          size="xs"
                          onClick={() =>
                            setStoplossDialog({ open: true, id: h.id })
                          }
                        >
                          更新止损
                        </Button>
                        <Button
                          variant="destructive"
                          size="xs"
                          onClick={() =>
                            setDeleteConfirm({ open: true, id: h.id })
                          }
                        >
                          删除
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

      <UpdatePriceDialog
        open={priceDialog.open}
        onOpenChange={(open) => setPriceDialog({ open, id: priceDialog.id })}
        onSubmit={(data) => {
          updatePrice.mutate(
            { id: priceDialog.id, data },
            {
              onSuccess: () => {
                setPriceDialog({ open: false, id: priceDialog.id });
                clearError();
              },
              onError: (err: Error) => { setMutationError(err.message || "操作失败"); },
            }
          );
        }}
        pending={updatePrice.isPending}
      />

      <UpdateStoplossDialog
        open={stoplossDialog.open}
        onOpenChange={(open) =>
          setStoplossDialog({ open, id: stoplossDialog.id })
        }
        onSubmit={(data) => {
          updateStoploss.mutate(
            { id: stoplossDialog.id, data },
            {
              onSuccess: () => {
                setStoplossDialog({ open: false, id: stoplossDialog.id });
                clearError();
              },
              onError: (err: Error) => { setMutationError(err.message || "操作失败"); },
            }
          );
        }}
        pending={updateStoploss.isPending}
      />

      <Dialog
        open={deleteConfirm.open}
        onOpenChange={(open) => setDeleteConfirm({ open, id: deleteConfirm.id })}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>确认删除</DialogTitle>
            <DialogDescription>
              确定要删除此持仓记录吗？此操作不可撤销。
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
              {removeHolding.isPending ? "删除中…" : "删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add Holding Dialog
// ---------------------------------------------------------------------------

function AddHoldingDialog({
  open,
  onOpenChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: InvestHoldingCreate) => void;
  pending: boolean;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [sector, setSector] = useState("");
  const [entryPrice, setEntryPrice] = useState("");
  const [stopLossHard, setStopLossHard] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [positionPct, setPositionPct] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({
      code,
      name,
      sector,
      entry_price: parseFloat(entryPrice),
      ...(stopLossHard ? { stop_loss_hard: parseFloat(stopLossHard) } : {}),
      ...(targetPrice ? { target_price: parseFloat(targetPrice) } : {}),
      ...(positionPct ? { position_pct: parseFloat(positionPct) } : {}),
    });
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setCode("");
      setName("");
      setSector("");
      setEntryPrice("");
      setStopLossHard("");
      setTargetPrice("");
      setPositionPct("");
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
            <label className="text-sm font-medium">代码 *</label>
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="如 600519"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">名称 *</label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如 贵州茅台"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">板块 *</label>
            <Input
              value={sector}
              onChange={(e) => setSector(e.target.value)}
              placeholder="如 白酒"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">买入价 *</label>
            <Input
              type="number"
              step="0.01"
              value={entryPrice}
              onChange={(e) => setEntryPrice(e.target.value)}
              placeholder="0.00"
              required
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">硬止损</label>
            <Input
              type="number"
              step="0.01"
              value={stopLossHard}
              onChange={(e) => setStopLossHard(e.target.value)}
              placeholder="可选"
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">目标价</label>
            <Input
              type="number"
              step="0.01"
              value={targetPrice}
              onChange={(e) => setTargetPrice(e.target.value)}
              placeholder="可选"
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">仓位%</label>
            <Input
              type="number"
              step="0.1"
              value={positionPct}
              onChange={(e) => setPositionPct(e.target.value)}
              placeholder="可选"
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
// Update Price Dialog
// ---------------------------------------------------------------------------

function UpdatePriceDialog({
  open,
  onOpenChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: InvestHoldingUpdatePrice) => void;
  pending: boolean;
}) {
  const [price, setPrice] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    onSubmit({ current_price: parseFloat(price) });
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) setPrice("");
    onOpenChange(nextOpen);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>更新价格</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid gap-3">
          <div className="grid gap-1">
            <label className="text-sm font-medium">当前价格 *</label>
            <Input
              type="number"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="0.00"
              required
            />
          </div>
          <DialogFooter showCloseButton>
            <Button type="submit" disabled={pending}>
              {pending ? "更新中…" : "确认更新"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Update Stoploss Dialog
// ---------------------------------------------------------------------------

function UpdateStoplossDialog({
  open,
  onOpenChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: InvestHoldingUpdateStoploss) => void;
  pending: boolean;
}) {
  const [logic, setLogic] = useState("");
  const [technical, setTechnical] = useState("");
  const [hard, setHard] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const data: InvestHoldingUpdateStoploss = {};
    if (logic) data.stop_loss_logic = parseFloat(logic);
    if (technical) data.stop_loss_technical = parseFloat(technical);
    if (hard) data.stop_loss_hard = parseFloat(hard);
    onSubmit(data);
  }

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen) {
      setLogic("");
      setTechnical("");
      setHard("");
    }
    onOpenChange(nextOpen);
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>更新止损</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="grid gap-3">
          <div className="grid gap-1">
            <label className="text-sm font-medium">逻辑止损</label>
            <Input
              type="number"
              step="0.01"
              value={logic}
              onChange={(e) => setLogic(e.target.value)}
              placeholder="可选"
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">技术止损</label>
            <Input
              type="number"
              step="0.01"
              value={technical}
              onChange={(e) => setTechnical(e.target.value)}
              placeholder="可选"
            />
          </div>
          <div className="grid gap-1">
            <label className="text-sm font-medium">硬止损</label>
            <Input
              type="number"
              step="0.01"
              value={hard}
              onChange={(e) => setHard(e.target.value)}
              placeholder="可选"
            />
          </div>
          <DialogFooter showCloseButton>
            <Button type="submit" disabled={pending}>
              {pending ? "更新中…" : "确认更新"}
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
