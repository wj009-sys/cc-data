#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
投资纪律回测：2021-01-01 ~ 2025-12-31
完全按照投资纪要规则模拟买卖操作和年度收益
输出：所有交易流水 + 每年收益统计
"""

import os, sys, json, math
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

os.environ['PYTHONIOENCODING'] = 'utf-8'
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

import tushare as ts
ts.set_token(os.environ.get('TUSHARE_TOKEN', ''))
pro = ts.pro_api()

# ============================================================
#  配置
# ============================================================
TOTAL_CAPITAL = 4_000_000
START_DATE = '20210101'
END_DATE = '20251231'

ETFS = [
    {'code': '510300', 'mkt': 'SH', 'name': '沪深300ETF',    'target': 0.1875, 'cat': 'equity',          'stop_profit': True,  'stop_loss': 'unified',   'bond': False},
    {'code': '588050', 'mkt': 'SH', 'name': '科创50ETF',     'target': 0.0625, 'cat': 'high_vol',        'stop_profit': True,  'stop_loss': 'high_vol',  'bond': False},
    {'code': '159915', 'mkt': 'SZ', 'name': '创业板ETF',     'target': 0.0625, 'cat': 'high_vol',        'stop_profit': True,  'stop_loss': 'high_vol',  'bond': False},
    {'code': '512890', 'mkt': 'SH', 'name': '红利低波ETF',   'target': 0.0625, 'cat': 'equity',          'stop_profit': True,  'stop_loss': 'unified',   'bond': False},
    {'code': '511380', 'mkt': 'SH', 'name': '可转债ETF',     'target': 0.125,  'cat': 'convertible',     'stop_profit': True,  'stop_loss': 'unified',   'bond': False},
    {'code': '511260', 'mkt': 'SH', 'name': '10年国债ETF',    'target': 0.125,  'cat': 'bond',           'stop_profit': False, 'stop_loss': None,       'bond': True},
    {'code': '511010', 'mkt': 'SH', 'name': '5年国债ETF',     'target': 0.125,  'cat': 'bond',           'stop_profit': False, 'stop_loss': None,       'bond': True},
    {'code': '511360', 'mkt': 'SH', 'name': '短融ETF',       'target': 0.0625, 'cat': 'bond',           'stop_profit': False, 'stop_loss': None,       'bond': True},
    {'code': '518880', 'mkt': 'SH', 'name': '黄金ETF',       'target': 0.1875, 'cat': 'commodity',       'stop_profit': True,  'stop_loss': 'unified',   'bond': False},
]

CODE_MAP = {e['code']: e for e in ETFS}
TS_CODES = [f"{e['code']}.{e['mkt']}" for e in ETFS]

# ============================================================
#  数据获取
# ============================================================
print("正在获取历史行情数据...")
all_data = {}
for e in ETFS:
    ts_code = f"{e['code']}.{e['mkt']}"
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date=START_DATE, end_date=END_DATE,
                            fields='trade_date,open,high,low,close,pre_close')
        if df is not None and len(df):
            df['trade_date'] = pd.to_datetime(df['trade_date'])
            df = df.sort_values('trade_date').reset_index(drop=True)
            # 计算复权因子近似（用pre_close和close比）
            df['ret'] = df['close'] / df['pre_close'] - 1
            all_data[e['code']] = df
            print(f"  [OK] {e['name']} ({ts_code}): {len(df)} 条记录 ({df['trade_date'].iloc[0].date()} ~ {df['trade_date'].iloc[-1].date()})")
        else:
            print(f"  [WARN] {e['name']} ({ts_code}): 无数据")
    except Exception as ex:
        print(f"  [ERR] {e['name']}: {ex}")

if not all_data:
    print("错误：未能获取任何行情数据")
    sys.exit(1)

# 统一交易日历（取所有品种的并集）
all_dates = set()
for code, df in all_data.items():
    all_dates.update(df['trade_date'].dt.date)
all_dates = sorted(all_dates)
print(f"\n交易日数量: {len(all_dates)}")

# ============================================================
#  辅助函数
# ============================================================
def round_lot(qty):
    """向下取整到100的倍数（1手）"""
    return int(max(0, math.floor(qty / 100)) * 100)

def get_price(code, date):
    """取指定日期收盘价"""
    df = all_data.get(code)
    if df is None:
        return None
    mask = df['trade_date'].dt.date == date
    if not mask.any():
        return None
    return float(df.loc[mask, 'close'].iloc[0])

def get_ret(code, date):
    """取指定日期收益率（close/pre_close-1）"""
    df = all_data.get(code)
    if df is None:
        return 0.0
    mask = df['trade_date'].dt.date == date
    if not mask.any():
        return 0.0
    return float(df.loc[mask, 'ret'].iloc[0])

# 提前获取所有日期的收益率数据
print("\n构建收益率矩阵...")
daily_rets = {}
for code in all_data:
    df = all_data[code]
    daily_rets[code] = dict(zip(df['trade_date'].dt.date, df['ret']))

# ============================================================
#  持仓状态类
# ============================================================
class Position:
    def __init__(self, etf):
        self.code = etf['code']
        self.name = etf['name']
        self.target_weight = etf['target']
        self.target_amount = TOTAL_CAPITAL * etf['target']
        self.cat = etf['cat']
        self.stop_profit_enabled = etf['stop_profit']
        self.stop_loss_type = etf['stop_loss']
        self.is_bond = etf['bond']

        self.qty = 0          # 持仓数量
        self.cost_price = 0.0  # 加权平均成本价
        self.cost_total = 0.0  # 总成本
        self.current_price = 0.0
        self.pnl = 0.0
        self.pnl_pct = 0.0
        self.value = 0.0
        self.weight = 0.0

        # 止盈状态
        self.sp_triggered = False   # 止盈已触发（+30%卖半，仅一次）

        # 止损状态
        self.sl_triggered = False   # 统一止损-25%

        # 清仓观察期
        self.cleared_date = None
        self.observe_days = 0
        self.observe_needed = 0
        self.reentry_allowed = True

    def update_price(self, price):
        self.current_price = price
        if self.qty > 0 and self.current_price > 0:
            self.value = self.qty * self.current_price
            self.pnl = self.value - self.cost_total
            self.pnl_pct = self.pnl / self.cost_total if self.cost_total > 0 else 0.0
        else:
            self.value = 0.0
            self.pnl = 0.0
            self.pnl_pct = 0.0

    def buy(self, price, amount, date):
        """买入操作，amount为投入金额"""
        if price <= 0:
            return 0, 0
        max_qty = round_lot(amount / price)
        if max_qty <= 0:
            return 0, 0
        # 加权平均成本
        new_cost = max_qty * price
        total_qty = self.qty + max_qty
        self.cost_total += new_cost
        self.cost_price = self.cost_total / total_qty if total_qty > 0 else 0
        self.qty = total_qty
        actual_amount = max_qty * price
        return max_qty, actual_amount

    def sell(self, qty, price):
        """卖出指定数量"""
        if qty <= 0 or self.qty <= 0 or price <= 0:
            return 0, 0
        qty = min(qty, self.qty)
        actual_amount = qty * price
        # 成本减少
        cost_portion = self.cost_price * qty
        self.cost_total -= cost_portion
        self.qty -= qty
        self.cost_price = self.cost_total / self.qty if self.qty > 0 else 0
        return qty, actual_amount

    def sell_all(self, price):
        """清仓"""
        return self.sell(self.qty, price)

    def check_stop_profit(self, date):
        """检查止盈：+30%卖半，仅触发一次"""
        if not self.stop_profit_enabled or self.qty <= 0 or self.current_price <= 0:
            return False, '', 0, ''

        if self.sp_triggered:
            return False, '', 0, ''

        pnl_pct = (self.current_price - self.cost_price) / self.cost_price if self.cost_price > 0 else 0

        if pnl_pct >= 0.30:
            sell_qty = round_lot(self.qty * 0.50)
            if sell_qty > 0:
                return True, '卖半', sell_qty, f'止盈+30%：浮盈+{pnl_pct*100:.1f}% >= 30%，卖出一半'

        return False, '', 0, ''

    def check_stop_loss(self, date):
        """检查止损：统一-25%清仓（仅非债券品种）"""
        if self.qty <= 0 or self.current_price <= 0:
            return False, '', 0, ''

        if self.sl_triggered:
            return False, '', 0, ''

        if self.is_bond or not self.stop_profit_enabled:
            return False, '', 0, ''

        pnl_pct = (self.current_price - self.cost_price) / self.cost_price if self.cost_price > 0 else 0

        if pnl_pct <= -0.25:
            return True, '清仓', self.qty, f'止损-25%：浮盈{pnl_pct*100:.1f}% <= -25%，清仓'

        return False, '', 0, ''

    def check_reentry(self, date):
        """检查是否可以重新入场"""
        if self.cleared_date is None and not self.sl_triggered:
            return False
        if self.qty > 0:
            return False
        if self.cleared_date is None:
            return False
        # 简化：用date_diff估算交易日（约每calendar天0.7个交易日）
        days_since = (date - self.cleared_date).days
        est_trading_days = int(days_since * 0.7)
        return est_trading_days >= self.observe_needed


# ============================================================
#  回测主循环
# ============================================================
print("\n开始回测...")
print("=" * 80)

# 初始化持仓
positions = {e['code']: Position(e) for e in ETFS}

# 建仓（第一个交易日）
first_date = all_dates[0]
print(f"\n建仓日期: {first_date}")

cash = TOTAL_CAPITAL
total_trades = []  # 所有交易记录

# 第一轮建仓：按目标权重买入
for e in ETFS:
    p = positions[e['code']]
    price = get_price(e['code'], first_date)
    if price is None or price <= 0:
        print(f"  [SKIP] {e['name']}: 无价格数据")
        continue
    target_amt = TOTAL_CAPITAL * e['target']
    qty, spent = p.buy(price, target_amt, first_date)
    if qty > 0:
        cash -= spent
        trade = {'date': str(first_date), 'code': e['code'], 'name': e['name'],
                 'action': '买入', 'price': round(price, 4), 'qty': qty,
                 'amount': round(spent, 2), 'reason': '建仓'}
        total_trades.append(trade)

# 剩余现金计入短融（511360）
remaining_cash = cash
if remaining_cash > 0:
    p360 = positions['511360']
    price = get_price('511360', first_date)
    if price and price > 0:
        # 按剩余现金买入短融
        qty = round_lot(remaining_cash / price)
        if qty > 0:
            spent = qty * price
            p360.qty += qty
            p360.cost_total += spent
            p360.cost_price = p360.cost_total / p360.qty
            cash -= spent
            trade = {'date': str(first_date), 'code': '511360', 'name': '短融ETF',
                     'action': '买入(余)', 'price': round(price, 4), 'qty': qty,
                     'amount': round(spent, 2), 'reason': '建仓余款'}
            total_trades.append(trade)

print(f"建仓完成，剩余现金: {cash:.2f}")
print(f"总交易笔数: {len(total_trades)}")

# ============================================================
#  每日循环
# ============================================================
portfolio_peak = TOTAL_CAPITAL
daily_values = []  # 每日组合价值
distribute_queue = []  # 待分配资金（止盈资金2日内再配置）

# 中国半年度末检查日：6月最后交易日, 12月最后交易日
def is_semiannual_end(date, all_dates_idx, all_dates_list):
    """判断是否是半年度末最后一个交易日"""
    month = date.month
    if month not in (6, 12):
        return False
    # 检查下一天是否还在同一个月
    current_idx = all_dates_idx
    if current_idx + 1 < len(all_dates_list):
        next_date = all_dates_list[current_idx + 1]
        if next_date.month == month:
            return False  # 同月还有交易日
    return True

# 提前准备每天的价格
daily_prices_cache = {}
for d in all_dates:
    daily_prices_cache[d] = {}
    for e in ETFS:
        p = get_price(e['code'], d)
        daily_prices_cache[d][e['code']] = p

# 提前准备daily price for all
price_df_map = {}
for code in all_data:
    df = all_data[code]
    price_df_map[code] = df.set_index('trade_date')

print("\n每日回测中...")

for idx, date in enumerate(all_dates):
    if idx % 250 == 0 and idx > 0:
        progress = idx / len(all_dates) * 100
        print(f"  进度: {progress:.0f}% ({idx}/{len(all_dates)} 日)")

    # 1. 更新所有品种价格
    for e in ETFS:
        p = daily_prices_cache[date].get(e['code'])
        if p and p > 0:
            positions[e['code']].update_price(p)

    # 2. 计算组合价值
    total_value = cash
    for e in ETFS:
        total_value += positions[e['code']].value

    # 更新权重
    for e in ETFS:
        if total_value > 0:
            positions[e['code']].weight = positions[e['code']].value / total_value

    # 更新峰值
    drawdown = (total_value - portfolio_peak) / portfolio_peak if portfolio_peak > 0 else 0
    if total_value > portfolio_peak:
        portfolio_peak = total_value
        drawdown = 0

    # 记录每日价值
    daily_values.append({
        'date': str(date),
        'total_value': round(total_value, 2),
        'cash': round(cash, 2),
        'drawdown': round(drawdown, 4),
        'peak': round(portfolio_peak, 2),
    })

    # 3. 熔断检查（只记录日志，影响操作策略）
    #   熔断影响新建仓/追涨，回测中我们简化处理：仅记录，不影响止盈止损执行
    circuit_breaker = None
    if drawdown <= -0.15:
        circuit_breaker = '黑天鹅'
    elif drawdown <= -0.12:
        circuit_breaker = '红色'
    elif drawdown <= -0.10:
        circuit_breaker = '黄色'
    elif drawdown <= -0.05:
        circuit_breaker = '绿色'

    # 4. 处理止盈资金待分配队列
    new_queue = []
    for item in distribute_queue:
        item['days_left'] -= 1
        if item['days_left'] <= 0:
            amount = item['amount']
            if amount < 1000:
                pass  # 金额太小，cash已包含此金额，无需操作
            else:
                sorted_pos = sorted(ETFS, key=lambda e: positions[e['code']].weight if total_value > 0 else 0)
                allocated = False
                for e2 in sorted_pos:
                    if e2['code'] == item['from_code']:
                        continue  # 不买回原品种（刚止盈/止损）
                    p2 = positions[e2['code']]
                    if p2.is_bond or p2.qty <= 0:
                        continue  # 跳过债券和已清仓品种
                    price2 = daily_prices_cache[date].get(e2['code'])
                    if price2 and price2 > 0 and p2.target_weight > 0:
                        if p2.weight < p2.target_weight:
                            buy_amt = min(amount, p2.target_amount - p2.cost_total)
                            if buy_amt > 1000:
                                # 现金不够则卖511360筹资
                                if cash < buy_amt:
                                    shortfall = buy_amt - cash
                                    p360 = positions['511360']
                                    p360_price = daily_prices_cache[date].get('511360')
                                    if p360_price and p360_price > 0 and p360.qty > 0:
                                        sell_qty = round_lot(shortfall / p360_price)
                                        if sell_qty > 0:
                                            sold_qty, sold_amt = p360.sell(sell_qty, p360_price)
                                            if sold_qty > 0:
                                                cash += sold_amt
                                                trade = {'date': str(date), 'code': '511360', 'name': '短融ETF',
                                                         'action': '卖出(筹资)', 'price': round(p360_price, 4),
                                                         'qty': sold_qty, 'amount': round(sold_amt, 2),
                                                         'reason': '再配置筹资'}
                                                total_trades.append(trade)
                                # 买入（cash已含止盈金额，直接扣减）
                                qty2, spent2 = p2.buy(price2, min(buy_amt, cash), date)
                                if qty2 > 0:
                                    cash -= spent2
                                    trade = {'date': str(date), 'code': e2['code'], 'name': e2['name'],
                                             'action': '买入', 'price': round(price2, 4), 'qty': qty2,
                                             'amount': round(spent2, 2), 'reason': '止盈资金再配置'}
                                    total_trades.append(trade)
                                    allocated = True
                                    break
                if not allocated:
                    # 没有合适的品种，买入511360（用cash中已有的资金）
                    p360 = positions['511360']
                    price360 = daily_prices_cache[date].get('511360')
                    if price360 and price360 > 0:
                        qty360 = round_lot(amount / price360)
                        if qty360 > 0:
                            spent360 = qty360 * price360
                            p360.qty += qty360
                            p360.cost_total += spent360
                            p360.cost_price = p360.cost_total / p360.qty
                            cash -= spent360
                            trade = {'date': str(date), 'code': '511360', 'name': '短融ETF',
                                     'action': '买入', 'price': round(price360, 4), 'qty': qty360,
                                     'amount': round(spent360, 2), 'reason': '止盈资金再配置(暂存)'}
                            total_trades.append(trade)
        else:
            new_queue.append(item)
    distribute_queue = new_queue

    # 5. 止盈检查（先卖高浮盈的，按浮盈率排序）
    sp_candidates = []
    for e in ETFS:
        p = positions[e['code']]
        if p.qty <= 0:
            continue
        should, action, qty, reason = p.check_stop_profit(date)
        if should and qty > 0:
            sp_candidates.append((p, action, qty, reason))
        elif should and action == '预警':
            pass  # 只预警不记录

    # 按浮盈率从高到低执行
    for p, action, qty, reason in sorted(sp_candidates, key=lambda x: x[0].pnl_pct, reverse=True):
        if p.qty <= 0:
            continue
        if action == '清仓':
            sold_qty, sold_amt = p.sell_all(p.current_price)
        else:
            sold_qty, sold_amt = p.sell(qty, p.current_price)

        if sold_qty > 0:
            cash += sold_amt
            # 更新止盈状态
            p.sp_triggered = True

            trade = {'date': str(date), 'code': p.code, 'name': p.name,
                     'action': '卖出', 'price': round(p.current_price, 4), 'qty': sold_qty,
                     'amount': round(sold_amt, 2), 'reason': reason}
            total_trades.append(trade)

            # 止盈资金进入2日待分配队列
            if action != '预警':
                distribute_queue.append({'amount': sold_amt, 'from_code': p.code, 'days_left': 2})

    # 6. 止损检查
    sl_candidates = []
    for e in ETFS:
        p = positions[e['code']]
        if p.qty <= 0:
            # 检查是否可以重新入场
            if p.cleared_date and p.observe_needed > 0:
                days_since = (date - p.cleared_date).days
                est_trading_days = int(days_since * 0.7)
                if est_trading_days >= p.observe_needed and p.reentry_allowed:
                    # 允许重新建仓
                    p.reentry_allowed = True
                    p.cleared_date = None
                    p.observe_needed = 0
                    p.sl_triggered = False
                    p.sp_triggered = False
                    # 重新建仓：目标按目标权重配置，现金不够则卖511360筹资
                    target_amt = min(p.target_amount, total_value * p.target_weight * 1.2)
                    price_now = daily_prices_cache[date].get(p.code)
                    if price_now and price_now > 0 and target_amt > 1000:
                        # 现金不够时卖511360
                        need_to_raise = max(0, target_amt - cash)
                        if need_to_raise > 100:
                            p360 = positions['511360']
                            p360_price = daily_prices_cache[date].get('511360')
                            if p360_price and p360_price > 0 and p360.qty > 0:
                                sell_qty = round_lot(need_to_raise / p360_price)
                                if sell_qty > 0:
                                    sold_qty, sold_amt = p360.sell(sell_qty, p360_price)
                                    if sold_qty > 0:
                                        cash += sold_amt
                                        trade = {'date': str(date), 'code': '511360', 'name': '短融ETF',
                                                 'action': '卖出(筹资)', 'price': round(p360_price, 4),
                                                 'qty': sold_qty, 'amount': round(sold_amt, 2),
                                                 'reason': '重建仓位筹资'}
                                        total_trades.append(trade)
                        # 买入重建
                        re_qty, re_spent = p.buy(price_now, min(target_amt, cash), date)
                        if re_qty > 0:
                            cash -= re_spent
                            trade = {'date': str(date), 'code': p.code, 'name': p.name,
                                     'action': '买入(重建)', 'price': round(price_now, 4), 'qty': re_qty,
                                     'amount': round(re_spent, 2), 'reason': '观察期满重新建仓'}
                            total_trades.append(trade)
            continue

        should, action, qty, reason = p.check_stop_loss(date)
        if should:
            sl_candidates.append((p, action, qty, reason))

    for p, action, qty, reason in sl_candidates:
        if p.qty <= 0:
            continue
        if action == '清仓':
            sold_qty, sold_amt = p.sell_all(p.current_price)
        else:
            sold_qty, sold_amt = p.sell(qty, p.current_price)

        if sold_qty > 0:
            cash += sold_amt
            # 更新止损状态
            p.sl_triggered = True
            p.cleared_date = date
            p.observe_needed = 40

            trade = {'date': str(date), 'code': p.code, 'name': p.name,
                     'action': '卖出', 'price': round(p.current_price, 4), 'qty': sold_qty,
                     'amount': round(sold_amt, 2), 'reason': reason}
            total_trades.append(trade)
            # 止损资金入现金（不清零，不强制再配置）
            # 但规则中"止盈"资金才需2日内配置，止损资金可以先放现金

    # 7. 半年度再平衡检查
    if is_semiannual_end(date, idx, all_dates):
        # 计算相对偏离
        triggers_rebalance = False
        deviation_info = []
        for e in ETFS:
            p = positions[e['code']]
            if p.target_weight > 0:
                if total_value > 0:
                    actual_weight = p.value / total_value if p.qty > 0 else 0
                else:
                    actual_weight = 0
                rel_dev = (actual_weight - p.target_weight) / p.target_weight if p.target_weight > 0 else 0
                deviation_info.append((p.code, p.name, actual_weight, p.target_weight, rel_dev))
                if abs(rel_dev) > 0.20:
                    triggers_rebalance = True

        if triggers_rebalance:
            # 全仓调回目标权重
            # 卖出超配品种，买入低配品种
            print(f"\n  [再平衡] {date}: 相对偏离超±20%，执行全仓再平衡")

            # 第一步：计算当前总资产（含现金）
            current_total = total_value

            # 第二步：卖出超配到目标
            for code, name, act_w, tgt_w, rel_dev in deviation_info:
                if rel_dev > 0.01 and positions[code].qty > 0:  # 超配
                    target_val = current_total * tgt_w
                    current_val = positions[code].value
                    excess = current_val - target_val
                    if excess > 100:  # >100元才操作
                        price_now = daily_prices_cache[date].get(code)
                        if price_now and price_now > 0:
                            sell_qty = round_lot(excess / price_now)
                            if sell_qty > 0:
                                sold_qty, sold_amt = positions[code].sell(sell_qty, price_now)
                                if sold_qty > 0:
                                    cash += sold_amt
                                    trade = {'date': str(date), 'code': code, 'name': name,
                                             'action': '卖出(再平衡)', 'price': round(price_now, 4),
                                             'qty': sold_qty, 'amount': round(sold_amt, 2),
                                             'reason': f'再平衡超配({act_w*100:.1f}%>{tgt_w*100:.1f}%)'}
                                    total_trades.append(trade)

            # 更新总价值
            total_value = cash
            for e in ETFS:
                total_value += positions[e['code']].value

            # 第三步：买入低配到目标
            for code, name, act_w, tgt_w, rel_dev in sorted(deviation_info, key=lambda x: x[4]):  # 最缺的先买
                if rel_dev < -0.01 and positions[code].qty >= 0:  # 低配
                    target_val = total_value * tgt_w
                    current_val = positions[code].value
                    shortfall = target_val - current_val
                    if shortfall > 100:
                        price_now = daily_prices_cache[date].get(code)
                        if price_now and price_now > 0:
                            buy_amt = min(shortfall, cash * 0.5)  # 不一次用完所有现金
                            qty_buy = round_lot(buy_amt / price_now)
                            if qty_buy > 0:
                                spent = qty_buy * price_now
                                # 对于已有持仓：加权平均
                                positions[code].cost_total += spent
                                positions[code].qty += qty_buy
                                positions[code].cost_price = positions[code].cost_total / positions[code].qty
                                cash -= spent
                                trade = {'date': str(date), 'code': code, 'name': name,
                                         'action': '买入(再平衡)', 'price': round(price_now, 4),
                                         'qty': qty_buy, 'amount': round(spent, 2),
                                         'reason': f'再平衡补配({act_w*100:.1f}%<{tgt_w*100:.1f}%)'}
                                total_trades.append(trade)

            # 第四步：如有剩余现金，买入511360
            if cash > 10000:
                p360 = positions['511360']
                price360 = daily_prices_cache[date].get('511360')
                if price360 and price360 > 0:
                    qty360 = round_lot(cash / price360)
                    if qty360 > 0:
                        spent360 = qty360 * price360
                        p360.qty += qty360
                        p360.cost_total += spent360
                        p360.cost_price = p360.cost_total / p360.qty
                        cash -= spent360
                        trade = {'date': str(date), 'code': '511360', 'name': '短融ETF',
                                 'action': '买入(再平衡余)', 'price': round(price360, 4),
                                 'qty': qty360, 'amount': round(spent360, 2), 'reason': '再平衡剩余资金'}
                        total_trades.append(trade)

            # 重新计算总价值
            total_value = cash
            for e in ETFS:
                total_value += positions[e['code']].value

            # 更新权重
            for e in ETFS:
                if total_value > 0:
                    positions[e['code']].weight = positions[e['code']].value / total_value

# ============================================================
#  结果统计
# ============================================================
print("\n" + "=" * 80)
print("回测完成！")
print(f"回测区间: {first_date} ~ {all_dates[-1]}")
print(f"交易日数: {len(all_dates)}")
print(f"总交易笔数: {len(total_trades)}")

# 计算年度收益
df_daily = pd.DataFrame(daily_values)
df_daily['date'] = pd.to_datetime(df_daily['date'])
df_daily['year'] = df_daily['date'].dt.year

annual_returns = []
for year, group in df_daily.groupby('year'):
    year_data = group.sort_values('date')
    start_val = year_data.iloc[0]['total_value']
    end_val = year_data.iloc[-1]['total_value']
    year_return = (end_val / start_val - 1) * 100
    # 年化波动率（日收益率标准差 * sqrt(252)）
    year_data['daily_ret'] = year_data['total_value'].pct_change()
    volatility = year_data['daily_ret'].std() * np.sqrt(252) * 100
    annual_returns.append({
        'year': year,
        'start_value': round(start_val, 2),
        'end_value': round(end_val, 2),
        'return_pct': round(year_return, 2),
        'volatility_pct': round(volatility, 2) if not np.isnan(volatility) else 0,
        'peak': round(year_data['peak'].max(), 2),
        'max_drawdown': round(year_data['drawdown'].min() * 100, 2),
        'avg_drawdown': round(year_data['drawdown'].mean() * 100, 2),
    })

# 总收益
total_return = (df_daily.iloc[-1]['total_value'] / TOTAL_CAPITAL - 1) * 100
years = (all_dates[-1] - first_date).days / 365.25
cagr = ((df_daily.iloc[-1]['total_value'] / TOTAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0

# ============================================================
#  输出
# ============================================================
print("\n" + "=" * 80)
print("【年度收益统计】")
print("=" * 80)
print(f"{'年份':>6} | {'年初市值':>12} | {'年末市值':>12} | {'年收益率':>8} | {'最大回撤':>8} | {'年化波动':>8}")
print("-" * 70)
for ar in annual_returns:
    print(f"{ar['year']:>6} | {ar['start_value']:>12,.0f} | {ar['end_value']:>12,.0f} | {ar['return_pct']:>7.2f}% | {ar['max_drawdown']:>7.2f}% | {ar['volatility_pct']:>7.2f}%")

print("-" * 70)
print(f"{'总计':>6} | {TOTAL_CAPITAL:>12,.0f} | {df_daily.iloc[-1]['total_value']:>12,.0f} | {total_return:>7.2f}% | | CAGR: {cagr:.2f}%")
print(f"回测时长: {years:.1f} 年")
print(f"累计收益: {df_daily.iloc[-1]['total_value'] - TOTAL_CAPITAL:>,.0f} 元")

# 各年度的沪深300基准（使用510300 ETF的年度收益）
print("\n" + "=" * 80)
print("【各品种年度收益】")
print("=" * 80)
for e in ETFS:
    code = e['code']
    name = e['name']
    df_etf = price_df_map.get(code)
    if df_etf is None:
        continue
    df_etf = df_etf.reset_index()
    df_etf['trade_date'] = pd.to_datetime(df_etf['trade_date'])
    df_etf['year'] = df_etf['trade_date'].dt.year
    annual_ret = df_etf.groupby('year').apply(
        lambda g: (g.sort_values('trade_date')['close'].iloc[-1] / g.sort_values('trade_date')['close'].iloc[0] - 1) * 100
    )
    rets = {yr: round(r, 1) for yr, r in annual_ret.items()}
    print(f"  {name} ({code}): {rets}")

# 输出所有交易流水
print("\n" + "=" * 80)
print("【完整交易流水】")
print("=" * 80)
print(f"{'日期':>10} | {'代码':>6} | {'名称':<12} | {'操作':<12} | {'单价':>8} | {'数量':>8} | {'金额':>12} | {'原因'}")
print("-" * 120)
for t in sorted(total_trades, key=lambda x: (x['date'], x['code'])):
    reason_short = t['reason'][:40] if len(t['reason']) > 40 else t['reason']
    print(f"{t['date']:>10} | {t['code']:>6} | {t['name']:<12} | {t['action']:<12} | {t['price']:>8.4f} | {t['qty']:>8} | {t['amount']:>12,.2f} | {reason_short}")

# 保存结果到JSON
output = {
    'config': {'total_capital': TOTAL_CAPITAL, 'start_date': str(first_date), 'end_date': str(all_dates[-1])},
    'summary': {
        'total_trades': len(total_trades),
        'total_return_pct': round(total_return, 2),
        'cagr_pct': round(cagr, 2),
        'final_value': round(df_daily.iloc[-1]['total_value'], 2),
        'years': round(years, 1),
    },
    'annual_returns': annual_returns,
    'trades': total_trades,
}

with open('backtest_rules_result.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"\n结果已保存到: backtest_rules_result.json")
print("\n完成！")
