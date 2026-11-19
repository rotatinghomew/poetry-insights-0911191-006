"""分析管线：从数据目录到遴选结果的完整流程，供 CLI 与轮次复核共用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .coverage import CoverageRow, coverage_table
from .estimates import EstimateSet, Estimand, estimate_issues
from .responses import Subject, build_subjects
from .schema import (
    Contact,
    Measure,
    ResponseRow,
    VenueTotal,
    load_contacts,
    load_measures,
    load_responses,
    load_venue_totals,
)
from .selection import SelectionResult, select_measures
from .weights import WeightReport, compute_weights

REQUIRED_FILES = (
    "sampled_contacts.csv",
    "responses.csv",
    "venue_totals.csv",
    "improvements.csv",
)


@dataclass
class Params:
    """一轮分析的全部参数（进入复核留痕）。"""

    budget: int
    ess_threshold: float = 30.0
    missing_multiplier: float | None = None  # 缺测倍率；None 表示完整 Manski 界
    protected_groups: tuple[str, ...] = ()
    min_group_benefit: dict[str, float] = field(default_factory=dict)
    bootstrap: int = 400
    seed: int = 20261118
    visits_per_person: float | None = None  # 显式假设才允许人次→人数换算

    def as_dict(self) -> dict:
        return {
            "budget": self.budget,
            "ess_threshold": self.ess_threshold,
            "missing_multiplier": self.missing_multiplier,
            "protected_groups": list(self.protected_groups),
            "min_group_benefit": dict(self.min_group_benefit),
            "bootstrap": self.bootstrap,
            "seed": self.seed,
            "visits_per_person": self.visits_per_person,
        }


@dataclass
class AnalysisResult:
    contacts: list[Contact]
    rows: list[ResponseRow]
    totals: list[VenueTotal]
    measures: list[Measure]
    subjects: list[Subject]
    subject_weights: dict[tuple[str, str], float]
    expected_stages: list[str]
    issues: list[str]
    weight_report: WeightReport
    coverage: list[CoverageRow]
    estimates: EstimateSet
    selection: SelectionResult
    warnings: list[str] = field(default_factory=list)


def load_data(data_dir: str | Path) -> tuple[list[Contact], list[ResponseRow], list[VenueTotal], list[Measure]]:
    data_dir = Path(data_dir)
    missing = [name for name in REQUIRED_FILES if not (data_dir / name).exists()]
    if missing:
        raise FileNotFoundError(f"{data_dir} 缺少数据文件：{'、'.join(missing)}")
    return (
        load_contacts(data_dir / "sampled_contacts.csv"),
        load_responses(data_dir / "responses.csv"),
        load_venue_totals(data_dir / "venue_totals.csv"),
        load_measures(data_dir / "improvements.csv"),
    )


def run_analysis(
    contacts: list[Contact],
    rows: list[ResponseRow],
    totals: list[VenueTotal],
    measures: list[Measure],
    params: Params,
) -> AnalysisResult:
    """执行完整分析。所有口径与红线约束在各模块 docstring 中说明。"""
    warnings: list[str] = []

    build = build_subjects(contacts, rows)
    warnings.extend(build.warnings)

    weight_report = compute_weights(contacts, build.responded_contact_ids)

    # 个体口径：被评价人继承其接触的调整权重（同一接触下代答对象与受访者
    # 通过同一抽样接触进入样本，假定入样概率相同）。
    subject_weights: dict[tuple[str, str], float] = {}
    dropped_subjects = 0
    for subject in build.subjects:
        weight = weight_report.weights.get(subject.contact_id)
        if weight is not None:
            subject_weights[subject.key] = weight
        else:
            dropped_subjects += 1
    if dropped_subjects:
        warnings.append(
            f"{dropped_subjects} 个被评价人的接触无权重（框外或未回应），"
            "未进入加权估计"
        )

    # 体验阶段的定义顺序：数据中出现的顺序（配置可覆盖）
    expected_stages: list[str] = []
    for row in rows:
        if row.stage not in expected_stages:
            expected_stages.append(row.stage)

    issues = sorted({issue for s in build.subjects for issue in s.issues})

    all_cells = sorted({c.cell for c in contacts} | {t.cell for t in totals})
    estimates = estimate_issues(
        build.subjects,
        weight_report.weights,
        contacts,
        issues,
        expected_stages,
        estimand=Estimand.PER_PERSON,
        missing_multiplier=params.missing_multiplier,
        bootstrap=params.bootstrap,
        seed=params.seed,
        all_cells=all_cells,
    )

    coverage = coverage_table(
        contacts,
        rows,
        weight_report,
        totals,
        ess_threshold=params.ess_threshold,
    )

    selection = select_measures(
        measures,
        build.subjects,
        subject_weights,
        expected_stages,
        params.budget,
        coverage,
        estimates,
        protected_groups=list(params.protected_groups),
        min_group_benefit=params.min_group_benefit or None,
    )
    warnings.extend(selection.warnings)

    return AnalysisResult(
        contacts=contacts,
        rows=rows,
        totals=totals,
        measures=measures,
        subjects=build.subjects,
        subject_weights=subject_weights,
        expected_stages=expected_stages,
        issues=issues,
        weight_report=weight_report,
        coverage=coverage,
        estimates=estimates,
        selection=selection,
        warnings=warnings,
    )
