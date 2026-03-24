"""
astock.calibration
概率标定 - 更新 + 报告 + 系数建议
"""
from config import CALIBRATION_BUCKETS
import sqlite3
from db import get_db


def prob_bucket(p):
    for i in range(len(CALIBRATION_BUCKETS) - 1):
        if CALIBRATION_BUCKETS[i] <= p < CALIBRATION_BUCKETS[i + 1]:
            return CALIBRATION_BUCKETS[i]
    return CALIBRATION_BUCKETS[-1]


def conf_label(n):
    if n >= 30:
        return '高'
    elif n >= 15:
        return '中'
    elif n >= 8:
        return '低'
    return '⚠️极低'


def update_calibration(conn):
    """
    从头重建标定数据：删除所有桶，从历史样本重新累计。
    每次outcome更新后调用，保证数据一致性。
    """
    conn.execute("DELETE FROM calibration")

    rows = conn.execute(
        "SELECT jb_prob, outcome FROM predictions WHERE outcome IS NOT NULL"
    ).fetchall()

    if not rows:
        return

    buckets_data = {b: {'n': 0, 'pred_sum': 0.0, 'actual_sum': 0} for b in CALIBRATION_BUCKETS}

    for row in rows:
        b = prob_bucket(row['jb_prob'])
        buckets_data[b]['n'] += 1
        buckets_data[b]['pred_sum'] += row['jb_prob']
        if row['outcome'] == '晋级':
            buckets_data[b]['actual_sum'] += 1

    for b, d in buckets_data.items():
        if d['n'] > 0:
            conn.execute("""
                INSERT INTO calibration
                (bucket, total_sample_count, total_predicted_prob_sum, total_actual_jb_sum)
                VALUES (?,?,?,?)
            """, (b, d['n'], d['pred_sum'], d['actual_sum']))

    conn.commit()


def get_calibration_report(conn):
    """返回概率标定报告（用于display）"""
    rows = conn.execute(
        "SELECT bucket, total_sample_count, total_predicted_prob_sum, total_actual_jb_sum "
        "FROM calibration ORDER BY bucket"
    ).fetchall()

    if not rows or sum(r['total_sample_count'] for r in rows) < 5:
        return None

    lines = []
    total_n = total_jb = 0

    for r in rows:
        n = r['total_sample_count']
        avg = r['total_predicted_prob_sum'] / n if n > 0 else 0
        actual = r['total_actual_jb_sum'] / n if n > 0 else 0
        bias = actual * 100 - avg
        bias_str = f"+{bias:.1f}%" if bias >= 0 else f"{bias:.1f}%"
        conf = conf_label(n)
        lines.append(
            f"  {r['bucket']}%~{r['bucket'] + 10}%  "
            f"{'⚠️' if conf == '⚠️极低' else '  '}"
            f"n={n:<6}{conf}     "
            f"预测{avg:.1f}%  实际{actual * 100:.1f}%  偏差{bias_str}"
        )
        total_n += n
        total_jb += r['total_actual_jb_sum']

    overall = total_jb / total_n * 100 if total_n > 0 else 0
    lines.append(f"\n  总样本:{total_n}只  整体晋级率:{overall:.1f}%")

    if total_n < 30:
        lines.append("  ⚠️ 样本不足30只，概率标定结果仅供参考")

    return '\n'.join(lines)


def suggest_coef_adjustments(conn, min_samples=8):
    """
    基于历史数据建议系数调整方向。
    返回 [(coef_name, old_val, new_val, reason)] 列表。
    """
    coef = {}
    rows = conn.execute(
        "SELECT trend, vr, rsi, lb, outcome FROM predictions WHERE outcome IS NOT NULL"
    ).fetchall()

    if len(rows) < min_samples:
        return []

    # 按条件分组统计实际晋级率
    conditions = {}
    for row in rows:
        key = (
            row['trend'] or '未知',
            f"vr_{'高' if row['vr'] and row['vr'] < 0.8 else '中低'}",
            f"rsi_{'超卖' if row['rsi'] and row['rsi'] < 40 else ('超买' if row['rsi'] and row['rsi'] > 70 else '正常')}",
            f"{row['lb']}板",
        )
        if key not in conditions:
            conditions[key] = {'total': 0, 'jb': 0}
        conditions[key]['total'] += 1
        if row['outcome'] == '晋级':
            conditions[key]['jb'] += 1

    suggestions = []
    for (trend, vr_k, rsi_k, lb_k), v in conditions.items():
        if v['total'] < 3:
            continue
        actual_rate = v['jb'] / v['total']
        # 如果某条件实际晋级率显著高于/低于预期，给出建议
        # 这部分可以扩展

    return suggestions
