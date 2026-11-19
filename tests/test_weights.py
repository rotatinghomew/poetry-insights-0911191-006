"""权重：框外排除、逆概率、层内无回应调整、有效样本量。"""

import unittest

from access_analysis.schema import Contact
from access_analysis.weights import compute_weights


def contact(cid, cell, p, invited=True):
    return Contact(
        contact_id=cid,
        school=cell[0],
        slot=cell[1],
        stratum=cell[2],
        selection_probability=p,
        invited=invited,
    )


X = ("甲校", "周六早", "交通支持")
Y = ("甲校", "周六早", "常规")
Z = ("乙校", "周日午", "未覆盖区")


class WeightTests(unittest.TestCase):
    def setUp(self):
        self.contacts = [
            contact("A1", X, 0.5),
            contact("A2", X, 0.5),
            contact("A3", X, 0.5),
            contact("B1", Y, 0.25),
            contact("C1", Z, 0.0, invited=False),
        ]

    def test_out_of_frame_excluded_from_weights(self):
        report = compute_weights(self.contacts, {"A1", "A2"})
        self.assertEqual([c.contact_id for c in report.out_of_frame], ["C1"])
        self.assertNotIn("C1", report.weights)

    def test_base_weight_and_nonresponse_adjustment(self):
        report = compute_weights(self.contacts, {"A1", "A2"})
        # 层 X：基础权重和 6，回应者基础权重和 4，倾向 2/3，调整权重 2/(2/3)=3
        self.assertAlmostEqual(report.weights["A1"], 3.0)
        self.assertAlmostEqual(report.weights["A2"], 3.0)
        info = report.cells[X]
        self.assertAlmostEqual(info.response_propensity, 2.0 / 3.0)
        self.assertAlmostEqual(info.ess, 2.0)  # (3+3)^2 / (9+9)

    def test_no_response_cell_gets_no_estimate(self):
        report = compute_weights(self.contacts, {"A1", "A2"})
        # 层 Y 有邀请但零回应：不出权重，列入无回应层
        self.assertNotIn("B1", report.weights)
        self.assertIn(Y, report.no_response_cells)

    def test_not_invited_tracked_separately(self):
        contacts = self.contacts + [contact("A4", X, 0.5, invited=False)]
        report = compute_weights(contacts, {"A1", "A2"})
        self.assertEqual([c.contact_id for c in report.not_invited], ["A4"])


if __name__ == "__main__":
    unittest.main()
