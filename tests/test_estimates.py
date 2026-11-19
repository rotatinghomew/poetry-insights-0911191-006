"""估计：点估计/上下界的手工核对、空层不借邻层、人次换算口径。"""

import unittest

from access_analysis.estimates import estimate_issues, scale_to_visits
from access_analysis.responses import Subject

CELL_X = ("甲校", "周六早", "交通支持")
CELL_Y = ("乙校", "周日午", "常规")
STAGES = ["入口", "中段"]


def make_subject(cid, issues, *, attended=True, answered_stages=("入口", "中段"), blocking=()):
    subject = Subject(
        contact_id=cid, subject_id=cid, cell=CELL_X, stratum=CELL_X[2], attended=attended
    )
    for stage in answered_stages:
        subject.stage_records[stage] = (True, set())
    subject.issues = set(issues)
    subject.blocking_issues = set(blocking)
    subject.blocked = bool(blocking) or not attended
    return subject


class EstimateTests(unittest.TestCase):
    def setUp(self):
        # S1 报告坡道（完整）；S2 完整无问题；S3 只答入口（缺测中段）；
        # S4 未到场且因接驳受阻（妨碍参加）
        self.subjects = [
            make_subject("S1", {"坡道"}),
            make_subject("S2", set()),
            make_subject("S3", set(), answered_stages=("入口",)),
            make_subject("S4", {"接驳"}, attended=False, answered_stages=(), blocking=("接驳",)),
        ]
        self.weights = {"S1": 2.0, "S2": 2.0, "S3": 2.0, "S4": 2.0}

    def run_estimates(self, **kwargs):
        return estimate_issues(
            self.subjects,
            self.weights,
            [],
            ["坡道", "接驳"],
            STAGES,
            bootstrap=0,
            all_cells=[CELL_X, CELL_Y],
            **kwargs,
        )

    def test_point_and_bounds_hand_computed(self):
        result = self.run_estimates()
        est = result.pooled["坡道"]
        # 合格个体：S1,S2,S3（S4 未到场不计入）；o=2, z=2, m=2
        self.assertAlmostEqual(est.point, 0.5)
        self.assertAlmostEqual(est.bound_lower, 2.0 / 6.0)
        self.assertAlmostEqual(est.bound_upper, 4.0 / 6.0)
        self.assertAlmostEqual(est.missing_share, 2.0 / 6.0)

    def test_prevented_share_uses_all_subjects_denominator(self):
        result = self.run_estimates()
        est = result.pooled["接驳"]
        self.assertAlmostEqual(est.prevented_share, 2.0 / 8.0)
        self.assertAlmostEqual(est.point, 0.0)
        self.assertAlmostEqual(est.bound_upper, 2.0 / 6.0)

    def test_missing_multiplier_tightens_upper_bound(self):
        result = self.run_estimates(missing_multiplier=1.0)
        est = result.pooled["坡道"]
        # q = min(1, 1.0 × 0.5) = 0.5 → 上界 (2 + 0.5×2)/6
        self.assertAlmostEqual(est.bound_upper, 0.5)

    def test_empty_cell_gets_no_estimate_and_no_borrowing(self):
        result = self.run_estimates()
        self.assertIn(CELL_Y, result.cells_without_data)
        self.assertNotIn(CELL_Y, result.by_cell)
        # 池化估计只含有数据的层：数值与单层估计一致
        self.assertAlmostEqual(
            result.pooled["坡道"].point, result.by_cell[CELL_X]["坡道"].point
        )

    def test_scale_to_visits_never_claims_persons_without_assumption(self):
        out = scale_to_visits(0.5, 100)
        self.assertEqual(out["affected_visits"], 50.0)
        self.assertNotIn("approx_persons", out)
        out = scale_to_visits(0.5, 100, visits_per_person=2.0)
        self.assertEqual(out["approx_persons"], 25.0)
        with self.assertRaises(ValueError):
            scale_to_visits(0.5, 100, visits_per_person=0.0)


if __name__ == "__main__":
    unittest.main()
