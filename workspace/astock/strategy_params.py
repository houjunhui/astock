#!/usr/bin/env python3
"""
策略参数管理器 - 核心底座
管理策略参数版本，支持自动回滚

参数文件: astock/position/strategy_params.json
结构:
{
  "current_version": "v1",
  "versions": {
    "v1": {
      "created": "20260324",
      "author": "auto",
      "params": { ... },
      "stats": { "win_rate": 0.55, "profit_loss_ratio": 1.5, ... }
    }
  }
}
"""

import json, os
from datetime import datetime

PARAMS_FILE = os.path.join(os.path.dirname(__file__), "position", "strategy_params.json")

DEFAULT_PARAMS = {
    # 仓位
    "position_S": 0.30,   # S/3板+
    "position_A": 0.20,   # A/2板
    "position_B": 0.15,   # B/1板
    "max_total_main_sheng": 0.70,
    "max_total_recession": 0.20,
    "max_positions_per_day": 3,

    # 止盈
    "target_3board_plus": 1.12,
    "target_2board": 1.09,
    "target_1board": 1.07,

    # 止损
    "stop_loss_default": 0.04,  # -4%
    "breakeven_threshold": 0.05,   # 浮盈≥5%保本
    "lock_profit_threshold": 0.10, # 浮盈≥10%锁5%利润

    # 风控
    "trailing_profit_pct": 0.40,   # 高点回落40%
    "trailing_profit_min": 0.06,    # 浮盈≥6%才启动
    "reduce_threshold": 0.02,       # 浮亏≥2%降仓
    "close_threshold": 0.05,         # 浮亏≥5%清仓

    # 炸板
    "broken_limit_dragon": 0.06,  # 龙头炸板回落≥6%
    "broken_limit_normal": 0.04,   # 非龙头炸板回落≥4%

    # 竞价过滤
    "auction_amount_min": 50_000_000,  # 竞价金额≥5000万
    "vol_ratio_min": 3.0,
    "turnover_min": 1.0,
    "auction_chg_max": 0.05,  # 竞价涨幅>5%不买

    # S级标准
    "s_level_min_boards": 4,
    "s_level_min_seal_rate": 0.85,
    "s_level_min_turnover": 2.0,
    "s_level_min_vr": 5.0,

    # 亏损保护
    "consecutive_loss_stop": 2,   # 连亏2笔当日停
    "week_drawdown_stop": 50_000, # 单周回撤≥5万降仓

    # 盈利保护
    "profit_protect_10": 0.50,   # 月盈≥10%仓位降至50%
    "profit_protect_20": 0.30,   # 月盈≥20%仓位降至30%
}


def _load():
    if os.path.exists(PARAMS_FILE):
        with open(PARAMS_FILE) as f:
            return json.load(f)
    return {
        "current_version": "v1",
        "versions": {
            "v1": {
                "created": datetime.now().strftime("%Y%m%d"),
                "author": "default",
                "params": DEFAULT_PARAMS.copy(),
                "stats": {},
                "rollback_count": 0,
            }
        },
        "test_version": None,
        "test_position_pct": 0.10,
        "rollback_log": [],
    }


def _save(data):
    with open(PARAMS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_params(version=None):
    """获取指定版本的参数（默认当前版本）"""
    data = _load()
    v = version or data["current_version"]
    return data["versions"][v]["params"].copy()


def get_current_version():
    return _load()["current_version"]


def get_all_versions():
    data = _load()
    return {
        v: {
            "created": d["created"],
            "author": d.get("author", "auto"),
            "stats": d.get("stats", {}),
            "rollback_count": d.get("rollback_count", 0),
        }
        for v, d in data["versions"].items()
    }


def save_new_version(params, author="auto", stats=None):
    """保存新版本参数"""
    data = _load()
    v = f"v{len(data['versions']) + 1}"
    data["versions"][v] = {
        "created": datetime.now().strftime("%Y%m%d"),
        "author": author,
        "params": params.copy(),
        "stats": stats or {},
        "rollback_count": 0,
    }
    data["current_version"] = v
    _save(data)
    return v


def rollback_to_previous():
    """回滚到上一版本（连续3天亏损触发）"""
    data = _load()
    current = data["current_version"]
    versions = list(data["versions"].keys())
    if len(versions) < 2:
        return False, "无可回滚版本"

    # 找到当前版本的前一个
    idx = versions.index(current)
    if idx == 0:
        return False, "已是第一个版本"

    prev_version = versions[idx - 1]
    data["versions"][current]["rollback_count"] += 1
    data["versions"][prev_version]["rollback_count"] += 1
    data["current_version"] = prev_version
    data["rollback_log"].append({
        "date": datetime.now().strftime("%Y%m%d"),
        "from": current,
        "to": prev_version,
        "reason": "连续3天收益为负",
    })
    # 清除测试版本
    data["test_version"] = None
    _save(data)
    return True, f"已回滚: {current} → {prev_version}"


def check_and_rollback(recent_pnl_list):
    """
    检查是否需要回滚
    recent_pnl_list: 最近N天的盈亏列表（正=盈利，负=亏损）
    连续3天亏损 → 自动回滚
    """
    if len(recent_pnl_list) < 3:
        return False, "天数不足"

    # 检查最后3天是否全亏
    if all(p < 0 for p in recent_pnl_list[-3:]):
        ok, msg = rollback_to_previous()
        if ok:
            return True, f"连续3天亏损，{msg}"
        return False, msg
    return False, ""


def start_test_version(params, stats=None):
    """用新参数开启测试（10%仓位）"""
    data = _load()
    v = f"test_{datetime.now().strftime('%m%d%H%M%S')}"
    data["versions"][v] = {
        "created": datetime.now().strftime("%Y%m%d"),
        "author": "auto",
        "params": params.copy(),
        "stats": stats or {},
        "rollback_count": 0,
        "is_test": True,
    }
    data["test_version"] = v
    _save(data)
    return v


def promote_test_version():
    """测试版本验证通过，并入主策略"""
    data = _load()
    tv = data.get("test_version")
    if not tv or tv not in data["versions"]:
        return False, "无测试版本"

    test_params = data["versions"][tv]["params"]
    new_v = save_new_version(test_params, author="auto_promote")
    data["test_version"] = None
    _save(data)
    return True, new_v


def update_version_stats(version, stats):
    """更新版本的统计信息"""
    data = _load()
    if version in data["versions"]:
        data["versions"][version]["stats"].update(stats)
        _save(data)


if __name__ == "__main__":
    import pprint
    data = _load()
    print("=== 当前版本:", data["current_version"])
    print("=== 所有版本:", list(data["versions"].keys()))
    print("=== 当前参数:")
    pprint.pprint(get_params())
