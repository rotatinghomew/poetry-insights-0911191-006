# 诗词导览无障碍覆盖的抽样推断与改善排序

本目录保存诗词导览无障碍覆盖的抽样推断与改善排序相关的本地演练数据。记录均为合成内容，使用 UTF-8 CSV；空字段表示资料未提供，不代表数值为零。

## 数据

- `data/sampled_contacts.csv`：sampled_contacts记录。
- `data/responses.csv`：responses记录。
- `data/venue_totals.csv`：venue_totals记录。
- `data/improvements.csv`：improvements记录。

## 环境与验证

需要 Python 3.11 或更新版本；项目仅使用 Python 标准库。可用 `python3 -m unittest discover -s tests -v` 检查 CSV 结构与本地资料完整性。

分析入口与业务解释由项目使用者按实际工作需要补充。
