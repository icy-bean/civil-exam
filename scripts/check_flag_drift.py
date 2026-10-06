# -*- coding: utf-8 -*-
"""年度 flag 分布漂移检查（哨兵）：机关政策不会突变，flag 数量骤降/归零 ≈ 正则失效信号。

在 clean_positions.py 之后、build_manifest.py 之前跑：
    python scripts/check_flag_drift.py [--json]

检查项:
- 各年 extra_flags 逐类计数对比（最新年 vs 上一年）：归零、腰斩(>50%降幅且基数>=100) → ALERT
- 「备注含限定表述但无任何 flag/年龄解析覆盖」的岗位数：翻三倍且 >50 → WARN（新句式漏检信号）
退出码: 有 ALERT 时 1（人工确认是政策变化还是规则失效），否则 0。
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
from engine import RESTRICTION_RX, UNLIMITED_PHRASES_RX  # noqa: E402  与引擎共用同一套哨兵正则


def year_stats(csv_path: Path) -> dict:
    df = pd.read_csv(csv_path, dtype={"dept_code": str, "position_code": str})
    stats = {"rows": int(len(df)), "flags": {}}
    for flag in ["english4", "english6", "male_only", "female_only", "male_fit", "female_fit",
                 "base_project", "household", "fresh_only", "cert", "physical"]:
        stats["flags"][flag] = int(df["extra_flags"].fillna("").str.contains(flag).sum())
    remarks = df["remarks"].fillna("").str.replace(UNLIMITED_PHRASES_RX, "", regex=True)
    covered = df["extra_flags"].fillna("").ne("") | df["age_min"].notna() | df["age_max"].notna()
    hit = remarks.str.contains(RESTRICTION_RX)
    stats["unflagged_restricted"] = int((hit & ~covered).sum())
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    files = sorted((ROOT / "clean" / "national").glob("*_position.csv"))
    if len(files) < 2:
        print("[skip] 清洗件不足两年，无法做漂移对比")
        sys.exit(0)

    all_stats = {int(f.name[:4]): year_stats(f) for f in files}
    years = sorted(all_stats)
    prev, last = years[-2], years[-1]
    alerts, warns = [], []
    for flag, n_last in all_stats[last]["flags"].items():
        n_prev = all_stats[prev]["flags"][flag]
        if n_prev >= 10 and n_last == 0:
            alerts.append(f"{flag}: {prev}年{n_prev} 条 → {last}年 0 条（归零）")
        elif n_prev >= 100 and n_last < n_prev * 0.5:
            alerts.append(f"{flag}: {prev}年{n_prev} 条 → {last}年{n_last} 条（腰斩）")
    u_prev, u_last = all_stats[prev]["unflagged_restricted"], all_stats[last]["unflagged_restricted"]
    if u_last > 50 and u_last > u_prev * 3:
        warns.append(f"未识别限定表述岗位 {prev}年{u_prev} → {last}年{u_last}（翻三倍），可能备注出现新句式，规则需更新")

    if args.json:
        print(json.dumps({"years": years, "stats": {str(y): all_stats[y] for y in years},
                          "alerts": alerts, "warnings": warns}, ensure_ascii=False, indent=2))
    else:
        flags = list(next(iter(all_stats.values()))["flags"])
        header = "flag".ljust(14) + "".join(str(y).ljust(10) for y in years)
        print(header)
        for flag in flags:
            print(flag.ljust(14) + "".join(str(all_stats[y]["flags"][flag]).ljust(10) for y in years))
        print("unflagged限定".ljust(13) + "".join(str(all_stats[y]["unflagged_restricted"]).ljust(10) for y in years))
        for a in alerts:
            print("  [ALERT]", a)
        for w in warns:
            print("  [WARN]", w)
        if not alerts and not warns:
            print(f"  [ok] {prev}→{last} 无漂移警报")
    sys.exit(1 if alerts else 0)


if __name__ == "__main__":
    main()
