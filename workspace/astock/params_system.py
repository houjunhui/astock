"""
参数系统 v2: 参数分区 + 灰度发布 + 周迭代

核心设计:
- 核心不变参数: 固定不变，禁止自动回滚，仅人工可改
- 周期自适应参数: 按市场阶段自动切换，支持版本管理

灰度发布:
- v_new先用10%资金测试3个交易日
- 跑赢→升30%测试1周
- 跑输2天→自动下线回滚

周迭代(每周五收盘后):
- IC验证: 淘汰IC<0.03的因子
- 参数优化: 生成新版本
- 入测试池
"""

import os
import json
import copy
from datetime import datetime, date, timedelta
from pathlib import Path

WORKSPACE = Path("/home/gem/workspace/agent/workspace")
PARAMS_FILE = WORKSPACE / "astock" / "position" / "strategy_params.json"

INITIAL_CAPITAL = 1_000_000.0

# ── 核心不变参数（固定，禁止自动修改）────────────────────────────
CORE_FIXED_PARAMS = {
    # 成本
    "commission_buy": 0.0003,   # 佣金万3
    "commission_sell": 0.0003,  # 佣金万3
    "stamp_tax": 0.001,         # 印花税千1
    "slippage_buy": 0.0005,     # 买入滑点千5
    "slippage_sell": 0.0005,    # 卖出滑点千5
    # 风控硬成本
    "risk_per_stock": 0.01,     # 单票风险1%
    "max_loss_per_trade_pct": 0.04,  # 止损线4%
    # 熔断
    "circuit_index_drop": 3.0,     # 熔断: 指数跌超3%
    "circuit_limit_down": 30,       # 熔断: 跌停超30家
    "circuit_max_lb": 2,            # 熔断: 连板≤2板
    "circuit_yz_return": -3.0,     # 熔断: 昨日涨停<-3%
    # 黑名单
    "blacklist_st": True,          # ST/*ST黑名单
    "blacklist_continue_num": True, # 连续亏损标的
}

# ── 周期自适应参数 ──────────────────────────────────────────────
ADAPTIVE_PARAM_CATEGORIES = {
    "position": ["position_S","position_A","position_B","max_total_main_sheng","max_total_recession"],
    "stop_loss": ["stop_loss_default","breakeven_threshold","lock_profit_threshold"],
    "target": ["target_3board_plus","target_2board","target_1board"],
    "dynamic": ["trailing_profit_pct","trailing_profit_min","broken_limit_dragon","broken_limit_normal"],
    "filter": ["auction_amount_min","vol_ratio_min","turnover_min","auction_chg_max","s_level_min_boards","s_level_min_seal_rate","s_level_min_turnover","s_level_min_vr"],
    "kelly": ["kelly_win_rate_S","kelly_win_rate_A","kelly_win_rate_B","kelly_avg_win_pct","kelly_avg_loss_pct","kelly_max_fraction"],
    "protection": ["consecutive_loss_stop","week_drawdown_stop","profit_protect_10","profit_protect_20"],
    "emotion": ["emotion_weight_lb","emotion_weight_ratio","emotion_weight_broken","emotion_weight_yesterday","emotion_weight_sector","emotion_weight_rise","emotion_weight_fall","emotion_weight_index"],
}


def load_params():
    with open(PARAMS_FILE) as f:
        return json.load(f)


def save_params(data):
    with open(PARAMS_FILE, "w") as f:
        json.dump(data, ensure_ascii=False, fp=f, indent=2)


def get_active_params():
    """获取当前活跃参数（核心+自适应+灰度权重）"""
    data = load_params()
    ver = data["versions"][data["current_version"]]
    return {
        "core": CORE_FIXED_PARAMS,
        "adaptive": ver["params"],
        "test_version": data.get("test_version"),
        "test_position_pct": data.get("test_position_pct", 0.0),
    }


def switch_version(version_name, reason=""):
    """切换参数版本"""
    data = load_params()
    if version_name not in data["versions"]:
        return False, f"版本{version_name}不存在"
    
    old = data["current_version"]
    data["current_version"] = version_name
    data.setdefault("rollback_log", []).append({
        "date": date.today().strftime("%Y%m%d"),
        "from": old,
        "to": version_name,
        "reason": reason or "manual",
    })
    save_params(data)
    return True, f"切换{old}→{version_name}"


# ── 灰度发布系统 ────────────────────────────────────────────────
GRAY_TEST_FILE = WORKSPACE / "astock" / "position" / "gray_test.json"


def load_gray_state():
    if GRAY_TEST_FILE.exists():
        with open(GRAY_TEST_FILE) as f:
            return json.load(f)
    return {
        "phase": "none",          # none / p10 / p30 / released
        "test_version": None,
        "start_date": None,
        "pct": 0.0,
        "daily_results": [],      # [{"date":str, "new_pnl":float, "old_pnl":float}]
    }


def save_gray_state(state):
    with open(GRAY_TEST_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False)


def start_gray_test(new_version):
    """启动10%资金灰度测试"""
    state = load_gray_state()
    if state["phase"] not in ("none",):
        return False, f"灰度测试中({state['phase']})，不能启动新测试"
    
    data = load_params()
    if new_version not in data["versions"]:
        return False, f"版本{new_version}不存在"
    
    state = {
        "phase": "p10",
        "test_version": new_version,
        "start_date": date.today().strftime("%Y%m%d"),
        "pct": 0.10,
        "daily_results": [],
        "consecutive_lose": 0,
    }
    save_gray_state(state)
    
    # 临时切换test_version
    data["test_version"] = new_version
    data["test_position_pct"] = 0.10
    save_params(data)
    
    return True, f"灰度测试启动: {new_version} 10%资金"


def record_gray_result(date_str, new_equity_change, old_equity_change):
    """记录灰度测试日结果"""
    state = load_gray_state()
    if state["phase"] == "none":
        return
    
    state["daily_results"].append({
        "date": date_str,
        "new": new_equity_change,
        "old": old_equity_change,
        "diff": new_equity_change - old_equity_change,
    })
    
    # 判断是否跑赢
    last = state["daily_results"][-1]
    is_win = last["diff"] >= 0
    
    if not is_win:
        state["consecutive_lose"] = state.get("consecutive_lose", 0) + 1
    else:
        state["consecutive_lose"] = 0
    
    # 阶段转换
    days = len(state["daily_results"])
    
    if state["phase"] == "p10":
        if state["consecutive_lose"] >= 2:
            # 灰度失败，回滚
            rollback_to_production()
            return "rollback", f"灰度P10连续2天跑输，回滚"
        if days >= 3 and is_win:
            # 升级P30
            state["phase"] = "p30"
            state["pct"] = 0.30
            state["consecutive_lose"] = 0
            update_test_position(0.30)
            return "upgrade_p30", f"灰度P10→P30 30%资金"
    
    elif state["phase"] == "p30":
        if state["consecutive_lose"] >= 2:
            rollback_to_production()
            return "rollback", f"灰度P30连续2天跑输，回滚"
        if days >= 5 and is_win:
            # 全量上线
            switch_version(state["test_version"], "灰度测试全量上线")
            state["phase"] = "released"
            clear_test_config()
            return "full_release", f"全量上线{state['test_version']}"
    
    save_gray_state(state)
    return "continue", f"继续测试({state['phase']})"


def rollback_to_production():
    """回滚到生产版本"""
    data = load_params()
    prod = data["current_version"]
    data["test_version"] = None
    data["test_position_pct"] = 0.0
    save_params(data)
    
    state = load_gray_state()
    state["phase"] = "rollback"
    save_gray_state(state)
    
    return True, f"已回滚到{prod}"


def update_test_position(pct):
    data = load_params()
    data["test_position_pct"] = pct
    save_params(data)
    
    state = load_gray_state()
    state["pct"] = pct
    save_gray_state(state)


def clear_test_config():
    data = load_params()
    data["test_version"] = None
    data["test_position_pct"] = 0.0
    save_params(data)


# ── 周迭代框架 ──────────────────────────────────────────────────
IC_MIN = 0.03  # IC<0.03淘汰
WEEKLY_REVIEW_FILE = WORKSPACE / "astock" / "position" / "weekly_review.json"


def weekly_review():
    """
    每周五收盘后执行:
    1. IC验证
    2. 淘汰失效因子
    3. 生成新参数版本
    4. 进入灰度测试池
    """
    today = date.today().strftime("%Y%m%d")
    results = {
        "date": today,
        "factors": {},     # 因子IC值
        "淘汰": [],
        "新版本": None,
        "action": None,
    }
    
    # 1. 读取周度统计数据（从daily_pnl表计算）
    from astock.position.position_sqlite import get_daily_pnl
    pnl_data = get_daily_pnl(date_str=today[:4] + "-" + today[4:6])
    
    # 简化：计算各因子的胜率/盈亏比
    # 实际应计算IC（预测值与实际值的相关系数）
    # 这里用胜率作为IC的近似
    all_trades = []
    for day_pnl in pnl_data:
        trades = day_pnl.get("trades", [])
        for t in trades:
            all_trades.append(t)
    
    if len(all_trades) < 5:
        results["action"] = "skip"
        results["reason"] = f"样本不足({len(all_trades)}笔)"
        return results
    
    # 2. 因子IC评估（简化版）
    # 各评级胜率
    tier_stats = {}
    for t in all_trades:
        tier = t.get("tier", "C")
        pnl = t.get("pnl_pct", 0)
        tier_stats.setdefault(tier, []).append(pnl)
    
    ic_results = {}
    for tier, pnls in tier_stats.items():
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        ic_results[tier] = {"win_rate": win_rate, "avg_pnl": avg_pnl, "count": len(pnls)}
    
    results["factors"] = ic_results
    
    # 3. 淘汰IC<0.03的因子
    for tier, stats in ic_results.items():
        # 用胜率近似IC
        effective_ic = stats["win_rate"] - 0.5  # 相对50%的超额
        if effective_ic < IC_MIN and stats["count"] >= 3:
            results["淘汰"].append(f"{tier}级因子(IC={effective_ic:.3f}<{IC_MIN})")
    
    # 4. 生成新版本
    data = load_params()
    current_ver = data["current_version"]
    current_params = data["versions"][current_ver]["params"].copy()
    
    # 根据IC调整仓位参数（简化）
    if "S" in ic_results and ic_results["S"]["win_rate"] > 0.5:
        # S级胜率高，可提高仓位
        current_params["position_S"] = min(0.35, current_params.get("position_S", 0.30) + 0.05)
    if "A" in ic_results and ic_results["A"]["win_rate"] < 0.4:
        # A级胜率低，降低仓位
        current_params["position_A"] = max(0.15, current_params.get("position_A", 0.20) - 0.05)
    
    # 创建新版本
    import uuid
    new_ver_name = f"v{len(data['versions']) + 1}"
    data["versions"][new_ver_name] = {
        "created": today,
        "author": "weekly_review",
        "params": current_params,
        "stats": ic_results,
        "rollback_count": 0,
    }
    save_params(data)
    
    results["新版本"] = new_ver_name
    results["action"] = "new_version_created"
    
    return results


def format_gray_report():
    """灰度发布状态报告"""
    state = load_gray_state()
    data = load_params()
    
    lines = [
        f"【🔬 灰度发布状态】",
        f"{'='*36}",
    ]
    
    phase_labels = {
        "none": "🟢 无灰度测试",
        "p10": f"🟡 P10灰度({state['pct']:.0%})",
        "p30": f"🟡 P30灰度({state['pct']:.0%})",
        "released": f"🟢 已全量上线({state.get('test_version')})",
        "rollback": f"🔴 已回滚",
    }
    
    lines.append(f"阶段: {phase_labels.get(state['phase'], state['phase'])}")
    
    if state["test_version"]:
        lines.append(f"测试版本: {state['test_version']}")
    if state["start_date"]:
        lines.append(f"开始日期: {state['start_date']}")
    if state["daily_results"]:
        lines.append(f"测试天数: {len(state['daily_results'])}天")
        wins = sum(1 for r in state["daily_results"] if r["diff"] >= 0)
        lines.append(f"跑赢天数: {wins}/{len(state['daily_results'])}")
        for r in state["daily_results"][-3:]:
            e = "✅" if r["diff"] >= 0 else "❌"
            lines.append(f"  {e} {r['date']} 新:{r['new']:+.0f} 老:{r['old']:+.0f} 差:{r['diff']:+.0f}")
    
    if state.get("consecutive_lose", 0) > 0:
        lines.append(f"连续跑输: {state['consecutive_lose']}天")
    
    lines.append(f"{'─'*36}")
    lines.append(f"【参数版本】")
    lines.append(f"生产版本: {data['current_version']}")
    if data.get("test_version"):
        lines.append(f"测试版本: {data['test_version']} @ {data.get('test_position_pct',0):.0%}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_gray_report())
