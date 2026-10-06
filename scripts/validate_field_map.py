# -*- coding: utf-8 -*-
"""新表入库门禁：人工下载放进 raw/ 之后、跑清洗之前，先用它做 30 秒体检。

用法:
  python scripts/validate_field_map.py 2027              # 校验 raw/national/2027/position_all.xlsx
  python scripts/validate_field_map.py --file 某表.xlsx   # 校验任意文件（尚不入库时）
  python scripts/validate_field_map.py 2027 --json       # 机器可读输出（skill/CI 用）
退出码: 0=通过（可含警告）  1=有阻断性问题（先按报告修 field_map.json 再清洗）

校验项: sheet 名对照 / 表头行自动定位 / 列名 diff（field_map.json 为准）/ 行数量级对照上年 /
        关键列空值率 / 招考人数可数值化率。
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

CRITICAL_COLS = ["专业", "学历", "学位", "政治面貌", "招考人数", "工作地点", "备注"]


def find_header_row(df_raw, known: set):
    """在前 6 行里找表头行：与已知列名匹配数最多且 >=4 的那一行。"""
    best_row, best_n = None, 0
    for i in range(min(6, len(df_raw))):
        cells = {str(v).strip() for v in df_raw.iloc[i].tolist()}
        n = len(cells & known)
        if n > best_n:
            best_row, best_n = i, n
    return (best_row, best_n) if best_row is not None and best_n >= 4 else (None, best_n)


def validate(file: Path, fm: dict, manifest: dict | None):
    errors, warns, infos = [], [], []
    known = set(fm["columns"])
    if not file.exists():
        errors.append(f"文件不存在: {file}")
        return errors, warns, infos, {}

    xl = pd.ExcelFile(file)
    expect_sheets = list(fm["sheets_agency_class"])
    for s in expect_sheets:
        if s not in xl.sheet_names:
            errors.append(f"缺少工作表「{s}」（field_map 约定 4 表结构）")
    for s in xl.sheet_names:
        if s not in expect_sheets:
            warns.append(f"出现约定外工作表「{s}」：确认是新版结构还是杂表")

    per_sheet, total = {}, 0
    for s in xl.sheet_names:
        head = xl.parse(s, header=None, nrows=8)
        hdr, nmatch = find_header_row(head, known)
        if hdr is None:
            errors.append(f"[{s}] 前 6 行内未定位到表头（与已知列名匹配数 {nmatch} < 4）——表头结构大改，先更新 field_map.json")
            continue
        df = xl.parse(s, header=hdr)
        cols = {str(c).strip() for c in df.columns}
        cols.discard("nan")
        missing = sorted(known - cols)
        extra = sorted(c for c in cols - known if not c.startswith("Unnamed"))
        if missing:
            errors.append(f"[{s}] 文件缺少映射列 {missing} → 这些字段将清洗为空，先在 field_map.json 里处理")
        if extra:
            warns.append(f"[{s}] 出现映射外新列 {extra} → 需要纳入清洗时更新 field_map.json（换年只改映射不改代码）")
        if hdr != fm.get("header_row", 1):
            warns.append(f"[{s}] 表头行在第 {hdr} 行（field_map 记录的是第 {fm.get('header_row', 1)} 行）")
        per_sheet[s] = {"header_row": hdr, "rows": int(len(df))}
        total += len(df)

    if total:
        infos.append(f"总行数 {total}")
        floor = 10000
        if total < floor:
            warns.append(f"总行数 {total} 低于国考常规量级（近年约 1.9万-2.1万），确认下到的是完整总表而非分省视图")
        if manifest:
            prev = None
            for it in manifest.get("items", []):
                p = it["path"]
                if p.startswith("clean/national/") and p.endswith("_position.csv"):
                    y = int(p.split("/")[2][:4])
                    rows = it.get("rows")
                    if rows and (prev is None or y > prev[0]):
                        prev = (y, rows)
            if prev and abs(total - prev[1]) / prev[1] > 0.30:
                warns.append(f"与 {prev[0]} 年行数 {prev[1]} 相比变化 {total / prev[1] - 1:+.0%}，超出 ±30%，人工确认是否完整")

    # 关键列质量（抽最大 sheet 深检）
    if per_sheet:
        big = max(per_sheet, key=lambda s: per_sheet[s]["rows"])
        df = xl.parse(big, header=per_sheet[big]["header_row"])
        df.columns = [str(c).strip() for c in df.columns]
        for col in CRITICAL_COLS:
            if col not in df.columns:
                continue
            nulls = int(df[col].isna().sum())
            if nulls:
                rate = nulls / len(df)
                (errors if rate > 0.5 else warns if rate > 0.05 else infos).append(
                    f"[{big}]「{col}」空值 {nulls}/{len(df)}（{rate:.0%}）" + ("，关键列大面积为空" if rate > 0.5 else ""))
        if "招考人数" in df.columns:
            bad = int(pd.to_numeric(df["招考人数"], errors="coerce").isna().sum())
            if bad:
                warns.append(f"[{big}]「招考人数」有 {bad} 行无法转为数字，检查是否有合并单元格/表尾附注")
    return errors, warns, infos, per_sheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("year", nargs="?", help="校验 raw/national/{year}/position_all.xlsx")
    ap.add_argument("--file", help="直接指定 xlsx 路径（优先于 year）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    fm = json.loads((ROOT / "clean" / "national" / "field_map.json").read_text(encoding="utf-8"))
    mf_path = ROOT / "MANIFEST.json"
    manifest = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.exists() else None

    if args.file:
        file = Path(args.file)
    elif args.year:
        file = ROOT / "raw" / "national" / args.year / "position_all.xlsx"
    else:
        years = sorted((p.name for p in (ROOT / "raw" / "national").iterdir() if p.is_dir()), reverse=True)
        file = ROOT / "raw" / "national" / (years[0] if years else "") / "position_all.xlsx"

    errors, warns, infos, per_sheet = validate(file, fm, manifest)
    if args.json:
        print(json.dumps({"file": str(file), "status": "error" if errors else ("warn" if warns else "ok"),
                          "errors": errors, "warnings": warns, "info": infos, "per_sheet": per_sheet},
                         ensure_ascii=False, indent=2))
    else:
        print(f"门禁校验: {file}")
        for x in errors:
            print("  [ERROR]", x)
        for x in warns:
            print("  [WARN]", x)
        for x in infos:
            print("  [info]", x)
        for s, d in per_sheet.items():
            print(f"  sheet「{s}」: 表头行 {d['header_row']}, {d['rows']} 行")
        print("结论:", "不通过，先修 field_map.json 再清洗" if errors else ("通过（含警告，人工过目）" if warns else "通过"))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
