"""复核留痕：决策记录持久化与轮次间变化归因。

复核人需要能判断：新一轮建议的变化究竟来自
(a) 抽样框/权重变化、(b) 样本覆盖改善，还是 (c) 预算约束变化。
compare_rounds 通过四个反事实配置分解归因，并保留第一轮
“为何不能作决定”的完整原因清单。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import AnalysisResult, Params, load_data, run_analysis
from .schema import Contact, ResponseRow
from .selection import Category

RECORD_VERSION = 1


@dataclass
class DecisionRecord:
    """一轮决策的完整留痕。"""

    created_at: str
    params: dict
    data_hashes: dict[str, str]
    selected: list[str]
    guaranteed: list[str]
    assessments: list[dict]  # measure_id / 类别 / 原因
    undecided: list[dict]  # 待补访事项及“为何不能作决定”
    coverage_snapshot: list[dict]  # 层 / 状态 / 有效样本量
    scenario_selected: dict[str, list[str]]
    warnings: list[str] = field(default_factory=list)
    version: int = RECORD_VERSION

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "DecisionRecord":
        data = json.loads(text)
        if data.get("version") != RECORD_VERSION:
            raise ValueError(f"决策记录版本不支持：{data.get('version')}")
        return cls(**data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "DecisionRecord":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def hash_data_dir(data_dir: str | Path) -> dict[str, str]:
    """数据目录内各 CSV 的内容散列，用于确认两轮输入是否一致。"""
    data_dir = Path(data_dir)
    hashes: dict[str, str] = {}
    for path in sorted(data_dir.glob("*.csv")):
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def record_from_result(result: AnalysisResult, params: Params, data_dir: str | Path) -> DecisionRecord:
    assessments = [
        {
            "measure_id": a.measure_id,
            "category": a.category.value,
            "marginal_benefit": round(a.marginal_benefit, 4),
            "reasons": list(a.reasons),
        }
        for a in result.selection.assessments
    ]
    undecided = [
        {"measure_id": a["measure_id"], "reasons": a["reasons"]}
        for a in assessments
        if a["category"] == Category.FOLLOWUP.value
    ]
    coverage_snapshot = [
        {
            "cell": "/".join(row.cell),
            "status": row.status.value,
            "ess": round(row.ess, 2),
            "responded": row.responded,
            "invited": row.invited,
        }
        for row in result.coverage
    ]
    return DecisionRecord(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        params=params.as_dict(),
        data_hashes=hash_data_dir(data_dir),
        selected=list(result.selection.selected),
        guaranteed=list(result.selection.guaranteed),
        assessments=assessments,
        undecided=undecided,
        coverage_snapshot=coverage_snapshot,
        scenario_selected={k: list(v) for k, v in result.selection.scenario_selected.items()},
        warnings=list(result.warnings),
    )


@dataclass
class AttributionStep:
    label: str
    selected: list[str]
    followup: list[str]  # 该配置下的待补访措施
    changed: list[str] = field(default_factory=list)  # 入选集相对上一步的对称差
    changed_followup: list[str] = field(default_factory=list)  # 待补访集相对上一步的对称差


@dataclass
class ComparisonReport:
    """两轮比较的归因结果。"""

    steps: list[AttributionStep]
    round1_undecided: list[dict]
    round2_undecided: list[dict]
    resolved: list[str]  # 第一轮待补访、第二轮已有结论的措施
    still_undecided: list[str]
    notes: list[str] = field(default_factory=list)


def _filter_responses_to_contacts(
    rows: list[ResponseRow], contacts: list[Contact]
) -> tuple[list[ResponseRow], list[str]]:
    valid = {c.contact_id for c in contacts}
    kept = [r for r in rows if r.contact_id in valid]
    dropped = sorted({r.response_id for r in rows if r.contact_id not in valid})
    return kept, dropped


def compare_rounds(
    round1_dir: str | Path,
    round2_dir: str | Path,
    params1: Params,
    params2: Params,
    *,
    record1_path: str | Path | None = None,
) -> ComparisonReport:
    """分解两轮建议变化的来源。

    四个配置：
    A 第一轮数据 + 第一轮预算（基线，应与第一轮留痕一致）
    B 第二轮接触记录 + 第一轮答卷 + 第一轮预算 → 归因于抽样框/权重
    C 第二轮全部数据 + 第一轮预算 → 归因于样本覆盖
    D 第二轮全部数据 + 第二轮预算 → 归因于预算约束
    """
    notes: list[str] = []
    contacts1, rows1, totals1, measures1 = load_data(round1_dir)
    contacts2, rows2, totals2, measures2 = load_data(round2_dir)

    result_a = run_analysis(contacts1, rows1, totals1, measures1, params1)

    rows1_in_frame2, dropped = _filter_responses_to_contacts(rows1, contacts2)
    if dropped:
        notes.append(
            f"配置 B 中 {len(dropped)} 份第一轮答卷的接触在新抽样框中不存在，已剔除："
            + "、".join(dropped)
        )
    result_b = run_analysis(contacts2, rows1_in_frame2, totals2, measures2, params1)

    result_c = run_analysis(contacts2, rows2, totals2, measures2, params1)
    result_d = run_analysis(contacts2, rows2, totals2, measures2, params2)

    # 第一轮留痕：优先使用显式提供的记录，否则以配置 A 重建
    if record1_path is not None and Path(record1_path).exists():
        record1 = DecisionRecord.load(record1_path)
        if record1.selected != result_a.selection.selected:
            notes.append(
                "警告：第一轮留痕的入选集与按当前代码重算的结果不一致，"
                "请以留痕为准排查代码或参数差异"
            )
    else:
        record1 = record_from_result(result_a, params1, round1_dir)

    record2 = record_from_result(result_d, params2, round2_dir)

    steps: list[AttributionStep] = []
    prev_selected: list[str] = []
    prev_followup: list[str] = []
    for label, result in (
        ("基线（第一轮数据与预算）", result_a),
        ("抽样框/权重变化", result_b),
        ("样本覆盖变化", result_c),
        ("预算约束变化", result_d),
    ):
        selected = list(result.selection.selected)
        followup = sorted(
            a.measure_id
            for a in result.selection.assessments
            if a.category == Category.FOLLOWUP
        )
        changed = sorted(set(selected) ^ set(prev_selected)) if prev_selected else []
        changed_followup = (
            sorted(set(followup) ^ set(prev_followup)) if prev_selected else []
        )
        steps.append(
            AttributionStep(
                label=label,
                selected=selected,
                followup=followup,
                changed=changed,
                changed_followup=changed_followup,
            )
        )
        prev_selected = selected
        prev_followup = followup

    undecided1_ids = {u["measure_id"] for u in record1.undecided}
    undecided2_ids = {u["measure_id"] for u in record2.undecided}
    resolved = sorted(undecided1_ids - undecided2_ids)
    still = sorted(undecided1_ids & undecided2_ids)

    return ComparisonReport(
        steps=steps,
        round1_undecided=record1.undecided,
        round2_undecided=record2.undecided,
        resolved=resolved,
        still_undecided=still,
        notes=notes,
    )
