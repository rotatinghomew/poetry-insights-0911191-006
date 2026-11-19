"""无障碍覆盖抽样推断与改善排序工具。

方法学红线（贯穿全包，单元测试逐条守护）：

1. 人次总量不是可识别个体：场馆台账仅用于“人次口径”的标注性换算，
   任何“受影响人数”不得直接由人次充当。
2. 未走到的环节不是体验正常：未作答/未到达分别按“缺测”与“结果”处理，
   缺测只进入上下界，不进入点估计的分母。
3. 没有数据的层不借邻层均值：无回应层不出估计，只在覆盖诊断中列示。
4. 多环节措施不重复计收益：收益按“受影响个体是否至少被解决一项”取并集。
5. 基本保障不可被替代、不可因样本少而取消。
"""

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
from .weights import compute_weights, WeightReport
from .responses import build_subjects, Subject
from .estimates import estimate_issues, IssueEstimate, Estimand
from .coverage import coverage_table, CoverageRow, CoverageStatus
from .selection import select_measures, SelectionResult, Category
from .provenance import DecisionRecord, compare_rounds

__all__ = [
    "Contact",
    "Measure",
    "ResponseRow",
    "VenueTotal",
    "load_contacts",
    "load_measures",
    "load_responses",
    "load_venue_totals",
    "compute_weights",
    "WeightReport",
    "build_subjects",
    "Subject",
    "estimate_issues",
    "IssueEstimate",
    "Estimand",
    "coverage_table",
    "CoverageRow",
    "CoverageStatus",
    "select_measures",
    "SelectionResult",
    "Category",
    "DecisionRecord",
    "compare_rounds",
]

__version__ = "0.1.0"
