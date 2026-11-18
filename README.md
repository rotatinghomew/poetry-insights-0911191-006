# 诗词导览无障碍覆盖的抽样推断与改善排序

本项目保存无障碍服务分析的合成抽样接触、答卷、场馆汇总及改善资料，均使用 UTF-8 CSV。空字段表示记录未提供。

## 数据

- `data/sampled_contacts.csv`：抽样分层、选择概率和邀请状态。
- `data/responses.csv`：答卷人、体验阶段及问题。
- `data/venue_totals.csv`：场馆提供的分层人数汇总。
- `data/improvements.csv`：改造点位、预算及适用问题。

## 环境与验证

需要 Python 3.11 或更新版本；项目仅使用 Python 标准库。可用 `python3 -m unittest discover -s tests -v` 检查 CSV 结构与本地资料完整性。
