#!/usr/bin/env python
"""
400万资产配置组合 - 每日自动更新脚本
用于 Windows 任务计划程序，在每个交易日 16:30 执行

功能：
1. 判断是否为交易日（含中国节假日）
2. 从 Tushare 获取所有持仓 ETF 最新收盘价
3. 更新「汇总」sheet：收盘价、浮盈/亏、权重、偏离、信号
4. 追加「日报」sheet：当日所有品种行情快照
5. 根据投资纪律检测并标注触发信号
"""
import os
import sys
import io
import json
import pickle
from datetime import datetime, date, timedelta
from pathlib import Path

# 修复 Windows GBK 编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ==== 配置 ====
FILE_PATH = Path(r'd:\cc-data\500万资产配置组合2026.xlsx')
CACHE_PATH = Path(r'd:\cc-data\.portfolio_cache.pkl')

# 中国节假日（2026年，需年末更新下一年）
CN_HOLIDAYS_2026 = {
    date(2026, 1, 1), date(2026, 1, 2),                                    # 元旦
    date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),               # 春节(2.17除夕)
    date(2026, 2, 19), date(2026, 2, 20),
    date(2026, 4, 6),                                                        # 清明
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),                  # 劳动节
    date(2026, 6, 19),                                                       # 端午(暂)
    date(2026, 9, 25),                                                       # 中秋(暂)
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),               # 国庆
    date(2026, 10, 6), date(2026, 10, 7),
}
# 调休工作日（周六日补班）
CN_WORKDAYS_2026 = set()

# 持仓定义：代码 -> (名称, tushare_code, 高波动品种, 权益/固收/商品分类)
HOLDINGS = [
    {"code": 510300, "name": "沪深300ETF",        "ts": "510300.SH", "high_vol": False, "type": "equity"},
    {"code": 588050, "name": "科创50ETF",         "ts": "588050.SH", "high_vol": True,  "type": "equity"},
    {"code": 159915, "name": "创业板ETF",         "ts": "159915.SZ", "high_vol": True,  "type": "equity"},
    {"code": 512890, "name": "红利低波ETF",       "ts": "512890.SH", "high_vol": False, "type": "equity"},
    {"code": 511380, "name": "可转债ETF",         "ts": "511380.SH", "high_vol": False, "type": "equity"},
    {"code": 511260, "name": "10年国债ETF",       "ts": "511260.SH", "high_vol": False, "type": "bond"},
    {"code": 511010, "name": "5年国债ETF",        "ts": "511010.SH", "high_vol": False, "type": "bond"},
    {"code": 511360, "name": "短融ETF",           "ts": "511360.SH", "high_vol": False, "type": "bond"},
    {"code": 518880, "name": "黄金ETF",           "ts": "518880.SH", "high_vol": False, "type": "commodity"},
]

HOLDING_MAP = {h["code"]: h for h in HOLDINGS}
# 汇总 sheet 中每个代码对应的行号（第2行开始，与 HOLDINGS 顺序一致）
CODE_ROWS = [2, 3, 4, 5, 6, 7, 8, 9, 10]

# ==== 交易日判断 ====
def is_trading_day(d: date) -> bool:
    """判断是否为 A 股交易日"""
    if d in CN_HOLIDAYS_2026:
        return False
    if d.weekday() < 5:  # 周一至周五
        return True
    if d in CN_WORKDAYS_2026:  # 调休补班
        return True
    return False


def get_latest_trading_day() -> date:
    """获取最近一个交易日"""
    d = date.today()
    for _ in range(10):
        if is_trading_day(d):
            return d
        d = d - timedelta(days=1)
    return date.today()


# ==== Tushare 数据获取 ====
def fetch_prices(ts_codes: list, trade_date: str) -> dict:
    """批量获取 ETF 最新收盘价，返回 {ts_code: {close, pct_chg, pre_close}}"""
    import tushare as ts
    pro = ts.pro_api()
    result = {}

    for code in ts_codes:
        try:
            df = pro.fund_daily(ts_code=code, trade_date=trade_date)
            if not df.empty:
                row = df.iloc[0]
                result[code] = {
                    "close": float(row["close"]),
                    "pct_chg": float(row["pct_chg"]),
                    "pre_close": float(row["pre_close"]),
                }
            else:
                # 尝试扩大范围取最近数据
                start = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
                df2 = pro.fund_daily(ts_code=code, start_date=start, end_date=trade_date)
                if not df2.empty:
                    row = df2.iloc[0]
                    result[code] = {
                        "close": float(row["close"]),
                        "pct_chg": float(row["pct_chg"]),
                        "pre_close": float(row["pre_close"]),
                    }
        except Exception as e:
            print(f"  [WARN] {code} 获取失败: {e}")

    return result


# ==== 信号检测 ====
def detect_signals(holdings_data: list) -> dict:
    """
    根据投资纪要规则检测触发信号。
    holdings_data: [{code, name, close, cost_price, quantity, weight, target_weight, pct_chg, type, high_vol}, ...]
    返回: {code: [signal_strings]}
    """
    signals = {}

    for h in holdings_data:
        s = []
        code = h["code"]
        close = h["close"]
        cost = h.get("cost_price")
        qty = h.get("quantity", 0)
        weight = h.get("weight", 0)
        target = h.get("target_weight", 0)
        deviation = weight - target
        is_high_vol = h.get("high_vol", False)
        htype = h.get("type", "equity")

        if qty and qty > 0 and cost and cost > 0:
            pnl_pct = (close - cost) / cost

            # 一、统一阶梯止盈（7%/9%/12%）
            if pnl_pct >= 0.12:
                s.append(f"🔴止盈第三档：浮盈{pnl_pct:.1%} >= 12%，清仓止盈")
            elif pnl_pct >= 0.09:
                s.append(f"🟡止盈第二档：浮盈{pnl_pct:.1%} >= 9%，减仓30%")
            elif pnl_pct >= 0.07:
                s.append(f"🟢止盈第一档：浮盈{pnl_pct:.1%} >= 7%，减仓20%")

            # 二、高波动品种硬止损（科创50/创业板）
            if is_high_vol:
                if pnl_pct <= -0.15:
                    s.append(f"🔴止损B：浮亏{pnl_pct:.1%} <= -15%，次日清仓")
                elif pnl_pct <= -0.10:
                    s.append(f"🟠止损A：浮亏{pnl_pct:.1%} <= -10%，次日卖出50%")
                elif pnl_pct <= -0.05:
                    s.append(f"⚡预警：浮亏{pnl_pct:.1%} >= -5%，暂停新增买入")

            # 三、其他权益品种统一止损（-15%）
            if htype == "equity" and not is_high_vol:
                if pnl_pct <= -0.15:
                    s.append(f"🔴统一止损：浮亏{pnl_pct:.1%} <= -15%，清仓观察40日")

        # 四、组合再平衡
        if abs(deviation) >= 0.10:
            s.append(f"🔴再平衡紧急线：偏离{deviation:+.1%} >= ±10%")
        elif abs(deviation) >= 0.07:
            s.append(f"🟠再平衡触发线：偏离{deviation:+.1%} >= ±7%，强制调仓")
        elif abs(deviation) >= 0.03:
            s.append(f"📊权重预警偏离{deviation:+.1%}")

        signals[code] = s

    return signals


def detect_portfolio_signals(total_pnl_pct: float, equity_weight: float) -> list:
    """五、熔断与黑天鹅应对"""
    s = []
    if total_pnl_pct <= -0.15:
        s.append("⚫黑天鹅：组合回撤>=15%，权益压至40%以下")
    elif total_pnl_pct <= -0.12:
        s.append("🔴组合熔断：回撤>=12%，权益压至60%以下")
    elif total_pnl_pct <= -0.10:
        s.append("🟡组合预警：回撤>=10%，暂停新建仓+追涨")
    return s


# ==== 日报缺失交易日回补 ====
def backfill_missing_trading_days(wb, ws_summary, ws_daily, latest_trade_date_str):
    """
    检查日报中缺失的交易日，自动回补。
    防止因脚本漏跑导致走势图断档。
    """
    # 收集日报已有日期
    existing_dates = set()
    for row in range(2, ws_daily.max_row + 1):
        dval = ws_daily.cell(row=row, column=1).value
        if dval:
            if isinstance(dval, (date, datetime)):
                existing_dates.add(dval.strftime("%Y%m%d"))
            else:
                raw = str(dval).strip()
                if len(raw) == 8 and raw.isdigit():
                    existing_dates.add(raw)
                elif len(raw) >= 10:
                    existing_dates.add(raw[:10].replace("-", ""))

    if not existing_dates:
        return

    latest_dt = datetime.strptime(latest_trade_date_str, "%Y%m%d").date()
    earliest_str = min(existing_dates)
    earliest_dt = datetime.strptime(earliest_str, "%Y%m%d").date()

    # 找出缺失的交易日
    missing_dates = []
    d = earliest_dt + timedelta(days=1)
    while d <= latest_dt:
        d_str = d.strftime("%Y%m%d")
        if is_trading_day(d) and d_str not in existing_dates:
            missing_dates.append(d_str)
        d = d + timedelta(days=1)

    if not missing_dates:
        return

    print(f"  [INFO] 检测到 {len(missing_dates)} 个缺失交易日: {missing_dates}")

    ts_codes = [h["ts"] for h in HOLDINGS]
    for d_str in missing_dates:
        print(f"    回补 {d_str}...")
        prices = fetch_prices(ts_codes, d_str)
        if not prices:
            print(f"      [WARN] {d_str} 无行情数据，跳过")
            continue

        # 汇总当日浮动盈亏
        total_mv = 0
        total_cost = 0
        rows_data = []
        for i, h in enumerate(HOLDINGS):
            row = CODE_ROWS[i]
            ts_code = h["ts"]
            cost_price = ws_summary.cell(row=row, column=8).value
            quantity = ws_summary.cell(row=row, column=9).value or 0
            target_weight = ws_summary.cell(row=row, column=5).value or 0

            if ts_code in prices:
                close = prices[ts_code]["close"]
                pct_chg = prices[ts_code]["pct_chg"]
            else:
                close = ws_summary.cell(row=row, column=11).value or 0
                pct_chg = 0

            if quantity and quantity > 0 and cost_price and cost_price > 0:
                mv = close * quantity
                cost_v = cost_price * quantity
                total_mv += mv
                total_cost += cost_v
                pnl = round((close - cost_price) * quantity, 2)
                pnl_pct = round((close - cost_price) / cost_price, 4)
            else:
                mv = 0
                cost_v = 0
                pnl = 0
                pnl_pct = 0

            rows_data.append({
                "code": h["code"], "name": h["name"], "close": close,
                "cost_price": cost_price, "quantity": quantity,
                "pct_chg": pct_chg, "pnl": pnl, "pnl_pct": pnl_pct,
                "market_value": mv, "cost_value": cost_v,
                "target_weight": target_weight,
            })

        # 计算权重
        for rd in rows_data:
            if total_mv > 0 and rd["quantity"] and rd["quantity"] > 0:
                rd["weight"] = round(rd["market_value"] / total_mv, 4)
            else:
                rd["weight"] = 0
            rd["deviation"] = round(rd["weight"] - rd["target_weight"], 4)

        # 删除旧数据（如有）并追加
        existing_rows = []
        for r in range(2, ws_daily.max_row + 1):
            dval = str(ws_daily.cell(row=r, column=1).value or "")
            if dval.replace("-", "") == d_str:
                existing_rows.append(r)
        for r in reversed(existing_rows):
            ws_daily.delete_rows(r)

        next_row = ws_daily.max_row + 1
        for rd in rows_data:
            ws_daily.cell(row=next_row, column=1).value = d_str
            ws_daily.cell(row=next_row, column=2).value = rd["name"]
            ws_daily.cell(row=next_row, column=3).value = str(rd["code"])
            ws_daily.cell(row=next_row, column=4).value = rd["quantity"] if rd["quantity"] else None
            ws_daily.cell(row=next_row, column=5).value = rd["cost_price"] if rd["cost_price"] else None
            ws_daily.cell(row=next_row, column=6).value = rd["cost_value"] if rd["cost_value"] else None
            ws_daily.cell(row=next_row, column=7).value = rd["close"]
            ws_daily.cell(row=next_row, column=8).value = round(rd["pct_chg"] / 100, 4) if rd["pct_chg"] else None
            ws_daily.cell(row=next_row, column=9).value = rd["market_value"]
            ws_daily.cell(row=next_row, column=10).value = rd["pnl"]
            ws_daily.cell(row=next_row, column=11).value = rd["pnl_pct"]
            ws_daily.cell(row=next_row, column=12).value = rd["target_weight"]
            ws_daily.cell(row=next_row, column=13).value = rd["weight"]
            ws_daily.cell(row=next_row, column=14).value = rd["deviation"]
            ws_daily.cell(row=next_row, column=15).value = "—"
            next_row += 1

        print(f"      已追加 {len(rows_data)} 个品种 ({d_str})")


# ==== Excel 更新 ====
def update_spreadsheet(prices: dict, trade_date_str: str):
    """主更新逻辑"""
    from openpyxl import load_workbook

    wb = load_workbook(FILE_PATH)
    ws_summary = wb["汇总"]
    ws_daily = wb["日报"]

    # --- 1. 更新汇总 sheet ---
    total_market_value = 0
    total_cost_value = 0
    holdings_data = []

    for i, h in enumerate(HOLDINGS):
        row = CODE_ROWS[i]
        ts_code = h["ts"]
        code = h["code"]
        cost_price = ws_summary.cell(row=row, column=8).value  # H: 成本价
        quantity = ws_summary.cell(row=row, column=9).value or 0  # I: 数量

        if ts_code in prices:
            close = prices[ts_code]["close"]
            pct_chg = prices[ts_code]["pct_chg"]
            # 更新 K: 今日收盘价
            ws_summary.cell(row=row, column=11).value = close

            if quantity and quantity > 0 and cost_price and cost_price > 0:
                cost_value = cost_price * quantity
                market_value = close * quantity
                total_market_value += market_value
                total_cost_value += cost_value

                holdings_data.append({
                    "code": code, "name": h["name"], "close": close,
                    "cost_price": cost_price, "quantity": quantity,
                    "target_weight": ws_summary.cell(row=row, column=5).value or 0,
                    "type": h["type"], "high_vol": h["high_vol"],
                    "pct_chg": pct_chg,
                })
            else:
                holdings_data.append({
                    "code": code, "name": h["name"], "close": close,
                    "cost_price": cost_price, "quantity": 0,
                    "target_weight": ws_summary.cell(row=row, column=5).value or 0,
                    "type": h["type"], "high_vol": h["high_vol"],
                    "pct_chg": pct_chg,
                })
        else:
            # 价格未获取，保留原值
            close = ws_summary.cell(row=row, column=11).value or 0
            cost_price = ws_summary.cell(row=row, column=8).value
            quantity = ws_summary.cell(row=row, column=9).value or 0
            if quantity and quantity > 0 and cost_price and cost_price > 0:
                market_value = close * quantity
                cost_value = cost_price * quantity
                total_market_value += market_value
                total_cost_value += cost_value

            holdings_data.append({
                "code": code, "name": h["name"], "close": close,
                "cost_price": cost_price, "quantity": quantity,
                "target_weight": ws_summary.cell(row=row, column=5).value or 0,
                "type": h["type"], "high_vol": h["high_vol"],
                "pct_chg": 0,
            })

    # 更新持仓权重和偏离
    for i, h in enumerate(HOLDINGS):
        row = CODE_ROWS[i]
        cost_price = ws_summary.cell(row=row, column=8).value
        quantity = ws_summary.cell(row=row, column=9).value or 0
        close = ws_summary.cell(row=row, column=11).value or 0
        target_weight = ws_summary.cell(row=row, column=5).value or 0

        if total_market_value > 0 and quantity and quantity > 0:
            actual_weight = round((close * quantity) / total_market_value, 4)
        else:
            actual_weight = 0

        ws_summary.cell(row=row, column=6).value = actual_weight  # F: 当前权重
        ws_summary.cell(row=row, column=7).value = round(actual_weight - target_weight, 4)  # G: 偏离

        # 更新 P&L 和 P&L%（统一处理，含零持仓）
        if quantity and quantity > 0 and cost_price and cost_price > 0:
            ws_summary.cell(row=row, column=12).value = round((close - cost_price) * quantity, 2)
            ws_summary.cell(row=row, column=13).value = round((close - cost_price) / cost_price, 4)
        else:
            ws_summary.cell(row=row, column=12).value = 0
            ws_summary.cell(row=row, column=13).value = 0

    # 更新表头日期
    today_str = datetime.now().strftime("%Y-%m-%d")
    trade_day_str = datetime.strptime(trade_date_str, "%Y%m%d").strftime("%Y-%m-%d")
    ws_summary.cell(row=1, column=1).value = f"更新日期: {today_str} (数据日期: {trade_day_str})"

    # 确保日报有触发信号列标题
    if ws_daily.cell(row=1, column=15).value is None:
        ws_daily.cell(row=1, column=15).value = "触发信号"

    # --- 2. 信号检测 ---
    # 补全 holdings_data 中的 weight 信息
    for i, hd in enumerate(holdings_data):
        row = CODE_ROWS[i]
        hd["weight"] = ws_summary.cell(row=row, column=6).value or 0
        hd["target_weight"] = ws_summary.cell(row=row, column=5).value or 0

    signals = detect_signals(holdings_data)

    # 组合级别信号
    total_pnl_pct = (total_market_value - total_cost_value) / total_cost_value if total_cost_value > 0 else 0
    equity_weight = sum(
        hd["weight"] for hd in holdings_data if HOLDING_MAP[hd["code"]]["type"] == "equity"
    )
    portfolio_signals = detect_portfolio_signals(total_pnl_pct, equity_weight)

    # 写入备注（始终更新，无信号则清空）
    for i, hd in enumerate(holdings_data):
        row = CODE_ROWS[i]
        ss = signals.get(hd["code"], [])
        note = " | ".join(ss) if ss else ""
        ws_summary.cell(row=row, column=14).value = note if note else None

    # --- 3. 追加日报 sheet ---
    # 日报列结构: A日期 B品种 C代码 D持仓股数 E成本价 F持仓成本 G今日收盘 H涨跌幅 I当前市值 J浮动盈亏 K盈亏% L目标权重 M实际权重 N偏离 O触发信号
    # 检查是否已存在当日数据
    today_short = trade_date_str  # YYYYMMDD
    existing_rows_to_delete = []
    for r in range(2, ws_daily.max_row + 1):
        dval = str(ws_daily.cell(row=r, column=1).value or "")
        if dval.replace("-", "") == today_short:
            existing_rows_to_delete.append(r)

    if existing_rows_to_delete:
        # 删除已有当日数据行（从后往前删，避免行号漂移）
        print(f"  [INFO] 删除已有 {trade_date_str} 数据 {len(existing_rows_to_delete)} 行")
        for r in reversed(existing_rows_to_delete):
            ws_daily.delete_rows(r)

    # 追加新数据
    next_row = ws_daily.max_row + 1
    for hd in holdings_data:
        code = hd["code"]
        close = hd["close"]
        cost_price = hd["cost_price"]
        quantity = hd["quantity"]
        pct_chg = hd.get("pct_chg", 0)  # 原始百分比值，如 1.41 表示 +1.41%
        pnl = round((close - cost_price) * quantity, 2) if (quantity and cost_price) else 0
        pnl_pct = round((close - cost_price) / cost_price, 4) if (quantity and cost_price and cost_price > 0) else 0
        market_value = round(close * quantity, 2) if quantity else 0
        cost_value = round(cost_price * quantity, 2) if (cost_price and quantity) else 0
        ss = signals.get(code, [])
        signal_str = " | ".join(ss) if ss else "—"
        deviation = hd.get("weight", 0) - hd.get("target_weight", 0)
        target_weight = hd.get("target_weight", 0)

        ws_daily.cell(row=next_row, column=1).value = today_short         # A: 日期
        ws_daily.cell(row=next_row, column=2).value = hd["name"]          # B: 品种
        ws_daily.cell(row=next_row, column=3).value = str(code)           # C: 代码
        ws_daily.cell(row=next_row, column=4).value = quantity if quantity else None  # D: 持仓股数
        ws_daily.cell(row=next_row, column=5).value = cost_price if cost_price else None  # E: 成本价
        ws_daily.cell(row=next_row, column=6).value = cost_value if cost_value else None  # F: 持仓成本
        ws_daily.cell(row=next_row, column=7).value = close                # G: 今日收盘
        ws_daily.cell(row=next_row, column=8).value = round(pct_chg / 100, 4) if pct_chg else None  # H: 涨跌幅(小数)
        ws_daily.cell(row=next_row, column=9).value = market_value         # I: 当前市值
        ws_daily.cell(row=next_row, column=10).value = pnl                 # J: 浮动盈亏
        ws_daily.cell(row=next_row, column=11).value = pnl_pct             # K: 盈亏%
        ws_daily.cell(row=next_row, column=12).value = target_weight       # L: 目标权重
        ws_daily.cell(row=next_row, column=13).value = hd.get("weight")    # M: 实际权重
        ws_daily.cell(row=next_row, column=14).value = round(deviation, 4) # N: 偏离
        ws_daily.cell(row=next_row, column=15).value = signal_str          # O: 触发信号
        next_row += 1

    print(f"  [OK] 日报追加 {len(holdings_data)} 条记录 ({trade_date_str})")

    # --- 3.5 回补缺失的交易日日报数据 ---
    backfill_missing_trading_days(wb, ws_summary, ws_daily, trade_date_str)

    # --- 4. 保存 ---
    wb.save(FILE_PATH)
    print(f"  [OK] 文件已保存: {FILE_PATH}")
    return True


# ==== 仪表盘 HTML 同步 ====
import re

DASHBOARD_PATH = Path(r'd:\cc-data\portfolio-dashboard.html')

# 仪表盘显示用元数据（名称、分类、颜色）
DASHBOARD_META = {
    510300: {"displayName": "华泰柏瑞沪深300ETF", "targetWeight": 18.75, "cat": "沪深300", "catColor": "#60a5fa"},
    588050: {"displayName": "科创50ETF",          "targetWeight": 6.25,  "cat": "科创50",  "catColor": "#c084fc"},
    159915: {"displayName": "易方达创业板ETF",    "targetWeight": 6.25,  "cat": "创业板",  "catColor": "#e879f9"},
    512890: {"displayName": "华泰柏瑞红利低波ETF","targetWeight": 6.25,  "cat": "红利低波","catColor": "#f472b6"},
    511380: {"displayName": "可转债ETF",          "targetWeight": 12.5,  "cat": "可转债",  "catColor": "#fb923c"},
    511260: {"displayName": "国泰上证10年期国债ETF","targetWeight":12.5, "cat": "10年国债","catColor": "#34d399"},
    511010: {"displayName": "国泰上证5年期国债ETF","targetWeight": 12.5, "cat": "5年国债", "catColor": "#2dd4bf"},
    511360: {"displayName": "海富通中证短融ETF",  "targetWeight": 6.25,  "cat": "短融",    "catColor": "#22d3ee"},
    518880: {"displayName": "华安黄金ETF",        "targetWeight": 18.75, "cat": "黄金",    "catColor": "#fbbf24"},
}


def sync_dashboard():
    """
    从 Excel 读取最新持仓/流水数据，同步写入 portfolio-dashboard.html
    确保仪表盘网页的 basePositions / transactions 数组与 Excel 一致。
    """
    from openpyxl import load_workbook

    if not FILE_PATH.exists():
        print("  [WARN] Excel 不存在，跳过仪表盘同步")
        return
    if not DASHBOARD_PATH.exists():
        print("  [WARN] 仪表盘 HTML 不存在，跳过同步")
        return

    print("  正在同步仪表盘 HTML...")
    wb = load_workbook(FILE_PATH, data_only=True)
    ws_sum = wb["汇总"]

    # --- 1. 读取汇总表持仓数据 ---
    positions = {}
    for i, h in enumerate(HOLDINGS):
        row = CODE_ROWS[i]
        code = h["code"]
        cost_price = ws_sum.cell(row=row, column=8).value   # H: 成本价
        quantity = ws_sum.cell(row=row, column=9).value or 0  # I: 数量
        cost_value = round(cost_price * quantity, 2) if cost_price and quantity else 0

        meta = DASHBOARD_META.get(code, {})
        positions[code] = {
            "cost_price": cost_price,
            "quantity": quantity,
            "cost_value": cost_value,
            "displayName": meta.get("displayName", h["name"]),
            "targetWeight": meta.get("targetWeight", 0),
            "cat": meta.get("cat", ""),
            "catColor": meta.get("catColor", "#888"),
        }

    # 按 HOLDINGS 顺序生成 basePositions JS
    pos_lines = []
    for h in HOLDINGS:
        p = positions[h["code"]]
        cp = p["cost_price"]
        cp_str = f"{cp}" if cp else "null"
        line = (
            f"  {{ code:'{h['code']}', name:'{p['displayName']}', "
            f"targetWeight:{p['targetWeight']}, costPrice:{cp_str}, "
            f"qty:{p['quantity']}, cost:{p['cost_value']}, "
            f"cat:'{p['cat']}', catColor:'{p['catColor']}' }}"
        )
        pos_lines.append(line)

    new_positions_block = "var basePositions = [\n" + ",\n".join(pos_lines) + "\n];"

    # --- 2. 读取流水表 ---
    ws_trade = wb["流水"]
    txns = []
    for row in ws_trade.iter_rows(min_row=2, values_only=True):
        date_val, code, name, price, qty, amount = row[:6]
        if not date_val or not code:
            continue
        # 处理日期格式
        if isinstance(date_val, (date, datetime)):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            date_str = str(date_val)[:10]
        code_str = str(int(code)) if isinstance(code, float) else str(code)
        name_str = str(name) if name else ""
        price_val = float(price) if price else 0
        qty_val = int(qty) if qty else 0
        amount_val = float(amount) if amount else 0
        # 容错：公式缓存丢失时，用 price * abs(qty) 计算
        if not amount_val and price_val and qty_val:
            amount_val = round(price_val * abs(qty_val), 2)
        txns.append({
            "date": date_str, "code": code_str, "name": name_str,
            "price": price_val, "qty": qty_val, "amount": amount_val,
        })

    txn_lines = []
    for t in txns:
        line = (
            f"  {{ date:'{t['date']}', code:'{t['code']}', name:'{t['name']}', "
            f"price:{t['price']}, qty:{t['qty']}, amount:{t['amount']} }}"
        )
        txn_lines.append(line)

    new_txns_block = "var transactions = [\n" + ",\n".join(txn_lines) + "\n];"

    # --- 3. 读取日报表，汇总每日浮动盈亏 ---
    ws_daily = wb["日报"]
    daily_totals = {}  # date_str -> total_floating_pnl
    for row in ws_daily.iter_rows(min_row=2, values_only=True):
        date_val = row[0]
        pnl = row[9]  # J: 浮动盈亏
        if not date_val:
            continue
        if isinstance(date_val, (date, datetime)):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            raw = str(date_val).strip()
            if len(raw) == 8 and raw.isdigit():
                date_str = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
            else:
                date_str = raw[:10] if len(raw) >= 10 else raw
        daily_totals[date_str] = daily_totals.get(date_str, 0) + (float(pnl) if pnl else 0)

    wb.close()

    # --- 4. 读取并更新 HTML ---
    html = DASHBOARD_PATH.read_text(encoding="utf-8")

    html = re.sub(
        r'var basePositions = \[[\s\S]*?\n\];',
        new_positions_block,
        html,
        count=1,
    )
    html = re.sub(
        r'var transactions = \[[\s\S]*?\n\];',
        new_txns_block,
        html,
        count=1,
    )

    # --- 5. SEED_DAILY 增量更新：解析已有条目 + 追加新日期 → 完整重建 ---
    existing_entries = []  # [(date, pnl, cumulative), ...]
    seed_match = re.search(r'var SEED_DAILY = \[([\s\S]*?)\n\];', html)
    if seed_match:
        entry_pattern = re.compile(r"date:'(\d{4}-\d{2}-\d{2})',\s*pnl:([-\d.]+),\s*cumulative:([-\d.]+)")
        for m in entry_pattern.finditer(seed_match.group(1)):
            existing_entries.append((m.group(1), float(m.group(2)), float(m.group(3))))

    # 构建已有日期集合
    existing_dates = {e[0] for e in existing_entries}

    # 找出新日期（Excel 有但 SEED_DAILY 没有的交易日）
    new_dates = sorted(d for d in daily_totals if d not in existing_dates)

    if new_dates:
        print(f"  [INFO] SEED_DAILY 追加 {len(new_dates)} 新日期: {new_dates}")
        # 合并新日期（先用 Excel 累计值，pnl 暂置 0）
        all_entries = existing_entries + [(d, 0, round(daily_totals[d], 2)) for d in new_dates]
        # 按日期排序
        all_entries.sort(key=lambda x: x[0])
        # 过滤非交易日
        filtered = []
        for d_str, p, c in all_entries:
            entry_date = date(int(d_str[:4]), int(d_str[5:7]), int(d_str[8:10]))
            if is_trading_day(entry_date):
                filtered.append((d_str, p, c))
        # 重新计算 pnl（基于排序后的累计值增量）
        all_entries = []
        prev_cum = 0
        for d_str, p, c in filtered:
            new_pnl = round(c - prev_cum, 2)
            all_entries.append((d_str, new_pnl, round(c, 2)))
            prev_cum = c
    else:
        # 无新日期，过滤非交易日
        all_entries = []
        for d_str, p, c in existing_entries:
            entry_date = date(int(d_str[:4]), int(d_str[5:7]), int(d_str[8:10]))
            if is_trading_day(entry_date):
                all_entries.append((d_str, p, c))
        if len(all_entries) < len(existing_entries):
            removed = [e[0] for e in existing_entries if e[0] not in {a[0] for a in all_entries}]
            print(f"  [INFO] SEED_DAILY 移除非交易日条目: {removed}")

    seed_lines = []
    for date_str, pnl, cum in all_entries:
        seed_lines.append(f"  {{ date:'{date_str}', pnl:{pnl}, cumulative:{cum} }}")
    new_seed_block = "var SEED_DAILY = [\n" + ",\n".join(seed_lines) + "\n];"

    html = re.sub(
        r'var SEED_DAILY = \[[\s\S]*?\n\];',
        new_seed_block,
        html,
        count=1,
    )

    if new_dates:
        print(f"  [INFO] SEED_DAILY 追加 {len(new_dates)} 新日期: {new_dates}")
    else:
        print(f"  [INFO] SEED_DAILY 无新日期需要追加")

    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    print(f"  [OK] 仪表盘已同步 ({len(pos_lines)} 持仓, {len(txn_lines)} 笔交易, +{len(new_dates)} 新快照)")


# ==== 主流程 ====
def main():
    print(f"\n{'='*60}")
    print(f"  400万资产配置组合 - 每日更新")
    print(f"  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    # 1. 判断交易日
    today = date.today()
    trade_day = get_latest_trading_day()
    trade_date_str = trade_day.strftime("%Y%m%d")

    if not is_trading_day(today):
        print(f"  [SKIP] {today} 非交易日，使用最近交易日 {trade_day}")
        # 如果是非交易日但想更新最近交易日数据，可以继续
        # return  # 如需严格跳过，取消此行注释

    print(f"  交易日: {trade_day} ({trade_date_str})")

    # 2. 检查文件
    if not FILE_PATH.exists():
        print(f"  [ERROR] 文件不存在: {FILE_PATH}")
        return 1

    # 3. 获取行情
    print("  正在获取行情数据...")
    ts_codes = [h["ts"] for h in HOLDINGS]
    prices = fetch_prices(ts_codes, trade_date_str)

    if not prices:
        print("  [ERROR] 未能获取任何行情数据，请检查 Tushare 连接和权限")
        return 1

    print(f"  获取到 {len(prices)}/{len(ts_codes)} 个品种价格")
    for ts_code, info in prices.items():
        print(f"    {ts_code}: close={info['close']}, chg={info['pct_chg']:+.2f}%")

    # 4. 更新 Excel
    print("  正在更新表格...")
    try:
        update_spreadsheet(prices, trade_date_str)
    except Exception as e:
        print(f"  [ERROR] 更新失败: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # 5. 同步仪表盘 HTML
    try:
        sync_dashboard()
    except Exception as e:
        print(f"  [WARN] 仪表盘同步失败（不中断主流程）: {e}")

    # 6. 保存缓存（记录最后更新时间）
    cache = {"last_update": datetime.now().isoformat(), "trade_date": trade_date_str}
    try:
        with open(CACHE_PATH, "wb") as f:
            pickle.dump(cache, f)
    except Exception:
        pass

    print(f"\n  [OK] 更新完成!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
