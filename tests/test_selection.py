"""遴选：并集收益不重复计、基本保障强制、缺测翻转、底线人群保护。"""

import unittest

from access_analysis.coverage import CoverageRow, CoverageStatus
from access_analysis.estimates import EstimateSet, Estimand
from access_analysis.responses import Subject
from access_analysis.schema import Measure
from access_analysis.selection import (
    Category,
    _benefit,
    _prepare,
    select_measures,
)

STAGES = ["入口", "中段"]
T = "交通支持"
U = "常规"


def subject(cid, stratum, issues, *, complete=True):
    s = Subject(
        contact_id=cid,
        subject_id=cid,
        cell=("校", "场", stratum),
        stratum=stratum,
        attended=True,
    )
    s.issues = set(issues)
    if complete:
        s.stage_records = {stage: (True, set()) for stage in STAGES}
    return s


def measure(mid, issues, cost, applies=("全部",), guarantee=False):
    return Measure(
        measure_id=mid,
        sites=("点位",),
        issues=tuple(issues),
        cost=cost,
        applies_to=tuple(applies),
        basic_guarantee=guarantee,
    )


def coverage_sufficient():
    return [
        CoverageRow(cell=("校", "场", T), status=CoverageStatus.SUFFICIENT),
        CoverageRow(cell=("校", "场", U), status=CoverageStatus.SUFFICIENT),
    ]


def empty_estimates():
    return EstimateSet(estimand=Estimand.PER_PERSON, expected_stages=STAGES)


class UnionBenefitTests(unittest.TestCase):
    def test_same_person_counted_once_across_measures(self):
        measures = [measure("A", ["坡道"], 30), measure("B", ["字幕"], 30)]
        subjects = [
            subject("K1", U, {"坡道", "字幕"}),
            subject("K2", U, {"坡道"}),
        ]
        weights = {("K1", "K1"): 1.0, ("K2", "K2"): 1.0}
        masks, strata, sig = _prepare(measures, subjects, weights, STAGES, "observed")
        # K1 同时被两项措施覆盖，只计一次：并集 = 2，而非 2+1=3
        self.assertEqual(_benefit({"A", "B"}, masks, strata, sig), 2.0)


class GuaranteeTests(unittest.TestCase):
    def test_guarantee_forced_even_without_observed_benefit(self):
        measures = [
            measure("G", ["罕见问题"], 10, guarantee=True),
            measure("A", ["坡道"], 30),
        ]
        subjects = [subject("K1", U, {"坡道"})]
        weights = {("K1", "K1"): 1.0}
        result = select_measures(
            measures, subjects, weights, STAGES, 10,
            coverage_sufficient(), empty_estimates(),
        )
        self.assertIn("G", result.selected)
        self.assertNotIn("A", result.selected)  # 预算被保障占用后不足
        category = {a.measure_id: a.category for a in result.assessments}
        self.assertEqual(category["G"], Category.GUARANTEE)

    def test_guarantee_over_budget_warns_but_not_cancelled(self):
        measures = [measure("G", ["罕见问题"], 100, guarantee=True)]
        result = select_measures(
            measures, [subject("K1", U, set())], {("K1", "K1"): 1.0},
            STAGES, 50, coverage_sufficient(), empty_estimates(),
        )
        self.assertIn("G", result.selected)
        self.assertTrue(any("超出预算" in w for w in result.warnings))


class FlipTests(unittest.TestCase):
    """缺测上界情形改变入选集 → 措施列为待补访。"""

    def setUp(self):
        # S3/S4 缺测：上界情形下分别可能患有本层任何可被解决的问题
        self.subjects = [
            subject("S1", T, {"坡道"}),
            subject("S2", T, {"字幕"}),
            subject("S3", T, set(), complete=False),
            subject("S4", U, set(), complete=False),
        ]
        self.weights = {(s, s): 1.0 for s in ("S1", "S2", "S3", "S4")}
        self.measures = [
            measure("A", ["坡道"], 30, applies=(T,)),
            measure("B", ["字幕"], 30, applies=("全部",)),
        ]

    def test_ranking_flips_under_plausible_missingness(self):
        result = select_measures(
            self.measures, self.subjects, self.weights, STAGES, 30,
            coverage_sufficient(), empty_estimates(),
        )
        # 观测情形：A、B 收益并列，A 先入选；上界情形：B 覆盖 S2+S3+S4 = 3 > A 的 2
        self.assertIn("A", result.scenario_selected["observed"])
        self.assertIn("B", result.scenario_selected["upper"])
        self.assertNotIn("A", result.scenario_selected["upper"])
        category = {a.measure_id: a.category for a in result.assessments}
        self.assertEqual(category["A"], Category.FOLLOWUP)
        self.assertEqual(category["B"], Category.FOLLOWUP)
        self.assertTrue(
            any("翻转" in r for a in result.assessments for r in a.reasons)
        )

    def test_stable_selection_with_coverage_is_priority(self):
        subjects = [subject("S1", T, {"坡道"})]
        weights = {("S1", "S1"): 1.0}
        measures = [measure("A", ["坡道"], 10, applies=(T,))]
        result = select_measures(
            measures, subjects, weights, STAGES, 10,
            coverage_sufficient(), empty_estimates(),
        )
        category = {a.measure_id: a.category for a in result.assessments}
        self.assertEqual(category["A"], Category.PRIORITY)

    def test_low_coverage_selection_is_followup_not_priority(self):
        subjects = [subject("S1", T, {"坡道"})]
        weights = {("S1", "S1"): 1.0}
        measures = [measure("A", ["坡道"], 10, applies=(T,))]
        coverage = [
            CoverageRow(cell=("校", "场", T), status=CoverageStatus.LOW_RESPONSE),
        ]
        result = select_measures(
            measures, subjects, weights, STAGES, 10, coverage, empty_estimates()
        )
        category = {a.measure_id: a.category for a in result.assessments}
        self.assertEqual(category["A"], Category.FOLLOWUP)
        self.assertTrue(result.followup_targets)


class ProtectedGroupTests(unittest.TestCase):
    def setUp(self):
        # 交通支持层 10 人患坡道；常规层 20 人患字幕；预算只够一项
        self.subjects = [subject(f"T{i}", T, {"坡道"}) for i in range(10)]
        self.subjects += [subject(f"U{i}", U, {"字幕"}) for i in range(20)]
        self.weights = {(s.contact_id, s.contact_id): 1.0 for s in self.subjects}
        self.measures = [
            measure("A", ["坡道"], 50, applies=(T,)),
            measure("B", ["字幕"], 50, applies=("全部",)),
        ]

    def test_average_benefit_sacrificing_baseline_group_warns(self):
        result = select_measures(
            self.measures, self.subjects, self.weights, STAGES, 50,
            coverage_sufficient(), empty_estimates(), protected_groups=[T],
        )
        self.assertIn("B", result.selected)  # 全场平均口径选 B
        self.assertTrue(any("底线人群" in w for w in result.warnings))

    def test_min_benefit_constraint_protects_baseline_group(self):
        result = select_measures(
            self.measures, self.subjects, self.weights, STAGES, 50,
            coverage_sufficient(), empty_estimates(),
            min_group_benefit={T: 0.5},
        )
        self.assertIn("A", result.selected)
        self.assertNotIn("B", result.selected)

    def test_infeasible_constraint_falls_back_with_warning(self):
        result = select_measures(
            self.measures, self.subjects, self.weights, STAGES, 50,
            coverage_sufficient(), empty_estimates(),
            min_group_benefit={T: 2.0},  # 份额 200% 不可行
        )
        self.assertTrue(any("不可行" in w for w in result.warnings))


class BudgetTests(unittest.TestCase):
    def test_selection_respects_budget(self):
        measures = [
            measure("A", ["坡道"], 30),
            measure("B", ["字幕"], 30),
        ]
        subjects = [subject("K1", U, {"坡道", "字幕"})]
        weights = {("K1", "K1"): 1.0}
        result = select_measures(
            measures, subjects, weights, STAGES, 30,
            coverage_sufficient(), empty_estimates(),
        )
        self.assertLessEqual(result.spent, 30)
        self.assertEqual(len([a for a in result.assessments
                              if a.measure_id in result.selected]), 1)


if __name__ == "__main__":
    unittest.main()
