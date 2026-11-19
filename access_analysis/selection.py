"""改善措施遴选：在预算内最大化“受影响个体的并集收益”。

核心规则：
- 收益按个体并集计：同一受影响者被多项措施覆盖，只计一次——
  多环节措施不得把同一人的收益重复相加。
- 基本保障（basic_guarantee）不进入优化，直接入选并先行占用预算，
  即使其对应问题在样本中很少甚至没有出现，也不得取消。
- 不用全场平均收益牺牲底线人群：输出各人群层的受益份额，
  支持 min_benefit 硬约束，并在平均口径明显牺牲某层时给出警告。
- 排序稳定性：分别在“观测情形”和“缺测上界情形”下重算入选集，
  成员发生翻转的措施列为“待补访”，并定位需要补访的层 × 问题。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum

from .coverage import CoverageRow, CoverageStatus
from .estimates import EstimateSet
from .responses import Subject
from .schema import Measure

_EXHAUSTIVE_LIMIT = 20  # 可选措施超过此数改用贪心+交换启发式


class Category(Enum):
    GUARANTEE = "基本保障（不可取消）"
    PRIORITY = "优先改造（证据充分）"
    FOLLOWUP = "待补访（证据不足或排序不稳）"
    DEFERRED = "暂不安排"


@dataclass
class MeasureAssessment:
    measure_id: str
    category: Category
    marginal_benefit: float = 0.0  # 入选集中的边际收益（加权个体数）
    reasons: list[str] = field(default_factory=list)


@dataclass
class FollowupTarget:
    cell: tuple[str, str, str]
    issue: str
    reason: str


@dataclass
class SelectionResult:
    budget: int
    spent: int
    selected: list[str]  # 全部入选（含基本保障）
    guaranteed: list[str]
    total_benefit: float  # 加权受益个体数（并集口径）
    benefit_share: float  # 占全部加权个体比例
    per_group_share: dict[str, float]  # 各人群层受益份额
    assessments: list[MeasureAssessment]
    followup_targets: list[FollowupTarget]
    scenario_selected: dict[str, list[str]]  # 各缺测情景入选集（复核用）
    warnings: list[str] = field(default_factory=list)


def _issue_universe(measures: list[Measure], subjects: list[Subject]) -> dict[str, int]:
    names = sorted({i for m in measures for i in m.issues} | {i for s in subjects for i in s.issues})
    return {name: idx for idx, name in enumerate(names)}


def _subject_signature(
    subject: Subject,
    issue_index: dict[str, int],
    expected_stages: list[str],
    scenario: str,
    stratum_full_mask: dict[str, int],
) -> int:
    """个体在某一情景下的“可被解决”位掩码。"""
    mask = 0
    for issue in subject.issues:
        mask |= 1 << issue_index[issue]
    if scenario == "upper" and (
        subject.missing_stages(expected_stages) or subject.blocked
    ):
        # 缺测上界：缺测个体可能患有其所在层任何可被候选措施解决的问题
        mask |= stratum_full_mask.get(subject.stratum, 0)
    return mask


def _prepare(
    measures: list[Measure],
    subjects: list[Subject],
    subject_weights: dict[tuple[str, str], float],
    expected_stages: list[str],
    scenario: str,
):
    """返回 (measure_masks, strata, sig_weights)。

    measure_masks: measure_id -> {stratum: mask}（仅适用的层）
    sig_weights: (stratum, mask) -> 权重和，把同层同问题谱的个体合并以加速枚举。
    """
    issue_index = _issue_universe(measures, subjects)
    strata = sorted({s.stratum for s in subjects})
    measure_masks: dict[str, dict[str, int]] = {}
    for measure in measures:
        mask = 0
        for issue in measure.issues:
            mask |= 1 << issue_index[issue]
        measure_masks[measure.measure_id] = {
            s: mask for s in strata if measure.applies_to_stratum(s)
        }
    stratum_full_mask: dict[str, int] = {}
    for s in strata:
        full = 0
        for measure in measures:
            if measure.applies_to_stratum(s):
                for issue in measure.issues:
                    full |= 1 << issue_index[issue]
        stratum_full_mask[s] = full

    sig_weights: dict[tuple[str, int], float] = {}
    for subject in subjects:
        w = subject_weights.get(subject.key)
        if not w:
            continue
        mask = _subject_signature(subject, issue_index, expected_stages, scenario, stratum_full_mask)
        key = (subject.stratum, mask)
        sig_weights[key] = sig_weights.get(key, 0.0) + w
    return measure_masks, strata, sig_weights


def _benefit(
    selected_ids: set[str],
    measure_masks: dict[str, dict[str, int]],
    strata: list[str],
    sig_weights: dict[tuple[str, int], float],
    group: str | None = None,
) -> float:
    """入选集的并集收益（加权个体数）；group 非空时只计该人群层。"""
    union_by_stratum: dict[str, int] = {}
    for stratum in strata:
        mask = 0
        for mid in selected_ids:
            mask |= measure_masks[mid].get(stratum, 0)
        union_by_stratum[stratum] = mask
    total = 0.0
    for (stratum, mask), weight in sig_weights.items():
        if group is not None and stratum != group:
            continue
        if mask & union_by_stratum[stratum]:
            total += weight
    return total


def _optimize(
    optional_ids: list[str],
    fixed_ids: set[str],
    remaining_budget: int,
    costs: dict[str, int],
    measure_masks: dict[str, dict[str, int]],
    strata: list[str],
    sig_weights: dict[tuple[str, int], float],
    min_group_benefit: dict[str, float] | None = None,
    group: str | None = None,
) -> tuple[set[str], float]:
    """在预算内选择可选措施子集，返回 (入选可选集, 总收益)。"""
    best_set: set[str] = set()
    best_benefit = _benefit(fixed_ids, measure_masks, strata, sig_weights, group)
    best_cost = 0

    def feasible_and_score(subset: tuple[str, ...]) -> float | None:
        cost = sum(costs[m] for m in subset)
        if cost > remaining_budget:
            return None
        selected = fixed_ids | set(subset)
        if min_group_benefit:
            for g, floor in min_group_benefit.items():
                if _benefit(selected, measure_masks, strata, sig_weights, g) < floor:
                    return None
        return _benefit(selected, measure_masks, strata, sig_weights, group)

    if len(optional_ids) <= _EXHAUSTIVE_LIMIT:
        for r in range(len(optional_ids) + 1):
            for subset in itertools.combinations(optional_ids, r):
                score = feasible_and_score(subset)
                if score is None:
                    continue
                cost = sum(costs[m] for m in subset)
                # 目标：收益最大；平局时花费最少
                if (score, -cost) > (best_benefit, -best_cost):
                    best_benefit, best_set, best_cost = score, set(subset), cost
    else:
        # 贪心 + 单次交换改进（大规模候选时的启发式，结果标注为近似）。
        # 注意：构造过程不逐步检查 min_benefit（最小份额约束在空集上本就不成立），
        # 仅在最终解上校验；不可行时返回空集，由调用方决定降级。
        current: set[str] = set()
        current_cost = 0
        while True:
            best_gain, best_m = 0.0, None
            base = _benefit(fixed_ids | current, measure_masks, strata, sig_weights, group)
            for m in optional_ids:
                if m in current or current_cost + costs[m] > remaining_budget:
                    continue
                gain = _benefit(
                    fixed_ids | current | {m}, measure_masks, strata, sig_weights, group
                ) - base
                if gain > best_gain:
                    best_gain, best_m = gain, m
            if best_m is None:
                break
            current.add(best_m)
            current_cost += costs[best_m]
        improved = True
        while improved:
            improved = False
            base = _benefit(fixed_ids | current, measure_masks, strata, sig_weights, group)
            for m_out in list(current):
                for m_in in optional_ids:
                    if m_in in current:
                        continue
                    trial = (current - {m_out}) | {m_in}
                    trial_cost = sum(costs[m] for m in trial)
                    if trial_cost > remaining_budget:
                        continue
                    score = _benefit(fixed_ids | trial, measure_masks, strata, sig_weights, group)
                    if score > base:
                        current, base, improved = trial, score, True
                        break
                if improved:
                    break
        if min_group_benefit and any(
            _benefit(fixed_ids | current, measure_masks, strata, sig_weights, g) < floor
            for g, floor in min_group_benefit.items()
        ):
            return set(), _benefit(fixed_ids, measure_masks, strata, sig_weights, group)
        best_set = current
        best_benefit = _benefit(fixed_ids | current, measure_masks, strata, sig_weights, group)
    return best_set, best_benefit


def select_measures(
    measures: list[Measure],
    subjects: list[Subject],
    subject_weights: dict[tuple[str, str], float],
    expected_stages: list[str],
    budget: int,
    coverage: list[CoverageRow],
    estimates: EstimateSet,
    *,
    protected_groups: list[str] | None = None,
    min_group_benefit: dict[str, float] | None = None,
) -> SelectionResult:
    """遴选措施并分类：基本保障 / 优先改造 / 待补访 / 暂不安排。"""
    warnings: list[str] = []
    guaranteed = [m for m in measures if m.basic_guarantee]
    optional = [m for m in measures if not m.basic_guarantee]
    guaranteed_cost = sum(m.cost for m in guaranteed)
    if guaranteed_cost > budget:
        warnings.append(
            f"基本保障合计费用 {guaranteed_cost} 超出预算 {budget}："
            "基本保障不可取消，请追加预算或重议保障清单"
        )
    remaining = max(0, budget - guaranteed_cost)
    costs = {m.measure_id: m.cost for m in measures}
    fixed_ids = {m.measure_id for m in guaranteed}
    optional_ids = [m.measure_id for m in optional]

    total_population = sum(subject_weights.values())

    # 两种缺测情景下的入选集：观测情形 / 缺测上界情形
    prepared: dict[str, tuple] = {}
    for scenario in ("observed", "upper"):
        prepared[scenario] = _prepare(measures, subjects, subject_weights, expected_stages, scenario)

    # min_group_benefit 以“受益份额”给出，换算为各层加权个体数下限
    min_group_count: dict[str, float] = {}
    if min_group_benefit:
        sig_weights = prepared["observed"][2]
        for group, share in min_group_benefit.items():
            group_total = sum(
                w for (s_stratum, _), w in sig_weights.items() if s_stratum == group
            )
            if group_total <= 0:
                warnings.append(f"min_benefit 约束的人群层 {group!r} 无加权个体，约束被忽略")
                continue
            min_group_count[group] = share * group_total

    def optimize(scenario: str, constraints: dict[str, float] | None) -> tuple[set[str], float]:
        prep = prepared[scenario]
        return _optimize(optional_ids, fixed_ids, remaining, costs, *prep,
                         min_group_benefit=constraints)

    def has_feasible(scenario: str, constraints: dict[str, float]) -> bool:
        prep = prepared[scenario]
        probe = _optimize(optional_ids, fixed_ids, remaining, costs, *prep,
                          min_group_benefit=constraints)
        # _optimize 在不可行时会退回“仅固定集”，需显式校验
        selected = fixed_ids | probe[0]
        return all(
            _benefit(selected, *prep, group=g) >= floor
            for g, floor in constraints.items()
        )

    active_constraints: dict[str, float] | None = min_group_count or None
    if active_constraints and not has_feasible("observed", active_constraints):
        warnings.append(
            "min_benefit 约束在当前预算下不可行，已按无约束求解并在报告中标注；"
            "请追加预算或下调约束"
        )
        active_constraints = None

    scenario_selected: dict[str, list[str]] = {}
    scenario_sets: dict[str, set[str]] = {}
    for scenario in ("observed", "upper"):
        chosen, _ = optimize(scenario, active_constraints)
        scenario_sets[scenario] = chosen
        scenario_selected[scenario] = sorted(fixed_ids | chosen)

    selected = fixed_ids | scenario_sets["observed"]
    prep_obs = prepared["observed"]
    total_benefit = _benefit(selected, *prep_obs)
    spent = guaranteed_cost + sum(costs[m] for m in scenario_sets["observed"])

    # 各人群层受益份额（观测情形）
    per_group_share: dict[str, float] = {}
    strata = sorted({s.stratum for s in subjects})
    for stratum in strata:
        group_total = sum(
            w for (s_stratum, _), w in prep_obs[2].items() if s_stratum == stratum
        )
        if group_total > 0:
            per_group_share[stratum] = (
                _benefit(selected, *prep_obs, group=stratum) / group_total
            )

    # 底线人群诊断：若某保护层在全场最优下份额远低于“以该层为目标”的可行方案，
    # 而总收益损失很小，提示存在“用平均收益牺牲底线人群”的风险。
    for group in protected_groups or []:
        group_total = sum(
            w for (s_stratum, _), w in prep_obs[2].items() if s_stratum == group
        )
        if group_total <= 0:
            continue
        _, group_best = _optimize(
            optional_ids, fixed_ids, remaining, costs, *prep_obs, group=group
        )
        share_selected = _benefit(selected, *prep_obs, group=group) / group_total
        share_achievable = group_best / group_total
        if share_achievable - share_selected > 0.2:
            warnings.append(
                f"保护层 {group!r} 在当前方案受益份额 {share_selected:.0%}，"
                f"同预算下可达 {share_achievable:.0%}："
                "存在以全场平均收益牺牲底线人群的风险，请考虑 min_benefit 约束"
            )

    # 翻转检测：两种情景入选集的对称差
    flipped = scenario_sets["observed"] ^ scenario_sets["upper"]

    # 覆盖状态索引
    coverage_by_cell = {row.cell: row for row in coverage}
    stratum_status: dict[str, set[CoverageStatus]] = {}
    for row in coverage:
        stratum_status.setdefault(row.cell[2], set()).add(row.status)

    assessments: list[MeasureAssessment] = []
    followup_targets: list[FollowupTarget] = []
    for measure in measures:
        mid = measure.measure_id
        assessment = MeasureAssessment(measure_id=mid, category=Category.DEFERRED)
        if measure.basic_guarantee:
            assessment.category = Category.GUARANTEE
            assessment.reasons.append("采购方认定的不可替代基本保障，不进入是否上马的优化")
        else:
            relevant_strata = (
                strata if "全部" in measure.applies_to else list(measure.applies_to)
            )
            bad_cells = [
                s for s in relevant_strata
                if stratum_status.get(s)
                and stratum_status[s] != {CoverageStatus.SUFFICIENT}
            ]
            unstable = mid in flipped
            in_obs = mid in scenario_sets["observed"]
            in_up = mid in scenario_sets["upper"]
            if in_obs and not unstable and not bad_cells:
                assessment.category = Category.PRIORITY
                assessment.reasons.append("入选集在两种缺测情景下稳定，相关层覆盖充分")
            elif unstable or (in_obs and bad_cells) or (not in_obs and in_up):
                assessment.category = Category.FOLLOWUP
                if unstable:
                    assessment.reasons.append(
                        "在合理缺测解释下排序翻转：观测情形"
                        + ("入选" if in_obs else "未入选")
                        + "，缺测上界情形"
                        + ("入选" if in_up else "未入选")
                    )
                if bad_cells:
                    assessment.reasons.append(
                        "相关人群层覆盖不足：" + "、".join(sorted(bad_cells))
                    )
                # 定位补访目标：相关层 × 该措施问题 × 缺测/低覆盖
                for row in coverage:
                    if row.cell[2] not in relevant_strata:
                        continue
                    if row.status in (
                        CoverageStatus.LOW_RESPONSE,
                        CoverageStatus.NO_RESPONSE,
                    ):
                        for issue in measure.issues:
                            followup_targets.append(
                                FollowupTarget(
                                    cell=row.cell,
                                    issue=issue,
                                    reason=f"措施 {mid} 相关层{row.status.value}",
                                )
                            )
                    elif estimates.by_cell.get(row.cell):
                        for issue in measure.issues:
                            est = estimates.by_cell[row.cell].get(issue)
                            if est and est.missing_share > 0.2:
                                followup_targets.append(
                                    FollowupTarget(
                                        cell=row.cell,
                                        issue=issue,
                                        reason=f"措施 {mid} 相关层缺测占比 {est.missing_share:.0%}",
                                    )
                                )
            else:
                assessment.category = Category.DEFERRED
                assessment.reasons.append("预算约束下排序稳定靠后")
        if mid in selected:
            without = selected - {mid}
            assessment.marginal_benefit = total_benefit - _benefit(without, *prep_obs)
        assessments.append(assessment)

    # 去重补访目标
    seen: set[tuple] = set()
    unique_targets: list[FollowupTarget] = []
    for target in followup_targets:
        key = (target.cell, target.issue, target.reason)
        if key not in seen:
            seen.add(key)
            unique_targets.append(target)

    return SelectionResult(
        budget=budget,
        spent=spent,
        selected=sorted(selected),
        guaranteed=sorted(fixed_ids),
        total_benefit=total_benefit,
        benefit_share=(total_benefit / total_population) if total_population else 0.0,
        per_group_share=per_group_share,
        assessments=assessments,
        followup_targets=unique_targets,
        scenario_selected=scenario_selected,
        warnings=warnings,
    )
