#!/usr/bin/env bash
# 盘后推送窗口防休眠(2026-09-15 用户授权)
# 机制:工作日由 user systemd timer(after-hours-inhibit.timer, 17:30)拉起,
#       持有 systemd-inhibit(sleep:idle, block)至当日 24:00 自然退出;
#       Persistent=true 使休眠/关机错过 17:30 的场景在机器恢复后补拉(周末自检退出)。
# 目的:保障 17:50 复盘卡 / 19:20 limitup 刷新 / 21:10 数据回流 / 23:00-23:59 公告日卡
#       等整排盘后 cron 所需的机器在线窗口。
# 局限:挡「空闲自动休眠」与(取决于 polkit)合盖挂起;人为 poweroff/显式 suspend 不拦。
set -u

dow=$(date +%u)
if (( dow < 1 || dow > 5 )); then
    echo "$(date '+%F %T') weekend($dow), no inhibit needed"
    exit 0
fi

now=$(date +%s)
midnight=$(date -d "tomorrow 00:00" +%s)
dur=$(( midnight - now ))
(( dur <= 0 )) && exit 0
# 最早 17:30 拉起,持锁上限 6.5h,避免异常时钟下永久持锁
(( dur > 23400 )) && dur=23400

echo "$(date '+%F %T') inhibit sleep:idle for ${dur}s until $(date -d "@$((now + dur))" '+%F %T')"
exec /usr/bin/systemd-inhibit --what=sleep:idle --mode=block \
    --who="盘后推送窗口" \
    --why="A股盘后cron(17:50复盘卡/19:20行情刷新/23点公告日卡)需要机器在线" \
    /bin/sleep "$dur"
