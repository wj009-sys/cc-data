# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

400 万人民币资产配置组合管理系统，包含投资纪律、持仓追踪、每日行情更新和可视化仪表盘。

## 核心文件

| 文件 | 说明 |
|------|------|
| `500万资产配置组合2026.xlsx` | 主数据文件，含 4 个 sheet（投资纪要、汇总、流水、日报） |
| `update_portfolio.py` | 每日自动更新脚本，Tushare API 获取行情，更新汇总+日报，信号检测 |
| `portfolio-dashboard.html` | 投资仪表盘，Chart.js 可视化（从 Excel 数据渲染） |
| `500万资产配置方案（优化版）.docx` | 原始资产配置方案文档 |

## Excel 结构

- **投资纪要** — 止盈/止损/再平衡/熔断规则定义
- **汇总** — 9 个 ETF 持仓表：代码、成本价、数量、收盘价、浮盈/亏、权重、偏离、信号
- **流水** — 交易记录：日期、代码、名称、单价、数量、金额
- **日报** — 每日行情快照，每个品种一行，末尾追加

## 常用命令

```bash
# 手动执行每日更新
python update_portfolio.py

# 查看定时任务
cat .claude/scheduled_tasks.json

# 读取 Excel 数据（openpyxl，保留公式）
python -c "from openpyxl import load_workbook; wb = load_workbook('500万资产配置组合2026.xlsx'); ..."
```

## 定时任务

- 交易日 16:30 自动执行 `update_portfolio.py`（cron: `30 16 * * 1-5`）
- 7 天自动过期，需续期：用户说「续期定时任务」时用 CronCreate 重建

## 交易录入约定

用户提供交易信息（日期/品种/方向/数量/单价）时：
1. 在「流水」sheet 末尾追加记录
2. 在「汇总」sheet 更新对应品种的成本价（加权平均）、数量、成本
3. 刷新权重和偏离数据

详见 memory: [[trade-update-workflow]]

## 数据来源

- 行情数据：Tushare `fund_daily` API，环境变量 `TUSHARE_TOKEN`
- 9 个持仓代码：510300/588050/159915/512890/511380/511260/511010/511360/518880
- 中国节假日列表硬编码在 `update_portfolio.py` 的 `CN_HOLIDAYS_2026` 中，需每年更新

## 信号检测规则

`update_portfolio.py` 内置以下检测逻辑（与投资纪要一致）：
- 止盈三档：+7%/+9%/+12%
- 高波动硬止损（科创50/创业板）：-5%预警/-10%卖半/-15%清仓
- 统一止损（其他权益）：-15%
- 再平衡：±3%预警/±7%触发/±10%紧急
- 熔断四级：-5%/-10%/-12%/-15% 组合回撤
