"""把分析结果渲染为中文 Markdown 报告（复核人视角）。"""

from __future__ import annotations

from .coverage import CoverageStatus
from .estimates import scale_to_visits
from .pipeline import AnalysisResult, Params
from .provenance import ComparisonReport, DecisionRecord
from .selection import Category


def _pct(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "—"


def _cell_name(cell: tuple[str, str, str]) -> str:
    return "/".join(cell)


def render_report(
    result: AnalysisResult,
    params: Params,
    record: DecisionRecord | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# 无障碍覆盖抽样推断与改善排序报告")
    lines.append("")
    lines.append("## 1. 人群、场次与估计口径")
    lines.append("")
    lines.append("- 层（人群×场次）定义：`学校 / 时段 / 人群分层`。")
    lines.append(f"- 估计口径：**{result.estimates.estimand.value}**。")
    lines.append(
        "- 台账为人次总量：本报告不把它当作可识别个体数；"
        "凡涉及规模的换算均标注“人次口径”。"
    )
    if params.visits_per_person:
        lines.append(
            f"- 人数换算：按显式假设人均到访 {params.visits_per_person} 次折算，"
            "属假设依赖结果，复核时请单独确认。"
        )
    lines.append(
        f"- 缺测处理：未作答环节只进入上下界"
        + (
            f"（缺测者受影响率上限取观测率的 {params.missing_multiplier:g} 倍）。"
            if params.missing_multiplier
            else "（完整 Manski 界：缺测者受影响率取 0–100%）。"
        )
        + "未到达环节按真实结果计，绝不视为体验正常。"
    )
    lines.append(
        f"- 有效样本量阈值：{params.ess_threshold:g}；自助法重复：{params.bootstrap} 次。"
    )
    if result.estimates.cells_without_data:
        names = "、".join(_cell_name(c) for c in result.estimates.cells_without_data)
        lines.append(
            f"- **无数据层**：{names}。这些层不出任何估计，未借用邻层均值。"
        )
    lines.append("")

    lines.append("## 2. 覆盖诊断")
    lines.append("")
    lines.append(
        "| 层 | 状态 | 框内接触 | 邀请 | 回应 | 回应率 | 有效样本量 | 台账人次 | 备注 |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in result.coverage:
        lines.append(
            f"| {_cell_name(row.cell)} | {row.status.value} | {row.frame_contacts} "
            f"| {row.invited} | {row.responded} | {_pct(row.response_rate) if row.invited else '—'} "
            f"| {row.ess:.1f} | {row.ledger_visits if row.ledger_visits is not None else '—'} "
            f"| {row.note} |"
        )
    lines.append("")
    out_of_frame = [r for r in result.coverage if r.status == CoverageStatus.OUT_OF_FRAME]
    if out_of_frame:
        names = "、".join(_cell_name(r.cell) for r in out_of_frame)
        lines.append(
            f"> 框外未覆盖（{names}）属于**推断范围之外**，不是“低响应”："
            "其不确定性无法用量化的置信区间表达，只能通过扩展抽样框解决。"
        )
        lines.append("")

    lines.append("## 3. 问题发生率估计（加权）")
    lines.append("")
    lines.append(
        "| 问题 | 点估计 | 95% 置信区间 | 缺测下界 | 缺测上界 | 缺测占比 | 妨碍参加率 |"
    )
    lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: |")
    for issue, est in sorted(result.estimates.pooled.items()):
        if est.point is None:
            lines.append(
                f"| {issue} | 无估计 | — | {_pct(est.bound_lower)} "
                f"| {_pct(est.bound_upper)} | — | {_pct(est.prevented_share)} |"
            )
            continue
        ci = f"{_pct(est.ci[0])}–{_pct(est.ci[1])}" if est.ci else "—"
        lines.append(
            f"| {issue} | {_pct(est.point)} | {ci} | {_pct(est.bound_lower)} "
            f"| {_pct(est.bound_upper)} | {_pct(est.missing_share)} "
            f"| {_pct(est.prevented_share)} |"
        )
    lines.append("")
    lines.append(
        f"- 总体被阻断（未到场或中途未能继续）比例：{_pct(result.estimates.blocked_share)}。"
        "离场扫码看不到中途放弃者，该比例只能来自本问卷。"
    )
    lines.append(
        "- 点估计仅基于完整观测；缺测上下界之间的宽度即“低响应/缺测造成的不确定性”。"
    )
    # 人次口径的规模提示：只用有数据层的点估计 × 该层台账人次，
    # 不外推到无数据层与框外层。
    visits = {row.cell: row.ledger_visits for row in result.coverage if row.ledger_visits}
    if visits:
        total_visits = sum(visits.values())
        lines.append(
            f"- 台账人次合计 {total_visits}（仅人次口径的规模提示，非可识别个体数）。"
        )
        covered = [
            (issue, cell_est)
            for cell, per_issue in result.estimates.by_cell.items()
            if cell in visits
            for issue, cell_est in per_issue.items()
            if cell_est.point is not None
        ]
        if covered:
            affected: dict[str, float] = {}
            for (issue, cell_est) in covered:
                affected[issue] = affected.get(issue, 0.0) + scale_to_visits(
                    cell_est.point, visits[cell_est.cell], params.visits_per_person
                )["affected_visits"]
            summary = "；".join(
                f"{issue} 约 {v:.0f} 人次" for issue, v in sorted(affected.items())
            )
            lines.append(
                f"- 受影响规模（人次口径，仅统计有数据层，不外推）：{summary}。"
            )
    lines.append("")

    lines.append("## 4. 改善措施遴选")
    lines.append("")
    sel = result.selection
    lines.append(f"- 预算：{sel.budget}；预计支出：{sel.spent}。")
    lines.append(
        f"- 入选措施：{('、'.join(sel.selected)) if sel.selected else '无'}"
        f"（受益为并集口径，同一受影响者被多项措施覆盖只计一次）。"
    )
    lines.append(
        f"- 入选集合计受益：约 {sel.total_benefit:.1f} 个加权个体"
        f"（占全部加权个体 {_pct(sel.benefit_share)}）。"
    )
    if sel.per_group_share:
        shares = "；".join(
            f"{g} {_pct(s)}" for g, s in sorted(sel.per_group_share.items())
        )
        lines.append(f"- 各人群层受益份额：{shares}。")
    lines.append("")
    lines.append("| 措施 | 分类 | 边际收益（加权个体） | 理由 |")
    lines.append("| --- | --- | ---: | --- |")
    for a in sel.assessments:
        reasons = "<br>".join(a.reasons) if a.reasons else "—"
        lines.append(f"| {a.measure_id} | {a.category.value} | {a.marginal_benefit:.1f} | {reasons} |")
    lines.append("")
    lines.append("### 缺测情景下的入选集（复核用）")
    lines.append("")
    for scenario, selected in sorted(sel.scenario_selected.items()):
        label = "观测情形" if scenario == "observed" else "缺测上界情形"
        lines.append(f"- {label}：{'、'.join(selected) if selected else '无'}")
    lines.append("")

    followup = [a for a in sel.assessments if a.category == Category.FOLLOWUP]
    if followup:
        lines.append("### 待补访事项与补访目标")
        lines.append("")
        lines.append(
            "以下事项在合理缺测解释下排序会翻转，或相关层覆盖不足，"
            "本轮不作上马/否决结论；补访仅用于校验这些特定缺口："
        )
        lines.append("")
        lines.append("| 层 | 问题 | 原因 |")
        lines.append("| --- | --- | --- |")
        for target in sel.followup_targets:
            lines.append(f"| {_cell_name(target.cell)} | {target.issue} | {target.reason} |")
        lines.append("")

    section = 4  # 前四节已固定
    if result.warnings:
        section += 1
        lines.append(f"## {section}. 警告")
        lines.append("")
        for warning in result.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    section += 1
    lines.append(f"## {section}. 口径与限制（必读）")
    lines.append("")
    lines.append(
        "- 人次 ≠ 人数：台账只提供分层人次总量，本报告不据此声称受影响“人数”。"
    )
    lines.append(
        "- 陪同者代答：同一接触下的代答对象继承该接触的入样权重，"
        "假定其与受访者通过同一抽样接触进入样本。"
    )
    lines.append(
        "- 无数据层未借用任何邻层均值；框外层不在推断范围内。"
    )
    lines.append(
        "- 基本保障措施不进入“是否上马”的优化，即使样本中很少出现对应问题也不取消。"
    )
    if record is not None:
        section += 1
        lines.append("")
        lines.append(f"## {section}. 留痕")
        lines.append("")
        lines.append(f"- 记录时间：{record.created_at}")
        lines.append("- 数据散列：")
        for name, digest in sorted(record.data_hashes.items()):
            lines.append(f"  - {name}：`{digest[:16]}…`")
    lines.append("")
    return "\n".join(lines)


def render_comparison(report: ComparisonReport) -> str:
    lines: list[str] = []
    lines.append("# 两轮建议比较（变化归因）")
    lines.append("")
    lines.append("## 归因分解")
    lines.append("")
    lines.append(
        "依次替换一个因素重算遴选：若某一步入选集或待补访集变化，则该变化归因于该因素。"
    )
    lines.append("")
    lines.append("| 步骤 | 入选集 | 入选集变化 | 待补访集变化 |")
    lines.append("| --- | --- | --- | --- |")
    for step in report.steps:
        changed = "、".join(step.changed) if step.changed else "无变化"
        changed_followup = (
            "、".join(step.changed_followup) if step.changed_followup else "无变化"
        )
        lines.append(
            f"| {step.label} | {'、'.join(step.selected) if step.selected else '无'} "
            f"| {changed} | {changed_followup} |"
        )
    lines.append("")
    lines.append("## 第一轮为何不能作决定（留痕原因）")
    lines.append("")
    if report.round1_undecided:
        for item in report.round1_undecided:
            reasons = "；".join(item["reasons"]) if item["reasons"] else "—"
            lines.append(f"- **{item['measure_id']}**：{reasons}")
    else:
        lines.append("- 第一轮无待补访事项。")
    lines.append("")
    lines.append("## 本轮结论")
    lines.append("")
    if report.resolved:
        lines.append(f"- 已有结论（原待补访）：{'、'.join(report.resolved)}")
    if report.still_undecided:
        lines.append(f"- 仍待补访：{'、'.join(report.still_undecided)}")
    if not report.resolved and not report.still_undecided:
        lines.append("- 两轮之间无待补访事项的状态变化。")
    if report.notes:
        lines.append("")
        lines.append("## 备注")
        lines.append("")
        for note in report.notes:
            lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)
