"""复核留痕：决策记录往返、轮次变化归因（覆盖/权重/预算）。"""

import csv
import tempfile
import unittest
from pathlib import Path

from access_analysis.pipeline import Params
from access_analysis.provenance import DecisionRecord, compare_rounds, record_from_result
from access_analysis.pipeline import load_data, run_analysis


def write_dir(root: Path, responses_rows: list[list[str]]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    with (root / "sampled_contacts.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["contact_id", "school", "slot", "stratum", "selection_probability", "invited"])
        writer.writerow(["K1", "甲校", "周六早", "常规", "0.5", "true"])
        writer.writerow(["K2", "甲校", "周六早", "常规", "0.5", "true"])
    with (root / "responses.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["response_id", "contact_id", "batch", "respondent_role",
                         "subject_id", "stage", "reached", "access_issue", "attended"])
        writer.writerows(responses_rows)
    with (root / "venue_totals.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["school", "slot", "stratum", "visit_count", "invited_count"])
        writer.writerow(["甲校", "周六早", "常规", "100", "2"])
    with (root / "improvements.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["measure_id", "site", "issue", "cost_minor", "applies_to", "basic_guarantee"])
        writer.writerow(["A", "西入口", "坡道", "30", "全部", "false"])
        writer.writerow(["B", "全馆", "字幕", "30", "全部", "false"])
    return root


R1 = ["R1", "K1", "1", "本人", "K1", "入口", "是", "坡道", "是"]
R2 = ["R2", "K2", "1", "本人", "K2", "入口", "是", "字幕", "是"]


class CompareRoundsTests(unittest.TestCase):
    def test_budget_only_change_attributed_to_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir1 = write_dir(Path(tmp) / "r1", [R1])
            dir2 = write_dir(Path(tmp) / "r2", [R1, R2])
            params1 = Params(budget=30, bootstrap=0)
            params2 = Params(budget=60, bootstrap=0)
            report = compare_rounds(dir1, dir2, params1, params2)

        steps = {step.label: step for step in report.steps}
        # 基线：预算 30 只能选 A（坡道有观测，字幕无观测）
        self.assertEqual(steps["基线（第一轮数据与预算）"].selected, ["A"])
        # 抽样框/权重未变（接触记录一致）
        self.assertEqual(steps["抽样框/权重变化"].changed, [])
        # 覆盖改善后预算仍 30：A、B 并列，平局规则下仍选 A → 无变化
        self.assertEqual(steps["样本覆盖变化"].changed, [])
        # 预算放宽到 60：B 进入入选集 → 归因于预算约束变化
        self.assertEqual(steps["预算约束变化"].changed, ["B"])

    def test_round1_undecided_reasons_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            dir1 = write_dir(Path(tmp) / "r1", [R1])
            dir2 = write_dir(Path(tmp) / "r2", [R1, R2])
            params1 = Params(budget=30, bootstrap=0)
            params2 = Params(budget=60, bootstrap=0)
            report = compare_rounds(dir1, dir2, params1, params2)
        # 有效样本量远低于阈值：第一轮 A 也是待补访，原因须留痕
        undecided_ids = {u["measure_id"] for u in report.round1_undecided}
        self.assertIn("A", undecided_ids)
        for item in report.round1_undecided:
            self.assertTrue(item["reasons"])


class DecisionRecordTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = write_dir(Path(tmp) / "r1", [R1])
            params = Params(budget=30, bootstrap=0)
            result = run_analysis(*load_data(data_dir), params)
            record = record_from_result(result, params, data_dir)
            path = Path(tmp) / "record.json"
            record.save(path)
            loaded = DecisionRecord.load(path)
        self.assertEqual(loaded.selected, record.selected)
        self.assertEqual(loaded.undecided, record.undecided)
        self.assertEqual(loaded.params["budget"], 30)
        self.assertIn("responses.csv", loaded.data_hashes)


if __name__ == "__main__":
    unittest.main()
