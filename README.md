# gov-exam-data — 公务员考试选岗助手（skill + 数据仓库）

一个开源的**国考选岗 AI Skill（/guokao）** 及其配套数据仓库：基于官方职位表做
**硬性条件布尔过滤 → 个性化意向加权打分 → 带理由交付**，产出 xlsx 岗位清单 + 选岗报告。
架构上可扩展省考/联考（目录名全 ASCII，新考试加一级目录即可）。

设计原则：**模型只负责对话与解读，过滤/打分/报告全部由确定性脚本完成**，杜绝逐行幻觉。

## 仓库结构

```
gov-exam-data/
├── MANIFEST.json          # 数据唯一入口：所有数据件 + SHA256 + 行数 + 更新时间
├── raw/                   # 官方原始件（原封不动，随附 source.txt 标注来源与日期）
│   ├── national/          # 国考职位表，按年分目录（2024/2025/2026...）
│   └── catalog/           # 专业目录（教育部本科目录 2025 版、税务系统 2026 目录）
├── clean/                 # 清洗件：运行时直接读，字段归一化
│   ├── national/          # {year}_position.csv + field_map.json（换年只改映射）
│   └── catalog/           # major_bachelor.csv / tax_major.csv（双轨专业匹配）
├── ref/                   # 参考评分表（粗评估口径，改档位只改表不改代码）
│   ├── system_treatment.csv   # 系统类别 → 待遇档（关键词匹配）
│   └── region_treatment.csv   # 省/市 → 地区档（计划单列市覆盖行）
├── scripts/               # 数据管线：清洗 + manifest 生成
└── skill/                 # /guokao skill 源码（SKILL.md + 运行引擎 + 画像模板）
```

## 安装（ZCode）

1. 把 `skill/` 拷贝为用户技能目录：`~/.agents/skills/guokao/`（Windows 即 `C:\Users\<你>\.agents\skills\guokao\`）。
2. 编辑 `skill/data_source.json`：
   - 本地用法：`local_path` 填本仓库的本地路径（克隆即含全部数据）；
   - 轻量用法：`local_path` 留空，引擎会自动从 `remote_manifest_urls` 拉取 MANIFEST 并同步 `clean/`+`ref/` 到 `~/.guokao/data`，逐件 SHA256 校验。
3. 在 ZCode 里说"帮我选国考岗位"或 `/guokao` 即可。

## 运行引擎（不经过模型也可用）

```bash
python skill/scripts/guokao_run.py --profile profile.json --year 2026 --out 输出目录
```

`profile.json` 结构见 `skill/assets/profile_template.json`：硬性条件（性别/年龄/专业/学历/
应届/四六级/基层年限…）+ 意向权重（离家近/待遇/指定单位/系统偏好/地域/层级，1-10）。

## 数据更新（每年 10 月中旬新表发布，人工采集 + 门禁校验）

> 采集一年只做一次且必须人工确认文件正确性，因此刻意不自动化下载；自动化放在"文件入库前"的门禁上。

1. 人工下载新表存入 `raw/national/{year}/position_all.xlsx`（来源记入同目录 `source.txt`）；
2. 跑门禁：`python scripts/validate_field_map.py {year}` —— 自动核对 sheet 名/表头行/列名 diff/行数量级/关键列空值率，
   **有差异按报告修 `clean/national/field_map.json`（换年只改映射不改代码），直到结论为通过**；
3. `python scripts/clean_positions.py {year}` → `python scripts/build_manifest.py`；
4. `python tests/test_clean_rules.py && python tests/test_filter_rules.py` 全绿后提交推送。
   已安装用户重新跑 skill 时会经 SHA256 校验发现更新。

### 候选源登记（人工采集时的入口备忘）

- 国考职位表：官方报名专题 `bm.scs.gov.cn/kl{year}`（JS 渲染，需浏览器）；华图分省直链模式
  `u3.huatu.com/uploads/soft/{发布月日}/{year}gkzw.xlsx`（历年可用，作为镜像首选）；
  中公/粉笔分省页为备选。
- 税务系统专业参考目录：随公告嵌在各省税务局「相关事项通知」正文表格里（如
  `shanghai.chinatax.gov.cn/xxgk/rsxx/`），非独立附件，用 pandas.read_html 提取。
- 教育部本科专业目录：教育部备案审批结果公告附件，或检索各省人社/政府网站转载的 xlsx。
- 原则：** raw 件永远保留原始来源 + 下载日期（source.txt），可追溯、可质疑、可撤换。**

## 口径与边界

- **待遇分是粗评估**：`地区档×0.6 + 系统档×0.4`，基于公开常识编制的 1-10 档位，
  非实际收入数据，仅用于意向加权排序。档位存于 `ref/`，欢迎按当地实情提 PR 修订。
- **职位表不含进面分数与报录比**，本仓库不评估录取难度（面试名单数据链每年 1-3 月更新，
  约定文件名 `{year}_report.csv`，规划中）。
- 专业为宽匹配：目录类别 + 名称三通道，**税务岗只认税务系统专用目录**（目录外一般不收）。
- 备注列的隐藏硬约束（限性别/应届/四六级/户籍/服务基层项目/最低服务年限）由脚本正则打标：
  能机判的硬过滤，不能机判的一律进「需人工确认」，绝不静默放行。

## 合规声明

- 代码部分：MIT License。
- **数据部分不适用 MIT**：国考职位表与专业目录为国家机关公开招录文件，版权归相应机关；
  本仓库仅作格式整理与聚合（来源与下载日期见各目录 `source.txt`），供个人报考参考，
  请遵循原发布方条款，勿用于商业变现。
- 报名前请以官方公告、招考简章与职位表原文为最终依据；专业等资格条件由招录机关负责解释。

## 免责

本项目与任何招录机关无关；分析结果仅供参考，不构成报考建议。
