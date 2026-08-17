"""周期股分类模块.

基于东财 industry 判断股票是否属于周期性行业，用于持仓辅助判断：
周期股的行情结构是"要么快死要么大赚"，中间地带（31-60天不涨）最差。

实证依据（S2 配置，5 年 327 笔交易）：
  周期股 31-60天持仓： 平均 -1.25%, 胜率 33%（死亡区间）
  周期股 60天+持仓：  平均 +41.5%（黄金/资源超级周期）
  成长股 31-60天持仓：平均 +3.49%, 胜率 73%（无此问题）

规则设计：
  周期股 + 持仓>30天 + 浮盈<3% → 清仓（周期已过）
  周期股 + 浮盈>15% → 宽止损保护（让超级周期跑）

用法：
    from davis_analyzer.cyclical import is_cyclical, get_stock_style
    cyc = is_cyclical("601899.SH")           # True/False
    style = get_stock_style("300750.SZ")     # "周期"/"成长"/"防御"/"其他"
"""
from __future__ import annotations

from functools import lru_cache

# ── 行业风格分类（基于申万/常识 + 东财 industry 名称）──

_CYCLICAL_INDUSTRIES = frozenset({
    # 资源/有色
    "小金属", "铝", "铜", "黄金", "钢铁", "铅锌", "镍钴", "稀有金属",
    "能源金属", "非金属矿", "矿物制品",
    # 化工
    "化工原料", "农药化肥", "塑料", "橡胶", "玻璃", "陶瓷", "化学纤维",
    "化工机械", "石油加工", "采掘服务",
    # 建材/地产链
    "水泥", "区域地产", "全国地产", "园区开发", "装修装饰", "建材",
    # 机械/重工
    "工程机械", "运输设备", "船舶制造", "农用机械", "机械基件",
    # 交运（强周期）
    "航运", "水运", "航空", "港口", "船舶运输",
    # 金融（ beta 属性）
    "证券", "保险", "多元金融", "信托",
    # 汽车/可选消费（周期性）
    "汽车整车", "汽车服务", "摩托车",
    # 轻工周期
    "纺织", "服饰", "造纸", "印刷", "广告包装",
})

_GROWTH_INDUSTRIES = frozenset({
    # 科技
    "电气设备", "半导体", "元器件", "软件服务", "互联网", "IT设备",
    "通信设备", "电器仪表", "计算机设备",
    # 医药
    "医疗保健", "生物制药", "化学制药", "中成药", "医药流通",
    # 新兴产业
    "光伏设备", "风电设备", "储能", "电池", "军工电子", "航天航空",
})

_DEFENSIVE_INDUSTRIES = frozenset({
    # 消费
    "白酒", "食品", "啤酒", "葡萄酒", "软饮料", "乳制品",
    "家用电器", "家居用品", "文教休闲", "旅游", "酒店餐饮",
    # 公用事业
    "电力", "供气供热", "水务", "高速公路", "铁路",
    # 稳定金融
    "银行",
})


def get_stock_style(industry: str) -> str:
    """Map an east-money industry name to a style category.

    Returns "周期" / "成长" / "防御" / "其他".
    """
    if industry in _CYCLICAL_INDUSTRIES:
        return "周期"
    if industry in _GROWTH_INDUSTRIES:
        return "成长"
    if industry in _DEFENSIVE_INDUSTRIES:
        return "防御"
    return "其他"


def is_cyclical(industry: str) -> bool:
    """True if the industry is cyclical (周期股)."""
    return industry in _CYCLICAL_INDUSTRIES


@lru_cache(maxsize=4096)
def _industry_cache(ts_code: str) -> str:
    """Cached lookup of a stock's industry from stock_basic."""
    try:
        from stockhot.data_layer.market_db import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT industry FROM stock_basic WHERE ts_code=? AND industry IS NOT NULL AND industry != ''",
                (ts_code,),
            ).fetchone()
        return row[0] if row else ""
    except Exception:
        return ""


def get_stock_style_by_code(ts_code: str) -> str:
    """Look up a stock's style by ts_code (cached DB lookup).

    Returns "周期" / "成长" / "防御" / "其他".
    """
    return get_stock_style(_industry_cache(ts_code))


def is_cyclical_by_code(ts_code: str) -> bool:
    """True if the stock (by ts_code) belongs to a cyclical industry."""
    return is_cyclical(_industry_cache(ts_code))
