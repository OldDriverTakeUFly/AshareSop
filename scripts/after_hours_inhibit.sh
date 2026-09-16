#!/usr/bin/env bash
# 在线窗口防休眠(2026-09-15 盘后窗口上线;2026-09-16 用户授权扩展交易日盘中窗口)
# 机制:工作日由 user systemd timer 拉起,持有 systemd-inhibit(sleep:idle, block)
#       至窗口结束自然退出;Persistent=true 使休眠/关机错过的场景在机器恢复后补拉
#       (周末自检退出;已过窗口尾声的自检也直接退出)。
# 窗口:
#   after(默认)  17:30 拉起,持锁至当日 24:00——保障 17:50 复盘卡/19:20 行情刷新/
#               21:10 数据回流/23:00+ 公告日卡等盘后 cron。
#   trading      09:25 拉起,持锁至当日 15:05——保障盘中 14:40 轮动/实时行情窗口
#               (0915 白日停机事故沉淀:三影子当日全缺,样本被迫剔除)。
# 局限:挡「空闲自动休眠」与(取决于 polkit)合盖挂起;人为 poweroff/显式 suspend 不拦;
#       法定节假日为非交易日但仍会持锁(无法从本地数据预判节假日,误持锁无害)。
set -u

MODE=${1:-after}
dow=$(date +%u)
if (( dow < 1 || dow > 5 )); then
    echo "$(date '+%F %T') weekend($dow), mode=$MODE no inhibit needed"
    exit 0
fi

now=$(date +%s)
case "$MODE" in
  trading)
    end=$(date -d "today 15:05" +%s)
    (( now >= end )) && { echo "$(date '+%F %T') past 15:05, trading inhibit skip"; exit 0; }
    dur=$(( end - now ))
    (( dur > 21000 )) && dur=21000   # 上限 5h50m
    who="A股交易日盘中窗口"
    why="盘中14:40轮动与实时行情需要机器在线(0915停机事故沉淀)"
    ;;
  after|*)
    end=$(date -d "tomorrow 00:00" +%s)
    (( now >= end )) && exit 0
    dur=$(( end - now ))
    (( dur > 23400 )) && dur=23400   # 上限 6.5h
    who="盘后推送窗口"
    why="A股盘后cron(17:50复盘卡/19:20行情刷新/23点公告日卡)需要机器在线"
    ;;
esac

echo "$(date '+%F %T') [$MODE] inhibit sleep:idle for ${dur}s until $(date -d "@$((now + dur))" '+%F %T')"
exec /usr/bin/systemd-inhibit --what=sleep:idle --mode=block --who="$who" --why="$why" /bin/sleep "$dur"
