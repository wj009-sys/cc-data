#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""更新网页仪表盘的纪律规则为V3版本"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

with open('portfolio-dashboard.html', 'r', encoding='utf-8') as f:
    content = f.read()

old = '一、统一阶梯止盈'
new = '一、单档止盈（+20%卖半）'

if old in content:
    # Replace section by section
    # 1. 止盈 title + body
    content = content.replace(
        "title:'一、统一阶梯止盈'",
        "title:'一、单档止盈（+20%卖半）'"
    )
    content = content.replace(
        "title:'二、高波动品种硬止损'",
        "title:'二、统一止损（-15%清仓，观察20日）'"
    )
    content = content.replace(
        "title:'三、权益品种统一止损'",
        "title:'--已合并到二--'  // 占位"
    )
    content = content.replace(
        "title:'四、组合再平衡（半年度）'",
        "title:'三、组合再平衡（年度 ±20% 阀值）'"
    )
    content = content.replace(
        "title:'五、熔断与黑天鹅应对'",
        "title:'四、熔断与黑天鹅应对'"
    )
    content = content.replace(
        "title:'六、黄金ETF建仓计划'",
        "title:'五、黄金ETF长期配置（不触发止盈止损）'"
    )
    content = content.replace(
        "title:'七、盈利提取规则'",
        "title:'六、盈利提取规则'"
    )
    content = content.replace(
        "title:'八、流动性三原则'",
        "title:'七、流动性三原则'"
    )

    # 2. 止盈 body
    old_body1 = "'<div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">第一档 +7%</span>：成本价×1.07，减仓<span class=\"highlight\">20%</span>，每档仅触发一次</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">第二档 +9%</span>：成本价×1.09，减仓<span class=\"highlight\">30%</span>，累计已减50%</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">第三档 +12%</span>：成本价×1.12，清仓<span class=\"highlight\">100%</span>，本轮交易结束</span></div><div class=\"rule-row\" style=\"margin-top:4px;font-size:11px;color:var(--text-muted)\">适用：510300/588050/159915/512890/511380/518880<br>止盈资金2个交易日内再配置</div>'"
    new_body1 = "'<div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">触发条件</span>：浮盈达到 <span class=\"highlight\">+20%</span>（成本价×1.20）</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">执行动作</span>：卖出持仓的 <span class=\"highlight\">50%</span>，仅触发一次（重建后重置）</span></div><div class=\"rule-row\"><span class=\"rule-dot blue-dot\"></span><span><span class=\"highlight\">资金处理</span>：卖出资金买入511360短融ETF，不临时再配置</span></div><div class=\"rule-row\" style=\"margin-top:4px;font-size:11px;color:var(--text-muted)\">适用：510300/588050/159915/512890/511380（不含黄金/债底）<br>等待年度再平衡统一调配</div>'"
    content = content.replace(old_body1, new_body1)

    # 3. 高波动止损 body → 统一止损
    old_body2 = "'<div class=\"rule-row\"><span class=\"rule-dot amber-dot\"></span><span><span class=\"highlight\">预警线 ≥-5%</span>：记录台账，暂停新增买入，保持仓位观察</span></div><div class=\"rule-row\"><span class=\"rule-dot red-dot\"></span><span><span class=\"highlight\">止损A ≥-10%</span>：次日开盘市价卖出50%，剩余~12.5万</span></div><div class=\"rule-row\"><span class=\"rule-dot red-dot\"></span><span><span class=\"highlight\">止损B ≥-15%</span>：次日开盘市价卖出100%，彻底清仓观察20日</span></div><div class=\"rule-row\" style=\"margin-top:4px;font-size:11px;color:var(--text-muted)\">动态止损：价格跌破20日均线且累计跌幅超8%→预警<br>适用：科创50/创业板专用</div>'"
    new_body2 = "'<div class=\"rule-row\"><span class=\"rule-dot red-dot\"></span><span>统一止损线：浮亏 <span class=\"highlight\">-15%</span>（较成本价）</span></div><div class=\"rule-row\"><span class=\"rule-dot red-dot\"></span><span>执行动作：当日/次日开盘市价卖出<span class=\"highlight\">全部持仓</span></span></div><div class=\"rule-row\"><span class=\"rule-dot blue-dot\"></span><span>重建规则：清仓后观察<span class=\"highlight\">20个交易日</span>方可重建（约1个月）</span></div><div class=\"rule-row\" style=\"margin-top:4px;font-size:11px;color:var(--text-muted)\">适用：510300/588050/159915/512890/511380（不含黄金/债底）<br>取消原高波动专用规则，统一执行</div>'"
    content = content.replace(old_body2, new_body2)

    # 4. 去掉旧统一止损body（第三项）
    old_body3 = "'<div class=\"rule-row\"><span class=\"rule-dot red-dot\"></span><span>统一止损线：<span class=\"highlight\">-15%</span></span></div><div class=\"rule-row\"><span class=\"rule-dot blue-dot\"></span><span>适用：510300/512890/518880/511380</span></div><div class=\"rule-row\"><span class=\"rule-dot blue-dot\"></span><span>清仓后观察<span class=\"highlight\">40个交易日</span>，站稳20日均线方可重建</span></div>'"
    content = content.replace(old_body3, "'--已合并到第二项--'")

    # 5. 再平衡 body
    old_body4 = "'<div class=\"rule-row\"><span class=\"rule-dot green-dot\"></span><span><span class=\"highlight\">半年度检查</span>：每年<span class=\"highlight\">6月末、12月末</span>最后一个交易日执行</span></div><div class=\"rule-row\"><span class=\"rule-dot amber-dot\"></span><span><span class=\"highlight\">触发阀值 ±20%</span>：任一品种实际权重偏离目标权重超过<span class=\"highlight\">±20%（相对偏离）</span>，全仓调回目标权重</span></div><div class=\"rule-row\" style=\"margin-top:4px;font-size:11px;color:var(--text-muted)\">例如：目标10%的品种，实际<8%或>12%时触发<br>卖出排序：黄金>科创/创业>可转债>沪深300>红利低波</div>'"
    new_body4 = "'<div class=\"rule-row\"><span class=\"rule-dot green-dot\"></span><span><span class=\"highlight\">年度检查</span>：每年<span class=\"highlight\">12月末</span>最后一个交易日执行</span></div><div class=\"rule-row\"><span class=\"rule-dot amber-dot\"></span><span><span class=\"highlight\">触发阀值 ±20%</span>：任一品种相对偏离超过±20%，全仓调回目标权重</span></div><div class=\"rule-row\" style=\"margin-top:4px;font-size:11px;color:var(--text-muted)\">偏离计算：(实际权重-目标权重)/目标权重<br>例：目标10%的品种，实际<8%或>12%时触发<br>卖出排序：黄金>科创/创业>可转债>沪深300>红利低波</div>'"
    content = content.replace(old_body4, new_body4)

    # 6. 黄金 body - 加长期配置说明
    old_body5 = "'<div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">底仓先行</span>：立即执行，25万 → 仓位33%</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">定投阶段</span>：每月固定4.5万（第2-12月）→ 累计25-70万</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">大跌加码</span>：单月跌幅超5%加5万/次，累计不超过75万</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">收官</span>：12个月完成或累计达75万，补齐剩余 → 100%</span></div>'"
    new_body5 = "'<div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span>黄金ETF（518880）为<span class=\"highlight\">长期配置</span>，<span class=\"highlight\">不适用</span>止盈/止损规则</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">底仓先行</span>：立即执行，25万 → 仓位33%</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">定投阶段</span>：每月固定4.5万（第2-12月）→ 累计25-70万</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">大跌加码</span>：单月跌幅超5%加5万/次，累计不超过75万</span></div><div class=\"rule-row\"><span class=\"rule-dot gold-dot\"></span><span><span class=\"highlight\">收官</span>：12个月完成或累计达75万，补齐剩余 → 100%</span></div>'"
    content = content.replace(old_body5, new_body5)

    with open('portfolio-dashboard.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print('OK: 网页已更新')
else:
    print('未找到目标文本')
