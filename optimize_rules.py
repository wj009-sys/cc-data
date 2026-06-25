#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
规则排列组合优化器
止盈/止损/再平衡 多参数全组合回测，找出最优收益
"""
import os, sys, json, math, time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
sys.stdout.reconfigure(encoding='utf-8')

import tushare as ts
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

TOTAL = 4_000_000

# ETF配置（简化为用字段控制行为）
ETFS = [
    {'code':'510300','mkt':'SH','name':'沪深300ETF', 'target':0.1875, 'enable_sp':True, 'enable_sl':True, 'is_bond':False},
    {'code':'588050','mkt':'SH','name':'科创50ETF',  'target':0.0625, 'enable_sp':True, 'enable_sl':True, 'is_bond':False},
    {'code':'159915','mkt':'SZ','name':'创业板ETF',  'target':0.0625, 'enable_sp':True, 'enable_sl':True, 'is_bond':False},
    {'code':'512890','mkt':'SH','name':'红利低波ETF','target':0.0625, 'enable_sp':True, 'enable_sl':True, 'is_bond':False},
    {'code':'511380','mkt':'SH','name':'可转债ETF',  'target':0.125,  'enable_sp':True, 'enable_sl':True, 'is_bond':False},
    {'code':'511260','mkt':'SH','name':'10年国债ETF','target':0.125,  'enable_sp':False,'enable_sl':False,'is_bond':True},
    {'code':'511010','mkt':'SH','name':'5年国债ETF', 'target':0.125,  'enable_sp':False,'enable_sl':False,'is_bond':True},
    {'code':'511360','mkt':'SH','name':'短融ETF',    'target':0.0625, 'enable_sp':False,'enable_sl':False,'is_bond':True},
    {'code':'518880','mkt':'SH','name':'黄金ETF',    'target':0.1875, 'enable_sp':True, 'enable_sl':True, 'is_bond':False},
]

def round_lot(q):
    return int(max(0, math.floor(q / 100)) * 100)

# 获取数据
print("获取行情数据...")
all_data = {}
for e in ETFS:
    ts_code = f"{e['code']}.{e['mkt']}"
    df = pro.fund_daily(ts_code=ts_code, start_date='20210101', end_date='20251231',
                        fields='trade_date,close,pre_close')
    if df is not None and len(df):
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
        all_data[e['code']] = df

all_dates = sorted(set().union(*[set(df['trade_date'].dt.date) for df in all_data.values()]))
print(f"  共 {len(all_dates)} 个交易日")

# 价格缓存
price_cache = {}
for d in all_dates:
    price_cache[d] = {}
    for e in ETFS:
        df = all_data[e['code']]
        mask = df['trade_date'].dt.date == d
        price_cache[d][e['code']] = float(df.loc[mask, 'close'].iloc[0]) if mask.any() else None

# ========== 参数网格 ==========
SP_CONFIGS = [
    ('0_无止盈', None, None),
    ('1_sp10卖半', 0.10, 0.50),
    ('2_sp20卖半', 0.20, 0.50),
    ('3_sp30卖半', 0.30, 0.50),  # 用户方案
    ('4_sp40卖半', 0.40, 0.50),
    ('5_sp50卖半', 0.50, 0.50),
]

SL_CONFIGS = [
    ('0_无止损', None),
    ('1_sl15', -0.15),
    ('2_sl20', -0.20),
    ('3_sl25', -0.25),  # 用户方案
    ('4_sl30', -0.30),
]

RB_CONFIGS = [
    ('0_不再平衡', None, None),
    ('1_半年20', 'semi', 0.20),   # 用户方案
    ('2_半年10', 'semi', 0.10),
    ('3_年20', 'annual', 0.20),
    ('4_季20', 'quarter', 0.20),
]

TOTAL_COMBOS = len(SP_CONFIGS) * len(SL_CONFIGS) * len(RB_CONFIGS)
print(f"\n参数组合: {len(SP_CONFIGS)}止盈 × {len(SL_CONFIGS)}止损 × {len(RB_CONFIGS)}再平衡 = {TOTAL_COMBOS} 种")
print()

# ========== 单一回测函数 ==========
def run_backtest(sp_trigger, sp_sell_pct, sl_trigger, rb_freq, rb_threshold):
    """快速回测一次，返回CAGR和交易笔数"""
    # 建仓
    cash = float(TOTAL)
    positions = {}
    for e in ETFS:
        p = {'code': e['code'], 'name': e['name'], 'target': e['target'],
             'enable_sp': e['enable_sp'], 'enable_sl': e['enable_sl'],
             'is_bond': e['is_bond'],
             'qty': 0, 'cost': 0.0, 'cost_total': 0.0,
             'price': 0.0, 'value': 0.0, 'pnl_pct': 0.0,
             'sp_triggered': False, 'sl_triggered': False,
             'cleared_date': None, 'observe_needed': 0}
        positions[e['code']] = p

    # 建仓
    first_date = all_dates[0]
    for e in ETFS:
        p = positions[e['code']]
        price = price_cache[first_date][e['code']]
        if price and price > 0:
            amt = TOTAL * e['target']
            qty = round_lot(amt / price)
            if qty > 0:
                spent = qty * price
                p['qty'] = qty
                p['cost'] = price
                p['cost_total'] = spent
                cash -= spent

    # 剩余现金买入短融
    p360 = positions['511360']
    price360 = price_cache[first_date]['511360']
    if price360 and price360 > 0 and cash > 1000:
        qty360 = round_lot(cash / price360)
        if qty360 > 0:
            spent360 = qty360 * price360
            p360['qty'] += qty360
            p360['cost_total'] += spent360
            p360['cost'] = p360['cost_total'] / p360['qty']
            cash -= spent360

    # 每日循环
    daily_values = []
    trade_count = 0
    peak = float(TOTAL)

    # 再平衡辅助
    def is_rb_date(date, idx, dates_list):
        if rb_freq is None:
            return False
        m = date.month
        if rb_freq == 'quarter':
            if m not in (3, 6, 9, 12):
                return False
        elif rb_freq == 'semi':
            if m not in (6, 12):
                return False
        elif rb_freq == 'annual':
            if m != 12:
                return False
        # 检查是否该月最后交易日
        if idx + 1 < len(dates_list):
            if dates_list[idx + 1].month == m:
                return False
        return True

    for idx, date in enumerate(all_dates):
        # 更新价格
        for e in ETFS:
            p = positions[e['code']]
            pr = price_cache[date][e['code']]
            if pr and pr > 0:
                p['price'] = pr
                if p['qty'] > 0:
                    p['value'] = p['qty'] * pr
                    p['pnl_pct'] = (p['value'] - p['cost_total']) / p['cost_total'] if p['cost_total'] > 0 else 0
                else:
                    p['value'] = 0.0

        # 组合价值
        total_val = cash + sum(positions[e['code']]['value'] for e in ETFS)
        if total_val > peak:
            peak = total_val

        # 权重
        for e in ETFS:
            p = positions[e['code']]
            p['weight'] = p['value'] / total_val if total_val > 0 else 0

        # ===== 止盈检查 =====
        if sp_trigger is not None:
            for e in ETFS:
                p = positions[e['code']]
                if not p['enable_sp'] or p['qty'] <= 0 or p['sp_triggered']:
                    continue
                if p['pnl_pct'] >= sp_trigger:
                    sell_qty = round_lot(p['qty'] * sp_sell_pct)
                    if sell_qty > 0:
                        sold_amt = sell_qty * p['price']
                        cost_part = p['cost'] * sell_qty
                        p['qty'] -= sell_qty
                        p['cost_total'] -= cost_part
                        p['cost'] = p['cost_total'] / p['qty'] if p['qty'] > 0 else 0 if p['qty'] > 0 else 0
                        cash += sold_amt
                        p['sp_triggered'] = True
                        trade_count += 1
                        # 止盈资金再配置：买入短融
                        p360_2 = positions['511360']
                        pr360 = price_cache[date]['511360']
                        if pr360 and pr360 > 0 and sold_amt > 1000:
                            q360 = round_lot(sold_amt / pr360)
                            if q360 > 0:
                                sp360 = q360 * pr360
                                p360_2['qty'] += q360
                                p360_2['cost_total'] += sp360
                                p360_2['cost'] = p360_2['cost_total'] / p360_2['qty'] if p360_2['qty'] > 0 else 0
                                cash -= sp360

        # ===== 止损检查 =====
        if sl_trigger is not None:
            for e in ETFS:
                p = positions[e['code']]
                if not p['enable_sl'] or p['qty'] <= 0 or p['sl_triggered']:
                    continue
                if p['pnl_pct'] <= sl_trigger:
                    sold_amt = p['qty'] * p['price']
                    cash += sold_amt
                    p['sl_triggered'] = True
                    p['cleared_date'] = date
                    p['observe_needed'] = 40
                    p['qty'] = 0
                    p['cost'] = 0
                    p['cost_total'] = 0
                    trade_count += 1

        # ===== 观察期满重新建仓 =====
        for e in ETFS:
            p = positions[e['code']]
            if p['qty'] > 0 or not p['sl_triggered'] or p['cleared_date'] is None:
                continue
            days_since = (date - p['cleared_date']).days
            est_td = int(days_since * 0.7)
            if est_td >= p['observe_needed']:
                # 重建
                target_amt = TOTAL * p['target']
                pr = price_cache[date][p['code']]
                if pr and pr > 0 and target_amt > 1000:
                    # 现金不够卖短融
                    if cash < target_amt:
                        p360_3 = positions['511360']
                        pr360 = price_cache[date]['511360']
                        if pr360 and pr360 > 0 and p360_3['qty'] > 0:
                            need = target_amt - cash
                            sell_q = round_lot(need / pr360)
                            if sell_q > 0:
                                sa = sell_q * pr360
                                cp = p360_3['cost'] * sell_q
                                p360_3['qty'] -= sell_q
                                p360_3['cost_total'] -= cp
                                p360_3['cost'] = p360_3['cost_total'] / p360_3['qty'] if p360_3['qty'] > 0 else 0 if p360_3['qty'] > 0 else 0
                                cash += sa
                    # 买入
                    buy_amt = min(target_amt, cash)
                    qty_buy = round_lot(buy_amt / pr)
                    if qty_buy > 0:
                        spent = qty_buy * pr
                        p['qty'] = qty_buy
                        p['cost'] = pr
                        p['cost_total'] = spent
                        p['sl_triggered'] = False
                        p['sp_triggered'] = False
                        p['cleared_date'] = None
                        p['observe_needed'] = 0
                        cash -= spent
                        trade_count += 1

        # ===== 再平衡 =====
        if is_rb_date(date, idx, all_dates):
            # 计算偏离
            triggers = False
            deviations = []
            for e in ETFS:
                p = positions[e['code']]
                if p['target'] > 0:
                    act_w = p['value'] / total_val if total_val > 0 else 0
                    rel_dev = (act_w - p['target']) / p['target']
                    deviations.append((e['code'], e['name'], act_w, p['target'], rel_dev, p))
                    if abs(rel_dev) > rb_threshold:
                        triggers = True

            if triggers:
                current_total = total_val
                # 卖超配
                for code, name, act_w, tgt_w, rel_dev, p in deviations:
                    if rel_dev > 0.01 and p['qty'] > 0:
                        excess = p['value'] - current_total * tgt_w
                        if excess > 100:
                            pr = price_cache[date][code]
                            if pr and pr > 0:
                                sq = round_lot(excess / pr)
                                if sq > 0:
                                    sa = sq * pr
                                    cp = p['cost'] * sq
                                    p['qty'] -= sq
                                    p['cost_total'] -= cp
                                    p['cost'] = p['cost_total'] / p['qty'] if p['qty'] > 0 else 0 if p['qty'] > 0 else 0
                                    cash += sa
                                    trade_count += 1

                total_val = cash + sum(positions[e['code']]['value'] for e in ETFS)

                # 买低配
                for code, name, act_w, tgt_w, rel_dev, p in sorted(deviations, key=lambda x: x[4]):
                    if rel_dev < -0.01 and p['qty'] >= 0:
                        target_amount = total_val * tgt_w
                        shortfall = target_amount - p['value']
                        if shortfall > 100:
                            pr = price_cache[date][code]
                            if pr and pr > 0:
                                # 现金不够卖短融
                                if cash < shortfall:
                                    p360_4 = positions['511360']
                                    pr360 = price_cache[date]['511360']
                                    if pr360 and pr360 > 0 and p360_4['qty'] > 0:
                                        need = shortfall - cash
                                        sq4 = round_lot(need / pr360)
                                        if sq4 > 0:
                                            sa4 = sq4 * pr360
                                            cp4 = p360_4['cost'] * sq4
                                            p360_4['qty'] -= sq4
                                            p360_4['cost_total'] -= cp4
                                            p360_4['cost'] = p360_4['cost_total'] / p360_4['qty'] if p360_4['qty'] > 0 else 0 if p360_4['qty'] > 0 else 0
                                            cash += sa4
                                buy_amt = min(shortfall, cash)
                                qb = round_lot(buy_amt / pr)
                                if qb > 0:
                                    spent = qb * pr
                                    p['qty'] += qb
                                    p['cost_total'] += spent
                                    p['cost'] = p['cost_total'] / p['qty'] if p['qty'] > 0 else 0
                                    cash -= spent
                                    trade_count += 1

                # 剩余现金入短融
                p360_5 = positions['511360']
                pr360 = price_cache[date]['511360']
                if cash > 5000 and pr360 and pr360 > 0:
                    qb5 = round_lot(cash / pr360)
                    if qb5 > 0:
                        spent5 = qb5 * pr360
                        p360_5['qty'] += qb5
                        p360_5['cost_total'] += spent5
                        p360_5['cost'] = p360_5['cost_total'] / p360_5['qty']
                        cash -= spent5

        # 记录每日值
        daily_values.append(total_val)

    # 计算CAGR
    start_val = float(TOTAL)
    end_val = daily_values[-1] if daily_values else start_val
    years = (all_dates[-1] - all_dates[0]).days / 365.25
    cagr = ((end_val / start_val) ** (1 / years) - 1) * 100 if years > 0 else 0
    total_ret = (end_val / start_val - 1) * 100

    # 最大回撤
    peak_val = 0
    max_dd = 0
    for v in daily_values:
        if v > peak_val:
            peak_val = v
        dd = (v - peak_val) / peak_val
        if dd < max_dd:
            max_dd = dd

    return cagr, total_ret, max_dd * 100, trade_count


# ========== 全组合扫描 ==========
print("运行全组合回测...")
results = []
start_time = time.time()

for si, (sp_label, sp_trig, sp_pct) in enumerate(SP_CONFIGS):
    for sj, (sl_label, sl_trig) in enumerate(SL_CONFIGS):
        for sk, (rb_label, rb_freq, rb_thresh) in enumerate(RB_CONFIGS):
            cagr, total_ret, max_dd, trades = run_backtest(
                sp_trig, sp_pct, sl_trig, rb_freq, rb_thresh)
            combo_id = f"{si}{sj}{sk}"
            results.append({
                'combo': combo_id,
                'sp': sp_label,
                'sl': sl_label,
                'rb': rb_label,
                'cagr': round(cagr, 2),
                'total_ret': round(total_ret, 2),
                'max_dd': round(max_dd, 2),
                'trades': trades,
            })

elapsed = time.time() - start_time
print(f"  完成 {len(results)} 组合，耗时 {elapsed:.0f}秒")

# 排序
results.sort(key=lambda x: x['cagr'], reverse=True)

# ========== 输出结果 ==========
print("\n" + "=" * 120)
print("【Top 20 组合排名（按CAGR降序）】")
print("=" * 120)
print(f"{'排名':>4} | {'止盈':<14} | {'止损':<10} | {'再平衡':<12} | {'CAGR':>7} | {'总收益':>7} | {'最大回撤':>7} | {'交易笔数':>6}")
print("-" * 120)
for i, r in enumerate(results[:20]):
    sp_display = r['sp'].split('_', 1)[1] if '_' in r['sp'] else r['sp']
    sl_display = r['sl'].split('_', 1)[1] if '_' in r['sl'] else r['sl']
    rb_display = r['rb'].split('_', 1)[1] if '_' in r['rb'] else r['rb']
    print(f"{i+1:>4} | {sp_display:<14} | {sl_display:<10} | {rb_display:<12} | {r['cagr']:>6.2f}% | {r['total_ret']:>6.2f}% | {r['max_dd']:>6.2f}% | {r['trades']:>6}")

print("\n" + "=" * 120)
print("【各规则独立影响分析】")
print("=" * 120)

# 按规则分组分析
print("\n--- 止盈影响（固定止损和再平衡取中间值）---")
for sp in SP_CONFIGS:
    subset = [r for r in results if r['sp'] == sp[0] and 'sl20' in r['sl'] and '半年20' in r['rb']]
    if subset:
        r = subset[0]
        sp_display = sp[0].split('_', 1)[1]
        print(f"  {sp_display:<14} → CAGR={r['cagr']:>6.2f}%  总收益={r['total_ret']:>6.2f}%  回撤={r['max_dd']:>5.2f}%  交易{r['trades']:>4}笔")

print("\n--- 止损影响（固定止盈和再平衡取中间值）---")
for sl in SL_CONFIGS:
    subset = [r for r in results if r['sl'] == sl[0] and 'sp30卖半' in r['sp'] and '半年20' in r['rb']]
    if subset:
        r = subset[0]
        sl_display = sl[0].split('_', 1)[1]
        print(f"  {sl_display:<10} → CAGR={r['cagr']:>6.2f}%  总收益={r['total_ret']:>6.2f}%  回撤={r['max_dd']:>5.2f}%  交易{r['trades']:>4}笔")

print("\n--- 再平衡影响（固定止盈和止损取用户方案）---")
for rb in RB_CONFIGS:
    subset = [r for r in results if r['rb'] == rb[0] and 'sp30卖半' in r['sp'] and 'sl25' in r['sl']]
    if subset:
        r = subset[0]
        rb_display = rb[0].split('_', 1)[1]
        print(f"  {rb_display:<12} → CAGR={r['cagr']:>6.2f}%  总收益={r['total_ret']:>6.2f}%  回撤={r['max_dd']:>5.2f}%  交易{r['trades']:>4}笔")

# 保存JSON
output = {
    'top20': results[:20],
    'all': results,
    'config': {
        'sp_configs': [s[0] for s in SP_CONFIGS],
        'sl_configs': [s[0] for s in SL_CONFIGS],
        'rb_configs': [r[0] for r in RB_CONFIGS],
    }
}
with open('optimization_results.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: optimization_results.json")
