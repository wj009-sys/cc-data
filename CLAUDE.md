# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

400 万人民币资产配置组合管理系统，包含投资纪律、持仓追踪、每日行情更新和可视化仪表盘。

## 核心文件

| 文件 | 说明 |
|------|------|
| `400万资产配置组合2026.xlsx` | 主数据文件，含 7 个 sheet（投资纪要、汇总、流水、日报、GA优化方案、已实现利润、分红） |
| `update_portfolio.py` | 每日自动更新脚本，Tushare API 获取行情，更新汇总+日报，信号检测 + 仪表盘同步 |
| `portfolio-dashboard.html` | 投资仪表盘，Chart.js 可视化（内嵌数据数组 + 东方财富实时行情API） |
| `generate_briefing.py` | 投资简报生成 + 企业微信推送（午间/收盘两种模式） |
| `400万资产配置方案（优化版）.docx` | 原始资产配置方案文档（优化版） |
| `400万资产配置方案（简化版）.docx` | 原始资产配置方案文档（简化版） |
| `项目审计报告.md` | 项目全面审计报告（规则/数据/文件一致性） |
| `backtest_v3_report.xlsx` | V3 回测结果 Excel 报表 |

### 其他文件

| 目录/文件 | 说明 |
|-----------|------|
| `backtest_v3.py` | V3 权威回测脚本（+20%卖半/-15%止损/年度±20%） |
| `backtest_sp_reentry.py` | 止盈后补仓策略对比（A/B/D 三方案） |
| `check_circuit_breaker.py` | 熔断触发次数统计（2021-2025） |
| `ga_optimize.py` | 达尔文遗传算法优化器（种群60×50代，5参数连续优化） |
| `optimize_rules.py` | 网格搜索全参数扫描优化器（150组合） |
| `gen_v3_excel.py` / `gen_backtest_excel.py` / `gen_best_excel.py` | 回测结果导出 Excel 报表 |
| `backfill_dates.py` | 日报数据回补工具 |
| `*.json` (5个) | 回测/优化结果存档 |
| `*.xlsx` (2个) | 回测报表（backtest_v3_report.xlsx）+ 主数据文件 |
| `*.docx` (2个) | 资产配置方案文档（优化版/简化版） |
| `archive/` | 历史备份文件（V3备份/日报清理前备份） |
| `.claude/scheduled_tasks.json` | 定时任务配置（午间/收盘简报） |

## Excel 结构

- **投资纪要** — 止盈/止损/再平衡/熔断规则定义（V3优化版 2026-06-27）
- **汇总** — 9 个 ETF 持仓表：代码、成本价、数量、收盘价、浮盈/亏、权重、偏离、信号
- **流水** — 交易记录：日期、代码、名称、单价、数量、金额（含临时品种 510500/511990）
- **日报** — 每日行情快照，每个品种一行，末尾追加（⚠️ 2026-06-01~06-18 数据损坏，含重复行和列偏移）
- **GA优化方案** — 遗传算法优化结果存档，4 方案对比表 + 详细参数
- **已实现利润** — 卖出交易 FIFO 匹配计算的实际盈亏（3 笔，含汇总行）
- **分红** — ETF 现金分红记录（2 笔，含汇总行）

## 常用命令

```bash
# 手动执行每日更新（Tushare行情 → Excel → 仪表盘同步）
python update_portfolio.py

# 运行 GA 遗传算法优化（种群60×50代，~17秒）
python ga_optimize.py

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
- `transactions` — 流水 sheet 的 26 笔交易记录
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
| `ga_optimization_results.json` | `ga_optimize.py` | **12.30%** | 78.33% | 56 | 🧬 GA最优(止盈+31.5%卖69%/止损-15%/观察51日/年度±39%) |
| `optimization_results.json` | `optimize_rules.py` | **10.68%** | 65.88% | 95 | 网格最优(参数sp30/sl15/半年10) |
| `backtest_best_result.json` | `backtest_v3.py` | **7.05%** | 40.48% | 69 | V3最优(止盈+20%/止损-15%/观察20日/年度±20%) |
| `backtest_sp_reentry.json` | `backtest_sp_reentry.py` | **6.66%** | 37.94% | 71 | 止盈后补仓方案A |
| `buyhold_result.json` | — | 5.81% | 32.64% | 0 | 买入持有基准 |

**GA优化方案 sheet** 中的"当前V3"(CAGR 5.39%)与 `backtest_best_result.json`(CAGR 7.05%) 参数不同：
- GA sheet的"当前V3"是遗传算法迭代中的中间存档
- `backtest_best_result.json` 是 V3修复版的最终回测结果
- 当前投资纪律按 V3优化版 执行（`update_portfolio.py` 检测逻辑）
- 🧬 GA 最优参数（止盈+31.5%卖69%/止损-15%/观察51日/年度±39%）回测 CAGR 12.30%，但尚未实装到 live 系统
