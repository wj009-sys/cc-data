"""
600388 龙净环保 综合研究脚本
行情 + 技术面 + 基本面 + 资金面
"""
import os
import sys
import pandas as pd
import numpy as np

# Check tushare
try:
    import tushare as ts
except ImportError:
    print("ERROR: tushare not installed. Run: pip install tushare")
    sys.exit(1)

token = os.environ.get("TUSHARE_TOKEN", "")
if not token:
    print("ERROR: TUSHARE_TOKEN not set")
    sys.exit(1)

ts.set_token(token)
pro = ts.pro_api()

ts_code = "600388.SH"
base_date = "20260501"  # recent data start

print("="*80)
print("600388 龙净环保 - 综合数据获取")
print("="*80)

# ====================== 1. 基本信息 ======================
print("\n>>> [1/7] 股票基本信息...")
try:
    basic = pro.stock_basic(ts_code=ts_code, fields='ts_code,name,area,industry,market,list_date,list_status')
    print(basic.to_string())
except Exception as e:
    print(f"stock_basic error: {e}")

# ====================== 2. 日线行情 (70个交易日，确保足够计算) ======================
print("\n>>> [2/7] 日线行情数据...")
try:
    daily = pro.daily(ts_code=ts_code, start_date='20260101', end_date='20260603',
                       fields='ts_code,trade_date,open,high,low,close,vol,amount')
    if daily.empty:
        print("No daily data returned! Trying broader range...")
        daily = pro.daily(ts_code=ts_code, start_date='20251201', end_date='20260603',
                           fields='ts_code,trade_date,open,high,low,close,vol,amount')
    daily = daily.sort_values('trade_date').reset_index(drop=True)
    print(f"Got {len(daily)} rows of daily data")
    print(f"Date range: {daily['trade_date'].iloc[0]} to {daily['trade_date'].iloc[-1]}")
    print(daily.tail(40).to_string())
except Exception as e:
    print(f"daily error: {e}")
    daily = pd.DataFrame()

# ====================== 3. 日线估值指标 ======================
print("\n>>> [3/7] 日线估值指标 (daily_basic)...")
try:
    daily_basic = pro.daily_basic(ts_code=ts_code, start_date='20260101', end_date='20260603',
                                   fields='ts_code,trade_date,close,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,total_share,float_share,total_mv,circ_mv')
    daily_basic = daily_basic.sort_values('trade_date').reset_index(drop=True)
    print(f"Got {len(daily_basic)} rows")
    if not daily_basic.empty:
        print(daily_basic.tail(10).to_string())
except Exception as e:
    print(f"daily_basic error: {e}")
    daily_basic = pd.DataFrame()

# ====================== 4. 资金流向 ======================
print("\n>>> [4/7] 资金流向 (moneyflow)...")
try:
    moneyflow = pro.moneyflow(ts_code=ts_code, start_date='20260101', end_date='20260603')
    moneyflow = moneyflow.sort_values('trade_date').reset_index(drop=True)
    print(f"Got {len(moneyflow)} rows")
    if not moneyflow.empty:
        print(moneyflow.tail(10).to_string())
except Exception as e:
    print(f"moneyflow error: {e}")
    moneyflow = pd.DataFrame()

# ====================== 5. 财务数据 - 利润表 ======================
print("\n>>> [5/7] 财务数据 - 利润表 (income)...")
try:
    income = pro.income(ts_code=ts_code, start_date='20240101', end_date='20260603',
                        fields='ts_code,end_date,report_type,revenue,operate_profit,total_profit,n_income,n_income_attr_p,basic_eps,revenue_yoy,n_income_yoy,operate_profit_yoy')
    income = income.sort_values('end_date').reset_index(drop=True)
    print(f"Got {len(income)} rows")
    if not income.empty:
        print(income.to_string())
except Exception as e:
    print(f"income error: {e}")
    income = pd.DataFrame()

# ====================== 6. 财务指标 ======================
print("\n>>> [6/7] 财务指标 (fina_indicator)...")
try:
    fina = pro.fina_indicator(ts_code=ts_code, start_date='20240101', end_date='20260603',
                              fields='ts_code,end_date,roe,roe_dt,roa,roic,grossprofit_margin,netprofit_margin,debt_to_assets,current_ratio,quick_ratio,cf_sales,assets_turn')
    fina = fina.sort_values('end_date').reset_index(drop=True)
    print(f"Got {len(fina)} rows")
    if not fina.empty:
        print(fina.to_string())
except Exception as e:
    print(f"fina_indicator error: {e}")
    fina = pd.DataFrame()

# ====================== 7. 业绩预告 ======================
print("\n>>> [7/7] 业绩预告 (forecast)...")
try:
    forecast = pro.forecast(ts_code=ts_code, start_date='20260101', end_date='20260603')
    print(f"Got {len(forecast)} rows")
    if not forecast.empty:
        print(forecast.to_string())
    else:
        print("无最新业绩预告")
except Exception as e:
    print(f"forecast error: {e}")
    forecast = pd.DataFrame()

print("\n" + "="*80)
print("数据获取完毕！开始分析...")
print("="*80)

# ====================== 技术面分析 ======================
if not daily.empty and len(daily) >= 10:
    daily = daily.sort_values('trade_date').reset_index(drop=True)

    # Moving averages
    daily['ma5'] = daily['close'].rolling(5).mean()
    daily['ma10'] = daily['close'].rolling(10).mean()
    daily['ma20'] = daily['close'].rolling(20).mean()

    # Volume MA
    daily['vol_ma5'] = daily['vol'].rolling(5).mean()
    daily['vol_ma20'] = daily['vol'].rolling(20).mean()

    # Latest row
    latest = daily.iloc[-1]
    prev_5 = daily.iloc[-6] if len(daily) >= 6 else daily.iloc[0]  # 5 trading days ago
    prev_10 = daily.iloc[-11] if len(daily) >= 11 else daily.iloc[0]
    prev_20 = daily.iloc[-21] if len(daily) >= 21 else daily.iloc[0]

    print("\n" + "="*60)
    print("【技术面分析】")
    print("="*60)
    print(f"\n最新交易日: {latest['trade_date']}")
    print(f"收盘价: {latest['close']:.2f}")
    print(f"MA5: {latest['ma5']:.2f} (偏离: {((latest['close']/latest['ma5'])-1)*100:.2f}%)")
    print(f"MA10: {latest['ma10']:.2f} (偏离: {((latest['close']/latest['ma10'])-1)*100:.2f}%)")
    print(f"MA20: {latest['ma20']:.2f} (偏离: {((latest['close']/latest['ma20'])-1)*100:.2f}%)")

    # Returns
    ret_5 = ((latest['close'] / prev_5['close']) - 1) * 100
    ret_10 = ((latest['close'] / prev_10['close']) - 1) * 100
    ret_20 = ((latest['close'] / prev_20['close']) - 1) * 100

    print(f"\n近5日涨跌幅: {ret_5:+.2f}%")
    print(f"近10日涨跌幅: {ret_10:+.2f}%")
    print(f"近20日涨跌幅: {ret_20:+.2f}%")

    # Volatility (20-day, daily return std, annualized)
    daily['ret_daily'] = daily['close'].pct_change()
    if len(daily) >= 20:
        vol_20_daily = daily['ret_daily'].tail(20).std()
        vol_annual = vol_20_daily * np.sqrt(252) * 100
        print(f"\n20日波动率 (年化): {vol_annual:.2f}%")

    # Volume analysis
    recent_vol = daily['vol'].tail(5).mean()
    mid_vol = daily['vol'].tail(20).head(15).mean()
    vol_ratio = recent_vol / mid_vol if mid_vol > 0 else 1
    print(f"\n近5日均量 / 前15日均量: {vol_ratio:.2f}")
    print(f"成交量趋势: {'放量' if vol_ratio > 1.2 else '缩量' if vol_ratio < 0.8 else '平稳'}")

    # Recent highs and lows
    recent_20 = daily.tail(20)
    print(f"\n近20日最高价: {recent_20['high'].max():.2f} (日期: {recent_20[recent_20['high']==recent_20['high'].max()]['trade_date'].values[0]})")
    print(f"近20日最低价: {recent_20['low'].min():.2f} (日期: {recent_20[recent_20['low']==recent_20['low'].min()]['trade_date'].values[0]})")

    # Price position in 20-day range
    price_range = recent_20['high'].max() - recent_20['low'].min()
    price_position = (latest['close'] - recent_20['low'].min()) / price_range * 100 if price_range > 0 else 50
    print(f"当前价格在20日区间内的位置: {price_position:.1f}% (0%=最低, 100%=最高)")

    # Volume-price relationship
    print(f"\n量价关系 (近5日):")
    recent_5 = daily.tail(5)
    for _, row in recent_5.iterrows():
        ret_sign = "+" if row['ret_daily'] > 0 else ""
        vol_str = "放量" if row['vol'] > row['vol_ma5'] else "缩量"
        print(f"  {row['trade_date']} 收盘{row['close']:.2f} 涨跌幅{ret_sign}{row['ret_daily']*100:.2f}% 成交量{row['vol']:.0f}手 ({vol_str})")

    # Annual performance (if data available)
    if len(daily) >= 40:
        ytd_start = daily.iloc[-40]  # roughly start of year
        ytd_ret = ((latest['close'] / daily.iloc[0]['close']) - 1) * 100
        print(f"\n今年累计涨跌幅 (从{daily.iloc[0]['trade_date']}起): {ytd_ret:+.2f}%")

# ====================== 估值分析 ======================
if not daily_basic.empty:
    print("\n" + "="*60)
    print("【估值分析】")
    print("="*60)
    latest_basic = daily_basic.iloc[-1]
    print(f"\n最新估值数据 (日期: {latest_basic['trade_date']}):")
    print(f"PE (TTM): {latest_basic.get('pe_ttm', 'N/A')}")
    print(f"PB: {latest_basic.get('pb', 'N/A')}")
    print(f"PS (TTM): {latest_basic.get('ps_ttm', 'N/A')}")
    print(f"总市值: {latest_basic.get('total_mv', 'N/A')} 万元" if pd.notna(latest_basic.get('total_mv')) else "总市值: N/A")
    print(f"流通市值: {latest_basic.get('circ_mv', 'N/A')} 万元" if pd.notna(latest_basic.get('circ_mv')) else "流通市值: N/A")
    print(f"股息率 (TTM): {latest_basic.get('dv_ttm', 'N/A')}%" if pd.notna(latest_basic.get('dv_ttm')) else "股息率: N/A")

# ====================== 资金面分析 ======================
if not moneyflow.empty:
    print("\n" + "="*60)
    print("【资金面分析】")
    print("="*60)

    # Aggregate recent money flow
    recent_mf = moneyflow.tail(20)
    total_net = recent_mf['net_mf_amount'].sum() if 'net_mf_amount' in recent_mf.columns else 0
    avg_net = recent_mf['net_mf_amount'].mean() if 'net_mf_amount' in recent_mf.columns else 0

    print(f"\n近20日主力资金净流入合计: {total_net:.0f} 万元")
    print(f"日均主力资金净流入: {avg_net:.0f} 万元")

    # Recent 5 days
    recent_mf_5 = moneyflow.tail(5)
    print(f"\n近5日资金流明细:")
    for _, row in recent_mf_5.iterrows():
        net = row.get('net_mf_amount', 0)
        direction = "流入" if net > 0 else "流出"
        print(f"  {row['trade_date']}: 主力净{direction} {abs(net):.0f}万元")

    # Cumulative flow trend
    moneyflow_sorted = moneyflow.sort_values('trade_date')
    if 'net_mf_amount' in moneyflow_sorted.columns:
        moneyflow_sorted['cum_net'] = moneyflow_sorted['net_mf_amount'].cumsum()
        print(f"\n累计主力资金净流向 (观察期起点至今): {moneyflow_sorted['cum_net'].iloc[-1]:.0f}万元")

# ====================== 财务分析 ======================
if not income.empty:
    print("\n" + "="*60)
    print("【财务面分析 - 利润表】")
    print("="*60)

    # Filter only annual and quarterly reports, remove duplicates
    income_display = income[income['report_type'].isin(['1', '0'])].copy() if 'report_type' in income.columns else income.copy()

    for _, row in income_display.iterrows():
        rev = row.get('revenue', 0)
        op = row.get('operate_profit', 0)
        ni = row.get('n_income_attr_p', 0) if pd.notna(row.get('n_income_attr_p')) else row.get('n_income', 0)
        eps = row.get('basic_eps', 0)
        rev_yoy = row.get('revenue_yoy', np.nan)
        ni_yoy = row.get('n_income_yoy', np.nan)
        op_yoy = row.get('operate_profit_yoy', np.nan)

        rev_str = f"{rev/1e4:.2f}亿" if pd.notna(rev) else "N/A"
        op_str = f"{op/1e4:.2f}亿" if pd.notna(op) else "N/A"
        ni_str = f"{ni/1e4:.2f}亿" if pd.notna(ni) else "N/A"
        rev_yoy_str = f"{rev_yoy:+.2f}%" if pd.notna(rev_yoy) else "N/A"
        ni_yoy_str = f"{ni_yoy:+.2f}%" if pd.notna(ni_yoy) else "N/A"

        print(f"\n报告期: {row['end_date']}")
        print(f"  营收: {rev_str} (同比: {rev_yoy_str})")
        print(f"  营业利润: {op_str} (同比: {op_yoy if pd.notna(op_yoy) else 'N/A'}%)")
        print(f"  归母净利润: {ni_str} (同比: {ni_yoy_str})")
        print(f"  基本EPS: {eps}")

if not fina.empty:
    print("\n" + "="*60)
    print("【财务面分析 - 核心财务指标】")
    print("="*60)

    for _, row in fina.iterrows():
        print(f"\n报告期: {row['end_date']}")
        roe = row.get('roe', np.nan)
        roa = row.get('roa', np.nan)
        gpm = row.get('grossprofit_margin', np.nan)
        npm = row.get('netprofit_margin', np.nan)
        dar = row.get('debt_to_assets', np.nan)
        cr = row.get('current_ratio', np.nan)
        cf_sales = row.get('cf_sales', np.nan)

        print(f"  ROE: {roe:.2f}%" if pd.notna(roe) else "  ROE: N/A")
        print(f"  ROA: {roa:.2f}%" if pd.notna(roa) else "  ROA: N/A")
        print(f"  毛利率: {gpm:.2f}%" if pd.notna(gpm) else "  毛利率: N/A")
        print(f"  净利率: {npm:.2f}%" if pd.notna(npm) else "  净利率: N/A")
        print(f"  资产负债率: {dar:.2f}%" if pd.notna(dar) else "  资产负债率: N/A")
        print(f"  流动比率: {cr:.2f}" if pd.notna(cr) else "  流动比率: N/A")
        print(f"  经营现金流/营收: {cf_sales:.2f}" if pd.notna(cf_sales) else "  经营现金流/营收: N/A")

# ====================== 综合摘要 ======================
print("\n" + "="*80)
print("数据获取与分析完毕")
print("="*80)
