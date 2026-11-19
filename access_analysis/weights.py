"""抽样权重：基础权重 = 1/入样概率，再按层（人群×场次）做无回应倾向调整。

要点：
- selection_probability 为 0 的接触属于抽样框外（未覆盖区），不进入任何
  加权估计，只在覆盖诊断中单独列示——这是“未纳入抽样框的人”，与
  “低响应造成的不确定性”是两类不同问题。
- 无回应调整仅在同一 (school, slot, stratum) 层内进行，不跨层借信息。
- 台账人次不进入个体口径的权重；如需人次口径的校准，见 calibrate_to_visits，
  其结果只可用于“人次”标注，不得解释为可识别个体数。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import Contact


@dataclass
class CellWeightInfo:
    """一个层的权重诊断。"""

    cell: tuple[str, str, str]
    frame_contacts: int = 0  # 框内联系记录数
    invited: int = 0
    responded: int = 0
    base_weight_sum: float = 0.0  # 邀请样本的基础权重和（框内规模的估计）
    response_propensity: float = 0.0  # 加权回应倾向
    adjusted_weight_sum: float = 0.0  # 回应者调整权重和
    ess: float = 0.0  # Kish 有效样本量（回应者）
    max_weight: float = 0.0


@dataclass
class WeightReport:
    """权重计算结果。weights 仅覆盖“框内且被邀请且已回应”的接触。"""

    weights: dict[str, float]  # contact_id -> 调整后权重
    cells: dict[tuple[str, str, str], CellWeightInfo]
    out_of_frame: list[Contact] = field(default_factory=list)  # 框外接触
    not_invited: list[Contact] = field(default_factory=list)  # 框内未邀请
    no_response_cells: list[tuple[str, str, str]] = field(default_factory=list)


def compute_weights(
    contacts: list[Contact],
    responded_contact_ids: set[str],
) -> WeightReport:
    """计算设计权重 + 层内无回应调整。

    responded_contact_ids：至少回收一份有效答卷的 contact_id 集合。
    """
    cells: dict[tuple[str, str, str], CellWeightInfo] = {}
    out_of_frame: list[Contact] = []
    not_invited: list[Contact] = []

    in_frame_invited: list[Contact] = []
    for contact in contacts:
        if not contact.in_frame:
            out_of_frame.append(contact)
            continue
        info = cells.setdefault(contact.cell, CellWeightInfo(cell=contact.cell))
        info.frame_contacts += 1
        if not contact.invited:
            not_invited.append(contact)
            continue
        in_frame_invited.append(contact)
        info.invited += 1
        info.base_weight_sum += 1.0 / contact.selection_probability
        if contact.contact_id in responded_contact_ids:
            info.responded += 1

    weights: dict[str, float] = {}
    no_response_cells: list[tuple[str, str, str]] = []

    for cell, info in cells.items():
        if info.invited == 0:
            continue
        responding = [
            c for c in in_frame_invited if c.cell == cell and c.contact_id in responded_contact_ids
        ]
        if not responding:
            # 整层无回应：该层不出任何估计，绝不借邻层均值。
            no_response_cells.append(cell)
            continue
        # 加权回应倾向：回应者基础权重和 / 邀请者基础权重和
        responded_base = sum(1.0 / c.selection_probability for c in responding)
        info.response_propensity = responded_base / info.base_weight_sum
        cell_weights: list[float] = []
        for contact in responding:
            adjusted = (1.0 / contact.selection_probability) / info.response_propensity
            weights[contact.contact_id] = adjusted
            cell_weights.append(adjusted)
        info.adjusted_weight_sum = sum(cell_weights)
        info.max_weight = max(cell_weights)
        # Kish 有效样本量
        sum_w = sum(cell_weights)
        sum_w2 = sum(w * w for w in cell_weights)
        info.ess = (sum_w * sum_w / sum_w2) if sum_w2 > 0 else 0.0

    return WeightReport(
        weights=weights,
        cells=cells,
        out_of_frame=out_of_frame,
        not_invited=not_invited,
        no_response_cells=no_response_cells,
    )


def calibrate_to_visits(
    weights: dict[str, float],
    contacts_by_id: dict[str, Contact],
    visit_totals: dict[tuple[str, str, str], int],
) -> dict[str, float]:
    """把权重校准到台账人次总量（仅限人次口径使用）。

    返回新的权重字典。校准目标是人次，不是人数；调用方必须在输出中
    标注“人次口径”，且不得把校准后的权重和解释为可识别个体数。
    没有回应的层无法校准（保持原样，由覆盖诊断说明）。
    """
    cell_weight_sum: dict[tuple[str, str, str], float] = {}
    for contact_id, weight in weights.items():
        cell = contacts_by_id[contact_id].cell
        cell_weight_sum[cell] = cell_weight_sum.get(cell, 0.0) + weight
    calibrated: dict[str, float] = {}
    for contact_id, weight in weights.items():
        cell = contacts_by_id[contact_id].cell
        target = visit_totals.get(cell)
        current = cell_weight_sum[cell]
        if target is None or current <= 0:
            calibrated[contact_id] = weight
        else:
            calibrated[contact_id] = weight * (target / current)
    return calibrated
