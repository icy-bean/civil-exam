# -*- coding: utf-8 -*-
"""清洗层规则单测：parse_age / parse_major / parse_location / Treatment。

跑法: python tests/test_clean_rules.py   或   pytest tests/test_clean_rules.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import clean_positions as cp  # noqa: E402


# ---- 备注年龄解析 ----
def test_age_pair_range():
    assert cp.parse_age("年龄为18周岁以上、35周岁以下（1989年10月至2007年10月期间出生）") == (18, 35)

def test_age_upper_bound_only():
    assert cp.parse_age("3.要求年龄25周岁以下，其他条件见公告") == (None, 25)
    assert cp.parse_age("年龄在30周岁及以下") == (None, 30)
    assert cp.parse_age("年龄不超过28周岁") == (None, 28)

def test_age_lower_bound_only():
    assert cp.parse_age("年满23周岁") == (23, None)
    assert cp.parse_age("年龄为30周岁以上") == (30, None)

def test_age_absent_or_irrelevant():
    assert cp.parse_age("具有5年以上基层工作经历") == (None, None)  # 无"周岁"直接短路
    assert cp.parse_age("1989年10月以后出生") == (None, None)
    assert cp.parse_age(None) == (None, None)

def test_age_real_remark_with_noise():
    # 真实备注结构：咨询电话 + 专业说明 + 年龄限定混排
    remark = "1.咨询电话：010-51832128、010-81583133；\n2.职位要求专业为职位要求最低学历及以上各学历阶段对应专业之一即可；\n3.要求年龄25周岁以下。"
    assert cp.parse_age(remark) == (None, 25)


# ---- 专业(代码,名称)对抽取 ----
def test_parse_major_basic_codes():
    out = cp.parse_major("0809电子科学与技术、0810信息与通信工程、0812计算机科学与技术")
    assert out == "0809电子科学与技术;0810信息与通信工程;0812计算机科学与技术"

def test_parse_major_connector_pollution():
    # "或研究生为" 会粘在名称尾部，必须剥掉
    out = cp.parse_major("本科为0807电子信息类、0809计算机类或研究生为0810信息与通信工程")
    assert "0809计算机类" in out and "0809计算机类或研究生为" not in out
    assert "0810信息与通信工程" in out

def test_parse_major_k_suffix_and_dedup():
    out = cp.parse_major("120203K会计学、120204财务管理、120203K会计学")
    assert "120203K会计学" in out and out.count("120203K会计学") == 1


# ---- 备注性别限定（2026 表出现"，男性，"逗号规格式） ----
def test_gender_flag_comma_style():
    assert cp.RX["male_only"].search("面向高校毕业生，男性，本单位不提供宿舍")
    assert cp.RX["female_only"].search("高校毕业生，女性，本单位不提供宿舍")
    assert not cp.RX["male_only"].search("男女不限")
    assert not cp.RX["male_only"].search("男性优先")  # 优先类是软倾向，不当硬限定


# ---- 工作地点省市解析 ----
def test_parse_location_province_city():
    assert cp.parse_location("广东省深圳市") == ("广东", "深圳市")
    assert cp.parse_location("上海市浦东新区") == ("上海", "浦东新区")
    assert cp.parse_location("广西壮族自治区南宁市") == ("广西", "南宁市")
    assert cp.parse_location("内蒙古自治区") == ("内蒙古", "")
    assert cp.parse_location("黑龙江省哈尔滨市") == ("黑龙江", "哈尔滨市")
    assert cp.parse_location(None) == ("", "")


# ---- 待遇粗评估（地区档×0.6 + 系统档×0.4） ----
def test_treatment_shenzhen_tax():
    tr = cp.Treatment()
    _, rtier, scls, stier, total = tr.score("广东", "深圳市", "国家税务总局深圳市税务局", "subcentral_agency")
    assert (rtier, scls, stier, total) == (9.0, "tax", 8.0, 8.6)

def test_treatment_province_fallback_and_default():
    tr = cp.Treatment()
    # 无城市覆盖行时回退省档（广东 7.5），税务系统档 8
    _, rtier, _, stier, total = tr.score("广东", "肇庆市", "国家税务总局肇庆市税务局", "subcentral_agency")
    assert rtier == 7.5 and stier == 8.0 and total == 7.7
    # 无关键词命中时按机构类别兜底（省级以下直属 → 6.0）；注意文本不能含 ref 表关键词（如"参公"）
    _, _, scls, stier, _ = tr.score("吉林", "松原市乾安县", "某省属事业单位", "subcentral_agency")
    assert scls == "default" and stier == 6.0


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
