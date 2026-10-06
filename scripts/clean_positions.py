# -*- coding: utf-8 -*-
"""清洗国考职位表 raw/national/{year}/position_all.xlsx -> clean/national/{year}_position.csv

用法:  python clean_positions.py [2024 2025 2026]   # 缺省清洗全部已存在年份
要点:
- 表头行固定第 2 行(header=1), 字段映射见 clean/national/field_map.json
- 专业列抽取 (学科代码, 名称) 对; 备注列正则匹配隐藏硬约束(四六级/性别/户籍/服务项目/应届/年龄/最低服务年限)
- 工作地点解析出省/市; 待遇粗评分 = 0.6*地区档 + 0.4*系统档(查 ref/ 两张表)
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROVINCES = [
    "北京市", "天津市", "上海市", "重庆市", "河北省", "山西省", "内蒙古自治区", "辽宁省",
    "吉林省", "黑龙江省", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省", "四川省", "贵州省",
    "云南省", "西藏自治区", "陕西省", "甘肃省", "青海省", "宁夏回族自治区", "新疆维吾尔自治区",
    "香港特别行政区", "澳门特别行政区", "台湾省",
]
PROV_SHORT = {p: re.sub(r"(省|市|壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区)$", "", p) for p in PROVINCES}

SHEET_CLASS = {
    "中央党群机关": "party_mass_central",
    "中央国家行政机关（本级）": "state_org_central",
    "中央国家行政机关省级以下直属机构": "subcentral_agency",
    "中央国家行政机关参照公务员法管理事业单位": "public_managed",
}

# ---- 备注隐藏约束的正则(顺序无关, 命中即打标) ----
RX = {
    "english4": re.compile(r"CET-?4|大学英语四级|国家四级|四级(?:英语)?(?:425|成绩)?[^，。;；]*425"),
    "english6": re.compile(r"CET-?6|大学英语六级|国家六级|六级[^，。;；]*425"),
    "male_only": re.compile(r"限男性|仅限男性|[，,；;]\s*男性\s*[，,。；;）)]"),
    "female_only": re.compile(r"限女性|仅限女性|[，,；;]\s*女性\s*[，,。；;）)]"),
    "male_fit": re.compile(r"适合男性"),
    "female_fit": re.compile(r"适合女性"),
    "base_project": re.compile(r"面向([^，。;；]{0,12}(服务基层项目|大学生村官|三支一扶|西部计划|特岗教师)[^，。;；]{0,6})报考|服务基层项目人员|大学生村官|三支一扶|西部计划志愿者?|退役大学生士兵"),
    "household": re.compile(r"户籍|生源|限本省|常住人口"),
    "fresh_only": re.compile(r"仅限(应届|202\d年毕业)|202\d年应届|应届高校毕业生"),
    "min_service": re.compile(r"最低服务年限为?(\d)年|最低服务(\d)年|服务期(\d)年"),
    "cert": re.compile(r"资格证书|执业资格|法律职业资格|会计专业技术|教师资格"),
    "physical": re.compile(r"体能测评|体能测试"),
    "age_pair": re.compile(r"(\d{2,3})周岁(?:[^，。;；]{0,6}?(\d{2,3})周岁|[^，。;；]{0,4}以下|[^，。;；]{0,4}及以下)?"),
}
EN_DEGREE = {"大专": 1, "本科": 2, "硕士研究生": 3, "博士研究生": 4}


def parse_major(cell: str) -> str:
    """抽取 (代码,名称) 对 -> '0809电子科学与技术;0812计算机科学与技术'"""
    if not isinstance(cell, str):
        return ""
    out, seen = [], set()
    for code, name in re.findall(r"(\d{4,6}[A-Za-z]?)\s*([^\d、，,；;/\s]{2,20})", cell):
        # 名称尾部可能粘上连接词（"计算机类或研究生为"），剥掉再入库
        name = re.sub(r"(?:或|及|和|为|研究生|本科|大专|博士|硕士|所学专业)+$", "", name)
        if len(name) < 2:
            continue
        key = code + name
        if key not in seen:
            seen.add(key)
            out.append(code + name)
    return ";".join(out)


def parse_age(remark: str):
    """解析备注年龄：'18周岁以上...35周岁以下'->(18,35)；'30周岁以下'->(None,30)；'不超过28周岁'->(None,28)；'年满23周岁'->(23,None)"""
    if not isinstance(remark, str) or "周岁" not in remark:
        return None, None
    m = re.search(r"(\d{2,3})周岁(?:以上|及以上)?[^，。;；]{0,4}?(\d{2,3})周岁", remark)
    if m and not re.search(r"(\d{2,3})周岁以下", remark[:m.start()] + remark[m.end():]):
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"不超过(\d{2,3})周岁", remark)
    if m:
        return None, int(m.group(1))
    m = re.search(r"(\d{2,3})周岁(?:以下|及以下)", remark)
    if m:
        return None, int(m.group(1))
    m = re.search(r"(\d{2,3})周岁(?:以上|及以上)", remark)
    if m:
        return int(m.group(1)), None
    m = re.search(r"年满(\d{2,3})周岁", remark)
    if m:
        return int(m.group(1)), None
    return None, None


def parse_location(loc: str):
    """'广东省深圳市'->(广东,深圳); '上海市浦东新区'->(上海,浦东新区)"""
    if not isinstance(loc, str):
        return "", ""
    for full, short in PROV_SHORT.items():
        if loc.startswith(full):
            rest = loc[len(full):]
            return short, rest if rest else ""
    return "", ""


class Treatment:
    """待遇粗评估: 地区档 * 0.6 + 系统档 * 0.4, 口径见 README"""

    def __init__(self):
        self.region = {}
        for row in pd.read_csv(ROOT / "ref" / "region_treatment.csv").itertuples():
            self.region[row.region_key] = float(row.tier)
        self.system = []
        sys_df = pd.read_csv(ROOT / "ref" / "system_treatment.csv")
        for row in sys_df.itertuples():
            self.system.append((str(row.keyword), str(row.system_class), float(row.tier)))
        self.system.sort(key=lambda x: -len(x[0]))  # 长关键词优先
        self.class_fallback = {
            "party_mass_central": ("party_central", 8.0),
            "state_org_central": ("state_central", 8.5),
            "public_managed": ("can_gong", 6.0),
            "subcentral_agency": ("default", 6.0),
        }

    def lookup_region(self, prov: str, city: str):
        if prov:
            city_short = re.sub(r"市$", "", city) if city else ""
            for key in (prov + city_short, prov + city, prov):
                if key in self.region:
                    return key, self.region[key]
        return "", None

    def lookup_system(self, text: str):
        for kw, cls, tier in self.system:
            if kw in text:
                return cls, tier
        return None, None

    def score(self, prov, city, dept_text, agency_class):
        rkey, rtier = self.lookup_region(prov, city)
        skey = self.lookup_system(dept_text)
        if skey[0] is None:
            scls, stier = self.class_fallback.get(agency_class, ("default", 6.0))
        else:
            scls, stier = skey
        total = round(0.6 * (rtier if rtier is not None else 6.0) + 0.4 * stier, 1)
        return rkey, rtier, scls, stier, total


def _field_map() -> dict:
    """表头映射惰性加载（import 本模块不应强依赖数据文件）。"""
    return json.loads((ROOT / "clean" / "national" / "field_map.json").read_text(encoding="utf-8"))["columns"]


def clean_year(year: str) -> pd.DataFrame:
    f = ROOT / "raw" / "national" / year / "position_all.xlsx"
    xl = pd.ExcelFile(f)
    frames = []
    for sheet in xl.sheet_names:
        df = xl.parse(sheet, header=1)
        df["agency_class"] = SHEET_CLASS.get(sheet, sheet)
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True).rename(columns=_field_map())

    tr = Treatment()
    recs = []
    for r in raw.itertuples(index=False):
        row = {c: getattr(r, c) for c in raw.columns}
        remark = str(row.get("remarks") or "")
        flags = []
        for flag in ("english4", "english6", "male_only", "female_only", "male_fit",
                     "female_fit", "base_project", "household", "fresh_only", "cert", "physical"):
            if RX[flag].search(remark):
                flags.append(flag)
        ms = RX["min_service"].search(remark)
        min_service = int(ms.group(1) or ms.group(2) or ms.group(3)) if ms else None
        age_lo, age_hi = parse_age(remark)

        prov, city = parse_location(str(row.get("work_location") or ""))
        dept_text = " ".join(str(row.get(k) or "") for k in ("dept_name", "org_name", "position_attr"))
        rkey, rtier, scls, stier, score = tr.score(prov, city, dept_text, row["agency_class"])

        grass = str(row.get("grassroots_raw") or "")
        g = re.search(r"(\d)", grass)
        grass_years = int(g.group(1)) if g else (None if ("不限" in grass or "无" in grass) else 0)

        recs.append({
            "year": int(year),
            "agency_class": row["agency_class"],
            "dept_code": str(row.get("dept_code") or ""),
            "dept_name": row.get("dept_name"),
            "org_name": row.get("org_name"),
            "org_nature": row.get("org_nature"),
            "org_level": row.get("org_level"),
            "position_name": row.get("position_name"),
            "position_attr": row.get("position_attr"),
            "position_code": str(row.get("position_code") or ""),
            "position_intro": row.get("position_intro"),
            "exam_category": row.get("exam_category"),
            "headcount": pd.to_numeric(row.get("headcount"), errors="coerce"),
            "major_raw": row.get("major_raw"),
            "major_codes": parse_major(str(row.get("major_raw") or "")),
            "education_raw": row.get("education_raw"),
            "degree_raw": row.get("degree_raw"),
            "politics_raw": row.get("politics_raw"),
            "grassroots_years": grass_years,
            "base_project_raw": row.get("base_project_raw"),
            "interview_ratio": row.get("interview_ratio"),
            "work_location": row.get("work_location"),
            "work_province": prov,
            "work_city": city,
            "household_location": row.get("household_location"),
            "remarks": row.get("remarks"),
            "extra_flags": ";".join(flags),
            "age_min": age_lo,
            "age_max": age_hi,
            "min_service_years": min_service,
            "treatment_region_key": rkey,
            "treatment_region_tier": rtier,
            "treatment_system_class": scls,
            "treatment_system_tier": stier,
            "treatment_score": score,
        })
    out = pd.DataFrame(recs)
    out["position_code"] = out["position_code"].str.replace(r"\.0$", "", regex=True)
    out["dept_code"] = out["dept_code"].str.replace(r"\.0$", "", regex=True)
    dest = ROOT / "clean" / "national" / f"{year}_position.csv"
    out.to_csv(dest, index=False, encoding="utf-8-sig")
    return out


def main():
    years = sys.argv[1:] or sorted(
        (p.name for p in (ROOT / "raw" / "national").iterdir() if p.is_dir()), reverse=True
    )
    for y in years:
        df = clean_year(y)
        n_tax = int(df["dept_name"].fillna("").str.contains("税务").sum())
        print(f"[ok] {y}: rows={len(df)} tax={n_tax} -> clean/national/{y}_position.csv")


if __name__ == "__main__":
    main()
