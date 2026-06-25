#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
止盈后补仓方案对比回测（2021-2025）
基础规则：止盈+20%卖半（黄金除外）+ 止损-15%观察20日（黄金除外）+ 年度±20%再平衡
对比三种止盈后处理方案：
  A - 等年度再平衡（现状）
  B - 价格回落≥10%时补回
  D - 季度审视，偏离<-20%时补回
"""
import os, sys, math, json
from datetime import datetime
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

import tushare as ts
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

TOTAL = 4_000_000
OBSERVE_DAYS = 20
SP_TRIGGER = 0.20  # 止盈+20%
SP_SELL_PCT = 0.50  # 卖一半
SL_TRIGGER = -0.15  # 止损-15%

ETFS = [
    {'code':'510300','mkt':'SH','name':'沪深300ETF','target':0.1875,'sp':True,'sl':True},
    {'code':'588050','mkt':'SH','name':'科创50ETF', 'target':0.0625,'sp':True,'sl':True},
    {'code':'159915','mkt':'SZ','name':'创业板ETF', 'target':0.0625,'sp':True,'sl':True},
    {'code':'512890','mkt':'SH','name':'红利低波ETF','target':0.0625,'sp':True,'sl':True},
    {'code':'511380','mkt':'SH','name':'可转债ETF', 'target':0.125, 'sp':True,'sl':True},
    {'code':'511260','mkt':'SH','name':'10年国债ETF','target':0.125, 'sp':False,'sl':False},
    {'code':'511010','mkt':'SH','name':'5年国债ETF', 'target':0.125, 'sp':False,'sl':False},
    {'code':'511360','mkt':'SH','name':'短融ETF',   'target':0.0625,'sp':False,'sl':False},
    {'code':'518880','mkt':'SH','name':'黄金ETF',   'target':0.1875,'sp':False,'sl':False},
]

def round_lot(q): return int(max(0, math.floor(q/100))*100)

# 获取数据
print("获取数据...")
all_data = {}
for e in ETFS:
    df = pro.fund_daily(ts_code=f"{e['code']}.{e['mkt']}", start_date='20210101', end_date='20251231',
                        fields='trade_date,close')
    if df is not None and len(df):
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
        all_data[e['code']] = df

all_dates = sorted(set().union(*[set(df['trade_date'].dt.date) for df in all_data.values()]))
price = {}
for d in all_dates:
    price[d] = {}
    for e in ETFS:
        df = all_data[e['code']]
        mask = df['trade_date'].dt.date == d
        price[d][e['code']] = float(df.loc[mask,'close'].iloc[0]) if mask.any() else None

print(f"共 {len(all_dates)} 交易日")

def year_end(date, idx, dl):
    return date.month == 12 and (idx+1>=len(dl) or dl[idx+1].month!=12)

def quarter_end(date, idx, dl):
    m = date.month
    if m not in (3,6,9,12): return False
    return idx+1>=len(dl) or dl[idx+1].month!=m

def run_backtest(mode):
    """mode: 'A'=年度再平衡  'B'=价格回落补回  'D'=季度审视"""
    pos = {}
    for e in ETFS:
        pos[e['code']] = {
            'code':e['code'],'name':e['name'],'target':e['target'],'target_amt':TOTAL*e['target'],
            'sp':e['sp'],'sl':e['sl'],
            'qty':0,'cost':0.0,'cost_total':0.0,
            'price':0.0,'value':0.0,'pnl_pct':0.0,'weight':0.0,
            'sp_ok':True,'sl_ok':True,'cleared':None,
            'sp_price':None,  # 止盈触发价（方案B用）
        }

    cash = float(TOTAL)
    trades = []

    # 建仓
    fd = all_dates[0]
    for e in ETFS:
        p = pos[e['code']]; pr = price[fd][e['code']]
        if not pr or pr<=0: continue
        qty = round_lot(p['target_amt']/pr)
        if qty<=0: continue
        spent = qty*pr; p['qty']=qty; p['cost']=pr; p['cost_total']=spent; cash-=spent
        trades.append({'date':str(fd),'code':e['code'],'name':e['name'],'action':'买入',
                       'price':round(pr,4),'qty':qty,'amount':round(spent,2),'reason':'建仓'})

    # 每日循环
    daily_values = []
    for idx, date in enumerate(all_dates):
        # 更新价格
        for e in ETFS:
            p = pos[e['code']]; pr = price[date][e['code']]
            if pr and pr>0:
                p['price']=pr
                if p['qty']>0:
                    p['value']=p['qty']*pr
                    p['pnl_pct']=(p['value']-p['cost_total'])/p['cost_total']
                else: p['value']=0.0

        total_val = cash + sum(pos[e['code']]['value'] for e in ETFS)
        for e in ETFS:
            p = pos[e['code']]; p['weight']=p['value']/total_val if total_val>0 else 0

        # ===== 止盈 =====
        for e in ETFS:
            p = pos[e['code']]
            if not p['sp'] or p['qty']<=0 or not p['sp_ok']: continue
            if p['pnl_pct'] >= SP_TRIGGER:
                sq = round_lot(p['qty']*SP_SELL_PCT)
                if sq<=0: continue
                sa = sq*p['price']
                p['qty']-=sq; p['cost_total']-=p['cost']*sq
                p['cost']=p['cost_total']/p['qty'] if p['qty']>0 else 0
                cash += sa
                p['sp_ok']=False
                p['sp_price']=p['price']  # 记录止盈触发价
                trades.append({'date':str(date),'code':e['code'],'name':e['name'],'action':'卖出',
                               'price':round(p['price'],4),'qty':sq,'amount':round(sa,2),
                               'reason':f'止盈+20%：浮盈+{p["pnl_pct"]*100:.1f}%'})

        # ===== 方案B：价格回落补回 =====
        if mode == 'B':
            for e in ETFS:
                p = pos[e['code']]
                if not p['sp'] or p['sp_ok'] or p['sp_price'] is None: continue
                if p['qty']>0: continue  # 已有仓位
                # 价格从止盈触发价回落≥10%
                if p['price'] <= p['sp_price'] * 0.90:
                    # 用511360现金补回至目标仓位
                    target_qty = round_lot(p['target_amt']/p['price'])
                    current_val = p['qty']*p['price']
                    need_val = p['target_amt'] - current_val
                    if need_val > 5000:
                        # 从511360或现金取钱
                        buy_amt = min(need_val, cash)
                        if buy_amt > 5000:
                            qb = round_lot(buy_amt/p['price'])
                            if qb > 0:
                                spent = qb*p['price']
                                p['qty']+=qb; p['cost_total']+=spent; p['cost']=p['cost_total']/p['qty']
                                cash -= spent
                                p['sp_ok']=True; p['sp_price']=None
                                trades.append({'date':str(date),'code':e['code'],'name':e['name'],'action':'买入(补回)',
                                               'price':round(p['price'],4),'qty':qb,'amount':round(spent,2),
                                               'reason':f'止盈后回落≥10%，补回至目标仓位'})

        # ===== 止损 =====
        for e in ETFS:
            p = pos[e['code']]
            if not p['sl'] or p['qty']<=0 or not p['sl_ok']: continue
            if p['pnl_pct'] <= SL_TRIGGER:
                sa = p['qty']*p['price']; cash += sa
                trades.append({'date':str(date),'code':e['code'],'name':e['name'],'action':'卖出',
                               'price':round(p['price'],4),'qty':p['qty'],'amount':round(sa,2),
                               'reason':f'止损-15%：浮盈{p["pnl_pct"]*100:.1f}%'})
                p['sl_ok']=False; p['cleared']=date; p['qty']=0; p['cost']=0; p['cost_total']=0

        # ===== 观察期满重建 =====
        for e in ETFS:
            p = pos[e['code']]
            if p['qty']>0 or p['sl_ok'] or p['cleared'] is None: continue
            days_since = (date-p['cleared']).days
            if int(days_since*0.7) >= OBSERVE_DAYS:
                pr_now = price[date][e['code']]
                if not pr_now or pr_now<=0: continue
                buy_amt = min(p['target_amt'], cash*0.8)
                if buy_amt<20000: continue
                qb = round_lot(buy_amt/pr_now)
                if qb<=0: continue
                spent = qb*pr_now
                p['qty']=qb; p['cost']=pr_now; p['cost_total']=spent
                p['sl_ok']=True; p['sp_ok']=True; p['cleared']=None
                cash -= spent
                trades.append({'date':str(date),'code':e['code'],'name':e['name'],'action':'买入(重建)',
                               'price':round(pr_now,4),'qty':qb,'amount':round(spent,2),
                               'reason':f'观察{OBSERVE_DAYS}日期满重建'})

        # ===== 方案D：季度审视补回 =====
        if mode == 'D' and quarter_end(date, idx, all_dates):
            for e in ETFS:
                p = pos[e['code']]
                if not p['sp'] or p['sp_ok']: continue  # 只处理已止盈的品种
                # 如果因止盈导致严重低配（相对偏离<-20%），补回至目标
                if p['target'] > 0:
                    rel_dev = (p['weight'] - p['target']) / p['target']
                    if rel_dev < -0.20:  # 严重低配
                        need_val = p['target_amt'] - p['value']
                        if need_val > 10000:
                            buy_amt = min(need_val, cash)
                            if buy_amt > 10000:
                                pr_now = price[date][e['code']]
                                if pr_now and pr_now>0:
                                    qb = round_lot(buy_amt/pr_now)
                                    if qb>0:
                                        spent = qb*pr_now
                                        p['qty']+=qb; p['cost_total']+=spent; p['cost']=p['cost_total']/p['qty']
                                        cash -= spent
                                        p['sp_ok']=True  # 补回后可以再次触发止盈
                                        trades.append({'date':str(date),'code':e['code'],'name':e['name'],'action':'买入(季度补回)',
                                                       'price':round(pr_now,4),'qty':qb,'amount':round(spent,2),
                                                       'reason':f'季度审视：止盈后偏离{rel_dev*100:.0f}%<-20%，补回至目标'})

        # ===== 年度再平衡 =====
        if year_end(date, idx, all_dates):
            triggers = False; devs = []
            for e in ETFS:
                p = pos[e['code']]
                if p['target']>0:
                    aw = p['value']/total_val if total_val>0 else 0
                    rd = (aw-p['target'])/p['target']
                    devs.append((e['code'],p,aw,p['target'],rd))
                    if abs(rd)>0.20: triggers=True

            if triggers:
                # 卖超配
                for code,p,aw,tw,rd in devs:
                    if rd>0.01 and p['qty']>0:
                        excess = p['value']-total_val*tw
                        if excess>5000:
                            pr_now=price[date][code]
                            if pr_now and pr_now>0:
                                sq=round_lot(excess/pr_now)
                                if sq>0:
                                    sa=sq*pr_now; p['qty']-=sq; p['cost_total']-=p['cost']*sq
                                    p['cost']=p['cost_total']/p['qty'] if p['qty']>0 else 0
                                    cash+=sa
                                    trades.append({'date':str(date),'code':code,'name':p['name'],'action':'卖出(再平衡)',
                                                   'price':round(pr_now,4),'qty':sq,'amount':round(sa,2),'reason':f'超配{aw*100:.1f}%>{tw*100:.1f}%'})

                total_val = cash+sum(pos[e['code']]['value'] for e in ETFS)

                # 买低配
                for code,p,aw,tw,rd in sorted(devs,key=lambda x:x[4]):
                    if rd<-0.01:
                        shortfall = total_val*tw-p['value']
                        if shortfall>5000:
                            pr_now=price[date][code]
                            if pr_now and pr_now>0 and cash>100:
                                ba=min(shortfall,cash); qb=round_lot(ba/pr_now)
                                if qb>0:
                                    spent=qb*pr_now; p['qty']+=qb; p['cost_total']+=spent; p['cost']=p['cost_total']/p['qty']
                                    cash-=spent; p['sp_ok']=True  # 再平衡补回后重置止盈状态
                                    trades.append({'date':str(date),'code':code,'name':p['name'],'action':'买入(再平衡)',
                                                   'price':round(pr_now,4),'qty':qb,'amount':round(spent,2),'reason':f'低配{aw*100:.1f}%<{tw*100:.1f}%'})

        daily_values.append(total_val)

    # 结果
    start_val = float(TOTAL)
    end_val = daily_values[-1] if daily_values else start_val
    years = (all_dates[-1]-all_dates[0]).days/365.25
    cagr = ((end_val/start_val)**(1/years)-1)*100 if years>0 else 0
    total_ret = (end_val/start_val-1)*100

    # 最大回撤
    peak=0; max_dd=0
    for v in daily_values:
        if v>peak: peak=v
        dd=(v-peak)/peak
        if dd<max_dd: max_dd=dd

    # 按年统计
    df = pd.DataFrame({'date':all_dates,'value':daily_values})
    df['year'] = df['date'].apply(lambda d: d.year)
    yearly = {}
    for y, grp in df.groupby('year'):
        grp=grp.sort_values('date')
        yearly[y] = round((grp.iloc[-1]['value']/grp.iloc[0]['value']-1)*100,2)

    return {
        'cagr': round(cagr,2), 'total_ret': round(total_ret,2),
        'max_dd': round(max_dd*100,2), 'trades': len(trades),
        'yearly': yearly, 'end_val': round(end_val,2),
    }

# ========== 跑三个方案 ==========
modes = {'A': '年度再平衡（现状）', 'B': '价格回落≥10%补回', 'D': '季度审视补回'}
print("\n跑三个方案...")
results = {}
for m, label in modes.items():
    r = run_backtest(m)
    results[m] = r
    print(f"  {label}: CAGR={r['cagr']}%  总收益={r['total_ret']}%  回撤={r['max_dd']}%  交易{r['trades']}笔")

# ========== 输出对比 ==========
print("\n" + "=" * 80)
print("【止盈后补仓方案对比】")
print("=" * 80)
print(f"{'方案':<16} | {'CAGR':>6} | {'总收益':>7} | {'最大回撤':>7} | {'交易笔数':>6} | {'2021':>7} | {'2022':>7} | {'2023':>7} | {'2024':>7} | {'2025':>7}")
print("-" * 95)
for m, label in modes.items():
    r = results[m]
    y = r['yearly']
    print(f"{label:<16} | {r['cagr']:>5.2f}% | {r['total_ret']:>6.2f}% | {r['max_dd']:>6.2f}% | {r['trades']:>6} | {y.get(2021,''):>6.2f}% | {y.get(2022,''):>6.2f}% | {y.get(2023,''):>6.2f}% | {y.get(2024,''):>6.2f}% | {y.get(2025,''):>6.2f}%")

# 保存JSON
with open('backtest_sp_reentry.json','w',encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n已保存: backtest_sp_reentry.json")
