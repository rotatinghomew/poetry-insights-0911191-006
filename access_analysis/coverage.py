"""覆盖诊断：说明加权结果在哪些层有足够覆盖。

明确区分两类“没有数据”：
- 低响应：层在抽样框内、发出了邀请，但回应少或有效样本量不足——
  这造成的是可量化的不确定性（置信区间与缺测上下界会变宽）。
- 框外未覆盖：层根本未纳入抽样框（入样概率为 0 或设计中不存在）——
  这不是不确定性问题，而是推断范围之外；工具只列示台账人次作为规模提示，
  不做任何加权估计，也不借邻层均值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .schema import Contact, ResponseRow, VenueTotal
from .weights import WeightReport


class CoverageStatus(Enum):
    SUFFICIENT = "覆盖充分"
    LOW_RESPONSE = "低响应"
    NO_RESPONSE = "无回应"
    OUT_OF_FRAME = "框外未覆盖"


@dataclass
class CoverageRow:
    cell: tuple[str, str, str]
    status: CoverageStatus
    frame_contacts: int = 0
    invited: int = 0
    responded: int = 0
    response_rate: float = 0.0
    ess: float = 0.0
    weight_sum: float = 0.0
    ledger_visits: int | None = None  # 台账人次（仅规模提示，非个体数）
    batch_rates: dict[str, float] = field(default_factory=dict)  # 分批回收率
    note: str = ""


def coverage_table(
    contacts: list[Contact],
    rows: list[ResponseRow],
    weight_report: WeightReport,
    venue_totals: list[VenueTotal],
    *,
    ess_threshold: float = 30.0,
) -> list[CoverageRow]:
    """生成逐层覆盖诊断表（含框内所有层与台账中出现但框外的层）。"""
    contacts_by_id = {c.contact_id: c for c in contacts}
    visits_by_cell = {t.cell: t.visit_count for t in venue_totals}
    invited_ledger = {t.cell: t.invited_count for t in venue_totals}

    # 分批回收率：每批回应数 / 该层邀请数
    batch_responded: dict[tuple[str, str, str], dict[str, int]] = {}
    for row in rows:
        contact = contacts_by_id.get(row.contact_id)
        cell = contact.cell if contact else ("未知", "未知", "未知")
        cell_batches = batch_responded.setdefault(cell, {})
        if row.batch:
            cell_batches[row.batch] = cell_batches.get(row.batch, 0) + 1

    table: list[CoverageRow] = []
    all_cells = sorted(
        set(weight_report.cells) | set(visits_by_cell) | {c.cell for c in weight_report.out_of_frame}
    )

    for cell in all_cells:
        info = weight_report.cells.get(cell)
        ledger_visits = visits_by_cell.get(cell)
        row_out = CoverageRow(
            cell=cell,
            status=CoverageStatus.SUFFICIENT,
            ledger_visits=ledger_visits,
        )
        invited = info.invited if info else 0
        responded = info.responded if info else 0
        row_out.frame_contacts = info.frame_contacts if info else 0
        row_out.invited = invited
        row_out.responded = responded
        row_out.response_rate = (responded / invited) if invited else 0.0
        row_out.ess = info.ess if info else 0.0
        row_out.weight_sum = info.adjusted_weight_sum if info else 0.0

        if info is None:
            # 抽样设计中不存在该层（或全部接触入样概率为 0）
            row_out.status = CoverageStatus.OUT_OF_FRAME
            row_out.note = "未纳入抽样框：不做加权估计，台账人次仅作规模提示"
        elif invited == 0:
            row_out.status = CoverageStatus.NO_RESPONSE
            row_out.note = "层内有框内接触但未兑现邀请：不出估计，未借用邻层均值"
        elif responded == 0:
            row_out.status = CoverageStatus.NO_RESPONSE
            row_out.note = "层内零回应：不出估计，未借用邻层均值"
        elif row_out.ess < ess_threshold:
            row_out.status = CoverageStatus.LOW_RESPONSE
            row_out.note = f"有效样本量 {row_out.ess:.1f} 低于阈值 {ess_threshold:g}：估计不确定性大"
        else:
            row_out.status = CoverageStatus.SUFFICIENT
            row_out.note = ""

        # 台账邀请数与接触记录的对账提示
        ledger_invited = invited_ledger.get(cell)
        if ledger_invited is not None and invited and ledger_invited != invited:
            extra = f"；台账邀请 {ledger_invited} 与接触记录 {invited} 不一致，请核对"
            row_out.note = (row_out.note + extra) if row_out.note else extra.lstrip("；")

        batches = batch_responded.get(cell, {})
        for batch, count in sorted(batches.items()):
            row_out.batch_rates[batch] = (count / invited) if invited else 0.0

        table.append(row_out)
    return table
