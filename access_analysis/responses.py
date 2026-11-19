"""答卷整理：把答卷行合并为“被评价人”分析单元，并区分缺测与真实结果。

规则：
- 分析单元 = (contact_id, subject_id)。同一单元的多行（本人作答、本人补答、
  陪同者就同一人补答）合并；同一 contact_id 下不同 subject_id 是代答的
  另一人，作为独立单元，但继承该接触的入样权重（见模块末尾假设说明）。
- reached=否 / attended=否 是真实结果（被阻断、未到场），不是缺测；
  若该行报告了问题，该问题计入“妨碍参加”。
- 应答而未答的阶段（空白）是缺测：只进入估计的上下界，绝不当作“无问题”。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import Contact, ResponseRow


@dataclass
class Subject:
    """一个被评价人（分析单元）。"""

    contact_id: str
    subject_id: str
    cell: tuple[str, str, str]
    stratum: str
    attended: bool | None = None
    # stage -> (reached, issues)；reached=None 表示该阶段缺测
    stage_records: dict[str, tuple[bool | None, set[str]]] = field(default_factory=dict)
    issues: set[str] = field(default_factory=set)  # 全部已报告问题的并集
    blocking_issues: set[str] = field(default_factory=set)  # 妨碍参加的问题
    blocked: bool = False  # 是否出现“未到达/未到场”的确认记录
    proxy_only: bool = False  # 是否全部来自陪同者代答

    @property
    def key(self) -> tuple[str, str]:
        return (self.contact_id, self.subject_id)

    def missing_stages(self, expected_stages: list[str]) -> set[str]:
        """应答而缺测的阶段：本人到场且未被阻断，但某阶段无任何记录或记录为空白。"""
        if self.attended is False or self.blocked:
            # 未到场/被阻断者的后续阶段属于结构性不可观测，不计缺测。
            return set()
        missing: set[str] = set()
        for stage in expected_stages:
            record = self.stage_records.get(stage)
            if record is None or record[0] is None:
                missing.add(stage)
        return missing


@dataclass
class SubjectBuildResult:
    subjects: list[Subject]
    responded_contact_ids: set[str]
    warnings: list[str] = field(default_factory=list)
    merged_rows: int = 0  # 被合并的补答行数（用于复核）


def build_subjects(
    contacts: list[Contact],
    rows: list[ResponseRow],
) -> SubjectBuildResult:
    """把答卷行整理为被评价人列表。"""
    contacts_by_id = {c.contact_id: c for c in contacts}
    subjects: dict[tuple[str, str], Subject] = {}
    warnings: list[str] = []
    merged_rows = 0

    for row in rows:
        contact = contacts_by_id.get(row.contact_id)
        if contact is None:
            warnings.append(
                f"答卷 {row.response_id} 引用了不存在的接触 {row.contact_id}，已忽略"
            )
            continue
        key = (row.contact_id, row.subject_id)
        subject = subjects.get(key)
        if subject is None:
            subject = Subject(
                contact_id=row.contact_id,
                subject_id=row.subject_id,
                cell=contact.cell,
                stratum=contact.stratum,
                proxy_only=row.respondent_role != "本人",
            )
            subjects[key] = subject
        else:
            merged_rows += 1  # 同一被评价人的补充作答：合并而非新增个体
            if row.respondent_role == "本人":
                subject.proxy_only = False

        if row.attended is not None:
            if subject.attended is not None and subject.attended != row.attended:
                warnings.append(
                    f"答卷 {row.response_id} 与既有记录在“是否到场”上不一致"
                    f"（{row.contact_id}/{row.subject_id}），按未到场优先处理"
                )
                subject.attended = False
            else:
                subject.attended = row.attended

        existing = subject.stage_records.get(row.stage)
        if existing is not None and existing[0] is not None and row.reached is not None:
            if existing[0] != row.reached:
                warnings.append(
                    f"答卷 {row.response_id} 与既有记录在阶段 {row.stage} 的"
                    f"“是否到达”上不一致，问题取并集、到达取“否”优先"
                )
        if existing is None:
            subject.stage_records[row.stage] = (row.reached, set(row.issues))
        else:
            reached = existing[0]
            if reached is None:
                reached = row.reached
            elif row.reached is False:
                reached = False
            subject.stage_records[row.stage] = (reached, existing[1] | set(row.issues))

    # 汇总问题集合与阻断标记
    for subject in subjects.values():
        for stage, (reached, issues) in subject.stage_records.items():
            subject.issues |= issues
            if reached is False:
                subject.blocked = True
                subject.blocking_issues |= issues
        if subject.attended is False:
            subject.blocked = True
            # 未到场行报告的问题同样属于“妨碍参加”
            for reached, issues in subject.stage_records.values():
                subject.blocking_issues |= issues

    return SubjectBuildResult(
        subjects=list(subjects.values()),
        responded_contact_ids={s.contact_id for s in subjects.values()},
        warnings=warnings,
        merged_rows=merged_rows,
    )
