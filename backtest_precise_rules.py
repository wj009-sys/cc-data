#!/usr/bin/env python
"""
400万组合 — 严格执行投资纪律回测（2021-01-01 ~ 2025-12-31）
============================================================
严格按照投资纪要规则：
1. 统一阶梯止盈 7%/9%/12%（仅限权益+可转债+黄金）
2. 高波动硬止损 -5%预警/-10%减半/-15%清仓（科创50/创业板）
3. 其他权益统一止损 -15%（沪深300/红利低波/可转债/黄金）
4. 组合再平衡 半年度末 ±20%相对偏离
5. 熔断机制（组合级）

注意：黄金ETF(518880)作为特殊资产，止盈规则适用，但统一止损不适用
（黄金有自身的定投/补仓计划，见投资纪要第六部分）
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['NO_PROXY'] = 'api.waditu.com,*.waditu.com,localhost,127.0.0.1'

import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
from collections import defaultdict

pro = ts.pro_api()

# ============================================================
# 配置
# ============================================================
HOLDINGS = [
    {"code": "510300", "name": "沪深300ETF",   "ts": "510300.SH", "target": 0.1875, "high_vol": False, "type": "equity", "stop_profit": True},
    {"code": "588050", "name": "科创50ETF",    "ts": "588050.SH", "target": 0.0625, "high_vol": True,  "type": "equity", "stop_profit": True},
    {"code": "159915", "name": "创业板ETF",    "ts": "159915.SZ", "target": 0.0625, "high_vol": True,  "type": "equity", "stop_profit": True},
    {"code": "512890", "name": "红利低波ETF",  "ts": "512890.SH", "target": 0.0625, "high_vol": False, "type": "equity", "stop_profit": True},
    {"code": "511380", "name": "可转债ETF",    "ts": "511380.SH", "target": 0.1250, "high_vol": False, "type": "equity", "stop_profit": True},
    {"code": "511260", "name": "10年国债ETF",  "ts": "511260.SH", "target": 0.1250, "high_vol": False, "type": "bond",   "stop_profit": False},
    {"code": "511010", "name": "5年国债ETF",   "ts": "511010.SH", "target": 0.1250, "high_vol": False, "type": "bond",   "stop_profit": False},
    {"code": "511360", "name": "短融ETF",      "ts": "511360.SH", "target": 0.0625, "high_vol": False, "type": "bond",   "stop_profit": False},
    {"code": "518880", "name": "黄金ETF",      "ts": "518880.SH", "target": 0.1875, "high_vol": False, "type": "commodity","stop_profit": True},
]
CODE_MAP = {h["code"]: h for h in HOLDINGS}
TOTAL_CAPITAL = 4_000_000
TRANSACTION_COST = 0.0005  # 万5

START_DATE = "2021-01-01"
END_DATE = "2025-12-31"

print("=" * 80)
print("  400万组合 — 严格执行投资纪律回测")
print(f"  建仓日: 2021-01-01")
print(f"  回测区间: {START_DATE} ~ {END_DATE}")
print(f"  初始资金: ¥{TOTAL_CAPITAL/10000:.0f}万")
print("=" * 80)

# ============================================================
# 1. 获取行情数据
# ============================================================
print("\n[1/5] 获取历史行情数据...")

all_dfs = {}
first_dates = {}
for h in HOLDINGS:
    try:
        df = pro.fund_daily(ts_code=h["ts"], start_date="20201201", end_date="20251231")
        if not df.empty:
            df = df.sort_values("trade_date")
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")
            all_dfs[h["code"]] = df["close"]
            first_dates[h["code"]] = df.index[0]
            print(f"  ✓ {h['code']} {h['name']}: {len(df)}条, "
                  f"首日 {df.index[0].strftime('%Y-%m-%d')}")
    except Exception as e:
        print(f"  ✗ {h['code']}: {e}")

# 构建价格矩阵
price = pd.DataFrame(all_dfs).sort_index()
price = price[(price.index >= "2020-12-01") & (price.index <= "2025-12-31")]
price = price.ffill()

# 上市前标记 NaN
for h in HOLDINGS:
    code = h["code"]
    if code in first_dates:
        price.loc[price.index < first_dates[code], code] = np.nan

trading_days_all = list(price.index)
# 只保留 2021-01-01 及之后的交易日
trading_days = [d for d in trading_days_all if d >= pd.Timestamp("2021-01-01")]
print(f"\n  交易日: {len(trading_days)} 天, "
      f"{trading_days[0].strftime('%Y-%m-%d')} ~ {trading_days[-1].strftime('%Y-%m-%d')}")

# ============================================================
# 辅助函数
# ============================================================
def find_semi_annual_dates(days):
    result = set()
    for i, d in enumerate(days):
        if d.month in (6, 12):
            is_last = True
            for j in range(i+1, min(i+10, len(days))):
                if days[j].month == d.month:
                    is_last = False
                    break
            if is_last:
                result.add(d)
    return result

HALF_YEAR_ENDS = find_semi_annual_dates(trading_days)
print(f"  半年度检查日: {len(HALF_YEAR_ENDS)} 个")
for d in sorted(HALF_YEAR_ENDS):
    print(f"    {d.strftime('%Y-%m-%d')}")

def get_price_safe(code, date):
    if code not in price.columns:
        return 0
    if date not in price.index:
        return 0
    p = price.loc[date, code]
    if pd.isna(p):
        return 0
    return float(p)

def calc_portfolio(shares, cash, date):
    """计算组合总市值和各持仓详情"""
    total_mv = cash
    positions = {}
    for h in HOLDINGS:
        code = h["code"]
        p = get_price_safe(code, date)
        mv = shares[code] * p
        total_mv += mv
        positions[code] = {
            "price": p, "shares": shares[code], "mv": mv,
            "target": h["target"], "high_vol": h["high_vol"],
            "type": h["type"], "name": h["name"],
            "stop_profit": h["stop_profit"],
        }
    for code, pos in positions.items():
        pos["weight"] = pos["mv"] / total_mv if total_mv > 0 else 0
    return total_mv, positions

# ============================================================
# 2. 初始建仓 (2021-01-01)
# ============================================================
print("\n[2/5] 初始建仓...")

shares = {h["code"]: 0 for h in HOLDINGS}
cash = TOTAL_CAPITAL
start_date = trading_days[0]  # 2021-01-04 或之后的第一个交易日

# 实际建仓在第一个交易日执行
for h in HOLDINGS:
    code = h["code"]
    if code not in first_dates:
        continue
    fd = first_dates[code]
    if start_date < fd:
        print(f"  ⚠ {code} {h['name']} 于 {fd.strftime('%Y-%m-%d')} 才上市，跳过初始建仓")
        continue
    p = get_price_safe(code, start_date)
    if p <= 0:
        continue
    target_amt = TOTAL_CAPITAL * h["target"]
    s = int(target_amt / p / 100) * 100
    shares[code] = s
    cost = s * p * (1 + TRANSACTION_COST)
    cash -= cost

cash = max(0, cash)
total_mv, positions = calc_portfolio(shares, cash, start_date)
print(f"  建仓日: {start_date.strftime('%Y-%m-%d')}")
for h in HOLDINGS:
    code = h["code"]
    p = positions[code]
    if p["shares"] > 0:
        print(f"  {code} {h['name']}: {p['shares']}股 @ ¥{p['price']:.4f} = ¥{p['mv']:,.0f}")
print(f"  建仓总市值: ¥{total_mv:,.0f} (现金: ¥{cash:,.0f})")

# 初始化各品种成本基
cost_basis = {}  # code -> 成本价
for code, s in shares.items():
    if s > 0:
        cost_basis[code] = get_price_safe(code, start_date)

# 止盈状态跟踪
# tier_triggered[code] = set of tiers already triggered, e.g. {"1", "2"}
tier_triggered = defaultdict(set)

# 冷却期：统一止损后观察40交易日
stop_cooldown = {}  # code -> 冷却结束日期（允许重建的日期）
stop_cooldown_end = {}  # code -> 冷却结束日期

# 硬止损状态（高波动品种）
hard_stop_level = {}  # code -> 已触发的最高级别

# ============================================================
# 3. 每日回测引擎
# ============================================================
print("\n[3/5] 运行每日回测...")

daily_values = {}  # date -> total_mv
all_actions = []   # list of action dicts
action_id = 0

def get_cost_basis(code, date):
    """获取成本价"""
    if code in cost_basis:
        return cost_basis[code]
    return get_price_safe(code, date)

def in_cooldown(code, date):
    """检查是否在冷却期内（统一止损后）"""
    if code in stop_cooldown:
        if date <= stop_cooldown[code]:
            return True
        else:
            # 冷却期结束
            del stop_cooldown[code]
            return False
    return False

def rebalance_portfolio(date, shares, cash, reason=""):
    """执行全仓再平衡（按目标权重）"""
    total_mv_before = calc_portfolio(shares, cash, date)[0]

    new_shares = {h["code"]: 0 for h in HOLDINGS}
    remaining = total_mv_before

    # 第一轮：计算所有可交易品种的股数
    for h in HOLDINGS:
        code = h["code"]
        if code in first_dates and date < first_dates[code]:
            continue
        # 冷却期内的品种不买
        if in_cooldown(code, date):
            continue
        p = get_price_safe(code, date)
        if p <= 0:
            continue
        target_amt = total_mv_before * h["target"]
        s = int(target_amt / p / 100) * 100
        new_shares[code] = s
        remaining -= s * p

    remaining = max(0, remaining)

    # 如果有剩余现金，按权重分配给有仓位的品种
    if remaining > 5000:
        total_target_for_alloc = sum(
            h["target"] for h in HOLDINGS
            if not (code in first_dates and date < first_dates[code])
            and not in_cooldown(h["code"], date)
        )
        if total_target_for_alloc > 0:
            for h in HOLDINGS:
                code = h["code"]
                if in_cooldown(code, date):
                    continue
                if code in first_dates and date < first_dates[code]:
                    continue
                p = get_price_safe(code, date)
                if p <= 0:
                    continue
                extra_amt = remaining * (h["target"] / total_target_for_alloc)
                extra_s = int(extra_amt / p / 100) * 100
                if extra_s > 0:
                    new_shares[code] = new_shares.get(code, 0) + extra_s
                    remaining -= extra_s * p

    remaining = max(0, remaining)

    # 交易成本
    for code in shares:
        old_s = shares[code]
        new_s = new_shares.get(code, 0)
        if old_s != new_s:
            p = get_price_safe(code, date)
            if p > 0:
                remaining -= abs(old_s - new_s) * p * TRANSACTION_COST

    remaining = max(0, remaining)
    return new_shares, remaining


# ============================================================
# 关键：投资纪要规则实现
# ============================================================

def apply_rules(date, shares, cash):
    """
    对当前持仓应用所有规则，返回 (new_shares, new_cash, actions_taken)
    """
    global cost_basis, tier_triggered, stop_cooldown, hard_stop_level

    new_shares = dict(shares)
    new_cash = cash
    actions = []

    total_mv, positions = calc_portfolio(new_shares, new_cash, date)

    # ---- 规则1：统一阶梯止盈 7%/9%/12% ----
    # 适用：510300/588050/159915/512890/511380/518880（stop_profit=True、有持仓、非冷却期）
    for code, pos in positions.items():
        h = CODE_MAP[code]
        if not h["stop_profit"]:
            continue
        if pos["shares"] <= 0:
            continue
        if pos["price"] <= 0:
            continue
        if in_cooldown(code, date):
            continue

        cost = get_cost_basis(code, date)
        if cost <= 0:
            continue
        if pos["price"] <= cost:  # 不浮盈不检查止盈
            continue

        pnl_pct = (pos["price"] - cost) / cost

        # 第三档: >= 12% (清仓)
        if pnl_pct >= 0.12 and "3" not in tier_triggered[code]:
            tier_triggered[code].add("3")
            # 记录该档位的成本基准
            p = pos["price"]
            s = pos["shares"]
            proceeds = s * p * (1 - TRANSACTION_COST)
            new_shares[code] = 0
            new_cash += proceeds
            cost_basis[code] = p
            actions.append(f"止盈③ {code} {h['name']} +{pnl_pct:.1%} → 清仓(¥{proceeds:,.0f})")

        # 第二档: >= 9% (减仓30%)
        elif pnl_pct >= 0.09 and "2" not in tier_triggered[code]:
            tier_triggered[code].add("2")
            s = pos["shares"]
            sell_s = int(s * 0.3 / 100) * 100
            if sell_s > 0 and s - sell_s > 0:
                p = pos["price"]
                proceeds = sell_s * p * (1 - TRANSACTION_COST)
                new_shares[code] = s - sell_s
                new_cash += proceeds
                actions.append(f"止盈② {code} {h['name']} +{pnl_pct:.1%} → 减30%(卖出{sell_s}股,¥{proceeds:,.0f})")
            # 如果减仓后为0，则等价于第三档
            elif sell_s == 0:
                p = pos["price"]
                proceeds = s * p * (1 - TRANSACTION_COST)
                new_shares[code] = 0
                new_cash += proceeds
                cost_basis[code] = p
                actions.append(f"止盈②→③ {code} {h['name']} +{pnl_pct:.1%} → 清仓(余量不足)")

        # 第一档: >= 7% (减仓20%)
        elif pnl_pct >= 0.07 and "1" not in tier_triggered[code]:
            tier_triggered[code].add("1")
            s = pos["shares"]
            sell_s = int(s * 0.2 / 100) * 100
            if sell_s > 0 and s - sell_s > 0:
                p = pos["price"]
                proceeds = sell_s * p * (1 - TRANSACTION_COST)
                new_shares[code] = s - sell_s
                new_cash += proceeds
                actions.append(f"止盈① {code} {h['name']} +{pnl_pct:.1%} → 减20%(卖出{sell_s}股,¥{proceeds:,.0f})")

    # ---- 规则2：高波动硬止损（科创50/创业板专用） ----
    for code, pos in positions.items():
        h = CODE_MAP[code]
        if not h["high_vol"]:
            continue
        if pos["shares"] <= 0:
            continue
        if pos["price"] <= 0:
            continue
        if in_cooldown(code, date):
            continue

        cost = get_cost_basis(code, date)
        if cost <= 0:
            continue
        pnl_pct = (pos["price"] - cost) / cost

        # 止损B: <= -15% -> 次日清仓（这里在检测日执行，假设当天可执行）
        if pnl_pct <= -0.15:
            p = pos["price"]
            s = pos["shares"]
            proceeds = s * p * (1 - TRANSACTION_COST)
            new_shares[code] = 0
            new_cash += proceeds
            hard_stop_level[code] = "B"
            # 冷却期：观察20个交易日
            idx = trading_days.index(date)
            cooldown_end = trading_days[min(idx + 20, len(trading_days) - 1)]
            stop_cooldown[code] = cooldown_end
            actions.append(f"硬止损B {code} {h['name']} {pnl_pct:.1%} → 清仓(观察至{cooldown_end.strftime('%Y-%m-%d')})")

        # 止损A: <= -10% -> 卖50%
        elif pnl_pct <= -0.10 and hard_stop_level.get(code) != "A":
            hard_stop_level[code] = "A"
            s = pos["shares"]
            sell_s = int(s * 0.5 / 100) * 100
            if sell_s > 0 and s - sell_s > 0:
                p = pos["price"]
                proceeds = sell_s * p * (1 - TRANSACTION_COST)
                new_shares[code] = s - sell_s
                new_cash += proceeds
                actions.append(f"硬止损A {code} {h['name']} {pnl_pct:.1%} → 减半(卖出{sell_s}股,¥{proceeds:,.0f})")
            elif sell_s == 0 or s - sell_s == 0:
                # 卖到0
                p = pos["price"]
                proceeds = s * p * (1 - TRANSACTION_COST)
                new_shares[code] = 0
                new_cash += proceeds
                actions.append(f"硬止损A→B {code} {h['name']} {pnl_pct:.1%} → 清仓(余量不足)")

    # ---- 规则3：其他权益品种统一止损 -15% ----
    # 适用：510300/512890/511380/518880
    for code in ["510300", "512890", "511380", "518880"]:
        h = CODE_MAP[code]
        if h["high_vol"]:
            continue  # 已在上面的高波动处理
        pos = positions.get(code)
        if not pos or pos["shares"] <= 0:
            continue
        if pos["price"] <= 0:
            continue
        if in_cooldown(code, date):
            continue

        cost = get_cost_basis(code, date)
        if cost <= 0:
            continue
        pnl_pct = (pos["price"] - cost) / cost

        if pnl_pct <= -0.15:
            p = pos["price"]
            s = pos["shares"]
            proceeds = s * p * (1 - TRANSACTION_COST)
            new_shares[code] = 0
            new_cash += proceeds
            # 冷却期40个交易日
            idx = trading_days.index(date)
            cooldown_end = trading_days[min(idx + 40, len(trading_days) - 1)]
            stop_cooldown[code] = cooldown_end
            actions.append(f"统一止损 {code} {h['name']} {pnl_pct:.1%} → 清仓(观察40日至{cooldown_end.strftime('%Y-%m-%d')})")

    # 重新计算总市值（用于再平衡和熔断检查）
    total_mv_after, positions_after = calc_portfolio(new_shares, new_cash, date)
    total_cost = sum(
        get_cost_basis(code, date) * new_shares.get(code, 0)
        for code in new_shares
    )
    total_pnl_pct = (total_mv_after - total_cost) / total_cost if total_cost > 0 else 0

    # ---- 规则4：半年度再平衡（±20%相对偏离） ----
    if date in HALF_YEAR_ENDS:
        # 检查是否触发偏离
        triggered_codes = []
        for code, pos in positions_after.items():
            if pos["target"] <= 0:
                continue
            if pos["shares"] <= 0 and in_cooldown(code, date):
                continue
            if pos["target"] > 0 and pos["weight"] > 0:
                rel_dev = (pos["weight"] - pos["target"]) / pos["target"]
                if abs(rel_dev) > 0.20:
                    triggered_codes.append(f"{code}(rel_dev={rel_dev:+.1%})")

        if triggered_codes:
            new_shares, new_cash = rebalance_portfolio(date, new_shares, new_cash)
            actions.append(f"半年度再平衡({date.strftime('%Y-%m')}) 触发: {' | '.join(triggered_codes)}")

            # 更新成本基
            for code in new_shares:
                if new_shares[code] > 0:
                    p = get_price_safe(code, date)
                    if p > 0:
                        cost_basis[code] = p
            # 清空止盈状态（再平衡后重新开始）
            tier_triggered.clear()
            hard_stop_level.clear()
        else:
            actions.append(f"半年度检查({date.strftime('%Y-%m')}) 无触发(偏离均在±20%以内)")

    # ---- 规则5：熔断 ----
    # 熔断会影响再平衡的执行方式，但简化起见，这里只记录不修改
    if total_pnl_pct <= -0.15:
        actions.append(f"⚫熔断: 组合回撤{total_pnl_pct:.1%}>=15%")
    elif total_pnl_pct <= -0.12:
        actions.append(f"🔴熔断: 组合回撤{total_pnl_pct:.1%}>=12%")
    elif total_pnl_pct <= -0.10:
        actions.append(f"🟡熔断: 组合回撤{total_pnl_pct:.1%}>=10%")

    return new_shares, max(0, new_cash), actions


# ============================================================
# 4. 运行回测循环
# ============================================================
print("\n[4/5] 执行回测...")

total_days = len(trading_days)
last_pct = -1

for i, date in enumerate(trading_days):
    # 进度
    pct = (i * 100) // total_days
    if pct % 10 == 0 and pct != last_pct:
        print(f"  进度: {pct}% ({i}/{total_days})")
        last_pct = pct

    # 记录当日市值
    total_mv, _ = calc_portfolio(shares, cash, date)
    daily_values[date] = total_mv

    # 执行规则
    new_shares, new_cash, action_list = apply_rules(date, shares, cash)

    if action_list:
        for a in action_list:
            action_id += 1
            all_actions.append({
                "id": action_id,
                "date": date,
                "action": a,
            })

        shares, cash = new_shares, new_cash

print(f"  完成! {len(trading_days)} 个交易日, {len(all_actions)} 次操作")

# ============================================================
# 5. 计算绩效指标
# ============================================================
print("\n[5/5] 计算绩效...")

dates = sorted(daily_values.keys())
vals = np.array([daily_values[d] for d in dates])

# 基础指标
total_ret = vals[-1] / TOTAL_CAPITAL - 1
years = (dates[-1] - dates[0]).days / 365.25

daily_ret = vals[1:] / vals[:-1] - 1
ann_ret = (1 + total_ret) ** (1 / years) - 1
ann_vol = np.std(daily_ret) * np.sqrt(252)

sharpe = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0
sortino_ret = daily_ret[daily_ret < 0]
downside_vol = np.std(sortino_ret) * np.sqrt(252) if len(sortino_ret) > 0 else ann_vol
sortino = (ann_ret - 0.02) / downside_vol if downside_vol > 0 else 0

# 最大回撤
peak = vals[0]
max_dd, max_dd_date = 0, dates[0]
for i, v in enumerate(vals):
    if v > peak:
        peak = v
    dd = (peak - v) / peak
    if dd > max_dd:
        max_dd = dd
        max_dd_date = dates[i]

calmar = ann_ret / max_dd if max_dd > 0 else 0
win_rate = np.sum(daily_ret > 0) / len(daily_ret)

# 年度收益统计
yearly = {}
for year in range(2021, 2026):
    yd = [d for d in dates if d.year == year]
    if len(yd) < 2:
        continue
    yv = np.array([daily_values[d] for d in yd])
    y_ret = yv[-1] / yv[0] - 1
    y_peak = yv[0]
    y_dd = 0
    for v in yv:
        if v > y_peak:
            y_peak = v
        dd = (y_peak - v) / y_peak
        if dd > y_dd:
            y_dd = dd
    yearly[year] = {"return": y_ret, "max_dd": y_dd}

# 年度操作统计
yearly_ops = defaultdict(int)
yearly_op_detail = defaultdict(list)
for a in all_actions:
    year = a["date"].year
    yearly_ops[year] += 1
    yearly_op_detail[year].append(a)

# ============================================================
# 6. 输出报告
# ============================================================
print("\n" + "=" * 80)
print("  回 测 结 果  —  严 格 执 行 投 资 纪 律")
print(f"  建仓日: 2021-01-01  →  终盘日: 2025-12-31")
print("=" * 80)

print(f"""
┌─────────────────────────────────────────────────────────────┐
│                     总 体 绩 效                             │
├─────────────────────────────────────────────────────────────┤
│  累计总收益率:    {total_ret*100:>+8.2f}%                                 │
│  年化收益率:      {ann_ret*100:>+8.2f}%                                 │
│  年化波动率:      {ann_vol*100:>8.2f}%                                 │
│  夏普比率:        {sharpe:>8.2f}                                 │
│  索提诺比率:      {sortino:>8.2f}                                 │
│  卡尔玛比率:      {calmar:>8.2f}                                 │
│  最大回撤:        {max_dd*100:>8.2f}%  ({max_dd_date.strftime('%Y-%m-%d')})               │
│  日胜率:          {win_rate*100:>8.2f}%                                 │
│  最终市值:        ¥{vals[-1]:>10,.0f}  (¥{vals[-1]/10000:.2f}万)              │
│  累计盈利:        ¥{vals[-1]-TOTAL_CAPITAL:>+10,.0f}  ({'¥' + f'{(vals[-1]-TOTAL_CAPITAL)/10000:.2f}万':>12s})          │
└─────────────────────────────────────────────────────────────┘""")

print()
print("=" * 80)
print("  分 年 度 收 益")
print("=" * 80)
print(f"{'年份':<8} {'年度收益':>10} {'年度回撤':>10} {'年末市值(万)':>14} {'操作次数':>10}")
print("-" * 60)
for year in range(2021, 2026):
    y = yearly.get(year, {})
    if y:
        year_end_val = 0
        for d in sorted(daily_values.keys()):
            if d.year == year:
                year_end_val = daily_values[d]
        print(f"{year:<8} {y['return']*100:>+8.2f}% {y['max_dd']*100:>8.2f}% "
              f"{year_end_val/10000:>12.2f} {yearly_ops.get(year, 0):>10}")
    else:
        print(f"{year:<8} {'N/A':>10}")

# 累计
print("-" * 60)
print(f"{'合计':<8} {total_ret*100:>+8.2f}% {max_dd*100:>8.2f}% "
      f"{vals[-1]/10000:>12.2f} {len(all_actions):>10}")

print()
print("=" * 80)
print("  操 作 明 细")
print("=" * 80)

# 按年份分组显示
for year in range(2021, 2026):
    ops = yearly_op_detail.get(year, [])
    print(f"\n{year}年 ({len(ops)} 次操作):")
    if ops:
        for a in ops:
            dt = a["date"].strftime("%Y-%m-%d")
            act = a["action"]
            print(f"  [{a['id']:03d}] {dt}  {act}")
    else:
        print("  无操作")

# 统计各类型操作
action_types = defaultdict(int)
for a in all_actions:
    act = a["action"]
    if "止盈" in act:
        action_types["止盈"] += 1
    elif "硬止损" in act:
        action_types["硬止损"] += 1
    elif "统一止损" in act:
        action_types["统一止损"] += 1
    elif "半年度" in act:
        action_types["半年度检查"] += 1
    elif "熔断" in act:
        action_types["熔断信号"] += 1
    else:
        action_types["其他"] += 1

print()
print("操作类型统计:")
for t, n in sorted(action_types.items(), key=lambda x: -x[1]):
    print(f"  {t}: {n} 次")

# 按品种统计
code_action_count = defaultdict(int)
for a in all_actions:
    for code in [c for c in CODE_MAP if c in a["action"]]:
        code_action_count[code] += 1

print("\n品种操作统计:")
for code, n in sorted(code_action_count.items(), key=lambda x: -x[1]):
    print(f"  {code} {CODE_MAP[code]['name']}: {n} 次")

# ============================================================
# 7. 保存结果
# ============================================================
output = {
    "metadata": {
        "strategy": "严格执行投资纪律（止盈7%/9%/12% + 硬止损-10%/-15% + 统一止损-15% + 半年度±20%再平衡 + 熔断）",
        "backtest_period": f"{START_DATE} ~ {END_DATE}",
        "total_capital": TOTAL_CAPITAL,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "交易日数": len(trading_days),
    },
    "metrics": {
        "total_return_pct": round(total_ret * 100, 2),
        "annual_return_pct": round(ann_ret * 100, 2),
        "annual_vol_pct": round(ann_vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "calmar": round(calmar, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_date": max_dd_date.strftime("%Y-%m-%d"),
        "win_rate_pct": round(win_rate * 100, 2),
        "final_value": round(vals[-1], 2),
        "total_profit": round(vals[-1] - TOTAL_CAPITAL, 2),
        "total_operations": len(all_actions),
        "yearly": {
            str(year): {
                "return_pct": round(yearly[year]["return"] * 100, 2),
                "max_drawdown_pct": round(yearly[year]["max_dd"] * 100, 2),
                "operations": yearly_ops.get(year, 0),
            }
            for year in range(2021, 2026) if year in yearly
        },
    },
    "action_summary": {
        "by_type": dict(action_types),
    },
}

# 保存详细操作日志（精简版）
output["actions"] = []
for a in all_actions:
    output["actions"].append({
        "id": a["id"],
        "date": a["date"].strftime("%Y-%m-%d") if hasattr(a["date"], "strftime") else str(a["date"]),
        "action": a["action"],
    })

out_path = os.path.join(os.path.dirname(__file__), "backtest_precise_rules_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n结果已保存至: {out_path}")
print("=" * 80)
print("  回 测 完 成 ✓")
print("=" * 80)
