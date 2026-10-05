# -*- coding: utf-8 -*-
"""清洗专业目录 -> clean/catalog/{major_bachelor.csv, tax_major.csv}

- 教育部本科目录(2025版): 门类/专业类/专业 三级, 输出每行带父级类别, 供"专业->类别"映射
- 税务系统目录(2026): 类别 -> 包含专业名列表, 拆成一行一专业
用法: python clean_catalogs.py
"""
from pathlib import Path

import pandas as pd
import re

ROOT = Path(__file__).resolve().parents[1]


def clean_bachelor():
    f = ROOT / "raw" / "catalog" / "major_bachelor_2025.xlsx"
    df = pd.ExcelFile(f).parse("国家教育行政部门学科专业目录-本科（2025版）", header=None)
    gate_code, gate_name, cls_code, cls_name = None, None, None, None
    recs = []
    for _, r in df.iterrows():
        c0, c1, c2 = r.iloc[0], r.iloc[1], r.iloc[2]
        s0 = str(c0)
        m_gate = re.match(r"^(\d{2})学科门类：(.+)", s0)
        if m_gate:
            gate_code, gate_name = m_gate.group(1), m_gate.group(2)
            recs.append(dict(code=gate_code, name=gate_name, level="gate",
                             parent_code="", parent_name="", gate_code=gate_code, gate_name=gate_name))
            continue
        code, name = str(c1).strip(), str(c2).strip()
        if code in ("nan", ""):
            continue
        if len(code) == 4:  # 专业类
            cls_code, cls_name = code, name
            recs.append(dict(code=code, name=name, level="class",
                             parent_code=gate_code or "", parent_name=gate_name or "",
                             gate_code=gate_code or "", gate_name=gate_name or ""))
        else:  # 专业(6位, 可带 K/T/K+T 后缀)
            recs.append(dict(code=code, name=name, level="major",
                             parent_code=cls_code or "", parent_name=cls_name or "",
                             gate_code=gate_code or "", gate_name=gate_name or ""))
    out = pd.DataFrame(recs)
    out["code_base"] = out["code"].str.replace(r"[KT]+$", "", regex=True)
    dest = ROOT / "clean" / "catalog" / "major_bachelor.csv"
    out.to_csv(dest, index=False, encoding="utf-8-sig")
    print(f"[ok] bachelor: {len(out)} rows (major={int((out.level == 'major').sum())}) -> {dest.name}")


def clean_tax():
    f = ROOT / "raw" / "catalog" / "tax_major_2026_notice.html"
    tables = pd.read_html(f)
    t = max(tables, key=len)
    t.columns = ["no", "category", "majors"]
    recs = []
    for _, r in t.iloc[1:].iterrows():  # 首行是表头
        majors = str(r["majors"])
        for m in re.split(r"[，,；;、\n]", majors):
            m = m.strip()
            if m and m != "nan":
                recs.append(dict(category_no=int(r["no"]), category=str(r["category"]).strip(), major=m))
    out = pd.DataFrame(recs)
    dest = ROOT / "clean" / "catalog" / "tax_major.csv"
    out.to_csv(dest, index=False, encoding="utf-8-sig")
    print(f"[ok] tax: {out['category'].nunique()} categories, {len(out)} majors -> {dest.name}")


if __name__ == "__main__":
    clean_bachelor()
    clean_tax()
