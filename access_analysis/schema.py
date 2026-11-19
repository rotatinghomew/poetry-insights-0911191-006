"""数据模式：接触记录、答卷、场馆台账、改善措施。

所有 CSV 均为 UTF-8；空字段表示“未提供”，与“否/无”严格区分。
层（cell）的唯一定义为 (school, slot, stratum) 三元组，即“人群 × 场次”。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

# 答卷中“陪同者代答”与“本人补答”的区分完全由 subject_id 承载：
# 同一 (contact_id, subject_id) 的多行视为同一被评价人的补充作答（合并）；
# 同一 contact_id 下不同 subject_id 视为代答的另一人（独立分析单元）。
ROLE_SELF = "本人"
ROLE_COMPANION = "陪同者"

NO_ISSUE = "无"


@dataclass(frozen=True)
class Contact:
    """一条抽样接触记录。selection_probability 为邀请时可得的入样概率。"""

    contact_id: str
    school: str
    slot: str
    stratum: str
    selection_probability: float
    invited: bool

    @property
    def cell(self) -> tuple[str, str, str]:
        return (self.school, self.slot, self.stratum)

    @property
    def in_frame(self) -> bool:
        """是否属于抽样框可覆盖范围。概率为 0 的层在框外，不得加权推断。"""
        return self.selection_probability > 0.0


@dataclass(frozen=True)
class ResponseRow:
    """一行答卷记录（一个被评价人 × 一个体验阶段）。

    reached 为 None 表示该阶段未作答（缺测）；reached=False 表示确认未到达
    （是一个真实结果，例如被无障碍问题阻断），二者不得混同。
    """

    response_id: str
    contact_id: str
    batch: str
    respondent_role: str
    subject_id: str
    stage: str
    reached: bool | None
    issues: tuple[str, ...]
    attended: bool | None


@dataclass(frozen=True)
class VenueTotal:
    """场馆台账行。visit_count 为分层人次总量，不是可识别个体数。"""

    school: str
    slot: str
    stratum: str
    visit_count: int
    invited_count: int

    @property
    def cell(self) -> tuple[str, str, str]:
        return (self.school, self.slot, self.stratum)


@dataclass(frozen=True)
class Measure:
    """一项候选改善措施。可同时解决多个环节/问题（issues 为集合）。

    basic_guarantee=True 表示采购方认定的不可替代基本保障：
    不进入“是否上马”的优化，直接计入必做清单并先行占用预算。
    """

    measure_id: str
    sites: tuple[str, ...]
    issues: tuple[str, ...]
    cost: int
    applies_to: tuple[str, ...]  # 适用人群层（stratum 名称）或 ("全部",)
    basic_guarantee: bool = False

    def applies_to_stratum(self, stratum: str) -> bool:
        return "全部" in self.applies_to or stratum in self.applies_to


def _require(row: dict[str, str], key: str, path: Path, lineno: int) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise ValueError(f"{path.name} 第 {lineno} 行缺少必填字段 {key!r}")
    return value


def _optional(row: dict[str, str], key: str) -> str | None:
    value = (row.get(key) or "").strip()
    return value or None


def _parse_bool(value: str | None, *, path: Path, lineno: int, key: str) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"是", "true", "1", "y", "yes"}:
        return True
    if lowered in {"否", "false", "0", "n", "no"}:
        return False
    raise ValueError(f"{path.name} 第 {lineno} 行字段 {key!r} 无法解析为是/否：{value!r}")


def _split_list(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _read_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"{path.name} 缺少表头")
        return [(i, row) for i, row in enumerate(reader, start=2)]


def load_contacts(path: str | Path) -> list[Contact]:
    path = Path(path)
    contacts: list[Contact] = []
    seen: set[str] = set()
    for lineno, row in _read_rows(path):
        contact_id = _require(row, "contact_id", path, lineno)
        if contact_id in seen:
            raise ValueError(f"{path.name} 第 {lineno} 行 contact_id 重复：{contact_id}")
        seen.add(contact_id)
        try:
            probability = float(_require(row, "selection_probability", path, lineno))
        except ValueError as exc:
            raise ValueError(
                f"{path.name} 第 {lineno} 行 selection_probability 不是数值"
            ) from exc
        if not 0.0 <= probability <= 1.0:
            raise ValueError(
                f"{path.name} 第 {lineno} 行 selection_probability 超出 [0,1]"
            )
        invited = _parse_bool(
            _optional(row, "invited"), path=path, lineno=lineno, key="invited"
        )
        contacts.append(
            Contact(
                contact_id=contact_id,
                school=_require(row, "school", path, lineno),
                slot=_require(row, "slot", path, lineno),
                stratum=_require(row, "stratum", path, lineno),
                selection_probability=probability,
                invited=bool(invited),
            )
        )
    return contacts


def load_responses(path: str | Path) -> list[ResponseRow]:
    path = Path(path)
    rows: list[ResponseRow] = []
    seen: set[str] = set()
    for lineno, row in _read_rows(path):
        response_id = _require(row, "response_id", path, lineno)
        if response_id in seen:
            raise ValueError(f"{path.name} 第 {lineno} 行 response_id 重复：{response_id}")
        seen.add(response_id)
        issues = tuple(
            issue for issue in _split_list(_optional(row, "access_issue")) if issue != NO_ISSUE
        )
        rows.append(
            ResponseRow(
                response_id=response_id,
                contact_id=_require(row, "contact_id", path, lineno),
                batch=_optional(row, "batch") or "",
                respondent_role=_require(row, "respondent_role", path, lineno),
                subject_id=_require(row, "subject_id", path, lineno),
                stage=_require(row, "stage", path, lineno),
                reached=_parse_bool(
                    _optional(row, "reached"), path=path, lineno=lineno, key="reached"
                ),
                issues=issues,
                attended=_parse_bool(
                    _optional(row, "attended"), path=path, lineno=lineno, key="attended"
                ),
            )
        )
    return rows


def load_venue_totals(path: str | Path) -> list[VenueTotal]:
    path = Path(path)
    totals: list[VenueTotal] = []
    for lineno, row in _read_rows(path):
        try:
            visit_count = int(_require(row, "visit_count", path, lineno))
            invited_count = int(_require(row, "invited_count", path, lineno))
        except ValueError as exc:
            raise ValueError(f"{path.name} 第 {lineno} 行人次/邀请数不是整数") from exc
        totals.append(
            VenueTotal(
                school=_require(row, "school", path, lineno),
                slot=_require(row, "slot", path, lineno),
                stratum=_require(row, "stratum", path, lineno),
                visit_count=visit_count,
                invited_count=invited_count,
            )
        )
    return totals


def load_measures(path: str | Path) -> list[Measure]:
    path = Path(path)
    measures: list[Measure] = []
    seen: set[str] = set()
    for lineno, row in _read_rows(path):
        measure_id = _require(row, "measure_id", path, lineno)
        if measure_id in seen:
            raise ValueError(f"{path.name} 第 {lineno} 行 measure_id 重复：{measure_id}")
        seen.add(measure_id)
        issues = _split_list(_optional(row, "issue"))
        if not issues:
            raise ValueError(f"{path.name} 第 {lineno} 行 measure 至少需对应一个问题")
        try:
            cost = int(_require(row, "cost_minor", path, lineno))
        except ValueError as exc:
            raise ValueError(f"{path.name} 第 {lineno} 行 cost_minor 不是整数") from exc
        if cost < 0:
            raise ValueError(f"{path.name} 第 {lineno} 行 cost_minor 为负")
        applies_to = _split_list(_optional(row, "applies_to")) or ("全部",)
        basic = _parse_bool(
            _optional(row, "basic_guarantee"), path=path, lineno=lineno, key="basic_guarantee"
        )
        measures.append(
            Measure(
                measure_id=measure_id,
                sites=_split_list(_optional(row, "site")) or ("未标注",),
                issues=issues,
                cost=cost,
                applies_to=applies_to,
                basic_guarantee=bool(basic),
            )
        )
    return measures
