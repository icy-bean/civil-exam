# -*- coding: utf-8 -*-
"""生成 MANIFEST.json: 仓库所有数据件的 SHA256 + 大小 + 行数 + 更新时间。

skill 运行流程: 拉 manifest -> 比对本地位 -> 增量下载 -> SHA256 校验 -> 解析。
约定: 当年职位表缺失时 manifest 的 latest_position_year 仍是旧年, skill 必须硬失败而非拿旧表当当年用。
"""
import csv
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_EXT = {".xlsx", ".xls", ".csv", ".json", ".html", ".pdf", ".txt"}
SKIP_TOP = {"scripts", "skill", "README.md", "LICENSE", ".git", "MANIFEST.json"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_rows(p: Path) -> int | None:
    """按 CSV 记录数统计（带引号的多行备注不算新记录）。"""
    if p.suffix != ".csv":
        return None
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return max(sum(1 for _ in csv.reader(f)) - 1, 0)


def main():
    items = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in INCLUDE_EXT:
            continue
        rel = p.relative_to(ROOT)
        if rel.parts[0] in SKIP_TOP:
            continue
        items.append({
            "path": rel.as_posix(),
            "bytes": p.stat().st_size,
            "sha256": sha256(p),
            "rows": count_rows(p),
        })
    years = []
    for it in items:
        parts = it["path"].split("/")
        if len(parts) >= 3 and parts[0] == "clean" and parts[1] == "national" and parts[2].endswith("_position.csv"):
            years.append(int(parts[2][:4]))
    manifest = {
        "updated_at": date.today().isoformat(),
        "latest_position_year": max(years) if years else None,
        "items": items,
    }
    dest = ROOT / "MANIFEST.json"
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] MANIFEST.json: {len(items)} files, latest_position_year={manifest['latest_position_year']}")


if __name__ == "__main__":
    main()
