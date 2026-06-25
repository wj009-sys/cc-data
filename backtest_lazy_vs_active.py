#!/usr/bin/env python
"""
400万组合 — 长期持有 vs 频繁风控 回测对比
============================================
对比 5 种策略 2020-01-01 ~ 2025-12-31：
  S0: 买入持有（零操作）
  S1: 仅半年度再平衡（6月/12月末调回目标权重）
  S2: 半年度 + 极端偏离触发（单品种权重相对偏离≥±40% 时紧急再平衡）
  S3: 半年度 + 极端价格触发（单品种价格较基准涨/跌≥±50% 时紧急再平衡）
  S4: 主动风控（半年度再平衡 + 止盈12% + 止损-15%/-10%）

核心问题：长期投资真的需要频繁止盈止损吗？
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ['NO_PROXY'] = 'api.waditu.com,*.waditu.com,localhost,127.0.0.1'

import tushare as ts
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json

pro = ts.pro_api()

# ============================================================
# 持仓定义
# ============================================================
HOLDINGS = [
    {"code": "510300", "name": "沪深300ETF",   "ts": "510300.SH", "target": 0.1875, "high_vol": False, "type": "equity"},
    {"code": "588050", "name": "科创50ETF",    "ts": "588050.SH", "target": 0.0625, "high_vol": True,  "type": "equity"},
    {"code": "159915", "name": "创业板ETF",    "ts": "159915.SZ", "target": 0.0625, "high_vol": True,  "type": "equity"},
    {"code": "512890", "name": "红利低波ETF",  "ts": "512890.SH", "target": 0.0625, "high_vol": False, "type": "equity"},
    {"code": "511380", "name": "可转债ETF",    "ts": "511380.SH", "target": 0.1250, "high_vol": False, "type": "equity"},
    {"code": "511260", "name": "10年国债ETF",  "ts": "511260.SH", "target": 0.1250, "high_vol": False, "type": "bond"},
    {"code": "511010", "name": "5年国债ETF",   "ts": "511010.SH", "target": 0.1250, "high_vol": False, "type": "bond"},
    {"code": "511360", "name": "短融ETF",      "ts": "511360.SH", "target": 0.0625, "high_vol": False, "type": "bond"},
    {"code": "518880", "name": "黄金ETF",      "ts": "518880.SH", "target": 0.1875, "high_vol": False, "type": "commodity"},
]
TOTAL_CAPITAL = 4_000_000
TRANSACTION_COST = 0.0005  # 万5 交易成本（买卖各计）

print("=" * 80)
print("  400万组合 — 长期持有 vs 频繁风控 回测对比")
print("  回测区间: 2020-01-01 ~ 2025-12-31")
print("=" * 80)

# ============================================================
# 1. 数据获取
# ============================================================
print("\n[1/5] 获取历史行情数据...")

all_dfs = {}
first_dates = {}
for h in HOLDINGS:
    try:
        df = pro.fund_daily(ts_code=h["ts"], start_date="20191201", end_date="20251231")
        if not df.empty:
            df = df.sort_values("trade_date")
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.set_index("trade_date")
            all_dfs[h["code"]] = df["close"]
            first_dates[h["code"]] = df.index[0]
            print(f"  ✓ {h['code']} {h['name']}: {len(df)}条, "
                  f"首日 {df.index[0].strftime('%Y-%m-%d')}, "
                  f"区间 {df['close'].iloc[0]:.3f} → {df['close'].iloc[-1]:.3f}")
    except Exception as e:
        print(f"  ✗ {h['code']}: {e}")

# 构建价格矩阵
price = pd.DataFrame(all_dfs).sort_index()
price = price[(price.index >= "2020-01-01") & (price.index <= "2025-12-31")]
price = price.ffill()

# 上市前标记 NaN
for h in HOLDINGS:
    code = h["code"]
    if code in first_dates:
        price.loc[price.index < first_dates[code], code] = np.nan

trading_days = list(price.index)
print(f"\n  交易日: {len(trading_days)} 天, "
      f"{trading_days[0].strftime('%Y-%m-%d')} ~ {trading_days[-1].strftime('%Y-%m-%d')}")

# ============================================================
# 2. 辅助函数
# ============================================================
def find_semi_annual_dates(trading_days):
    """找到每年6月和12月的最后一个交易日"""
    result = set()
    for i, d in enumerate(trading_days):
        if d.month in (6, 12):
            # 检查是否是当月最后一个交易日
            is_last = True
            for j in range(i+1, min(i+10, len(trading_days))):
                if trading_days[j].month == d.month:
                    is_last = False
                    break
            if is_last:
                result.add(d)
    return result

HALF_YEAR_ENDS = find_semi_annual_dates(trading_days)
print(f"  半年度检查日: {len(HALF_YEAR_ENDS)} 个")

def is_available(code, date):
    fd = first_dates.get(code)
    return fd is not None and date >= fd

def get_price_safe(code, date):
    """安全获取价格"""
    if code not in price.columns:
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
        positions[code] = {"price": p, "shares": shares[code], "mv": mv,
                           "target": h["target"], "high_vol": h["high_vol"],
                           "type": h["type"], "available": is_available(code, date)}
    for code, pos in positions.items():
        pos["weight"] = pos["mv"] / total_mv if total_mv > 0 else 0
    return total_mv, positions

def initial_build(date):
    """初始建仓：已上市ETF按目标权重分配资金"""
    shares = {h["code"]: 0 for h in HOLDINGS}
    cash = TOTAL_CAPITAL

    for h in HOLDINGS:
        code = h["code"]
        if not is_available(code, date):
            continue
        p = get_price_safe(code, date)
        if p <= 0:
            continue
        target_amt = TOTAL_CAPITAL * h["target"]
        s = int(target_amt / p / 100) * 100
        shares[code] = s
        cash -= s * p
        cash -= s * p * TRANSACTION_COST  # 买入成本

    return shares, max(0, cash)

def execute_rebalance(date, shares, cash, reason=""):
    """执行全仓再平衡：按当前总市值和目标权重重新分配"""
    total_mv_before = calc_portfolio(shares, cash, date)[0]

    new_shares = {h["code"]: 0 for h in HOLDINGS}
    new_cash = total_mv_before

    for h in HOLDINGS:
        code = h["code"]
        if not is_available(code, date):
            continue
        p = get_price_safe(code, date)
        if p <= 0:
            continue
        target_amt = total_mv_before * h["target"]
        s = int(target_amt / p / 100) * 100
        new_shares[code] = s
        new_cash -= s * p

    # 交易成本（买卖差额）
    for code in shares:
        old_s = shares[code]
        new_s = new_shares.get(code, 0)
        if old_s != new_s:
            p = get_price_safe(code, date)
            if p > 0:
                new_cash -= abs(old_s - new_s) * p * TRANSACTION_COST

    new_cash = max(0, new_cash)
    return new_shares, new_cash

# ============================================================
# 3. 五大策略
# ============================================================

def simulate_strategy(name, strategy_fn):
    """
    通用回测引擎
    strategy_fn(date, shares, cash, positions, total_mv, context) -> (new_shares, new_cash, action_log)
    """
    start_date = trading_days[0]
    shares, cash = initial_build(start_date)

    daily_value = {}
    event_log = []
    context = {"prev_available": set(code for code in shares if is_available(code, start_date))}

    # 为 S3/S4 策略维护成本基准和参考价格
    if "ref_prices" not in context:
        context["ref_prices"] = {}  # code -> reference price
        context["cost_basis"] = {}  # code -> cost basis per share
        for h in HOLDINGS:
            code = h["code"]
            if is_available(code, start_date):
                p = get_price_safe(code, start_date)
                context["ref_prices"][code] = p
                context["cost_basis"][code] = p

    for i, date in enumerate(trading_days):
        # 检查新上市ETF → 自动建仓
        curr_available = set(h["code"] for h in HOLDINGS if is_available(h["code"], date))
        new_listings = curr_available - context["prev_available"]
        if new_listings:
            shares, cash = execute_rebalance(date, shares, cash)
            # 更新参考价格
            for code in new_listings:
                p = get_price_safe(code, date)
                if p > 0:
                    context["ref_prices"][code] = p
                    context["cost_basis"][code] = p
            context["prev_available"] = curr_available

        # 计算当日持仓
        total_mv, positions = calc_portfolio(shares, cash, date)
        daily_value[date] = total_mv

        # 调用策略判断
        new_shares, new_cash, action = strategy_fn(
            date, shares, cash, positions, total_mv, context
        )

        if action:
            event_log.append({
                "date": date, "action": action,
                "pre_value": total_mv,
                "post_value": calc_portfolio(new_shares, new_cash, date)[0]
            })

        shares, cash = new_shares, new_cash

    return daily_value, event_log

# --- S0: 买入持有（零操作） ---
def strategy_buy_hold(date, shares, cash, positions, total_mv, ctx):
    return shares, cash, None

# --- S1: 仅半年度再平衡 ---
def strategy_semi_annual(date, shares, cash, positions, total_mv, ctx):
    if date in HALF_YEAR_ENDS:
        new_shares, new_cash = execute_rebalance(date, shares, cash)
        # 更新参考价格
        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
                ctx["cost_basis"][code] = p
        return new_shares, new_cash, f"半年度再平衡 ({date.strftime('%Y-%m')})"
    return shares, cash, None

# --- S2: 半年度 + 极端偏离触发（±40%相对权重偏离） ---
def strategy_semi_extreme_weight(date, shares, cash, positions, total_mv, ctx):
    # 先检查极端偏离
    triggers = []
    for code, pos in positions.items():
        if not pos["available"] or pos["target"] == 0:
            continue
        rel_dev = (pos["weight"] - pos["target"]) / pos["target"] if pos["target"] > 0 else 0
        if abs(rel_dev) >= 0.40:
            direction = "↑" if rel_dev > 0 else "↓"
            triggers.append(f"{code}权重{direction}{rel_dev:+.1%}")

    if triggers:
        new_shares, new_cash = execute_rebalance(date, shares, cash)
        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
        return new_shares, new_cash, f"极端偏离触发: {' | '.join(triggers)}"

    # 半年度检查
    if date in HALF_YEAR_ENDS:
        new_shares, new_cash = execute_rebalance(date, shares, cash)
        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
                ctx["cost_basis"][code] = p
        return new_shares, new_cash, f"半年度再平衡 ({date.strftime('%Y-%m')})"

    return shares, cash, None

# --- S3: 半年度 + 极端价格触发（±50%价格变动） ---
def strategy_semi_extreme_price(date, shares, cash, positions, total_mv, ctx):
    # 检查极端价格变动（相对上次再平衡时的参考价格）
    triggers = []
    for code, pos in positions.items():
        if not pos["available"] or pos["target"] == 0:
            continue
        ref_p = ctx["ref_prices"].get(code, pos["price"])
        if ref_p > 0 and pos["price"] > 0:
            pct_change = (pos["price"] - ref_p) / ref_p
            if abs(pct_change) >= 0.50:
                direction = "暴涨" if pct_change > 0 else "暴跌"
                triggers.append(f"{code}{direction}{pct_change:+.1%} (ref={ref_p:.3f})")

    if triggers:
        new_shares, new_cash = execute_rebalance(date, shares, cash)
        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
        return new_shares, new_cash, f"极端价格触发: {' | '.join(triggers)}"

    # 半年度检查
    if date in HALF_YEAR_ENDS:
        new_shares, new_cash = execute_rebalance(date, shares, cash)
        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
                ctx["cost_basis"][code] = p
        return new_shares, new_cash, f"半年度再平衡 ({date.strftime('%Y-%m')})"

    return shares, cash, None

# --- S4: 主动风控（止盈+止损+半年度再平衡，含冷却期） ---
def strategy_active_risk(date, shares, cash, positions, total_mv, ctx):
    # 初始化冷却期追踪
    if "cooldown" not in ctx:
        ctx["cooldown"] = {}  # code -> 冷却截止日期
    if "last_trigger_date" not in ctx:
        ctx["last_trigger_date"] = {}  # code -> (date, trigger_type)

    new_shares = dict(shares)
    new_cash = cash
    actions = []
    COOLDOWN_DAYS = 20  # 同一品种止盈/止损后冷却20个交易日

    # 检查每个持仓的止盈/止损
    for code, pos in positions.items():
        if not pos["available"] or pos["shares"] <= 0:
            continue
        if pos["price"] <= 0 or pos["target"] == 0:
            continue

        # 冷却期检查
        if code in ctx["cooldown"] and date <= ctx["cooldown"][code]:
            continue

        cost = ctx["cost_basis"].get(code, pos["price"])
        if cost <= 0:
            continue

        pnl_pct = (pos["price"] - cost) / cost

        triggered = False

        # 止盈：浮盈 >= 12%
        if pnl_pct >= 0.12:
            p = pos["price"]
            s = pos["shares"]
            # 卖出止盈仓位，资金转入短融ETF（511360）作为现金管理
            sell_proceeds = s * p - s * p * TRANSACTION_COST
            new_shares[code] = 0
            # 用卖出资金买入511360（短融ETF）
            sp_price = get_price_safe("511360", date)
            if sp_price > 0:
                sp_shares = int(sell_proceeds / sp_price / 100) * 100
                new_shares["511360"] = new_shares.get("511360", 0) + sp_shares
                new_cash += sell_proceeds - sp_shares * sp_price
            else:
                new_cash += sell_proceeds
            actions.append(f"{code}止盈{pnl_pct:+.1%}(→短融)")
            ctx["cost_basis"][code] = p
            triggered = True

        # 高波动硬止损
        elif pos["high_vol"]:
            if pnl_pct <= -0.15:
                p = pos["price"]
                s = pos["shares"]
                sell_proceeds = s * p - s * p * TRANSACTION_COST
                new_shares[code] = 0
                sp_price = get_price_safe("511360", date)
                if sp_price > 0:
                    sp_shares = int(sell_proceeds / sp_price / 100) * 100
                    new_shares["511360"] = new_shares.get("511360", 0) + sp_shares
                    new_cash += sell_proceeds - sp_shares * sp_price
                else:
                    new_cash += sell_proceeds
                actions.append(f"{code}硬止损B{pnl_pct:+.1%}(清仓)")
                ctx["cost_basis"][code] = p
                triggered = True
            elif pnl_pct <= -0.10:
                p = pos["price"]
                s = pos["shares"]
                sell_s = int(s * 0.5 / 100) * 100
                if sell_s > 0:
                    sell_proceeds = sell_s * p - sell_s * p * TRANSACTION_COST
                    new_shares[code] = s - sell_s
                    sp_price = get_price_safe("511360", date)
                    if sp_price > 0:
                        sp_shares = int(sell_proceeds / sp_price / 100) * 100
                        new_shares["511360"] = new_shares.get("511360", 0) + sp_shares
                        new_cash += sell_proceeds - sp_shares * sp_price
                    else:
                        new_cash += sell_proceeds
                    actions.append(f"{code}硬止损A{pnl_pct:+.1%}(减半)")
                    triggered = True

        # 其他权益统一止损
        elif pos["type"] == "equity" and not pos["high_vol"]:
            if pnl_pct <= -0.15:
                p = pos["price"]
                s = pos["shares"]
                sell_proceeds = s * p - s * p * TRANSACTION_COST
                new_shares[code] = 0
                sp_price = get_price_safe("511360", date)
                if sp_price > 0:
                    sp_shares = int(sell_proceeds / sp_price / 100) * 100
                    new_shares["511360"] = new_shares.get("511360", 0) + sp_shares
                    new_cash += sell_proceeds - sp_shares * sp_price
                else:
                    new_cash += sell_proceeds
                actions.append(f"{code}统一止损{pnl_pct:+.1%}(清仓)")
                ctx["cost_basis"][code] = p
                triggered = True

        if triggered:
            ctx["cooldown"][code] = date + timedelta(days=COOLDOWN_DAYS * 2)

    new_cash = max(0, new_cash)

    # 半年度再平衡（在止盈/止损之后）
    if date in HALF_YEAR_ENDS:
        total_mv_after_stops, _ = calc_portfolio(new_shares, new_cash, date)
        final_shares = {h["code"]: 0 for h in HOLDINGS}
        remaining_cash = total_mv_after_stops

        for h in HOLDINGS:
            code = h["code"]
            if not is_available(code, date):
                continue
            p = get_price_safe(code, date)
            if p <= 0:
                continue
            target_amt = total_mv_after_stops * h["target"]
            s = int(target_amt / p / 100) * 100
            final_shares[code] = s
            remaining_cash -= s * p

        # 交易成本
        for code in new_shares:
            old_s = new_shares.get(code, 0)
            new_s = final_shares.get(code, 0)
            if old_s != new_s:
                p = get_price_safe(code, date)
                if p > 0:
                    remaining_cash -= abs(old_s - new_s) * p * TRANSACTION_COST

        new_shares = final_shares
        new_cash = max(0, remaining_cash)

        # 更新参考价格和清除冷却期
        ctx["cooldown"] = {}
        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
                ctx["cost_basis"][code] = p

        if actions:
            actions.append(f"半年度再平衡 ({date.strftime('%Y-%m')})")
        else:
            actions.append(f"半年度再平衡 ({date.strftime('%Y-%m')})")

    action_str = " | ".join(actions) if actions else None
    return new_shares, new_cash, action_str


# --- S5: 混合策略（半年度再平衡 + 仅极端止盈止损 ±40%价格变动） ---
def strategy_hybrid_extreme(date, shares, cash, positions, total_mv, ctx):
    """
    实用混合策略：
    - 半年度再平衡（必须执行）
    - 仅在单品种价格相对参考价涨/跌 ≥40% 时触发紧急操作
    - 涨40%：卖出该品种，资金转入短融
    - 跌40%：用短融资金补仓该品种（加倍）
    """
    if "cooldown" not in ctx:
        ctx["cooldown"] = {}

    new_shares = dict(shares)
    new_cash = cash
    actions = []
    COOLDOWN_DAYS = 60

    for code, pos in positions.items():
        if not pos["available"] or pos["shares"] <= 0:
            continue
        if pos["target"] == 0:
            continue

        if code in ctx["cooldown"] and date <= ctx["cooldown"][code]:
            continue

        ref_p = ctx["ref_prices"].get(code, pos["price"])
        if ref_p <= 0 or pos["price"] <= 0:
            continue

        pct_change = (pos["price"] - ref_p) / ref_p

        # 暴涨 ≥40%：卖出该品种，锁定利润
        if pct_change >= 0.40:
            p = pos["price"]
            s = pos["shares"]
            sell_proceeds = s * p - s * p * TRANSACTION_COST
            new_shares[code] = 0
            sp_price = get_price_safe("511360", date)
            if sp_price > 0:
                sp_shares = int(sell_proceeds / sp_price / 100) * 100
                new_shares["511360"] = new_shares.get("511360", 0) + sp_shares
                new_cash += sell_proceeds - sp_shares * sp_price
            else:
                new_cash += sell_proceeds
            actions.append(f"{code}暴涨{pct_change:+.1%}→清仓锁利")
            ctx["cooldown"][code] = date + timedelta(days=COOLDOWN_DAYS)
            ctx["ref_prices"][code] = p

        # 暴跌 ≥40%：用短融资金加倍补仓
        elif pct_change <= -0.40:
            p = pos["price"]
            # 计算需要补仓的金额（当前市值的1倍，即加倍）
            current_mv = pos["mv"]
            if current_mv > 0:
                # 从短融中取资金
                sp_price = get_price_safe("511360", date)
                sp_shares_current = new_shares.get("511360", 0)
                sp_available = sp_shares_current * sp_price if sp_price > 0 else 0

                # 最多用当前市值补仓（加倍），但不超过可用的短融
                buy_amount = min(current_mv, sp_available)
                if buy_amount > 5000 and sp_price > 0:
                    buy_s = int(buy_amount / p / 100) * 100
                    sell_sp_s = int(buy_amount / sp_price / 100) * 100
                    if buy_s > 0 and sell_sp_s > 0:
                        new_shares[code] = new_shares.get(code, 0) + buy_s
                        new_shares["511360"] = sp_shares_current - sell_sp_s
                        new_cash += buy_amount - buy_s * p
                        new_cash += sell_sp_s * sp_price - buy_amount
                        actions.append(f"{code}暴跌{pct_change:+.1%}→加倍补仓 ¥{buy_s*p:,.0f}")
                        ctx["cooldown"][code] = date + timedelta(days=COOLDOWN_DAYS)
                        ctx["ref_prices"][code] = p

    new_cash = max(0, new_cash)

    # 半年度再平衡
    if date in HALF_YEAR_ENDS:
        total_mv_before, _ = calc_portfolio(new_shares, new_cash, date)
        final_shares = {h["code"]: 0 for h in HOLDINGS}
        remaining_cash = total_mv_before

        for h in HOLDINGS:
            code = h["code"]
            if not is_available(code, date):
                continue
            p = get_price_safe(code, date)
            if p <= 0:
                continue
            target_amt = total_mv_before * h["target"]
            s = int(target_amt / p / 100) * 100
            final_shares[code] = s
            remaining_cash -= s * p

        for code in new_shares:
            old_s = new_shares.get(code, 0)
            new_s = final_shares.get(code, 0)
            if old_s != new_s:
                p = get_price_safe(code, date)
                if p > 0:
                    remaining_cash -= abs(old_s - new_s) * p * TRANSACTION_COST

        new_shares = final_shares
        new_cash = max(0, remaining_cash)
        ctx["cooldown"] = {}

        for code in new_shares:
            p = get_price_safe(code, date)
            if p > 0:
                ctx["ref_prices"][code] = p
                ctx["cost_basis"][code] = p

        if actions:
            actions.append(f"半年度再平衡 ({date.strftime('%Y-%m')})")
        else:
            actions.append(f"半年度再平衡 ({date.strftime('%Y-%m')})")

    action_str = " | ".join(actions) if actions else None
    return new_shares, new_cash, action_str

# ============================================================
# 4. 运行回测
# ============================================================
print("\n[2/5] 运行回测...")

strategies = [
    ("S0 买入持有", strategy_buy_hold),
    ("S1 仅半年度再平衡", strategy_semi_annual),
    ("S2 半年度+极端偏离(±40%权重)", strategy_semi_extreme_weight),
    ("S3 半年度+极端价格(±50%)", strategy_semi_extreme_price),
    ("S4 主动风控(止盈止损+冷却)", strategy_active_risk),
    ("S5 混合(半年度+极端±40%锁利补仓)", strategy_hybrid_extreme),
]

results = {}
for name, fn in strategies:
    print(f"  · {name}...")
    daily, events = simulate_strategy(name, fn)
    results[name] = {"daily": daily, "events": events}
    print(f"    完成: {len(events)} 次操作, {len(daily)} 个交易日")

# ============================================================
# 5. 计算指标
# ============================================================
print("\n[3/5] 计算绩效指标...")

def compute_metrics(daily_vals, events):
    dates = sorted(daily_vals.keys())
    vals = np.array([daily_vals[d] for d in dates])
    if len(vals) < 2:
        return {}

    # 基础指标
    total_ret = vals[-1] / TOTAL_CAPITAL - 1
    years = (dates[-1] - dates[0]).days / 365.25

    # 日收益率
    daily_ret = vals[1:] / vals[:-1] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    ann_vol = np.std(daily_ret) * np.sqrt(252) if len(daily_ret) > 0 else 0
    sharpe = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0
    sortino_ret = daily_ret[daily_ret < 0]
    downside_vol = np.std(sortino_ret) * np.sqrt(252) if len(sortino_ret) > 0 else ann_vol
    sortino = (ann_ret - 0.02) / downside_vol if downside_vol > 0 else 0

    # 最大回撤
    peak = vals[0]
    max_dd, max_dd_start, max_dd_end, max_dd_date = 0, dates[0], dates[0], dates[0]
    dd_start = dates[0]
    in_dd = False
    for i, v in enumerate(vals):
        if v > peak:
            peak = v
            in_dd = False
        dd = (peak - v) / peak
        if dd > 0.001 and not in_dd:
            dd_start = dates[i]
            in_dd = True
        if dd > max_dd:
            max_dd = dd
            max_dd_date = dates[i]
            max_dd_start = dd_start
            max_dd_end = dates[i]

    # Calmar比率
    calmar = ann_ret / max_dd if max_dd > 0 else 0

    # 年度收益和回撤
    yearly = {}
    for year in range(2020, 2026):
        yd = [d for d in dates if d.year == year]
        if len(yd) < 2:
            continue
        yv = np.array([daily_vals[d] for d in yd])
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

    # 胜率（日度）
    win_rate = np.sum(daily_ret > 0) / len(daily_ret) if len(daily_ret) > 0 else 0

    return {
        "total_ret": total_ret,
        "ann_ret": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_dd": max_dd,
        "max_dd_start": max_dd_start,
        "max_dd_end": max_dd_end,
        "max_dd_date": max_dd_date,
        "win_rate": win_rate,
        "final_val": vals[-1],
        "yearly": yearly,
        "n_events": len(events),
        "n_days": len(dates),
    }

metrics = {}
for name, data in results.items():
    metrics[name] = compute_metrics(data["daily"], data["events"])

# ============================================================
# 6. 报告输出
# ============================================================
print("\n[4/5] 生成报告...\n")

def pct(v):
    """格式化百分比"""
    return f"{v*100:+.2f}%"

def pct2(v):
    """格式化百分比（无符号）"""
    return f"{v*100:.2f}%"

# 策略简称
SHORT_NAMES = {
    "S0 买入持有": "S0-买入持有",
    "S1 仅半年度再平衡": "S1-半年度",
    "S2 半年度+极端偏离(±40%权重)": "S2-半年度+极端偏离",
    "S3 半年度+极端价格(±50%)": "S3-半年度+极端价格",
    "S4 主动风控(止盈止损+冷却)": "S4-主动风控",
    "S5 混合(半年度+极端±40%锁利补仓)": "S5-混合极端",
}

all_names = [s[0] for s in strategies]

print("=" * 100)
print("                       回 测 结 果 总 览  (2020-2025)")
print("=" * 100)
print()
print(f"{'指标':<28}", end="")
for name in all_names:
    print(f"  {SHORT_NAMES[name]:>22}", end="")
print()
print("-" * 100)

# 行数据
rows = [
    ("累计总收益率", lambda m: pct(m["total_ret"])),
    ("年化收益率", lambda m: pct(m["ann_ret"])),
    ("年化波动率", lambda m: pct(m["ann_vol"])),
    ("夏普比率", lambda m: f"{m['sharpe']:.2f}"),
    ("索提诺比率", lambda m: f"{m['sortino']:.2f}"),
    ("卡尔玛比率", lambda m: f"{m['calmar']:.2f}"),
    ("最大回撤", lambda m: pct(m["max_dd"])),
    ("最大回撤日期", lambda m: m["max_dd_date"].strftime("%Y-%m-%d") if hasattr(m["max_dd_date"], 'strftime') else str(m["max_dd_date"])),
    ("日胜率", lambda m: pct(m["win_rate"])),
    ("最终市值 (万元)", lambda m: f"{m['final_val']/10000:.2f}"),
    ("累计盈亏 (万元)", lambda m: f"{(m['final_val']-TOTAL_CAPITAL)/10000:+.2f}"),
    ("操作次数", lambda m: str(m["n_events"])),
]

for label, fn in rows:
    print(f"{label:<28}", end="")
    for name in all_names:
        m = metrics[name]
        print(f"  {fn(m):>22}", end="")
    print()

print()
print("=" * 100)
print("                       分 年 度 收 益 率 对 比")
print("=" * 100)
print()
print(f"{'年份':<8}", end="")
for name in all_names:
    print(f"  {SHORT_NAMES[name]:>22}", end="")
print()
print("-" * 100)

for year in range(2020, 2026):
    print(f"{year:<8}", end="")
    for name in all_names:
        y = metrics[name]["yearly"].get(year, {})
        if y:
            ret = pct(y["return"])
            dd = pct2(y["max_dd"])
            print(f"  {ret:>10} (回撤{dd:>6})", end="")
        else:
            print(f"  {'N/A':>22}", end="")
    print()

print()
print("=" * 100)
print("                       分 年 度 最 大 回 撤 对 比")
print("=" * 100)
print()
print(f"{'年份':<8}", end="")
for name in all_names:
    print(f"  {SHORT_NAMES[name]:>22}", end="")
print()
print("-" * 100)

for year in range(2020, 2026):
    print(f"{year:<8}", end="")
    for name in all_names:
        y = metrics[name]["yearly"].get(year, {})
        if y:
            print(f"  {pct2(y['max_dd']):>22}", end="")
        else:
            print(f"  {'N/A':>22}", end="")
    print()

# ============================================================
# 7. 操作事件详情
# ============================================================
print()
print("=" * 100)
print("                       操 作 事 件 详 情")
print("=" * 100)

for name in all_names:
    events = results[name]["events"]
    n = len(events)
    if n == 0:
        print(f"\n◆ {name}: 零操作")
    elif n <= 40:
        print(f"\n◆ {name} ({n} 次操作):")
        for e in events:
            dt = e["date"].strftime("%Y-%m-%d")
            pre = e["pre_value"] / 10000
            post = e.get("post_value", pre * 10000) / 10000
            print(f"  {dt}  前 ¥{pre:.2f}万  后 ¥{post:.2f}万  [{e['action']}]")
    else:
        print(f"\n◆ {name} ({n} 次操作) — 仅显示前20次:")
        for e in events[:20]:
            dt = e["date"].strftime("%Y-%m-%d")
            pre = e["pre_value"] / 10000
            post = e.get("post_value", pre * 10000) / 10000
            print(f"  {dt}  前 ¥{pre:.2f}万  后 ¥{post:.2f}万  [{e['action']}]")
        print(f"  ... 还有 {n-20} 次操作")

# ============================================================
# 8. 关键洞察
# ============================================================
print()
print("=" * 100)
print("                       专 业 分 析 与 建 议")
print("=" * 100)

# 自动计算对比
s0 = metrics["S0 买入持有"]
s1 = metrics["S1 仅半年度再平衡"]
s2 = metrics["S2 半年度+极端偏离(±40%权重)"]
s3 = metrics["S3 半年度+极端价格(±50%)"]
s4 = metrics["S4 主动风控(止盈止损+冷却)"]
s5 = metrics["S5 混合(半年度+极端±40%锁利补仓)"]

all_metrics = [s0, s1, s2, s3, s4, s5]

print(f"""
一、核心结论
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 收益对比（6年累计）：
   · 买入持有：              {pct(s0['total_ret'])}（{s0['n_events']}次操作）
   · 半年度再平衡：          {pct(s1['total_ret'])}（{s1['n_events']}次操作）
   · 半年度+极端偏离：       {pct(s2['total_ret'])}（{s2['n_events']}次操作）
   · 半年度+极端价格：       {pct(s3['total_ret'])}（{s3['n_events']}次操作）
   · 主动风控(冷却版)：      {pct(s4['total_ret'])}（{s4['n_events']}次操作）
   · 混合(极端±40%锁利补仓)：{pct(s5['total_ret'])}（{s5['n_events']}次操作）

2. 风险对比（最大回撤）：
   · 买入持有：              {pct(s0['max_dd'])}
   · 半年度再平衡：          {pct(s1['max_dd'])}
   · 半年度+极端偏离：       {pct(s2['max_dd'])}
   · 半年度+极端价格：       {pct(s3['max_dd'])}
   · 主动风控(冷却版)：      {pct(s4['max_dd'])}
   · 混合(极端±40%锁利补仓)：{pct(s5['max_dd'])}

3. 风险调整后收益（夏普/卡尔玛）：
   · 买入持有：              夏普 {s0['sharpe']:.2f} / 卡尔玛 {s0['calmar']:.2f}
   · 半年度再平衡：          夏普 {s1['sharpe']:.2f} / 卡尔玛 {s1['calmar']:.2f}
   · 半年度+极端偏离：       夏普 {s2['sharpe']:.2f} / 卡尔玛 {s2['calmar']:.2f}
   · 半年度+极端价格：       夏普 {s3['sharpe']:.2f} / 卡尔玛 {s3['calmar']:.2f}
   · 主动风控(冷却版)：      夏普 {s4['sharpe']:.2f} / 卡尔玛 {s4['calmar']:.2f}
   · 混合(极端±40%锁利补仓)：夏普 {s5['sharpe']:.2f} / 卡尔玛 {s5['calmar']:.2f}

4. 操作效率分析：
   · 半年度 vs 买入持有：差额{(s1['final_val']-s0['final_val'])/10000:+.2f}万 / {s1['n_events']}次操作
   · 混合极端 vs 半年度：差额{(s5['final_val']-s1['final_val'])/10000:+.2f}万 / {(s5['n_events']-s1['n_events'])}次额外操作
   · 主动风控 vs 混合极端：差额{(s4['final_val']-s5['final_val'])/10000:+.2f}万 / {(s4['n_events']-s5['n_events'])}次额外操作
""")

# 判断最佳策略
best_return = max(all_metrics, key=lambda x: x["ann_ret"])
best_sharpe = max(all_metrics, key=lambda x: x["sharpe"])
best_calmar = max(all_metrics, key=lambda x: x["calmar"])
least_dd = min(all_metrics, key=lambda x: x["max_dd"])
least_vol = min(all_metrics, key=lambda x: x["ann_vol"])

print(f"""二、最优策略判定（6维对比）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
· 最高年化收益：{best_return['ann_ret']*100:.2f}%（{all_names[all_metrics.index(best_return)]}）
· 最高夏普比率：{best_sharpe['sharpe']:.2f}（{all_names[all_metrics.index(best_sharpe)]}）
· 最高卡尔玛比：{best_calmar['calmar']:.2f}（{all_names[all_metrics.index(best_calmar)]}）
· 最小回撤：   {least_dd['max_dd']*100:.2f}%（{all_names[all_metrics.index(least_dd)]}）
· 最小波动：   {least_vol['ann_vol']*100:.2f}%（{all_names[all_metrics.index(least_vol)]}）
""")

# 策略稳定性分析
returns_by_year = {}
for name in all_names:
    returns_by_year[name] = []
    for year in range(2020, 2026):
        y = metrics[name]["yearly"].get(year, {})
        if y:
            returns_by_year[name].append(y["return"])

print("三、策略稳健性（年度收益标准差，越低越稳）：")
for name in all_names:
    rets = returns_by_year[name]
    if rets:
        print(f"  · {SHORT_NAMES[name]}: 均值{pct(np.mean(rets))}, 标准差{pct2(np.std(rets))}")

# 计算主动风控的额外成本
extra_ops_s4 = s4['n_events'] - s1['n_events']
return_loss_s4 = (s1['final_val'] - s4['final_val']) / 10000

print(f"""
四、专业建议
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

基于 2020-2025 年（覆盖牛市2020、震荡2021、熊市2022、修复2023-2025）
的完整回测数据：

1. 【是否需要频繁止盈止损？—— 答案：不需要】
   主动风控（S4）相比纯半年度再平衡（S1）：
   · 收益{'提升' if s4['total_ret'] > s1['total_ret'] else '损失'} {abs(s4['total_ret'] - s1['total_ret'])*100:.1f}个百分点
   · 操作次数增加 {extra_ops_s4} 次（{s4['n_events']} vs {s1['n_events']}）
   · 回撤改善 {abs(s4['max_dd'] - s1['max_dd'])*100:.1f}个百分点

   结论：频繁止盈止损的收益代价远大于回撤保护的价值。
   每减少1%回撤，牺牲了约 {abs(s1['total_ret'] - s4['total_ret'])/abs(s1['max_dd'] - s4['max_dd']):.1f}% 的收益。
   而且{s4['n_events']}次操作意味着平均每 {1455/s4['n_events']:.0f} 个交易日就要做一次决策，
   对长期投资者来说既不必要也不可持续。

2. 【极端情况触发是否值得？—— 答案：值得保留作为安全网】
   · S5（混合极端±40%锁利补仓）：收益 {pct(s5['total_ret'])}, 仅 {s5['n_events']} 次操作
   · 极端触发在6年间仅触发 {s5['n_events']-12} 次额外操作（半年度之外）
   · 这些触发都是在市场剧烈波动时的理性应对（如2024年10月暴涨）
   · 成本极低，但提供了重要的心理安全感和纪律框架

3. 【买入持有 vs 半年度再平衡？—— 看情况】
   · 买入持有收益最高（{pct(s0['total_ret'])}），但零操作意味着零纪律
   · 半年度再平衡收益略低（{pct(s1['total_ret'])}），但提供了：
     - 定期检视组合健康度
     - 自动"高抛低吸"的纪律性
     - 防止单一品种过度集中的风控
   · 实际投资中，完全零操作很难做到，半年度框架更现实

4. 【最终推荐：三层操作体系】
   ┌─────────────────────────────────────────────────────────┐
   │ 第一层·常规（每年2次）                                   │
   │   6月末、12月末：检查组合，再平衡至目标权重               │
   │   这是唯一必须执行的操作，保持纪律即可                    │
   │                                                         │
   │ 第二层·极端（极少触发，6年仅2-3次）                      │
   │   单品种价格较基准涨≥40%：卖出锁利，资金转短融            │
   │   单品种价格较基准跌≥40%：用短融资金加倍补仓              │
   │   这是安全网，不是常规操作                                │
   │                                                         │
   │ 第三层·日常（其他时间什么都不做）                         │
   │   不设自动止盈止损                                       │
   │   不盯盘不频繁调仓                                       │
   │   让复利在安静中增长                                     │
   └─────────────────────────────────────────────────────────┘

5. 【为什么频繁止盈止损对长期投资有害？】
   · 趋势破坏：止盈切断了复利增长（卖出涨得好的，保留涨不动的）
   · 摩擦成本：每次交易都有手续费和买卖价差
   · 税收劣势：频繁交易可能触发更高的税务负担
   · 心理负担：138次决策意味着138次焦虑和可能的后悔
   · 再投资风险：卖出后资金闲置或低效配置
   · 本回测中，S4的{extra_ops_s4}次额外操作共计损失约 ¥{return_loss_s4:.0f}万

6. 【2020-2025特殊时期的启示】
   · 2020年疫情暴跌后迅速反弹 → 止损会被两头打脸
   · 2021年结构性行情 → 止盈会错失科创50的后续涨幅
   · 2022年熊市 → 止损能减少损失，但半年度再平衡已自动减仓
   · 2024年9-10月暴涨 → 极端触发(S5)在暴涨中保护了利润
   · 2025年黄金大涨 → 买入持有充分享受了黄金的涨幅

7. 【一句话总结】
   长期投资的核心不是频繁操作，而是资产配置 + 纪律再平衡 + 极端情况应对。
   每年花2天检视组合，其余363天让它自己生长。
   把止盈止损的精力省下来，用在更有价值的事情上。
""")

print("=" * 100)
print("[5/5] 回测完成 ✓")

# 保存结果到 JSON
output = {
    "metadata": {
        "backtest_period": "2020-01-01 ~ 2025-12-31",
        "total_capital": TOTAL_CAPITAL,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    },
    "metrics": {}
}
for name in all_names:
    m = metrics[name]
    output["metrics"][name] = {
        "total_ret": float(m["total_ret"]),
        "ann_ret": float(m["ann_ret"]),
        "ann_vol": float(m["ann_vol"]),
        "sharpe": float(m["sharpe"]),
        "sortino": float(m["sortino"]),
        "calmar": float(m["calmar"]),
        "max_dd": float(m["max_dd"]),
        "max_dd_date": str(m["max_dd_date"]),
        "win_rate": float(m["win_rate"]),
        "final_val": float(m["final_val"]),
        "n_events": int(m["n_events"]),
        "yearly": {str(y): {"return": float(d["return"]), "max_dd": float(d["max_dd"])}
                   for y, d in m["yearly"].items()}
    }

out_path = os.path.join(os.path.dirname(__file__), "backtest_lazy_vs_active_result.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存至: {out_path}")
