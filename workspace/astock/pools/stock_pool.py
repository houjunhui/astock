"""
分层股票池体系 v1
- 核心基础池：昨日涨停 + 昨日炸板 + 板块龙头
- 预选候选池：基础池 + 剔除黑天鹅标的
- 当日开仓池：预选池 + 竞价因子评分排序

数据源: quicktiny API
更新频率: 每日收盘后自动更新
"""

import os
import json
from datetime import datetime, date
from pathlib import Path

POOL_DIR = Path("/home/gem/workspace/agent/workspace/astock/pools")
POOL_DIR.mkdir(parents=True, exist_ok=True)
BLACKLIST_FILE = POOL_DIR / "blacklist.json"
POOL_FILE = POOL_DIR / "pool_{date}.json"


def get_blacklist():
    """读取终身黑名单"""
    if BLACKLIST_FILE.exists():
        with open(BLACKLIST_FILE) as f:
            return set(json.load(f).get("codes", []))
    return set()


def add_to_blacklist(code, reason=""):
    """将标的加入终身黑名单"""
    data = {}
    if BLACKLIST_FILE.exists():
        with open(BLACKLIST_FILE) as f:
            data = json.load(f)
    if code not in data.get("codes", []):
        data.setdefault("codes", []).append(code)
        data.setdefault("reasons", {})[code] = reason
        data.setdefault("added_dates", {})[code] = date.today().strftime("%Y%m%d")
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False)


def load_pool(date_str):
    """加载指定日期的股票池"""
    pool_file = POOL_DIR / f"pool_{date_str}.json"
    if not pool_file.exists():
        return None
    with open(pool_file) as f:
        return json.load(f)


def build_core_pool(date_str):
    """
    构建核心基础池（每日收盘后更新）
    纳入：昨日涨停股 + 昨日炸板股
    剔除：终身黑名单标的
    """
    from astock.quicktiny import get_ladder, get_broken_limit_up
    
    blacklist = get_blacklist()
    today = date_str  # YYYYMMDD
    
    # 昨日涨停池
    ladder = get_ladder(today)
    limit_up_codes = {item["code"] for item in ladder if item.get("limit_up_type")}
    
    # 昨日炸板池
    broken = get_broken_limit_up(today)
    broken_codes = {item["code"] for item in broken}
    
    # 合并去重
    core_codes = (limit_up_codes | broken_codes) - blacklist
    
    core_pool = []
    for item in ladder:
        if item["code"] in core_codes:
            core_pool.append({
                "code": item["code"],
                "name": item.get("name", ""),
                "lb": item.get("lb", item.get("continue_num", 1)),
                "limit_up_type": item.get("limit_up_type", ""),
                "limit_up_suc_rate": item.get("limit_up_suc_rate"),
                "auction_amount": item.get("auction_amount"),
                "source": "涨停",
                "date": date_str,
            })
    for item in broken:
        if item["code"] in core_codes and item["code"] not in [c["code"] for c in core_pool]:
            core_pool.append({
                "code": item["code"],
                "name": item.get("name", ""),
                "lb": item.get("lb", 1),
                "limit_up_type": "炸板",
                "limit_up_suc_rate": item.get("limit_up_suc_rate"),
                "auction_amount": item.get("auction_amount"),
                "source": "炸板",
                "date": date_str,
            })
    
    return {
        "date": date_str,
        "pool_type": "core",
        "count": len(core_pool),
        "codes": [c["code"] for c in core_pool],
        "stocks": core_pool,
    }


def build_preferred_pool(core_pool, date_str):
    """
    构建预选候选池
    剔除: ST/*ST, 退市预警, 成交额<1亿, 限售解禁, 业绩暴雷
    """
    # 简化版：先全部纳入，后续扩展黑天鹅过滤
    preferred = []
    blacklist = get_blacklist()
    
    for stock in core_pool.get("stocks", []):
        code = stock["code"]
        name = stock.get("name", "")
        
        # 终身黑名单
        if code in blacklist:
            stock["exclude_reason"] = "终身黑名单"
            continue
        
        # ST/*ST
        if name.startswith("ST") or name.startswith("*ST"):
            stock["exclude_reason"] = "ST/*ST"
            continue
        
        # 无成交数据（auction_amount=0）
        if not stock.get("auction_amount"):
            stock["exclude_reason"] = "无竞价数据"
            continue
        
        preferred.append(stock)
    
    return {
        "date": date_str,
        "pool_type": "preferred",
        "count": len(preferred),
        "codes": [s["code"] for s in preferred],
        "stocks": preferred,
        "core_count": core_pool.get("count", 0),
        "excluded": core_pool.get("count", 0) - len(preferred),
    }


def score_stocks(preferred_pool, date_str):
    """
    三级因子评分体系
    
    核心必选因子（50%权重，一票否决）:
      F1 板块竞价强度: 个股所属板块竞价涨幅全市场排名
      F2 龙头辨识度: 市场总龙头/板块龙头/跟风 量化评分
      F3 竞价量能健康度: 竞价金额/流通市值≥0.8%
      F4 日线技术健康度: RSI<70, MACD无顶背离
    
    弹性加分因子（40%权重）:
      F5 竞价偏离度: 相对板块均值的偏离
      F6 动态量比VR: 按连板梯队的动态阈值
      F7 历史封板率: 近3个月涨停后次日晋级率
      F8 机器学习晋级概率: 多周期融合模型
    
    风险剔除因子（10%权重，一票否决）:
      R1 一字板高开断板风险
      R2 近3个月异常波动
      R3 竞价承接不足
      R4 板块退潮期
    """
    from astock.quicktiny import get_ladder, get_market_phase
    
    phase, _ = get_market_phase(date_str)
    
    scored = []
    for stock in preferred_pool.get("stocks", []):
        code = stock["code"]
        lb = stock.get("lb", 1)
        
        score_detail = {}
        
        # ── R1: 一字板高开断板 ──
        if stock.get("limit_up_type") == "一字板" and lb == 1:
            stock["action"] = "exclude"
            stock["exclude_reason"] = "R1: 首板一字板高开"
            continue
        
        # ── R4: 退潮/冰点期高标配 ──
        if lb >= 6 and phase in ("退潮", "冰点"):
            stock["action"] = "exclude"
            stock["exclude_reason"] = "R4: 退潮/冰点期高标配禁止"
            continue
        
        # ── F1: 板块竞价强度（简化：个股竞价涨幅 vs 市场均值）──
        chg = stock.get("auction_chg", 0)
        sector_chg = stock.get("sector_chg_avg", chg)  # 有板块数据时用
        sector_strength = chg - sector_chg  # 相对偏离
        score_detail["F1_板块竞价强度"] = round(sector_strength, 2)
        
        # ── F2: 龙头辨识度 ──
        # 按连板数和板块地位评分
        if lb >= 6:
            dragon_score = 10  # 市场总龙头
        elif lb >= 3:
            dragon_score = 7   # 板块龙头
        elif lb >= 2:
            dragon_score = 4  # 跟风强势
        else:
            dragon_score = 2  # 普通
        score_detail["F2_龙头辨识度"] = dragon_score
        
        # ── F3: 竞价量能健康度 ──
        amount = stock.get("auction_amount", 0)
        # 用昨日收盘价估算流通市值（简化）
        last_close = stock.get("last_close", 10.0)
        est_market_cap = last_close * stock.get("float_share", 1e8)  # 简化估算
        if est_market_cap > 0:
            amount_ratio = amount / est_market_cap * 100
        else:
            amount_ratio = 0
        score_detail["F3_竞价量能"] = round(amount_ratio, 3)
        if amount_ratio < 0.5:  # <0.5% 危险
            stock["action"] = "exclude"
            stock["exclude_reason"] = "F3: 竞价量能不足"
            continue
        
        # ── F6: 动态量比VR（已有ladder数据）──
        vr = stock.get("vr", 1.0)
        # 按板位动态阈值
        if lb >= 4:
            vr_threshold = 2.4  # 4板+ vr>=2.4
        elif lb >= 3:
            vr_threshold = 3.0
        elif lb >= 2:
            vr_threshold = 3.6
        else:
            vr_threshold = 3.0
        vr_score = min(vr / vr_threshold, 2.0) * 5  # 最高10分
        score_detail["F6_VR量比"] = round(vr_score, 1)
        
        # ── F7: 历史封板率 ──
        seal_rate = stock.get("limit_up_suc_rate", 0.5)
        seal_score = seal_rate * 10  # 0.9封板率→9分
        score_detail["F7_历史封板率"] = round(seal_score, 1)
        
        # ── 综合评分（加权）──
        # 核心因子权重50%: F1×0.15 + F2×0.15 + F3×0.10 + F4×0.10
        # 弹性因子权重40%: F5×0.10 + F6×0.10 + F7×0.10 + F8×0.10
        # 简化版（无F8 ML模型时）:
        core_score = (score_detail["F1_板块竞价强度"] / 10 * 15 + 
                      score_detail["F2_龙头辨识度"] + 
                      score_detail["F3_竞价量能"] * 10 + 5)  # F4暂用常数
        elastic_score = (score_detail["F6_VR量比"] + score_detail["F7_历史封板率"]) * 2
        
        total_score = core_score * 0.6 + elastic_score * 0.4
        
        # ── 评级映射 ──
        if lb >= 3 and total_score >= 18:
            tier = "S"
        elif total_score >= 14:
            tier = "A"
        elif total_score >= 10:
            tier = "B"
        else:
            tier = "C"
        
        stock["score_detail"] = score_detail
        stock["core_score"] = round(core_score, 1)
        stock["elastic_score"] = round(elastic_score, 1)
        stock["total_score"] = round(total_score, 1)
        stock["tier"] = tier
        stock["action"] = "consider"
        
        scored.append(stock)
    
    # 按评分排序，最多取前5只
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    
    return {
        "date": date_str,
        "pool_type": "opening",
        "count": len(scored),
        "stocks": scored[:5],
        "all_scored": scored,
    }


def daily_pool_update(date_str):
    """
    每日收盘后执行完整股票池更新流程
    1. 构建核心基础池
    2. 构建预选候选池
    3. 因子评分生成当日开仓池
    4. 保存到文件
    """
    print(f"📊 开始构建 {date_str} 股票池...")
    
    # 1. 核心基础池
    core = build_core_pool(date_str)
    print(f"  核心基础池: {core['count']}只")
    
    # 2. 预选候选池
    preferred = build_preferred_pool(core, date_str)
    print(f"  预选候选池: {preferred['count']}只 (排除{preferred['excluded']}只)")
    
    # 3. 因子评分
    opening = score_stocks(preferred, date_str)
    print(f"  当日开仓池: {opening['count']}只")
    for s in opening.get("stocks", []):
        print(f"    {s['code']} {s.get('name')} 评分{s['total_score']} tier={s['tier']}")
    
    # 4. 保存
    pool_file = POOL_DIR / f"pool_{date_str}.json"
    with open(pool_file, "w") as f:
        json.dump({
            "date": date_str,
            "core": core,
            "preferred": preferred,
            "opening": opening,
        }, f, ensure_ascii=False, indent=2)
    
    print(f"  ✅ 已保存至 {pool_file}")
    return opening


if __name__ == "__main__":
    import sys
    date_str = sys.argv[1] if len(sys.argv) > 1 else date.today().strftime("%Y%m%d")
    result = daily_pool_update(date_str)
    print(f"\n当日可开仓标的: {result['count']}只")
    for s in result.get("stocks", []):
        print(f"  {s['code']} {s.get('name')} tier={s['tier']} score={s['total_score']}")
