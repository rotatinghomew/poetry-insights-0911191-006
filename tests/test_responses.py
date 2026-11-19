"""答卷整理：代答/补答区分、缺测与未到达的区分。"""

import unittest

from access_analysis.responses import build_subjects
from access_analysis.schema import Contact, ResponseRow


def contact(cid):
    return Contact(
        contact_id=cid,
        school="甲校",
        slot="周六早",
        stratum="交通支持",
        selection_probability=0.5,
        invited=True,
    )


def row(rid, cid, role, sid, stage, reached, issues, attended):
    return ResponseRow(
        response_id=rid,
        contact_id=cid,
        batch="1",
        respondent_role=role,
        subject_id=sid,
        stage=stage,
        reached=reached,
        issues=tuple(issues),
        attended=attended,
    )


class SubjectBuildTests(unittest.TestCase):
    def setUp(self):
        self.contacts = [contact("C1"), contact("C2"), contact("C3")]
        rows = [
            # 本人作答入口
            row("R1", "C1", "本人", "C1", "入口", True, ["坡道"], True),
            # 陪同者就同一人补答中段：应合并，不得新增个体
            row("R2", "C1", "陪同者", "C1", "中段", True, ["字幕"], True),
            # 陪同者代答另一同行者：独立分析单元
            row("R3", "C1", "陪同者", "C1-X", "入口", True, [], True),
            # 中途被阻断：reached=否 是真实结果
            row("R4", "C2", "本人", "C2", "中段", False, ["接驳"], True),
            # 未到场：妨碍参加
            row("R5", "C3", "陪同者", "P", "入口", False, ["坡道"], False),
        ]
        self.result = build_subjects(self.contacts, rows)

    def test_proxy_supplement_merged_not_double_counted(self):
        c1_subjects = [s for s in self.result.subjects if s.contact_id == "C1"]
        self.assertEqual(len(c1_subjects), 2)  # C1 本人 + 代答的另一人
        self.assertEqual(self.result.merged_rows, 1)
        main = next(s for s in c1_subjects if s.subject_id == "C1")
        self.assertEqual(main.issues, {"坡道", "字幕"})

    def test_proxy_for_other_is_separate_unit(self):
        other = next(s for s in self.result.subjects if s.subject_id == "C1-X")
        self.assertTrue(other.proxy_only)
        self.assertEqual(other.issues, set())

    def test_blocked_is_outcome_not_missing(self):
        blocked = next(s for s in self.result.subjects if s.contact_id == "C2")
        self.assertTrue(blocked.blocked)
        self.assertEqual(blocked.blocking_issues, {"接驳"})
        # 被阻断者的后续阶段属结构性不可观测，不计缺测
        self.assertEqual(blocked.missing_stages(["入口", "中段", "后半程"]), set())

    def test_non_attendee_counts_as_prevented(self):
        prevented = next(s for s in self.result.subjects if s.subject_id == "P")
        self.assertFalse(prevented.attended)
        self.assertEqual(prevented.blocking_issues, {"坡道"})

    def test_partial_response_flags_missing_stages(self):
        main = next(s for s in self.result.subjects if s.subject_id == "C1")
        self.assertEqual(main.missing_stages(["入口", "中段", "后半程"]), {"后半程"})

    def test_unknown_contact_warned_and_skipped(self):
        rows = [row("R9", "C9", "本人", "C9", "入口", True, [], True)]
        result = build_subjects(self.contacts, rows)
        self.assertEqual(result.subjects, [])
        self.assertTrue(any("C9" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
