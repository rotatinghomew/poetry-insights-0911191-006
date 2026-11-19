"""端到端：真实数据目录的完整管线、CLI 与合成数据演示。"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from access_analysis.cli import main as cli_main
from access_analysis.coverage import CoverageStatus
from access_analysis.pipeline import Params, load_data, run_analysis
from access_analysis.provenance import DecisionRecord
from access_analysis.report import render_report
from access_analysis.selection import Category

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


class PipelineOnFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.params = Params(budget=80000, bootstrap=50, seed=1)
        cls.result = run_analysis(*load_data(DATA), cls.params)

    def test_guarantee_selected_despite_absent_from_sample(self):
        # I4（无障碍咨询）样本中无人报告，但属基本保障，必须入选
        self.assertEqual(self.result.selection.guaranteed, ["I4"])
        self.assertIn("I4", self.result.selection.selected)

    def test_out_of_frame_cell_listed_without_estimates(self):
        status = {row.cell: row.status for row in self.result.coverage}
        self.assertEqual(
            status[("榆树小学", "周六午", "未覆盖区")], CoverageStatus.OUT_OF_FRAME
        )
        self.assertNotIn(("榆树小学", "周六午", "未覆盖区"), self.result.estimates.by_cell)

    def test_no_data_cells_not_filled_from_neighbors(self):
        # 松风常规有接触记录但无人回应：列入无数据层，不出估计
        self.assertIn(("松风小学", "周日午", "常规"), self.result.estimates.cells_without_data)
        for cell in self.result.estimates.cells_without_data:
            self.assertNotIn(cell, self.result.estimates.by_cell)
        # 有数据但无到场个体的层（松风交通支持只有未到场记录）：
        # 合法地无点估计，但必须给出原因，而不是借邻层填一个数
        松风交通 = self.result.estimates.by_cell[("松风小学", "周日午", "交通支持")]
        for est in 松风交通.values():
            self.assertIsNone(est.point)
            self.assertIsNotNone(est.reason)

    def test_partial_response_not_treated_as_normal(self):
        # C02 只答了入口：中段缺测必须体现在缺测占比，而非计入“无问题”
        字幕 = self.result.estimates.pooled["字幕"]
        self.assertGreater(字幕.missing_share, 0.0)
        self.assertGreater(字幕.bound_upper, 字幕.bound_lower)

    def test_prevented_participation_visible(self):
        # P03A 因接驳问题未到场：妨碍参加率必须非零
        接驳 = self.result.estimates.pooled["接驳"]
        self.assertGreater(接驳.prevented_share, 0.0)

    def test_low_coverage_blocks_priority_classification(self):
        category = {a.measure_id: a.category for a in self.result.selection.assessments}
        # 全部层有效样本量极低：任何非保障措施都不得标为“证据充分的优先改造”
        self.assertNotIn(Category.PRIORITY, category.values())
        self.assertEqual(category["I4"], Category.GUARANTEE)

    def test_report_renders_key_sections(self):
        text = render_report(self.result, self.params)
        for marker in ("覆盖诊断", "缺测", "妨碍参加", "人次", "待补访", "基本保障"):
            self.assertIn(marker, text)


class CliTests(unittest.TestCase):
    def test_run_and_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            code = cli_main([
                "run", "--data-dir", str(DATA), "--budget", "80000",
                "--out-dir", str(out_dir), "--bootstrap", "20",
            ])
            self.assertEqual(code, 0)
            self.assertTrue((out_dir / "report.md").exists())
            record = DecisionRecord.load(out_dir / "decision_record.json")
            self.assertTrue(record.undecided)  # 小样本下应有待补访事项

            comparison = Path(tmp) / "comparison.md"
            code = cli_main([
                "compare", "--round1", str(DATA), "--round2", str(DATA),
                "--budget1", "80000", "--budget2", "120000",
                "--out", str(comparison), "--bootstrap", "20",
            ])
            self.assertEqual(code, 0)
            text = comparison.read_text(encoding="utf-8")
            self.assertIn("预算约束变化", text)
            self.assertIn("为何不能作决定", text)


class SyntheticDemoTests(unittest.TestCase):
    def test_generator_and_full_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "synthetic"
            subprocess.run(
                [sys.executable, str(ROOT / "examples" / "make_synthetic.py"),
                 "--out", str(data_dir), "--seed", "7"],
                check=True, capture_output=True, text=True,
            )
            params = Params(budget=100000, bootstrap=50, seed=1,
                            protected_groups=("交通支持",))
            result = run_analysis(*load_data(data_dir), params)
            self.assertGreater(len(result.subjects), 50)
            # 框外层被识别
            status = {row.cell: row.status for row in result.coverage}
            self.assertEqual(
                status[("榆树小学", "周六午", "未覆盖区")],
                CoverageStatus.OUT_OF_FRAME,
            )
            # 低响应层被识别
            self.assertEqual(
                status[("临湖小学", "周日早", "交通支持")],
                CoverageStatus.LOW_RESPONSE,
            )
            # 基本保障入选
            self.assertIn("M6", result.selection.selected)
            text = render_report(result, params)
            self.assertIn("待补访", text)


if __name__ == "__main__":
    unittest.main()
