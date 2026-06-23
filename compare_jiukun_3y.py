"""3-year backtest: baseline portfolio vs replacing 中证500+货基 with 九坤500指增."""
import os
import pandas as pd
import numpy as np
import tushare as ts

CAPITAL = 5_000_000
YEARS = 3

weights_base = {
    "510300.SH": 15,
    "510500.SH": 10,
    "588050.SH": 5,
    "159915.SZ": 5,
    "512890.SH": 5,
    "511380.SH": 10,
    "511260.SH": 10,
    "511010.SH": 10,
    "511360.SH": 5,
    "518880.SH": 15,
    "511880.SH": 5,
    "511990.SH": 5,
}

# Public annual returns for 九坤日享中证500指数增强1号 (representative line)
# 九坤日享中证500指数增强1号 — 公开年度收益(国金/私募排排等)，2024全年取策略中性估计
JK_ANNUAL_REP = {
    2023: 0.2010,
    2024: 0.00,    # H1约-13.7%后修复，全年近似持平(非进取系列)
    2025: 0.18,
    2026: 0.06,
}
JK_ANNUAL_PESS = {**JK_ANNUAL_REP, 2024: -0.1367}
JK_ANNUAL_OPT = {**JK_ANNUAL_REP, 2024: 0.08, 2025: 0.25}

# 九坤官方材料（日享中证500指增 X号 B份额，截至 2026-04-30，Wind/九坤）
JK_OFFICIAL = {
    "ann_return": 0.2188,       # 年化收益率(复利)，成立以来
    "ann_excess": 0.1809,       # 年化超额(复利)
    "ann_vol": 0.2116,
    "sharpe": 1.03,
    "max_dd": -0.29,
    "since_inception_cum": 4.5791,
    "benchmark_cum": 0.3156,    # 同期中证500
    "b_share_start": "20211011",
}


def resolve_window(pro):
    """Last 3 calendar years ending at latest SSE trading day."""
    cal = pro.trade_cal(
        exchange="SSE",
        start_date=(pd.Timestamp.today() - pd.DateOffset(years=YEARS + 1)).strftime("%Y%m%d"),
        end_date=pd.Timestamp.today().strftime("%Y%m%d"),
        is_open="1",
    )
    cal = cal.sort_values("cal_date")
    end = cal["cal_date"].iloc[-1]
    start_cut = (pd.Timestamp(end) - pd.DateOffset(years=YEARS)).strftime("%Y%m%d")
    start = cal.loc[cal["cal_date"] >= start_cut, "cal_date"].iloc[0]
    return start, end


def load_data(pro, start, end):
    frames = {}
    for c in weights_base:
        df = pro.fund_daily(ts_code=c, start_date=start, end_date=end)
        df = df.sort_values("trade_date")
        df["ret"] = df["close"].pct_change()
        frames[c] = df.set_index("trade_date")["ret"]
    idx = pro.index_daily(ts_code="000905.SH", start_date=start, end_date=end)
    idx = idx.sort_values("trade_date")
    idx["ret"] = idx["close"].pct_change()
    frames["000905.SH"] = idx.set_index("trade_date")["ret"]
    dates = sorted(set.intersection(*[set(s.index) for s in frames.values()]))
    return frames, dates


def port_return(frames, dates, weights, sleeve_series=None):
    out = []
    for d in dates:
        r = 0.0
        for k, w in weights.items():
            if k == "JK":
                rr = sleeve_series.get(d, np.nan) if sleeve_series is not None else np.nan
            else:
                rr = frames[k].get(d, np.nan)
            if pd.notna(rr):
                r += w / 100 * rr
        out.append(r)
    return pd.Series(out, index=dates)


def stats(s, name):
    cum = (1 + s).prod() - 1
    n = len(s)
    ann = (1 + cum) ** (252 / n) - 1
    nav = (1 + s).cumprod()
    dd = (nav / nav.cummax() - 1).min()
    vol = s.std() * np.sqrt(252)
    sharpe = ann / vol if vol > 0 else 0
    return {
        "name": name,
        "cum": cum,
        "ann": ann,
        "mdd": dd,
        "vol": vol,
        "sharpe": sharpe,
        "end": CAPITAL * (1 + cum),
        "pnl": CAPITAL * cum,
    }


def annualize_sleeve(frames, dates, annual_map):
    """Distribute (target - index) excess evenly as daily additive alpha within each year."""
    idx = frames["000905.SH"]
    s = pd.Series(index=dates, dtype=float)
    for y, target in annual_map.items():
        m = [d for d in dates if d.startswith(str(y))]
        if not m:
            continue
        r = idx.loc[m].fillna(0)
        idx_cum = (1 + r).prod() - 1
        # Spread required excess linearly across trading days (stable vs multiplicative scaling)
        daily_alpha = (target - idx_cum) / len(m)
        s.loc[m] = r + daily_alpha
    return s


def official_sleeve(frames, dates, mode="return"):
    """Reconstruct daily sleeve from official deck: index + excess, or fixed ann return."""
    idx = frames["000905.SH"].reindex(dates).fillna(0)
    n = len(dates)
    if mode == "return":
        target_cum = (1 + JK_OFFICIAL["ann_return"]) ** (n / 252) - 1
    else:
        target_cum = (1 + JK_OFFICIAL["ann_excess"]) ** (n / 252) - 1
        idx_cum = (1 + idx).prod() - 1
        target_cum = (1 + idx_cum) * (1 + target_cum) - 1
    idx_cum = (1 + idx).prod() - 1
    daily_alpha = (target_cum - idx_cum) / n
    return idx + daily_alpha


def main():
    ts.set_token(os.environ["TUSHARE_TOKEN"])
    pro = ts.pro_api()
    start, end = resolve_window(pro)
    frames, dates = load_data(pro, start, end)

    base = port_return(frames, dates, weights_base)
    s500 = frames["510500.SH"].reindex(dates)
    cash = (
        frames["511880.SH"].reindex(dates).fillna(0)
        + frames["511990.SH"].reindex(dates).fillna(0)
    ) / 2
    sleeve_old = 0.5 * s500 + 0.5 * cash

    weights_alt = {
        k: v
        for k, v in weights_base.items()
        if k not in ("510500.SH", "511880.SH", "511990.SH")
    }
    weights_alt["JK"] = 20

    jk_rep = annualize_sleeve(frames, dates, JK_ANNUAL_REP)
    jk_pess = annualize_sleeve(frames, dates, JK_ANNUAL_PESS)
    jk_opt = annualize_sleeve(frames, dates, JK_ANNUAL_OPT)
    jk_official = official_sleeve(frames, dates, mode="return")

    alt_same = port_return(frames, dates, weights_alt, sleeve_old.to_dict())
    alt_rep = port_return(frames, dates, weights_alt, jk_rep.to_dict())
    alt_pess = port_return(frames, dates, weights_alt, jk_pess.to_dict())
    alt_opt = port_return(frames, dates, weights_alt, jk_opt.to_dict())
    alt_official = port_return(frames, dates, weights_alt, jk_official.to_dict())

    rows = [
        stats(base, "基准组合(12只ETF目标权重)"),
        stats(alt_same, "替代-仅合并仓位(收益=原50万合成)"),
        stats(alt_official, "替代-九坤官方(B份额 年化21.88%路径拟合)"),
        stats(alt_rep, "替代-九坤代表(公开年度收益拟合-旧)"),
        stats(alt_pess, "替代-悲观(2024按H1 -13.67%全年)"),
        stats(alt_opt, "替代-乐观(2024微正/2025更强)"),
    ]

    print("=" * 72)
    print(f"回测区间: {dates[0]} ~ {dates[-1]}  (约{YEARS}年, {len(dates)}个交易日)  本金: {CAPITAL:,}")
    print("替换规则: 510500(10%/50万) + 511880(5%) + 511990(5%) -> 九坤500指增 20%/100万")
    print("=" * 72)
    for r in rows:
        print(
            f"{r['name']}\n"
            f"  累计 {r['cum']*100:6.2f}%  年化 {r['ann']*100:6.2f}%  "
            f"最大回撤 {r['mdd']*100:6.2f}%  波动 {r['vol']*100:5.2f}%  "
            f"夏普 {r['sharpe']:.2f}\n"
            f"  期末市值 {r['end']:,.0f}  盈亏 {r['pnl']:+,.0f}\n"
        )

    print("--- 100万仓位（被替换部分）三年表现 ---")
    jk_sleeve_stats = stats(jk_official, "九坤100万")
    print(
        "--- 九坤官方材料对照 (成立以来至2026-04-30) ---\n"
        f"  材料披露: 年化{JK_OFFICIAL['ann_return']*100:.2f}%  超额年化{JK_OFFICIAL['ann_excess']*100:.2f}%  "
        f"波动{JK_OFFICIAL['ann_vol']*100:.2f}%  夏普{JK_OFFICIAL['sharpe']:.2f}  "
        f"最大回撤{JK_OFFICIAL['max_dd']*100:.2f}%\n"
        f"  同期中证500累计{JK_OFFICIAL['benchmark_cum']*100:.2f}%  产品累计{JK_OFFICIAL['since_inception_cum']*100:.2f}%\n"
        f"  B份额运作自 {JK_OFFICIAL['b_share_start']}\n"
        f"  本回测窗内100万 sleeve拟合: 累计{jk_sleeve_stats['cum']*100:.2f}%  "
        f"年化{jk_sleeve_stats['ann']*100:.2f}%  回撤{jk_sleeve_stats['mdd']*100:.2f}%\n"
    )

    for label, s in [
        ("原方案: 50%510500+50%货基", sleeve_old),
        ("九坤官方路径拟合", jk_official),
        ("九坤代表(年度公开-旧)", jk_rep),
        ("仅510500ETF", s500),
        ("仅货基(银华+华宝均值)", cash),
        ("中证500指数", frames["000905.SH"].reindex(dates)),
    ]:
        cum = (1 + s.fillna(0)).prod() - 1
        print(f"  {label}: {cum*100:.2f}%  -> 100万期末约 {1_000_000*(1+cum):,.0f}")

    print("\n--- 分年度组合收益 vs 差额(替代-基准) ---")
    print(f"{'年份':<6} {'基准':>8} {'替代(代表)':>10} {'差额pp':>8} {'原100万':>10} {'九坤100万':>10}")
    for y in [2023, 2024, 2025, 2026]:
        m = [d for d in dates if d.startswith(str(y))]
        if not m:
            continue
        b = (1 + base.loc[m]).prod() - 1
        a = (1 + alt_official.loc[m]).prod() - 1
        so = (1 + sleeve_old.loc[m]).prod() - 1
        sj = (1 + jk_official.loc[m]).prod() - 1
        print(
            f"{y:<6} {b*100:7.2f}% {a*100:9.2f}% {(a-b)*100:+7.2f} "
            f"{so*100:9.2f}% {sj*100:9.2f}%"
        )

    diff = stats(alt_official, "")["cum"] - stats(base, "")["cum"]
    diff_old = stats(alt_rep, "")["cum"] - stats(base, "")["cum"]
    print(
        f"\n三年累计差额(官方年化路径): {diff*100:+.2f}个百分点 ≈ {CAPITAL*diff:+,.0f} 元\n"
        f"三年累计差额(旧-年度拟合):   {diff_old*100:+.2f}个百分点 ≈ {CAPITAL*diff_old:+,.0f} 元"
    )


if __name__ == "__main__":
    main()
