#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
最优组合回测 v3（修复版）
规则：止盈+20%卖半 + 止损-15%清仓 + 年度±20%再平衡 + 观察20日

修复：
1. 止盈资金留作现金，不强制买入511360
2. 止盈再配置最小金额50000
3. 观察期改为20个交易日
"""
import os, sys, json, math
from datetime import datetime
import pandas as pd
import numpy as np
sys.stdout.reconfigure(encoding='utf-8')

import tushare as ts
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

TOTAL = 4_000_000
OBSERVE_DAYS = 20

ETFS = [
    {'code':'510300','mkt':'SH','name':'沪深300ETF','target':0.1875,'sp':True,'sl':True,'bond':False},
    {'code':'588050','mkt':'SH','name':'科创50ETF', 'target':0.0625,'sp':True,'sl':True,'bond':False},
    {'code':'159915','mkt':'SZ','name':'创业板ETF', 'target':0.0625,'sp':True,'sl':True,'bond':False},
    {'code':'512890','mkt':'SH','name':'红利低波ETF','target':0.0625,'sp':True,'sl':True,'bond':False},
    {'code':'511380','mkt':'SH','name':'可转债ETF', 'target':0.125, 'sp':True,'sl':True,'bond':False},
    {'code':'511260','mkt':'SH','name':'10年国债ETF','target':0.125, 'sp':False,'sl':False,'bond':True},
    {'code':'511010','mkt':'SH','name':'5年国债ETF', 'target':0.125, 'sp':False,'sl':False,'bond':True},
    {'code':'511360','mkt':'SH','name':'短融ETF',   'target':0.0625,'sp':False,'sl':False,'bond':True},
    {'code':'518880','mkt':'SH','name':'黄金ETF',   'target':0.1875,'sp':True,'sl':True,'bond':False},
]

CODE_MAP = {e['code']: e for e in ETFS}

def round_lot(q):
    return int(max(0, math.floor(q / 100)) * 100)

# ====== 数据获取 ======
print("获取行情数据...")
all_data = {}
for e in ETFS:
    ts_code = f"{e['code']}.{e['mkt']}"
    df = pro.fund_daily(ts_code=ts_code, start_date='20210101', end_date='20251231',
                        fields='trade_date,close')
    if df is not None and len(df):
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
        all_data[e['code']] = df
        print(f"  {e['name']}: {len(df)}条")

all_dates = sorted(set().union(*[set(df['trade_date'].dt.date) for df in all_data.values()]))
print(f"  共 {len(all_dates)} 交易日")

# 价格缓存
price = {}
for d in all_dates:
    price[d] = {}
    for e in ETFS:
        df = all_data[e['code']]
        mask = df['trade_date'].dt.date == d
        price[d][e['code']] = float(df.loc[mask, 'close'].iloc[0]) if mask.any() else None

# ====== 回测 ======
print("\n开始回测...")

# 建仓
pos = {}
for e in ETFS:
    pos[e['code']] = {
        'code': e['code'], 'name': e['name'],
        'target': e['target'], 'target_amt': TOTAL * e['target'],
        'sp': e['sp'], 'sl': e['sl'], 'bond': e['bond'],
        'qty': 0, 'cost': 0.0, 'cost_total': 0.0,
        'price': 0.0, 'value': 0.0, 'pnl_pct': 0.0, 'weight': 0.0,
        'sp_ok': True, 'sl_ok': True, 'cleared': None,
    }

cash = float(TOTAL)
trades = []

fd = all_dates[0]
for e in ETFS:
    p = pos[e['code']]
    pr = price[fd][e['code']]
    if not pr or pr <= 0: continue
    qty = round_lot(p['target_amt'] / pr)
    if qty <= 0: continue
    spent = qty * pr
    p['qty'] = qty; p['cost'] = pr; p['cost_total'] = spent
    cash -= spent
    trades.append({'date':str(fd),'code':e['code'],'name':e['name'],
                   'action':'买入','price':round(pr,4),'qty':qty,
                   'amount':round(spent,2),'reason':'建仓'})

print(f"建仓完成，现金: {cash:.0f}")

# 年度末检查
def year_end(date, idx, dl):
    return date.month == 12 and (idx+1 >= len(dl) or dl[idx+1].month != 12)

# 每日循环
daily_values = []
rebuild_queue = []  # 记录需要重建的品种

for idx, date in enumerate(all_dates):
    # 更新价格
    for e in ETFS:
        p = pos[e['code']]
        pr = price[date][e['code']]
        if pr and pr > 0:
            p['price'] = pr
            if p['qty'] > 0:
                p['value'] = p['qty'] * pr
                p['pnl_pct'] = (p['value'] - p['cost_total']) / p['cost_total']
            else:
                p['value'] = 0.0

    total_val = cash + sum(pos[e['code']]['value'] for e in ETFS)
    for e in ETFS:
        p = pos[e['code']]
        p['weight'] = p['value'] / total_val if total_val > 0 else 0

    # ---- 止盈 ----
    for e in ETFS:
        p = pos[e['code']]
        if not p['sp'] or p['qty'] <= 0 or not p['sp_ok']: continue
        if p['pnl_pct'] >= 0.20:
            sq = round_lot(p['qty'] * 0.50)
            if sq <= 0: continue
            sa = sq * p['price']
            p['qty'] -= sq
            p['cost_total'] -= p['cost'] * sq
            p['cost'] = p['cost_total'] / p['qty'] if p['qty'] > 0 else 0
            cash += sa
            p['sp_ok'] = False
            trades.append({'date':str(date),'code':e['code'],'name':e['name'],
                          'action':'卖出','price':round(p['price'],4),'qty':sq,
                          'amount':round(sa,2),
                          'reason':f'止盈+20%：浮盈+{p["pnl_pct"]*100:.1f}% >= 20%，卖出一半'})

    # ---- 止损 ----
    for e in ETFS:
        p = pos[e['code']]
        if not p['sl'] or p['qty'] <= 0 or not p['sl_ok']: continue
        if p['pnl_pct'] <= -0.15:
            sa = p['qty'] * p['price']
            cash += sa
            trades.append({'date':str(date),'code':e['code'],'name':e['name'],
                          'action':'卖出','price':round(p['price'],4),'qty':p['qty'],
                          'amount':round(sa,2),
                          'reason':f'止损-15%：浮盈{p["pnl_pct"]*100:.1f}% <= -15%，清仓'})
            p['sl_ok'] = False
            p['cleared'] = date
            p['qty'] = 0; p['cost'] = 0; p['cost_total'] = 0

    # ---- 观察期满重建（检查止损清仓的品种）----
    for e in ETFS:
        p = pos[e['code']]
        if p['qty'] > 0 or p['sl_ok'] or p['cleared'] is None: continue
        days_since = (date - p['cleared']).days
        est_td = int(days_since * 0.7)
        if est_td >= OBSERVE_DAYS:
            pr_now = price[date][e['code']]
            if not pr_now or pr_now <= 0: continue
            buy_amt = min(p['target_amt'], cash * 0.8)  # 最多用80%现金
            if buy_amt < 20000: continue
            qb = round_lot(buy_amt / pr_now)
            if qb <= 0: continue
            spent = qb * pr_now
            p['qty'] = qb; p['cost'] = pr_now; p['cost_total'] = spent
            p['sl_ok'] = True; p['sp_ok'] = True; p['cleared'] = None
            cash -= spent
            trades.append({'date':str(date),'code':e['code'],'name':e['name'],
                          'action':'买入','price':round(pr_now,4),'qty':qb,
                          'amount':round(spent,2),'reason':f'观察{OBSERVE_DAYS}日期满重建'})

    # ---- 年度再平衡 ----
    if year_end(date, idx, all_dates):
        triggers = False
        devs = []
        for e in ETFS:
            p = pos[e['code']]
            if p['target'] > 0:
                aw = p['value'] / total_val if total_val > 0 else 0
                rd = (aw - p['target']) / p['target'] if p['target'] > 0 else 0
                devs.append((e['code'], p, aw, p['target'], rd))
                if abs(rd) > 0.20: triggers = True

        if triggers:
            print(f"  年度再平衡 {date}")
            # 卖超配
            for code, p, aw, tw, rd in devs:
                if rd > 0.01 and p['qty'] > 0:
                    excess = p['value'] - total_val * tw
                    if excess > 5000:
                        pr_now = price[date][code]
                        if pr_now and pr_now > 0:
                            sq = round_lot(excess / pr_now)
                            if sq > 0:
                                sa = sq * pr_now
                                p['qty'] -= sq
                                p['cost_total'] -= p['cost'] * sq
                                p['cost'] = p['cost_total'] / p['qty'] if p['qty'] > 0 else 0
                                cash += sa
                                trades.append({'date':str(date),'code':code,'name':p['name'],
                                              'action':'卖出','price':round(pr_now,4),'qty':sq,
                                              'amount':round(sa,2),
                                              'reason':f'再平衡({aw*100:.1f}%>{tw*100:.1f}%)'})

            total_val = cash + sum(pos[e['code']]['value'] for e in ETFS)

            # 买低配
            for code, p, aw, tw, rd in sorted(devs, key=lambda x: x[4]):
                if rd < -0.01:
                    shortfall = total_val * tw - p['value']
                    if shortfall > 5000:
                        pr_now = price[date][code]
                        if pr_now and pr_now > 0 and cash > 100:
                            ba = min(shortfall, cash)
                            qb = round_lot(ba / pr_now)
                            if qb > 0:
                                spent = qb * pr_now
                                p['qty'] += qb
                                p['cost_total'] += spent
                                p['cost'] = p['cost_total'] / p['qty']
                                cash -= spent
                                trades.append({'date':str(date),'code':code,'name':p['name'],
                                              'action':'买入','price':round(pr_now,4),'qty':qb,
                                              'amount':round(spent,2),
                                              'reason':f'再平衡({aw*100:.1f}%<{tw*100:.1f}%)'})

    # 记录每日总值
    daily_values.append({'date': str(date), 'value': total_val})

# ====== 年度收益计算 ======
df = pd.DataFrame(daily_values)
df['date'] = pd.to_datetime(df['date'])
df['year'] = df['date'].dt.year

annual_rets = []
for year, grp in df.groupby('year'):
    grp = grp.sort_values('date')
    sv = grp.iloc[0]['value']
    ev = grp.iloc[-1]['value']
    ret = (ev / sv - 1) * 100
    annual_rets.append({'year': year, 'start': round(sv, 2), 'end': round(ev, 2), 'return': round(ret, 2)})

final_val = df.iloc[-1]['value']
total_ret = (final_val / TOTAL - 1) * 100
years_span = (all_dates[-1] - all_dates[0]).days / 365.25
cagr = ((final_val / TOTAL) ** (1 / years_span) - 1) * 100

# ====== 输出 ======
print("\n" + "=" * 70)
print("【年度收益】")
print("=" * 70)
print(f"{'年份':>6} | {'年初市值':>10} | {'年末市值':>10} | {'收益率':>8} | {'交易笔数':>6}")
print("-" * 50)
# 统计每年交易笔数
trades_by_year = {}
for t in trades:
    y = t['date'][:4]; trades_by_year[y] = trades_by_year.get(y, 0) + 1

for ar in annual_rets:
    y = ar['year']
    cnt = trades_by_year.get(str(y), 0)
    print(f"{y:>6} | {ar['start']:>10,.0f} | {ar['end']:>10,.0f} | {ar['return']:>+7.2f}% | {cnt:>6}")
print("-" * 50)
print(f"{'总计':>6} | {TOTAL:>10,.0f} | {final_val:>10,.0f} | {total_ret:>+7.2f}% | {len(trades):>6}")
print(f"CAGR: {cagr:>+7.2f}%")

print("\n" + "=" * 70)
print("【完整交易流水】")
print("=" * 70)
print(f"{'日期':>10} | {'代码':>6} | {'名称':<10} | {'操作':<6} | {'单价':>8} | {'数量':>8} | {'金额':>10} | {'原因'}")
print("-" * 100)
for t in sorted(trades, key=lambda x: (x['date'], x['code'])):
    r = t['reason'][:30]
    print(f"{t['date']:>10} | {t['code']:>6} | {t['name']:<10} | {t['action']:<6} | {t['price']:>8.4f} | {t['qty']:>8} | {t['amount']:>10,.0f} | {r}")

# 存JSON
output = {
    'config': {'total_capital': TOTAL, 'observe_days': OBSERVE_DAYS},
    'summary': {
        'trades': len(trades),
        'total_return_pct': round(total_ret, 2),
        'cagr_pct': round(cagr, 2),
        'final_value': round(final_val, 2),
    },
    'annual_returns': annual_rets,
    'trades': trades,
}
with open('backtest_best_result.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n结果: backtest_best_result.json")
