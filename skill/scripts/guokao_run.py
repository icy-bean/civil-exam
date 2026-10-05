# -*- coding: utf-8 -*-
"""guokao 选岗引擎：确定性过滤 + 加权打分 + xlsx/md 交付。

职责边界：模型只做对话（画像/意向收集与结果解读），本脚本做全部数据计算。
流程：定位数据仓库 -> manifest 完整性核对（当年缺失则硬失败）-> 硬过滤 ->
      意向加权打分 -> 交付 xlsx + 报告 md -> stdout 输出统计 JSON。
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

EDU_LEVEL = {"大专": 1, "本科": 2, "硕士研究生": 3, "博士研究生": 4}
DEGREE_LEVEL = {"无": 0, "学士": 2, "硕士": 3, "博士": 4}
POLITICS_LEVEL = {"群众": 0, "共青团员": 1, "中共预备党员": 2, "中共党员": 2}

EDU_RULES = [
    (r"仅限大专", {1}), (r"大专及以上|大专或本科", {1, 2}),
    (r"仅限本科", {2}), (r"本科及以上", {2, 3, 4}), (r"本科或硕士", {2, 3}),
    (r"仅限硕士", {3}), (r"硕士研究生及以上|硕士及以上", {3, 4}),
    (r"仅限博士", {4}), (r"博士及以上", {4}),
]

FLAG_LABEL = {
    "english4": "要求英语四级425+", "english6": "要求英语六级425+",
    "male_only": "限男性", "female_only": "限女性",
    "male_fit": "备注倾向男性", "female_fit": "备注倾向女性",
    "base_project": "面向服务基层项目人员", "household": "涉户籍/生源限制",
    "fresh_only": "仅限应届毕业生", "cert": "要求资格证书", "physical": "需体能测评",
}


def fail(msg: str, code: int = 2):
    print(json.dumps({"status": "error", "message": msg}, ensure_ascii=False, indent=2))
    sys.exit(code)


# ---------------------------------------------------------------- 数据仓库
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _http_get(url: str, timeout: int = 60) -> bytes:
    # raw.githubusercontent 的 CDN 在强推后会短暂缓存旧版本；数据更新恰恰最需要新鲜度，故加穿透参数
    if "raw.githubusercontent.com" in url:
        url = f"{url}{'&' if '?' in url else '?'}t={int(time.time() * 1000)}"
    req = urllib.request.Request(url, headers={"User-Agent": "guokao-skill"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def try_remote_sync(data_dir: Path, remote_urls: list) -> bool:
    """本地无 manifest 时从远端拉 MANIFEST.json + 运行时必需件（clean/ 与 ref/），逐件 SHA256 校验。

    raw/ 原始件不在同步范围（只有重建清洗件才需要）。任一远端成功即返回 True。
    """
    for u in remote_urls:
        try:
            manifest = json.loads(_http_get(u))
            base = u.rsplit("/", 1)[0]
            need = [i for i in manifest.get("items", []) if i["path"].startswith(("clean/", "ref/"))]
            for item in need:
                dest = data_dir / item["path"]
                if dest.exists() and _sha256(dest.read_bytes()) == item["sha256"]:
                    continue
                data = _http_get(f"{base}/{item['path']}", timeout=300)
                if _sha256(data) != item["sha256"]:
                    raise ValueError(f"sha256 mismatch: {item['path']}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            (data_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
            return True
        except Exception:
            continue
    return False


def load_repo(data_dir: Path, year: int):
    manifest_path = data_dir / "MANIFEST.json"
    if not manifest_path.exists():
        fail(f"数据仓库缺 MANIFEST.json：{data_dir}。先在仓库里跑 python scripts/build_manifest.py")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = {i["path"]: i for i in manifest["items"]}

    pos_rel = f"clean/national/{year}_position.csv"
    latest = manifest.get("latest_position_year")
    if pos_rel not in items:
        fail(
            f"{year} 年度职位表尚未收录（仓库最新为 {latest}）。"
            f"官方约每年 10 月中旬发布 {year + 1} 年度招考简章。"
            f"请先更新数据源：下载 raw 放入 raw/national/{year}/ → 跑 clean_positions.py {year} → 跑 build_manifest.py。"
            f"拒绝用旧年份表冒充 {year} 年度分析。"
        )
    verify = []
    for rel in (pos_rel, "clean/catalog/major_bachelor.csv", "clean/catalog/tax_major.csv"):
        p = data_dir / rel
        if rel not in items or not p.exists():
            fail(f"数据件缺失：{rel}（请重跑仓库清洗脚本）")
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        if h != items[rel]["sha256"]:
            verify.append(rel)
    if verify:
        print(json.dumps({"status": "warn", "message": "以下文件与 manifest 不一致，请重跑 build_manifest.py："
                          + ", ".join(verify)}, ensure_ascii=False))

    pos = pd.read_csv(data_dir / pos_rel, dtype={"dept_code": str, "position_code": str})
    hist = {}
    for y in range(year - 3, year):
        rel = f"clean/national/{y}_position.csv"
        if rel in items and (data_dir / rel).exists():
            hist[y] = pd.read_csv(data_dir / rel, dtype={"dept_code": str, "position_code": str})
    bachelor = pd.read_csv(data_dir / "clean" / "catalog" / "major_bachelor.csv").fillna("")
    tax = pd.read_csv(data_dir / "clean" / "catalog" / "tax_major.csv").fillna("")
    updated = manifest.get("updated_at")
    return pos, hist, bachelor, tax, updated


# ---------------------------------------------------------------- 专业匹配
def user_major_profile(profile: dict, bachelor: pd.DataFrame, tax: pd.DataFrame):
    """用户专业 -> (专业名集合, 代码集合, 专业类集合, 门类集合, 税务类别集合, 未解析名单)"""
    hard = profile["hard"]
    names, codes, classes, gates = set(), set(), set(), set()
    unresolved = []
    for m in hard.get("majors", []):
        m = str(m).strip()
        if not m:
            continue
        names.add(m)
        row = bachelor[(bachelor["name"] == m) | (bachelor["code"] == m) | (bachelor["code_base"] == m)]
        if row.empty:
            row = bachelor[bachelor["name"].str.contains(re.escape(m), na=False)]
            if len(row) != 1:
                unresolved.append(m)
                row = row.head(1) if len(row) == 1 else row.iloc[0:0]
        for _, r in row.iterrows():
            if r["level"] == "major":
                names.add(r["name"])
                if r["code"]:
                    codes.update({r["code"], r["code_base"]})
                if r["parent_name"]:
                    classes.add(r["parent_name"])
                if r["parent_code"]:
                    codes.add(r["parent_code"])
                if r["gate_name"]:
                    gates.add(r["gate_name"])
            elif r["level"] == "class":
                classes.add(r["name"])
                codes.add(r["code"])
                if r["parent_name"]:
                    gates.add(r["parent_name"])
            elif r["level"] == "gate":
                gates.add(r["name"])
    tax_cats = set(tax[tax["major"].isin(names)]["category"])
    tax_cats.update(tax[tax["category"].isin(classes | gates)]["category"])
    in_tax_catalog = bool(tax_cats) or bool(tax[tax["major"].isin(classes)]["category"].size)
    return names, codes, classes, gates, tax_cats, unresolved, in_tax_catalog or bool(tax_cats)


def cell_tokens(major_raw: str):
    """把专业要求单元格拆成词元：剥离'本科为/研究生为'前缀、学科代码、括注。"""
    if not isinstance(major_raw, str):
        return []
    toks = []
    for p in re.split(r"[、，,;；/\n]+", major_raw):
        p = p.strip()
        p = re.sub(r"^(本科|研究生|硕士|博士|大专)(研究生)?(所学专业)?为?", "", p)
        p = re.sub(r"^\d{2,6}[A-Za-z]?", "", p)
        p = re.split(r"[（(]", p)[0].strip()
        if p:
            toks.append(p)
    return toks


def unlimited_major(major_raw) -> bool:
    s = str(major_raw)
    return ("不限" in s and "专业" in s) or s.strip() == "不限"


# ---------------------------------------------------------------- 硬过滤
def make_age_of(exam_year: int):
    def age_of(birth: str) -> float:
        y, m = (int(x) for x in str(birth).split("-")[:2])
        cut_y, cut_m = exam_year - 1, 10  # 报名截止口径：上年 10 月
        return (cut_y - y) + (cut_m - m) / 12.0
    return age_of


def hard_filter(pos: pd.DataFrame, profile: dict, mprof: tuple):
    names, codes, classes, gates, tax_cats, unresolved, in_tax_catalog = mprof
    hard = profile["hard"]
    age_of = make_age_of(int(pos["year"].max()))
    age = age_of(hard["birth"])
    edu = EDU_LEVEL.get(hard["education"])
    deg = DEGREE_LEVEL.get(hard.get("degree", "无"), 0)
    pol = POLITICS_LEVEL.get(hard["politics"], 0)
    gen = hard.get("gender")
    fresh = bool(hard.get("fresh"))
    gyears = int(hard.get("grassroots_years", 0))
    default_age_max = 40 if (fresh and edu and edu >= 3) else 35

    eligible, excluded, review = [], [], []
    for r in pos.itertuples(index=False):
        reasons = []

        def exclude(code):
            reasons.append(code)

        # 性别
        flags = str(r.extra_flags if isinstance(r.extra_flags, str) else "")
        if "male_only" in flags and gen != "男":
            exclude("gender")
        if "female_only" in flags and gen != "女":
            exclude("gender")
        # 年龄（备注有单独限定则用之，否则用公告默认口径）
        a_max = r.age_max if pd.notna(r.age_max) else default_age_max
        a_min = r.age_min if pd.notna(r.age_min) else 18
        if age > a_max:
            exclude("age")
        elif age < a_min:
            exclude("age")
        # 学历
        if edu is None:
            exclude("education")
        else:
            rule = next((lv for pat, lv in EDU_RULES if re.search(pat, str(r.education_raw))), None)
            if rule is None:
                review.append((r, "education_unparsed", str(r.education_raw)))
            elif edu not in rule:
                exclude("education")
        # 学位
        dreq = str(r.degree_raw)
        if dreq in ("学士", "硕士", "博士") and deg < DEGREE_LEVEL[dreq]:
            exclude("degree")
        # 政治面貌
        preq = str(r.politics_raw)
        if preq == "中共党员" and pol < 2:
            exclude("politics")
        elif preq == "中共党员或共青团员" and pol < 1:
            exclude("politics")
        # 基层年限
        gy = int(r.grassroots_years) if pd.notna(r.grassroots_years) else 0
        if gyears < gy:
            exclude("grassroots")
        # 服务基层项目经历
        bproj = str(r.base_project_raw)
        if bproj not in ("无限制", "nan", "") and not hard.get("base_project_eligible"):
            exclude("base_project")
        # 应届限定
        if "fresh_only" in flags and not fresh:
            exclude("fresh")
        # 四六级
        if "english4" in flags and int(hard.get("cet4") or 0) < 425:
            exclude("english")
        if "english6" in flags and int(hard.get("cet6") or 0) < 425:
            exclude("english")
        # 专业
        if not reasons:
            ok, tax_ok, matched = major_match(r, names, codes, classes, gates, tax_cats, in_tax_catalog)
            if not ok:
                exclude("major")
            if ok and not tax_ok:
                exclude("tax_catalog")
            if unresolved and not ok:
                review.append((r, "major_unresolved", "、".join(unresolved)))
        # 户籍：无法自动判定 -> 人工确认
        if not reasons and "household" in flags:
            review.append((r, "household_check", "备注涉户籍/生源限制，需人工核对"))
        if "male_fit" in flags or "female_fit" in flags:
            review.append((r, "gender_fit_note", FLAG_LABEL["male_fit" if "male_fit" in flags else "female_fit"]))

        if reasons:
            excluded.append((r, reasons[0]))
        else:
            eligible.append(r)
    return eligible, excluded, review


def major_match(r, names, codes, classes, gates, tax_cats, in_tax_catalog):
    ok, matched = False, ""
    if unlimited_major(r.major_raw):
        ok, matched = True, "不限专业"
    else:
        toks = set(cell_tokens(r.major_raw))
        cell_codes = set(re.findall(r"\d{2,6}[A-Za-z]?", str(r.major_codes or "")))
        hit_code = codes & cell_codes
        if hit_code:
            ok, matched = True, f"代码匹配 {sorted(hit_code)[0]}"
        else:
            hit_name = names & toks
            if hit_name:
                ok, matched = True, f"专业匹配 {sorted(hit_name)[0]}"
            else:
                hit_cls = classes & toks
                if hit_cls:
                    ok, matched = True, f"类别匹配 {sorted(hit_cls)[0]}"
                else:
                    hit_gate = gates & toks
                    if hit_gate:
                        ok, matched = True, f"门类宽匹配 {sorted(hit_gate)[0]}"
    if not ok:
        return False, True, ""
    # 税务岗只认税务系统目录
    dept = str(r.dept_name or "")
    if "税务" in dept:
        if unlimited_major(r.major_raw):
            return True, True, matched
        toks = set(cell_tokens(r.major_raw))
        if (tax_cats & toks) or (names & toks):
            return True, True, matched
        return True, False, matched
    return True, True, matched


# ---------------------------------------------------------------- 打分
def loc_match(loc_list, province: str, city: str):
    """返回 (granularity, hit) : 2=同城 1=同省 0=未命中。loc 支持'广东肇庆'/'广东'/'深圳'。"""
    city_n = (city or "").rstrip("市")
    prov_n = province or ""
    best, hit = 0, ""
    for loc in loc_list or []:
        loc = str(loc).strip().rstrip("市")
        if not loc:
            continue
        if city_n and (loc in city_n or city_n in loc):
            return 2, loc
        if prov_n and loc.startswith(prov_n) and len(loc) > len(prov_n):
            rest = loc[len(prov_n):]
            if city_n and (rest in city_n or city_n in rest):
                return 2, loc
            if best < 1:
                best, hit = 1, loc
        elif prov_n and loc in prov_n:
            if best < 1:
                best, hit = 1, loc
    return best, hit


def score_positions(eligible: list, profile: dict):
    intents = profile.get("intents", {})
    rows = []
    for r in eligible:
        s, reasons = {}, []
        prov, city = str(r.work_province or ""), str(r.work_city or "")

        w = intents.get("near_home", 0)
        if w:
            g, hit = loc_match(profile["hard"].get("base_locations"), prov, city)
            s["near_home"] = {2: 10.0, 1: 7.0, 0: 2.0}[g]
            if g:
                reasons.append(f"离家近：工作地{r.work_location}（{'同城' if g == 2 else '同省'}匹配[{hit}]）")

        w = intents.get("treatment", 0)
        if w:
            t = float(r.treatment_score)
            s["treatment"] = t
            reasons.append(f"待遇粗评 {t}/10（地区档{r.treatment_region_tier}×0.6＋系统档{r.treatment_system_tier}×0.4，非实际收入）")

        w = intents.get("unit", {})
        if isinstance(w, dict) and w.get("weight"):
            hit = [k for k in w.get("keywords", []) if k in str(r.dept_name) or k in str(r.org_name)]
            s["unit"] = 10.0 if hit else 0.0
            if hit:
                reasons.append(f"指定单位：命中[{hit[0]}]")

        w = intents.get("system", {})
        if isinstance(w, dict) and w.get("weight"):
            text = f"{r.dept_name}{r.org_name}{r.position_attr}"
            hit = [k for k in w.get("keywords", []) if k in text]
            s["system"] = 10.0 if hit else 0.0
            if hit:
                reasons.append(f"系统偏好：命中[{hit[0]}]")

        w = intents.get("region", {})
        if isinstance(w, dict) and w.get("weight"):
            g, hit = loc_match(w.get("include"), prov, city)
            s["region"] = {2: 10.0, 1: 7.0, 0: 3.0}[g]
            if hit:
                reasons.append(f"地域偏好：命中[{hit}]（{'市' if g == 2 else '省'}级）")

        w = intents.get("level", {})
        if isinstance(w, dict) and w.get("weight"):
            pref = w.get("prefer", [])
            pref = pref if isinstance(pref, list) else [pref]
            s["level"] = 10.0 if str(r.org_level) in pref else 0.0
            if s["level"]:
                reasons.append(f"层级偏好：{r.org_level}")

        num = sum(float(intents.get(k, 0) if not isinstance(intents.get(k), dict) else intents[k].get("weight", 0)) * v
                  for k, v in s.items())
        den = sum(float(intents.get(k, 0) if not isinstance(intents.get(k), dict) else intents[k].get("weight", 0))
                  for k in s)
        total = round(num / den, 2) if den else 0.0
        rows.append({"row": r, "scores": s, "total": total, "reasons": reasons})
    rows.sort(key=lambda x: (-x["total"], -(x["row"].headcount or 0)))
    for i, item in enumerate(rows, 1):
        item["rank"] = i
    return rows


def apply_region_exclude(eligible: list, profile: dict):
    """region.exclude 是用户硬排除"""
    excl = profile.get("intents", {}).get("region", {}).get("exclude", []) or []
    if not excl:
        return eligible, []
    keep, dropped = [], []
    for item in eligible:
        r = item["row"] if isinstance(item, dict) else item
        text = f"{r.work_province}{r.work_city}"
        if any(str(k) in text for k in excl):
            dropped.append(r)
        else:
            keep.append(r)
    return keep, dropped


# ---------------------------------------------------------------- 交付物
NOTES_OF = ("english4", "english6", "fresh_only", "base_project", "household", "cert", "physical",
            "male_only", "female_only", "male_fit", "female_fit")


def build_output(rows, excluded, review, profile, year, updated, hist, out_dir: Path, top_n: int,
                 age: float = 0.0, default_age_max: int = 35, major_unresolved=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    hard = profile["hard"]

    def note_of(r):
        flags = str(r.extra_flags or "")
        return "；".join(FLAG_LABEL[f] for f in NOTES_OF if f in flags)

    msrv = lambda r: f"最低服务{int(r.min_service_years)}年" if pd.notna(getattr(r, "min_service_years", None)) else ""

    # ---- xlsx
    INTENT_CN = {"near_home": "离家近", "treatment": "待遇", "unit": "单位",
                 "system": "系统", "region": "地域", "level": "层级"}

    def rowdict(item):
        r, s = item["row"], item["scores"]
        return {
            "排名": item["rank"], "总分": item["total"],
            **{INTENT_CN.get(k, k): round(v, 1) for k, v in s.items()},
            "部门名称": r.dept_name, "用人司局": r.org_name, "职位名称": r.position_name,
            "工作地点": r.work_location, "招考人数": r.headcount, "机构层级": r.org_level,
            "学历要求": r.education_raw, "政治面貌": r.politics_raw, "职位代码": r.position_code,
            "推荐理由": "；".join(item["reasons"]), "报名提醒": "；".join(x for x in (note_of(r), msrv(r)) if x),
            "专业要求": r.major_raw, "备注": r.remarks,
        }

    df_all = pd.DataFrame([rowdict(it) for it in rows])
    df_top = df_all.head(50) if len(df_all) else df_all
    # 历史画像：按用人司局（实际用人单位，如某县税务局）聚合近三年招录；司局为空回退部门名
    hist_lines = []
    if rows:
        def hist_key(r):
            return r.org_name if isinstance(r.org_name, str) and r.org_name.strip() else r.dept_name
        top_keys = list(dict.fromkeys(hist_key(it["row"]) for it in rows[:top_n]))[:30]
        for key in top_keys:
            per = []
            for y in sorted(hist):
                sub = hist[y]
                sub = sub[sub["org_name"].fillna("") == key] if "org_name" in sub else sub[sub["dept_name"] == key]
                sub = sub if len(sub) else (hist[y][hist[y]["dept_name"] == key])
                if len(sub):
                    per.append(f"{y}招{int(sub['headcount'].sum())}人/{len(sub)}岗")
            cur = [it for it in rows[:top_n] if hist_key(it["row"]) == key]
            cur_n = sum(int(it["row"].headcount or 0) for it in cur)
            if per:
                hist_lines.append({"部门/司局": key, "历年招录": " · ".join(per) + f" · {year}招{cur_n}人",
                                   "连续招录年数": len(per) + 1})
    df_hist = pd.DataFrame(hist_lines)
    exc_counts = pd.Series([c for _, c in excluded]).value_counts() if excluded else pd.Series(dtype=int)
    EXC_LABEL = {"gender": "性别不符", "age": "年龄不符", "education": "学历不符", "degree": "学位不符",
                 "politics": "政治面貌不符", "grassroots": "基层年限不足", "base_project": "限服务基层项目人员",
                 "fresh": "限应届", "english": "四六级不符", "major": "专业不符", "tax_catalog": "专业不在税务目录内",
                 "region": "地域排除（用户exclude设定）"}
    df_exc = pd.DataFrame({"排除原因": [EXC_LABEL.get(c, c) for c in exc_counts.index], "岗位数": exc_counts.values})
    xlsx_path = out_dir / f"guokao_{year}_结果.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as w:
        df_all.to_excel(w, sheet_name="可报岗位排序", index=False)
        df_top.to_excel(w, sheet_name="Top50详情", index=False)
        df_hist.to_excel(w, sheet_name="历史部门画像", index=False)
        df_exc.to_excel(w, sheet_name="落选统计", index=False)
        for ws in w.book.worksheets:
            ws.freeze_panes = "C2"  # 冻结首行 + 排名/总分两列
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col[:50])
                ws.column_dimensions[col[0].column_letter].width = min(max(width * 1.9 + 2, 9), 60)

    # ---- 报告 md
    top10 = rows[:10]
    md = [f"# {year} 年度国考选岗报告", "",
          f"- 数据：{year} 年度职位表（{len(rows) + len(excluded)} 岗，manifest 更新 {updated}，粗评估口径见附注）",
          f"- 画像：{hard['gender']} / {hard['birth']}生 / {hard['politics']} / {hard['education']}"
          f"{'（应届）' if hard.get('fresh') else ''} / 专业：{'、'.join(hard['majors'])}",
          f"- 意向权重：" + "，".join(
              f"{k}={v if not isinstance(v, dict) else v.get('weight')}"
              for k, v in profile.get("intents", {}).items() if (v if not isinstance(v, dict) else v.get("weight"))),
          "",
          "## 漏斗", "",
          f"- 原始岗位 **{len(rows) + len(excluded)}** 个",
          f"- 硬性条件过滤后可报 **{len(rows)}** 个（落选 {len(excluded)}，明细见 xlsx 落选统计表）",
          f"- 需人工确认（户籍/边缘年龄等）**{len(review)}** 个，报名前逐条核对备注",
          "",
          "## Top 10", "",
          "| # | 总分 | 部门 | 职位 | 地点 | 人数 | 核心理由 |",
          "|---|---|---|---|---|---|---|"]
    for it in top10:
        r = it["row"]
        md.append(f"| {it['rank']} | {it['total']} | {r.dept_name} | {r.position_name} | {r.work_location} "
                  f"| {int(r.headcount) if pd.notna(r.headcount) else '?'} | {'；'.join(it['reasons'])} |")
    md += ["", "### 报名提醒（Top 10 中）", ""]
    for it in top10:
        n = "；".join(x for x in (note_of(it["row"]), msrv(it["row"])) if x)
        if n:
            md.append(f"- #{it['rank']} {it['row'].dept_name} {it['row'].position_name}：{n}")
    if hist_lines:
        md += ["", "## 附：目标部门历年招录画像（仅供参考，不参与排序）", ""]
        for h in hist_lines[:15]:
            md.append(f"- {h['部门/司局']}：{h['历年招录']} —— {'连续招录，编制缺口稳定' if h['连续招录年数'] >= 2 else '近年新开/有中断'}")
    md += ["",
           "## 口径与边界（务必读）", "",
           "- **待遇分是粗评估**：地区经济档×0.6＋系统津贴档×0.4，非实际收入，仅供意向排序。",
           "- **职位表不含进面分数/报录比**，本报告不评估录取难度；面试名单数据链每年 1-3 月另行更新。",
           "- 专业为**宽匹配**（目录类别+名称），边缘专业以招录机关解释为准；「需人工确认」岗位务必核对备注原文。",
           "- 报名前以官方公告、招考简章与职位表原文为最终依据。数据整理自公开招录信息。",
           f"- 报告生成：{stamp}"]
    md_path = out_dir / f"guokao_{year}_报告.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    stats = {
        "status": "ok",
        "year": year,
        "data_updated_at": updated,
        "total": len(rows) + len(excluded),
        "eligible": len(rows),
        "excluded": len(excluded),
        "needs_review": len(review),
        "needs_review_kinds": pd.Series([k for _, k, _ in review]).value_counts().to_dict() if review else {},
        "profile_warnings": (
            ["按报名口径年龄 {:.1f}，接近公告默认上限 {}，边缘岗位需按公告出生月份口径逐条复核".format(age, default_age_max)
             if default_age_max - age < 0.5 else None] or []),
        "major_unresolved": major_unresolved or [],
        "excluded_reasons": {EXC_LABEL.get(c, c): int(n) for c, n in exc_counts.items()},
        "top10": [
            {"rank": it["rank"], "total": it["total"], "dept": it["row"].dept_name,
             "position": it["row"].position_name, "location": it["row"].work_location,
             "headcount": int(it["row"].headcount) if pd.notna(it["row"].headcount) else None}
            for it in top10],
        "outputs": {"xlsx": str(xlsx_path), "report": str(md_path)},
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--data", default=None)
    args = ap.parse_args()

    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    if not profile.get("hard", {}).get("majors"):
        fail("profile.hard.majors 为空：至少要有一个专业名称")
    src = json.loads((SKILL_DIR / "data_source.json").read_text(encoding="utf-8"))
    data_dir = Path(args.data or src.get("local_path") or (Path.home() / ".guokao" / "data"))
    if not (data_dir / "MANIFEST.json").exists():
        if try_remote_sync(data_dir, src.get("remote_manifest_urls") or []):
            print(json.dumps({"status": "info", "message": f"已从远端同步数据件到 {data_dir}"}, ensure_ascii=False))
        else:
            fail(f"数据仓库不存在且远端同步失败：{data_dir}（检查 data_source.json 的 local_path / remote_manifest_urls）")

    pos, hist, bachelor, tax, updated = load_repo(data_dir, args.year)
    mprof = user_major_profile(profile, bachelor, tax)
    eligible, excluded, review = hard_filter(pos, profile, mprof)
    kept, dropped = apply_region_exclude(eligible, profile)
    excluded += [(r, "region") for r in dropped]
    rows = score_positions(kept, profile)
    age_of = make_age_of(args.year)
    age = age_of(profile["hard"]["birth"])
    default_age_max = 40 if (profile["hard"].get("fresh") and EDU_LEVEL.get(profile["hard"]["education"], 0) >= 3) else 35
    build_output(rows, excluded, review, profile, args.year, updated, hist, Path(args.out), args.top,
                 age, default_age_max, mprof[5])


if __name__ == "__main__":
    main()
