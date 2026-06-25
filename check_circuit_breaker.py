#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""统计2021-2025年熔断触发次数"""
import os, sys, math
from datetime import datetime, timedelta
sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts
import pandas as pd

ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

TOTAL = 4_000_000; OBSERVE_DAYS=20; SP_TRIGGER=0.20; SP_SELL_PCT=0.50; SL_TRIGGER=-0.15

ETFS = [
    {'code':'510300','mkt':'SH','name':'沪深300ETF','target':0.1875,'sp':True,'sl':True},
    {'code':'588050','mkt':'SH','name':'科创50ETF','target':0.0625,'sp':True,'sl':True},
    {'code':'159915','mkt':'SZ','name':'创业板ETF','target':0.0625,'sp':True,'sl':True},
    {'code':'512890','mkt':'SH','name':'红利低波ETF','target':0.0625,'sp':True,'sl':True},
    {'code':'511380','mkt':'SH','name':'可转债ETF','target':0.125,'sp':True,'sl':True},
    {'code':'511260','mkt':'SH','name':'10年国债ETF','target':0.125,'sp':False,'sl':False},
    {'code':'511010','mkt':'SH','name':'5年国债ETF','target':0.125,'sp':False,'sl':False},
    {'code':'511360','mkt':'SH','name':'短融ETF','target':0.0625,'sp':False,'sl':False},
    {'code':'518880','mkt':'SH','name':'黄金ETF','target':0.1875,'sp':False,'sl':False},
]

def round_lot(q): return int(max(0, math.floor(q/100))*100)

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
    return date.month==12 and (idx+1>=len(dl) or dl[idx+1].month!=12)

# 建仓
pos = {}
for e in ETFS:
    pos[e['code']] = {
        'code':e['code'],'name':e['name'],'target':e['target'],'target_amt':TOTAL*e['target'],
        'sp':e['sp'],'sl':e['sl'],
        'qty':0,'cost':0.0,'cost_total':0.0,'price':0.0,'value':0.0,'pnl_pct':0.0,'weight':0.0,
        'sp_ok':True,'sl_ok':True,'cleared':None,
    }

cash = float(TOTAL)
fd = all_dates[0]
for e in ETFS:
    p = pos[e['code']]; pr = price[fd][e['code']]
    if not pr or pr<=0: continue
    qty = round_lot(p['target_amt']/pr)
    if qty<=0: continue
    spent = qty*pr; p['qty']=qty; p['cost']=pr; p['cost_total']=spent; cash-=spent

# 每日循环
print("运行回测...")
peak = float(TOTAL)
cb_daily = []  # 每天的回撤状态

for idx, date in enumerate(all_dates):
    for e in ETFS:
        p = pos[e['code']]; pr = price[date][e['code']]
        if pr and pr>0:
            p['price']=pr
            if p['qty']>0:
                p['value']=p['qty']*pr
                p['pnl_pct']=(p['value']-p['cost_total'])/p['cost_total']
            else: p['value']=0.0

    total_val = cash + sum(pos[e['code']]['value'] for e in ETFS)

    if total_val > peak: peak = total_val
    dd = (total_val - peak) / peak

    level = None
    if dd <= -0.15: level = '4-黑天鹅'
    elif dd <= -0.12: level = '3-红色'
    elif dd <= -0.10: level = '2-黄色'
    elif dd <= -0.05: level = '1-绿色'
    else: level = '0-正常'

    cb_daily.append({'date': str(date), 'level': level, 'dd': dd, 'value': total_val})

    # --- 止盈 ---
    for e in ETFS:
        p=pos[e['code']]
        if not p['sp'] or p['qty']<=0 or not p['sp_ok']: continue
        if p['pnl_pct'] >= SP_TRIGGER:
            sq=round_lot(p['qty']*SP_SELL_PCT)
            if sq<=0: continue
            sa=sq*p['price']; p['qty']-=sq; p['cost_total']-=p['cost']*sq
            p['cost']=p['cost_total']/p['qty'] if p['qty']>0 else 0
            cash+=sa; p['sp_ok']=False

    # --- 止损 ---
    for e in ETFS:
        p=pos[e['code']]
        if not p['sl'] or p['qty']<=0 or not p['sl_ok']: continue
        if p['pnl_pct'] <= SL_TRIGGER:
            cash+=p['qty']*p['price']; p['sl_ok']=False; p['cleared']=date
            p['qty']=0; p['cost']=0; p['cost_total']=0

    # --- 观察期满重建 ---
    for e in ETFS:
        p=pos[e['code']]
        if p['qty']>0 or p['sl_ok'] or p['cleared'] is None: continue
        days_since=(date-p['cleared']).days
        if int(days_since*0.7)>=OBSERVE_DAYS:
            pr_now=price[date][e['code']]
            if not pr_now or pr_now<=0: continue
            ba=min(p['target_amt'],cash*0.8)
            if ba<20000: continue
            qb=round_lot(ba/pr_now)
            if qb<=0: continue
            spent=qb*pr_now; p['qty']=qb; p['cost']=pr_now; p['cost_total']=spent
            p['sl_ok']=True; p['sp_ok']=True; p['cleared']=None; cash-=spent

    # --- 年度再平衡 ---
    if year_end(date, idx, all_dates):
        triggers=False; devs=[]
        for e in ETFS:
            p=pos[e['code']]
            if p['target']>0:
                aw=p['value']/total_val if total_val>0 else 0
                rd=(aw-p['target'])/p['target']
                devs.append((e['code'],p,aw,p['target'],rd))
                if abs(rd)>0.20: triggers=True
        if triggers:
            for code,p,aw,tw,rd in devs:
                if rd>0.01 and p['qty']>0:
                    excess=p['value']-total_val*tw
                    if excess>5000:
                        pr_now=price[date][code]
                        if pr_now and pr_now>0:
                            sq=round_lot(excess/pr_now)
                            if sq>0:
                                sa=sq*pr_now; p['qty']-=sq; p['cost_total']-=p['cost']*sq
                                p['cost']=p['cost_total']/p['qty'] if p['qty']>0 else 0; cash+=sa
            total_val=cash+sum(pos[e['code']]['value'] for e in ETFS)
            for code,p,aw,tw,rd in sorted(devs,key=lambda x:x[4]):
                if rd<-0.01:
                    shortfall=total_val*tw-p['value']
                    if shortfall>5000:
                        pr_now=price[date][code]
                        if pr_now and pr_now>0 and cash>100:
                            ba=min(shortfall,cash); qb=round_lot(ba/pr_now)
                            if qb>0:
                                spent=qb*pr_now; p['qty']+=qb; p['cost_total']+=spent
                                p['cost']=p['cost_total']/p['qty']; cash-=spent; p['sp_ok']=True

# ====== 统计分析 ======
print("\n" + "=" * 70)
print("熔断触发统计（2021-2025）")
print("=" * 70)

# 按级别统计
for level_name, level_code in [('绿色（<-5%）','1-绿色'), ('黄色（<-10%）','2-黄色'),
                                ('红色（<-12%）','3-红色'), ('黑天鹅（<-15%）','4-黑天鹅')]:
    days_in_level = [d for d in cb_daily if level_code in d['level']]
    if not days_in_level:
        print(f"  {level_name}: 0 次")
        continue

    # 合并连续日期为同一次事件
    events = []
    for d in days_in_level:
        if not events:
            events.append([d])
        else:
            prev = events[-1][-1]
            prev_date = datetime.strptime(prev['date'], '%Y-%m-%d').date()
            cur_date = datetime.strptime(d['date'], '%Y-%m-%d').date()
            if (cur_date - prev_date).days <= 3:
                events[-1].append(d)
            else:
                events.append([d])

    print(f"  {level_name}: {len(events)} 次（共{len(days_in_level)}个交易日）")
    for ev in events[:6]:
        start_d = ev[0]['date']
        end_d = ev[-1]['date']
        max_dd = min(d['dd'] for d in ev)
        min_val = min(d['value'] for d in ev)
        duration = len(ev)
        if start_d != end_d:
            print(f"    {start_d} ~ {end_d}  ({duration}日)  最大回撤{max_dd*100:.2f}%  最低市值{min_val:,.0f}")
        else:
            print(f"    {start_d}  (1日)  回撤{max_dd*100:.2f}%  市值{min_val:,.0f}")
    if len(events) > 6:
        print(f"    ... 还有{len(events)-6}次")

# 各年度统计
print("\n" + "-" * 70)
print("各年度熔断触发天数")
print("-" * 70)
print(f"{'年份':>6} | {'绿色':>6} | {'黄色':>6} | {'红色':>6} | {'黑天鹅':>6} | {'合计':>6}")
print("-" * 50)

for year in range(2021, 2026):
    year_days = [d for d in cb_daily if d['date'][:4] == str(year)]
    g = sum(1 for d in year_days if '1-绿色' in d['level'])
    y = sum(1 for d in year_days if '2-黄色' in d['level'])
    r = sum(1 for d in year_days if '3-红色' in d['level'])
    b = sum(1 for d in year_days if '4-黑天鹅' in d['level'])
    print(f"{year:>6} | {g:>6} | {y:>6} | {r:>6} | {b:>6} | {g+y+r+b:>6}")

total_g = sum(1 for d in cb_daily if '1-绿色' in d['level'])
total_y = sum(1 for d in cb_daily if '2-黄色' in d['level'])
total_r = sum(1 for d in cb_daily if '3-红色' in d['level'])
total_b = sum(1 for d in cb_daily if '4-黑天鹅' in d['level'])
print("-" * 50)
print(f"{'合计':>6} | {total_g:>6} | {total_y:>6} | {total_r:>6} | {total_b:>6} | {total_g+total_y+total_r+total_b:>6}")

total_days = len(cb_daily)
normal_days = total_days - total_g - total_y - total_r - total_b
print(f"\n{total_days}个交易日中，{normal_days}天正常（{normal_days/total_days*100:.1f}%），"
      f"{total_g+total_y+total_r+total_b}天触发熔断观察（{(total_g+total_y+total_r+total_b)/total_days*100:.1f}%）")
