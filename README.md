# 诗词导览无障碍覆盖的抽样推断与改善排序

本项目保存无障碍服务分析的合成抽样接触、答卷、场馆汇总及改善资料，均使用 UTF-8 CSV；空字段表示记录未提供。`access_analysis/` 为分析工具：在明确的人群与场次定义下，估计各改善事项的受影响范围与不确定性，并在预算约束下给出可复核的改造排序建议。

## 方法学红线（工具强制执行，单元测试逐条守护）

1. **人次 ≠ 人数**：场馆台账只提供分层人次总量，仅用于“人次口径”的规模提示；不据此声称可识别的受影响“人数”。只有显式给出人均到访次数假设（`--visits-per-person`）时才做假设依赖的人数换算，并明确标注。
2. **未走到 ≠ 体验正常**：`reached=否` 是真实结果（被阻断，计入“妨碍参加”）；空白未答是缺测，只进入估计的上下界，绝不当作“无问题”。离场扫码看不到的中途放弃者，只能由问卷的阻断记录体现。
3. **无数据层不借邻层均值**：零回应层不出任何估计，只在覆盖诊断中列示；池化估计仅聚合有数据的层。
4. **低响应 ≠ 框外**：低响应是“可量化的不确定性”（置信区间与缺测界变宽）；框外未覆盖是“推断范围之外”，只列台账人次作规模提示，不做加权估计。
5. **收益按个体并集计**：同一受影响者被多项措施覆盖只计一次；多环节措施不得重复相加。
6. **基本保障不可取消**：`basic_guarantee=true` 的措施不进入“是否上马”的优化，即使样本中对应问题很少或没有出现。
7. **不用全场平均牺牲底线人群**：报告逐层受益份额；`--protected-group` 触发牺牲诊断，`--min-benefit 层=份额` 可设硬约束。
8. **建议分三类**：证据充分的优先改造 / 合理缺测解释下排序会翻转的待补访 / 不可取消的基本保障。补访只用于校验特定缺口，复核人可通过归因分解判断新建议源于样本覆盖、权重还是预算变化；第一轮“为何不能作决定”完整留痕。

## 数据

- `data/sampled_contacts.csv`：抽样接触记录。`selection_probability` 为邀请时可得的入样概率（0 表示框外未覆盖）；`invited` 为是否兑现邀请。层 = `school × slot × stratum`（人群 × 场次）。
- `data/responses.csv`：答卷。`subject_id` 区分“陪同者代答另一人”（新 id）与“同一受访者补答”（同 id，自动合并，不重复计数）；`reached` 空 = 该环节未作答（缺测），`否` = 确认未到达；`attended=否` 且报告问题 = 妨碍参加；`batch` 为回收批次。
- `data/venue_totals.csv`：场馆台账，`visit_count` 为分层**人次**总量（非可识别个体数）。
- `data/improvements.csv`：候选措施。`issue` 支持 `;` 分隔的多个问题（多环节措施）；`applies_to` 为适用人群层或 `全部`；`basic_guarantee` 为不可替代的基本保障。

## 用法

需要 Python 3.11 或更新版本；仅使用标准库。

```bash
# 一轮分析：报告 + 决策留痕
python3 -m access_analysis run --data-dir data --budget 80000 --out-dir out \
    --protected-group 交通支持

# 生成合成演示数据（含框外层、低响应层、代答、部分作答、妨碍参加等情形）
python3 examples/make_synthetic.py --out examples/synthetic --seed 7

# 两轮比较：归因建议变化来源（抽样框/权重、样本覆盖、预算约束）
python3 -m access_analysis compare --round1 out_round1_data --round2 round2_data \
    --record1 out_round1/decision_record.json \
    --budget1 80000 --budget2 80000 --out comparison.md
```

常用参数：`--ess-threshold`（有效样本量阈值，默认 30）、`--missing-multiplier`（缺测者受影响率上限 = 观测率 × 倍率；缺省为完整 Manski 界）、`--min-benefit 层=份额`（底线人群硬约束）、`--visits-per-person`（人均到访次数假设，给出后才允许人数换算）、`--bootstrap` / `--seed`。

## 输出

- `report.md`：口径定义、覆盖诊断（逐层状态与有效样本量）、加权估计（点估计 / 置信区间 / 缺测上下界 / 妨碍参加率）、措施分类与补访目标、警告与限制。
- `decision_record.json`：参数、数据散列、入选集、各措施分类与理由、待补访事项及“为何不能作决定”的原因——供下一轮复核比对。
- `comparison.md`（compare）：四配置归因分解（基线 → 换框/权重 → 换样本 → 换预算），第一轮留痕原因，本轮已决/仍待补访清单。

## 验证

```bash
python3 -m unittest discover -s tests -t . -v
```
