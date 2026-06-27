# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

400 万人民币资产配置组合管理系统，包含投资纪律、持仓追踪、每日行情更新和可视化仪表盘。

## 核心文件

| 文件 | 说明 |
|------|------|
| `400万资产配置组合2026.xlsx` | 主数据文件，含 5 个 sheet（投资纪要、汇总、流水、日报、GA优化方案） |
| `update_portfolio.py` | 每日自动更新脚本，Tushare API 获取行情，更新汇总+日报，信号检测 + 仪表盘同步 |
| `portfolio-dashboard.html` | 投资仪表盘，Chart.js 可视化（内嵌数据数组 + 东方财富实时行情API） |
| `generate_briefing.py` | 投资简报生成 + 企业微信推送（午间/收盘两种模式） |
| `500万资产配置方案（优化版）.docx` | 原始资产配置方案文档 |

### 其他文件

| 目录/文件 | 说明 |
|-----------|------|
| `backtest_*.py` (7个) | 不同版本/参数的回测脚本 |
| `gen_*_excel.py` (3个) | 回测结果导出 Excel 报表 |
| `check_circuit_breaker.py` | 熔断触发次数统计（2021-2025） |
| `optimize_rules.py` | 规则排列组合全参数扫描优化器 |
| `update_dashboard_rules.py` | 更新仪表盘纪律规则为V3版本 |
| `backfill_dates.py` | 日报数据回补工具 |
| `*.json` (7个) | 各回测方案的结果存档 |
| `*.xlsx` (4个) | 回测报表Excel |
| `.claude/scheduled_tasks.json` | 定时任务配置（午间/收盘简报） |

## Excel 结构

- **投资纪要** — 止盈/止损/再平衡/熔断规则定义（V3优化版 2026-06-27）
- **汇总** — 9 个 ETF 持仓表：代码、成本价、数量、收盘价、浮盈/亏、权重、偏离、信号
- **流水** — 交易记录：日期、代码、名称、单价、数量、金额（含临时品种 510500/511990）
- **日报** — 每日行情快照，每个品种一行，末尾追加（⚠️ 2026-06-01~06-18 数据损坏，含重复行和列偏移）
- **GA优化方案** — 遗传算法优化结果存档，4 方案对比表 + 详细参数

## 常用命令

```bash
# 手动执行每日更新（Tushare行情 → Excel → 仪表盘同步）
python update_portfolio.py

# 查看定时任务
cat .claude/scheduled_tasks.json

# 读取 Excel 数据（openpyxl，保留公式）
python -c "from openpyxl import load_workbook; wb = load_workbook('400万资产配置组合2026.xlsx'); ..."

# 仅同步仪表盘（不更新行情）
python -c "exec(open('update_portfolio.py').read().replace('if __name__==\"__main__\":','if False:')); sync_dashboard()"
```

## 定时任务

- 午间简报 11:45 `generate_briefing.py midday`（cron: `45 11 * * 1-5`）
- 收盘简报 16:33 `generate_briefing.py afternoon`（cron: `33 16 * * 1-5`）
- 7 天自动过期，需续期：用户说「续期定时任务」时用 CronCreate 重建
- 注意：收盘简报依赖 `update_portfolio.py` 先执行完毕（手动或通过 `update_portfolio.py` 主流程）

## 交易录入约定

用户提供交易信息（日期/品种/方向/数量/单价）时：
1. 在「流水」sheet 末尾追加记录
2. 在「汇总」sheet 更新对应品种的成本价（加权平均）、数量、成本
3. 刷新权重和偏离数据

详见 memory: [[trade-update-workflow]]

## 数据来源

- 行情数据（收盘）：Tushare `fund_daily` API，环境变量 `TUSHARE_TOKEN`
- 行情数据（实时）：东方财富 `push2.eastmoney.com` API（仪表盘前端直接调用）
- 推送渠道：企业微信群机器人，环境变量 `WECOM_WEBHOOK`（可选，默认硬编码在 generate_briefing.py）
- 9 个核心持仓代码：510300/588050/159915/512890/511380/511260/511010/511360/518880
- 临时品种（已清仓）：510500 中证500ETF、511990 华宝添益ETF
- 中国节假日列表硬编码在 `update_portfolio.py` 和 `portfolio-dashboard.html` 中，需每年更新

## 仪表盘数据同步机制

`update_portfolio.py` 的 `sync_dashboard()` 函数定期将 Excel 数据同步到 HTML 文件：
- `basePositions` — 汇总 sheet 的 9 个品种持仓数据
- `transactions` — 流水 sheet 的 25 笔交易记录
- `SEED_DAILY` — 日报 sheet 的每日浮动盈亏（**已修复 v2026.06.27**：从总P&L正确计算每日变化量，而非直接用总P&L）

## 信号检测规则

`update_portfolio.py` 内置以下检测逻辑（与投资纪要一致，V3优化版）：
- 止盈（单档）：+20% 卖半仓，仅权益类，资金转入511360短融ETF
- 止损（统一）：-15% 次日清仓，观察20个交易日方可重建
- 再平衡（年度）：12月末检查，相对偏离 ≥ ±20% 时触发
- 熔断四级：-5%/-10%/-12%/-15% 组合回撤

## 回测结果参考（2021-2025，400万基准）

| JSON文件 | 对应脚本 | CAGR | 总收益 | 交易笔数 | 说明 |
|----------|---------|------|--------|---------|------|
| `buyhold_result.json` | — | 5.81% | 32.64% | 0 | 买入持有基准 |
| `backtest_best_result.json` | `backtest_rules_best.py` | **7.05%** | 40.48% | 69 | V3最优(止盈+20%/止损-15%/观察20日) |
| `backtest_sp_reentry.json` | `backtest_sp_reentry.py` | **6.66%** | 37.94% | 71 | 止盈后补仓方案A |
| `backtest_rules_result.json` | `backtest_rules_v2.py` | 5.09% | 28.07% | 90 | V2阶梯止盈(7%/9%/12%) |
| `backtest_precise_rules_result.json` | `backtest_precise_rules.py` | 0.18% | 0.88% | — | 极严格纪律 |
| `optimization_results.json` | `optimize_rules.py` | **10.68%** | 65.88% | 95 | GA全组合最优(参数sp30/sl15/半年10) |

**GA优化方案 sheet** 中的"当前V3"(CAGR 5.39%)与 `backtest_best_result.json`(CAGR 7.05%) 参数不同：
- GA sheet的"当前V3"是遗传算法迭代中的中间存档
- `backtest_best_result.json` 是 V3修复版的最终回测结果
- 当前投资纪律按 V3优化版 执行（`update_portfolio.py` 检测逻辑）
