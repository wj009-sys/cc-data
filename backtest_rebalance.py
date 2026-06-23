#!/usr/bin/env python
"""
400万组合再平衡策略回测: 2020-01-01 ~ 2025-12-31
策略A：当前规则（±7%/±10% 绝对偏离再平衡）
策略B：每半年度末 ±20% 相对阀值再平衡

处理逻辑：
- ETF 未上市期间：对应目标资金保留为现金
- 始终使用原始目标权重计算偏离
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import tushare as ts
import pandas as pd
import numpy as np

pro = ts.pro_api()

HOLDINGS = [
    {"code": "510300", "name": "沪深300ETF",   "ts": "510300.SH", "target": 0.1875},
    {"code": "588050", "name": "科创50ETF",    "ts": "588050.SH", "target": 0.0625},
    {"code": "159915", "name": "创业板ETF",    "ts": "159915.SZ", "target": 0.0625},
    {"code": "512890", "name": "红利低波ETF",  "ts": "512890.SH", "target": 0.0625},
    {"code": "511380", "name": "可转债ETF",    "ts": "511380.SH", "target": 0.1250},
    {"code": "511260", "name": "10年国债ETF",  "ts": "511260.SH", "target": 0.1250},
    {"code": "511010", "name": "5年国债ETF",   "ts": "511010.SH", "target": 0.1250},
    {"code": "511360", "name": "短融ETF",      "ts": "511360.SH", "target": 0.0625},
    {"code": "518880", "name": "黄金ETF",      "ts": "518880.SH", "target": 0.1875},
]
TOTAL_CAPITAL = 4_000_000

print("=" * 70)
print("  400万组合再平衡策略回测: 2020-01-01 ~ 2025-12-31")
print("=" * 70)

# ============================================================
# 1. 获取数据
# ============================================================
print("\n[1/4] 获取历史行情...")

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
            print(f"  ✓ {h['code']} {h['name']}: {len(df)}条, 首日 {df.index[0].strftime('%Y-%m-%d')}")
    except Exception as e:
        print(f"  ✗ {h['code']}: {e}")

# 价格矩阵
price = pd.DataFrame(all_dfs).sort_index()
price = price[(price.index >= "2020-01-01") & (price.index <= "2025-12-31")]
price = price.ffill()

# 上市前标记为 NaN
for h in HOLDINGS:
    code = h["code"]
    if code in first_dates:
        price.loc[price.index < first_dates[code], code] = np.nan

trading_days = list(price.index)
print(f"\n  交易日: {len(trading_days)} 天, {trading_days[0].strftime('%Y-%m-%d')} ~ {trading_days[-1].strftime('%Y-%m-%d')}")

# 半年度检查日
half_year_ends = set()
for i, d in enumerate(trading_days):
    if d.month in (6, 12):
        is_last = True
        for j in range(i+1, min(i+10, len(trading_days))):
            if trading_days[j].month == d.month:
                is_last = False
                break
        if is_last:
            half_year_ends.add(d)
print(f"  半年度检查日: {len(half_year_ends)} 个")

# ============================================================
# 2. 回测引擎
# ============================================================
print("\n[2/4] 运行回测...")

def is_available(code, date):
    """ETF 在指定日期是否已上市"""
    fd = first_dates.get(code)
    return fd is not None and date >= fd

def simulate(strategy_name, check_fn):
    """
    回测模拟
    - 使用原始目标权重计算偏离
    - 未上市 ETF 的资金保留为现金
    - 新 ETF 上市时自动用现金建仓
    """
    start_date = trading_days[0]
    shares = {h["code"]: 0 for h in HOLDINGS}
    cash = TOTAL_CAPITAL

    # 初始建仓: 每只已上市 ETF 按其原始目标分配资金
    for h in HOLDINGS:
        code = h["code"]
        if not is_available(code, start_date):
            continue
        p = price.loc[start_date, code]
        if pd.isna(p) or p <= 0:
            continue
        target_amt = TOTAL_CAPITAL * h["target"]
        s = int(target_amt / p / 100) * 100
        shares[code] = s
        cash -= s * p
    cash = max(0, cash)

    daily_value = {}
    rebalance_log = []
    half_checked = set()
    prev_available = set(code for code in shares if is_available(code, start_date))

    for i, date in enumerate(trading_days):
        # 1. 检查新上市 ETF → 现金建仓
        curr_available = set(h["code"] for h in HOLDINGS if is_available(h["code"], date))
        new_listings = curr_available - prev_available

        if new_listings:
            # 新 ETF 上市: 用现有现金 + 卖出部分现有持仓来建仓
            # 计算当前总市值
            total_mv_now = cash
            for h in HOLDINGS:
                code = h["code"]
                p = price.loc[date, code] if code in price.columns else 0
                if pd.isna(p): p = 0
                total_mv_now += shares[code] * p

            # 重新分配: 所有已上市 ETF 按原始目标权重
            new_cash = total_mv_now
            for h in HOLDINGS:
                code = h["code"]
                if not is_available(code, date):
                    continue
                p = price.loc[date, code] if code in price.columns else 0
                if pd.isna(p) or p <= 0:
                    continue
                target_amt = total_mv_now * h["target"]
                s = int(target_amt / p / 100) * 100
                shares[code] = s
                new_cash -= s * p

            cash = max(0, new_cash)
            prev_available = curr_available

        # 2. 计算当日持仓 (先算总市值，再算权重)
        total_mv = cash
        mvs = {}
        for h in HOLDINGS:
            code = h["code"]
            p = price.loc[date, code] if code in price.columns else 0
            if pd.isna(p): p = 0
            mv = shares[code] * p
            mvs[code] = mv
            total_mv += mv

        positions = {}
        for h in HOLDINGS:
            code = h["code"]
            mv = mvs[code]
            p = price.loc[date, code] if code in price.columns else 0
            if pd.isna(p): p = 0
            positions[code] = {
                "shares": shares[code], "price": p, "mv": mv,
                "target": h["target"],
                "weight": mv / total_mv if total_mv > 0 else 0,
                "available": is_available(code, date),
            }

        daily_value[date] = total_mv

        # 3. 半年度末判断
        is_half_end = date in half_year_ends

        # 4. 检查再平衡
        should_rebalance, reason = check_fn(date, positions, total_mv, is_half_end, half_checked)

        if should_rebalance and reason:
            rebalance_log.append({
                "date": date, "reason": reason, "pre_value": total_mv,
            })

            # 执行再平衡: 全仓按原始目标权重重调
            new_total = total_mv
            new_cash = new_total
            for h in HOLDINGS:
                code = h["code"]
                if not is_available(code, date):
                    shares[code] = 0
                    continue
                p = price.loc[date, code] if code in price.columns else 0
                if pd.isna(p) or p <= 0:
                    shares[code] = 0
                    continue
                target_amt = new_total * h["target"]
                s = int(target_amt / p / 100) * 100
                shares[code] = s
                new_cash -= s * p

            cash = max(0, new_cash)

    return daily_value, rebalance_log


# ============================================================
# 策略A: 当前规则（±7%/±10%）
# ============================================================
def check_a(date, positions, total_mv, half_end, half_checked):
    triggers = []
    for code, pos in positions.items():
        if not pos["available"]:
            continue  # 未上市, 跳过检查
        dev = pos["weight"] - pos["target"]
        if abs(dev) >= 0.10:
            triggers.append(f"{code}偏离{dev:+.2%}(≥±10%)")
        elif abs(dev) >= 0.07:
            triggers.append(f"{code}偏离{dev:+.2%}(≥±7%)")
    if triggers:
        return True, " | ".join(triggers)
    return False, None

print("  · 策略A (当前规则)...")
daily_a, events_a = simulate("A", check_a)
print(f"    完成, {len(events_a)} 次再平衡")

# ============================================================
# 策略B: 半年度末 ±20% 阀值
# ============================================================
def check_b(date, positions, total_mv, half_end, half_checked):
    if not half_end:
        return False, None

    month = date.month
    half_key = f"{date.year}-H{1 if month <= 6 else 2}"
    if half_key in half_checked:
        return False, None
    half_checked.add(half_key)

    triggers = []
    for code, pos in positions.items():
        if not pos["available"]:
            continue
        target = pos["target"]
        if target == 0:
            continue
        rel_dev = (pos["weight"] - target) / target
        if abs(rel_dev) > 0.20:
            triggers.append(f"{code}相对偏离{rel_dev:+.1%}")

    if triggers:
        return True, f"半年度{half_key}检查: " + " | ".join(triggers)
    return False, None

print("  · 策略B (半年度末±20%阀值)...")
daily_b, events_b = simulate("B", check_b)
print(f"    完成, {len(events_b)} 次再平衡")

# ============================================================
# 3. 指标
# ============================================================
print("\n[3/4] 计算指标...")

def compute_metrics(daily_vals, events):
    dates = sorted(daily_vals.keys())
    vals = np.array([daily_vals[d] for d in dates])
    if len(vals) < 2:
        return {}

    daily_ret = vals[1:] / vals[:-1] - 1
    total_ret = vals[-1] / TOTAL_CAPITAL - 1
    years = (dates[-1] - dates[0]).days / 365.25
    ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    ann_vol = np.std(daily_ret) * np.sqrt(252) if len(daily_ret) > 0 else 0
    sharpe = (ann_ret - 0.02) / ann_vol if ann_vol > 0 else 0

    peak = vals[0]
    max_dd, max_dd_date = 0, dates[0]
    for i, v in enumerate(vals):
        if v > peak: peak = v
        dd = (peak - v) / peak
        if dd > max_dd: max_dd, max_dd_date = dd, dates[i]

    yearly = {}
    for year in range(2020, 2026):
        yd = [d for d in dates if d.year == year]
        if len(yd) < 2: continue
        yv = np.array([daily_vals[d] for d in yd])
        y_ret = yv[-1] / yv[0] - 1
        y_peak, y_dd = yv[0], 0
        for v in yv:
            if v > y_peak: y_peak = v
            dd = (y_peak - v) / y_peak
            if dd > y_dd: y_dd = dd
        yearly[year] = {"return": y_ret, "max_dd": y_dd}

    return {
        "total_ret": total_ret, "ann_ret": ann_ret, "ann_vol": ann_vol,
        "sharpe": sharpe, "max_dd": max_dd, "max_dd_date": max_dd_date,
        "final_val": vals[-1], "yearly": yearly, "n_events": len(events),
        "n_days": len(dates),
    }

ma = compute_metrics(daily_a, events_a)
mb = compute_metrics(daily_b, events_b)

# ============================================================
# 4. 报告
# ============================================================
print("\n" + "=" * 70)
print("                    回 测 结 果 报 告")
print("=" * 70)

def p(v): return f"{v*100:+.2f}%"

print(f"""
┌──────────────────────────────────────────────────────────────────────┐
│ {'指标':<36} {'策略A (当前规则)':>18}  {'策略B (半年度±20%)':>18} │
├──────────────────────────────────────────────────────────────────────┤
│ {'再平衡触发次数':<36} {ma['n_events']:>18}  {mb['n_events']:>18} │
│ {'累计总收益率':<36} {p(ma['total_ret']):>18}  {p(mb['total_ret']):>18} │
│ {'年化收益率':<36} {p(ma['ann_ret']):>18}  {p(mb['ann_ret']):>18} │
│ {'年化波动率':<36} {p(ma['ann_vol']):>18}  {p(mb['ann_vol']):>18} │
│ {'夏普比率':<36} {ma['sharpe']:>18.2f}  {mb['sharpe']:>18.2f} │
│ {'最大回撤':<36} {p(ma['max_dd']):>18}  {p(mb['max_dd']):>18} │
│ {'最大回撤日期':<36} {ma['max_dd_date'].strftime('%Y-%m-%d'):>18}  {mb['max_dd_date'].strftime('%Y-%m-%d'):>18} │
│ {'最终市值 (万元)':<36} {ma['final_val']/10000:>18.2f}  {mb['final_val']/10000:>18.2f} │
└──────────────────────────────────────────────────────────────────────┘
""")

print("┌──────┬──────────────────────────────┬──────────────────────────────┐")
print("│ 年份 │       策略A (当前规则)       │    策略B (半年度±20%)        │")
print("│      │    收益率        最大回撤    │    收益率        最大回撤    │")
print("├──────┼──────────────────────────────┼──────────────────────────────┤")
for year in range(2020, 2026):
    ya = ma["yearly"].get(year, {})
    yb = mb["yearly"].get(year, {})
    ra = p(ya.get("return", 0)) if ya else "     N/A"
    da = p(ya.get("max_dd", 0)) if ya else "     N/A"
    rb = p(yb.get("return", 0)) if yb else "     N/A"
    db = p(yb.get("max_dd", 0)) if yb else "     N/A"
    print(f"│ {year} │ {ra:>10}      {da:>10} │ {rb:>10}      {db:>10} │")
print("└──────┴──────────────────────────────┴──────────────────────────────┘")

if events_a:
    print(f"\n◆ 策略A 再平衡事件 ({len(events_a)} 次):")
    for e in events_a:
        print(f"  {e['date'].strftime('%Y-%m-%d')}  调仓前 ¥{e['pre_value']/10000:.2f}万  [{e['reason']}]")

if events_b:
    print(f"\n◆ 策略B 再平衡事件 ({len(events_b)} 次):")
    for e in events_b:
        print(f"  {e['date'].strftime('%Y-%m-%d')}  调仓前 ¥{e['pre_value']/10000:.2f}万  [{e['reason']}]")

print("\n[4/4] 回测完成 ✓")
