# -*- coding: utf-8 -*-
"""过滤/打分引擎规则单测：cell_tokens / 学历阶梯 / loc_match / 年龄口径 / 税务目录门。

跑法: python tests/test_filter_rules.py   或   pytest tests/test_filter_rules.py
"""
import sys
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skill" / "scripts"))
import guokao_run as gr  # noqa: E402

Row = namedtuple("Row", "dept_name major_raw major_codes")


# ---- 专业要求单元格拆词元 ----
def test_cell_tokens_basic():
    assert gr.cell_tokens("0809电子科学与技术、0810信息与通信工程") == ["电子科学与技术", "信息与通信工程"]

def test_cell_tokens_prefix_and_connector():
    # "本科为…或研究生为…" 混排结构，连接词与前缀都应剥净
    toks = gr.cell_tokens("本科为0807电子信息类、0809计算机类或研究生为0812计算机科学与技术、0305马克思主义理论")
    assert toks == ["电子信息类", "计算机类", "计算机科学与技术", "马克思主义理论"]

def test_cell_tokens_parenthetical():
    toks = gr.cell_tokens("0301法学（含：劳动法学、社会保障法学）、0351法律")
    assert "法学" in toks and "劳动法学" not in toks and "法律" in toks

def test_cell_tokens_unlimited():
    assert gr.cell_tokens("不限") == ["不限"]
    assert gr.cell_tokens(None) == []

def test_cell_tokens_never_splits_real_major_names():
    # 目录里的真实专业名含"与/及"，必须保持完整（分词只断在 或/为/前缀/代码 处）
    for name in ["船舶与海洋工程", "语言学及应用语言学", "材料科学与工程"]:
        assert gr.cell_tokens(name) == [name]


# ---- 学历阶梯匹配 ----
def _edu_rule(s):
    return next((lv for pat, lv in gr.EDU_RULES if __import__("re").search(pat, s)), None)

def test_edu_ladder():
    assert _edu_rule("仅限大专") == {1}
    assert _edu_rule("大专或本科") == {1, 2}
    assert _edu_rule("仅限本科") == {2}
    assert _edu_rule("本科及以上") == {2, 3, 4}
    assert _edu_rule("本科或硕士研究生") == {2, 3}
    assert _edu_rule("仅限硕士研究生") == {3}
    assert _edu_rule("硕士研究生及以上") == {3, 4}
    assert _edu_rule("仅限博士研究生") == {4}
    assert _edu_rule(" unspecified ") is None

def test_edu_ladder_no_cross_match():
    # "硕士研究生及以上" 不得被 "仅限硕士"/"本科及以上" 之外的规则抢先误配
    assert _edu_rule("硕士研究生及以上") == {3, 4}
    assert _edu_rule("大专或本科") == {1, 2}


# ---- 地理匹配（同城/同省/未命中） ----
def test_loc_match_city_and_province():
    assert gr.loc_match(["广东肇庆"], "广东省", "肇庆市") == (2, "广东肇庆")
    assert gr.loc_match(["深圳"], "广东省", "深圳市") == (2, "深圳")
    assert gr.loc_match(["广东"], "广东省", "深圳市") == (1, "广东")
    assert gr.loc_match(["肇庆"], "广东省", "") == (0, "")  # 岗位无市信息时不虚报同城

def test_loc_match_prefixed_loc_with_district_city():
    # 真实案例：工作地点"吉林省松原市乾安县"这类带区县的市级串
    assert gr.loc_match(["吉林松原"], "吉林省", "松原市乾安县") == (2, "吉林松原")
    assert gr.loc_match(["广东肇庆"], "吉林省", "松原市乾安县") == (0, "")


# ---- 报名口径年龄 ----
def test_age_of_cutoff():
    age = gr.make_age_of(2027)("2001-06")   # 报名截止 = 2026-10
    assert abs(age - 25.0 - 4 / 12) < 1e-9

def test_default_age_max():
    profile = {"hard": {"education": "硕士研究生", "fresh": True}}
    default = 40 if (profile["hard"]["fresh"] and gr.EDU_LEVEL.get(profile["hard"]["education"], 0) >= 3) else 35
    assert default == 40
    profile["hard"]["education"] = "本科"
    default = 40 if (profile["hard"]["fresh"] and gr.EDU_LEVEL.get(profile["hard"]["education"], 0) >= 3) else 35
    assert default == 35


# ---- 税务目录门（税务岗只认税务系统目录） ----
def test_major_match_tax_pass():
    names, codes = {"软件工程"}, set()
    classes, gates, tax_cats = {"计算机类"}, set(), {"计算机类"}
    r = Row("国家税务总局肇庆市税务局", "0809计算机类", "0809计算机类")
    ok, tax_ok, matched = gr.major_match(r, names, codes, classes, gates, tax_cats, True)
    assert ok and tax_ok and "类别匹配" in matched

def test_major_match_tax_catalog_block():
    # 专业类别命中岗位要求，但该类别不在税务系统目录 → 拦下（目录外一般不收）
    names, classes = {"考古学"}, {"考古学类"}
    r = Row("国家税务总局某税务局", "0601历史学类、考古学类", "0601历史学类;考古学类")
    ok, tax_ok, _ = gr.major_match(r, names, set(), classes, set(), set(), True)
    assert ok and not tax_ok

def test_major_match_non_tax_ignores_tax_catalog():
    names, classes = {"考古学"}, {"考古学类"}
    r = Row("某部委办公厅", "0601历史学类、考古学类", "0601历史学类;考古学类")
    ok, tax_ok, _ = gr.major_match(r, names, set(), classes, set(), set(), True)
    assert ok and tax_ok

def test_major_match_unlimited():
    ok, tax_ok, matched = gr.major_match(Row("某单位", "不限", ""), {"软件工程"}, set(), set(), set(), set(), True)
    assert ok and tax_ok and matched == "不限专业"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, "-", e)
    print(f"{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
