# 个人统计与同口径比较

后端目录公布`personal_sanctions.statistics_option`。旧后台未返回个人统计时说明未就绪，不本地拆人或补算。

`compare --summary-only`输入示例：

```json
{"periods":[
  {"label":"2025上半年","request":{"filters":{"date":{"from":"2025-01-01","to":"2025-06-30","basis":"publication"}},"statistics":{"group_by":["subject_composition"],"include_personal_sanctions":true}}},
  {"label":"2026上半年","request":{"filters":{"date":{"from":"2026-01-01","to":"2026-06-30","basis":"publication"}},"statistics":{"group_by":["subject_composition"],"include_personal_sanctions":true}}}
]}
```

全筛选集返回四类记录/公告数；个人指标仅来自其中的纯个人正式决定。若用户只查纯个人，可在各期统一加`subjects.compositions=["person"]`；混合公示在`related_mixed`另列。

| statistics.personal_sanctions字段 | 表达口径 |
| --- | --- |
| record_counts | person/mixed/institution/unknown的公示记录与公告数 |
| pure_person.confirmed_subject_mentions | 原文明示且处罚归属确认的个人主体提及次数；不是去重人数 |
| pure_person.matched_identity_count | 按完整姓名＋具体机构＋职务归并的身份键数量；不是总体真实人数 |
| pure_person.unknown_identity_mentions | 身份信息不全或冲突的主体提及，不能并入已确认身份数 |
| pure_person.records_without_confirmed_named_sanction | 未识别明确具名处罚对象的纯个人记录，不能视为无人受罚 |
| pure_person.sanction_types | 按主体提及计、多标签可叠加；含罚款与含取消资格可同时命中 |
| pure_person.restriction_durations | 按明确个人限制措施条目计，区分禁业/取消资格与固定/终身/未知 |
| pure_person.fine_amounts | 明确个人罚款措施样本、金额状态覆盖、min_yuan/max_yuan及minimum/maximum原文依据；无样本为null，不求和、不算均值或中位数 |
| pure_person.fine_amounts.minimum/maximum | 对应原文、case_id、subject_ref、实际来源；并列最多5项，matched_entries/truncated说明展示是否完整 |

不将身份键跨期相加成人数；混合姓名、多人共计金额和未知期限保留原文，不分配。样本覆盖不同不能推断总体处罚轻重。文号年仍标推定，零结果限于当前日期、类别和关键词条件。
