# -*- coding: utf-8 -*-
"""2027 官方 .xls 原件入库：保留原始件 + 生成规范化 position_all.xlsx（仅 4 个标准表，保留原始行结构）。"""
import shutil
from pathlib import Path

import pandas as pd

SRC = Path(r"D:\xwechat_files\wxid_hau4hpm377qj22_8ab2\temp\RWTemp\2026-10\da1794ac2bd62bf92ffb08fe13975a18\中央机关及其直属机构2027年度考试录用公务员招考简章(1).xls")
DEST = Path("raw/national/2027")
DEST.mkdir(parents=True, exist_ok=True)

shutil.copy2(SRC, DEST / "position_all_official.xls")

xl = pd.ExcelFile(SRC)
sheets = [s for s in xl.sheet_names if s != "Sheet1"]  # Sheet1 为空表，规范化时剔除
with pd.ExcelWriter(DEST / "position_all.xlsx", engine="openpyxl") as w:
    for s in sheets:
        xl.parse(s, header=None).to_excel(w, sheet_name=s, index=False, header=False)
print("written:", [p.name for p in DEST.iterdir()], "| sheets:", sheets)
