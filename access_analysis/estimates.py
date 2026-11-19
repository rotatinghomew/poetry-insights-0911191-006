"""加权估计：问题发生率、妨碍参加率、缺测上下界与不确定性。

口径定义（估计前必须明确）：
- PER_PERSON（默认）：以可识别个体为单位。台账人次不得充当个体数，
  因此个体口径只输出比例；如需换算人数，必须显式给出人均到访次数假设。
- PER_VISIT：以人次为单位，可用台账人次做标注性换算，输出必须标注“人次”。

缺测处理：未作答阶段只进入 Manski 式上下界（或用户给定的缺测倍率），
点估计仅基于完整观测；未到达（reached=否）是真实结果，不计缺测。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from .responses import Subject
from .schema import Contact


class Estimand(Enum):
    PER_PERSON = "可识别个体口径"
    PER_VISIT = "人次口径"


@dataclass
class IssueEstimate:
    """单个问题在某一范围（总体或某层）的估计。"""

    issue: str
    scope: str  # “总体”或 “学校/时段/人群”
    cell: tuple[str, str, str] | None
    n_subjects: int = 0
    ess: float = 0.0
    point: float | None = None  # 完整观测例的加权发生率
    ci: tuple[float, float] | None = None  # 自助法百分位置信区间
    bound_lower: float | None = None  # 缺测全部按“未受影响”
    bound_upper: float | None = None  # 缺测全部按“受影响”（或按缺测倍率截断）
    prevented_share: float = 0.0  # 妨碍参加率：因该问题未能到场/未能继续
    missing_share: float = 0.0  # 合格个体中缺测占比
    reason: str | None = None  # 无估计时的原因（如“该层无回应，未借用邻层”）


@dataclass
class EstimateSet:
    """一轮估计结果。"""

    estimand: Estimand
    expected_stages: list[str]
    pooled: dict[str, IssueEstimate] = field(default_factory=dict)
    by_cell: dict[tuple[str, str, str], dict[str, IssueEstimate]] = field(
        default_factory=dict
    )
    cells_without_data: list[tuple[str, str, str]] = field(default_factory=list)
    blocked_share: float = 0.0  # 总体被阻断（未到场或中途未能继续）比例
    bootstrap_replicates: int = 0


def _subject_components(
    subjects: list[Subject],
    weights: dict[tuple[str, str], float],
    issue: str,
    expected_stages: list[str],
) -> tuple[float, float, float, float, float]:
    """返回 (o, z, m, prevented, total) 五个权重和。

    o：已报告该问题；z：完整观测且未报告；m：缺测（未作答/被阻断后不可观测）；
    prevented：因该问题被阻断（妨碍参加）；total：范围内全部个体权重和。
    """
    o = z = m = prevented = total = 0.0
    for subject in subjects:
        w = weights.get(subject.key)
        if not w:
            continue  # 自助复制品中未被抽中的个体
        total += w
        if issue in subject.blocking_issues:
            prevented += w
        if subject.attended is False:
            # 未到场者不计入“到场者体验”分母；其阻断问题已计入 prevented。
            continue
        if issue in subject.issues:
            o += w
        elif subject.missing_stages(expected_stages) or subject.blocked:
            m += w
        else:
            z += w
    return o, z, m, prevented, total


def _finalize(
    issue: str,
    scope: str,
    cell: tuple[str, str, str] | None,
    n_subjects: int,
    ess: float,
    components: tuple[float, float, float, float, float],
    missing_multiplier: float | None,
) -> IssueEstimate:
    o, z, m, prevented, total = components
    estimate = IssueEstimate(
        issue=issue, scope=scope, cell=cell, n_subjects=n_subjects, ess=ess
    )
    if total <= 0:
        estimate.reason = "该范围无可加权个体"
        return estimate
    estimate.prevented_share = prevented / total
    eligible = o + z + m
    if eligible <= 0:
        estimate.reason = "该范围无到场个体"
        return estimate
    estimate.missing_share = m / eligible
    observed = o + z
    if observed <= 0:
        estimate.reason = "该范围无完整观测，不出点估计（未借用邻层）"
        estimate.bound_lower = 0.0
        estimate.bound_upper = 1.0
        return estimate
    estimate.point = o / observed
    estimate.bound_lower = o / eligible
    if missing_multiplier is None:
        estimate.bound_upper = (o + m) / eligible
    else:
        # 合理缺测解释：缺测者受影响率不超过观测发生率的 missing_multiplier 倍
        q = min(1.0, missing_multiplier * estimate.point)
        estimate.bound_upper = (o + q * m) / eligible
    return estimate


def estimate_issues(
    subjects: list[Subject],
    contact_weights: dict[str, float],
    contacts: list[Contact],
    issues: list[str],
    expected_stages: list[str],
    *,
    estimand: Estimand = Estimand.PER_PERSON,
    missing_multiplier: float | None = None,
    bootstrap: int = 400,
    seed: int = 20261118,
    all_cells: list[tuple[str, str, str]] | None = None,
) -> EstimateSet:
    """对每个问题做总体与分层估计。

    contact_weights：contact_id -> 调整权重；被评价人继承其接触的权重。
    contacts：全部接触记录（自助法需重抽受邀接触并重算回应倾向）。
    all_cells：抽样设计涉及的全部层，用于显式列出“无数据层”。
    """
    # 个体口径下，每个被评价人继承其接触的调整权重。
    subject_weights: dict[tuple[str, str], float] = {}
    for subject in subjects:
        weight = contact_weights.get(subject.contact_id)
        if weight is not None:
            subject_weights[subject.key] = weight
    weighted_subjects = [s for s in subjects if s.key in subject_weights]

    result = EstimateSet(estimand=estimand, expected_stages=list(expected_stages))

    cells_with_data = sorted({s.cell for s in weighted_subjects})
    if all_cells is not None:
        result.cells_without_data = [c for c in all_cells if c not in cells_with_data]

    # 总体被阻断比例
    total_w = sum(subject_weights[s.key] for s in weighted_subjects)
    if total_w > 0:
        result.blocked_share = (
            sum(subject_weights[s.key] for s in weighted_subjects if s.blocked) / total_w
        )

    # 分层点估计与上下界（无回应层不出现在结果中——不借邻层均值）
    subjects_by_cell: dict[tuple[str, str, str], list[Subject]] = {}
    for subject in weighted_subjects:
        subjects_by_cell.setdefault(subject.cell, []).append(subject)
    for cell, cell_subjects in sorted(subjects_by_cell.items()):
        cell_ess = _ess([subject_weights[s.key] for s in cell_subjects])
        per_issue: dict[str, IssueEstimate] = {}
        for issue in issues:
            components = _subject_components(
                cell_subjects, subject_weights, issue, expected_stages
            )
            per_issue[issue] = _finalize(
                issue,
                scope="/".join(cell),
                cell=cell,
                n_subjects=len(cell_subjects),
                ess=cell_ess,
                components=components,
                missing_multiplier=missing_multiplier,
            )
        result.by_cell[cell] = per_issue

    # 总体估计 + 自助法置信区间（按层内整群重抽接触记录）
    pooled_ess = _ess([subject_weights[s.key] for s in weighted_subjects])
    for issue in issues:
        components = _subject_components(
            weighted_subjects, subject_weights, issue, expected_stages
        )
        estimate = _finalize(
            issue,
            scope="总体",
            cell=None,
            n_subjects=len(weighted_subjects),
            ess=pooled_ess,
            components=components,
            missing_multiplier=missing_multiplier,
        )
        result.pooled[issue] = estimate

    if bootstrap > 0 and weighted_subjects:
        _bootstrap_cis(
            result,
            subjects=weighted_subjects,
            contacts=contacts,
            issues=issues,
            expected_stages=expected_stages,
            missing_multiplier=missing_multiplier,
            replicates=bootstrap,
            seed=seed,
        )
        result.bootstrap_replicates = bootstrap
    return result


def _ess(weights: list[float]) -> float:
    sum_w = sum(weights)
    sum_w2 = sum(w * w for w in weights)
    return (sum_w * sum_w / sum_w2) if sum_w2 > 0 else 0.0


def _bootstrap_cis(
    result: EstimateSet,
    *,
    subjects: list[Subject],
    contacts: list[Contact],
    issues: list[str],
    expected_stages: list[str],
    missing_multiplier: float | None,
    replicates: int,
    seed: int,
) -> None:
    """层内整群自助法：重抽受邀接触（含未回应者），逐次重算回应倾向与权重。"""
    rng = random.Random(seed)
    subjects_by_contact: dict[str, list[Subject]] = {}
    for subject in subjects:
        subjects_by_contact.setdefault(subject.contact_id, []).append(subject)
    responded_ids = set(subjects_by_contact)
    invited_by_cell: dict[tuple[str, str, str], list[Contact]] = {}
    for contact in contacts:
        if contact.in_frame and contact.invited:
            invited_by_cell.setdefault(contact.cell, []).append(contact)

    draws: dict[str, list[float]] = {issue: [] for issue in issues}
    for _ in range(replicates):
        replicate_weights: dict[tuple[str, str], float] = {}
        for cell, invited in invited_by_cell.items():
            drawn = [rng.choice(invited) for _ in range(len(invited))]
            base_sum = sum(1.0 / c.selection_probability for c in drawn)
            responded = [c for c in drawn if c.contact_id in responded_ids]
            responded_sum = sum(1.0 / c.selection_probability for c in responded)
            if responded_sum <= 0:
                continue  # 复制品中该层无回应：该层本轮不出估计
            propensity = responded_sum / base_sum
            for contact in responded:
                weight = (1.0 / contact.selection_probability) / propensity
                for subject in subjects_by_contact[contact.contact_id]:
                    replicate_weights[subject.key] = (
                        replicate_weights.get(subject.key, 0.0) + weight
                    )
        for issue in issues:
            components = _subject_components(
                subjects, replicate_weights, issue, expected_stages
            )
            estimate = _finalize(
                issue,
                scope="总体",
                cell=None,
                n_subjects=0,
                ess=0.0,
                components=components,
                missing_multiplier=missing_multiplier,
            )
            if estimate.point is not None:
                draws[issue].append(estimate.point)

    for issue, values in draws.items():
        pooled = result.pooled[issue]
        if pooled.point is None or len(values) < 20:
            continue
        values.sort()
        lo = values[int(0.025 * (len(values) - 1))]
        hi = values[int(0.975 * (len(values) - 1))]
        pooled.ci = (lo, hi)


def scale_to_visits(
    proportion: float,
    visit_count: int,
    visits_per_person: float | None = None,
) -> dict[str, float | str]:
    """把比例换算为规模，返回带口径标注的字典。

    - 默认只给“受影响人次”（人次口径），不声称是可识别个体数。
    - 仅当显式给出人均到访次数假设时才给“约相当于人数”，并标注为假设依赖。
    """
    affected_visits = proportion * visit_count
    out: dict[str, float | str] = {
        "affected_visits": affected_visits,
        "basis": "人次口径（台账总量×比例），非可识别个体数",
    }
    if visits_per_person is not None:
        if visits_per_person <= 0:
            raise ValueError("人均到访次数必须为正")
        out["approx_persons"] = affected_visits / visits_per_person
        out["basis"] += f"；人数为假设依赖换算（人均到访 {visits_per_person} 次）"
    return out
