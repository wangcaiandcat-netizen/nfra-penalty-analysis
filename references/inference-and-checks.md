# 文号、任职关系与集合核对

## 文号地区和时间：使用服务端推定字段

用户允许通过文号推断，无须为采用这一方法再次确认。先读取 catalog.inference、date_bases、date_precision_policy、dimension_semantics 和 scope 中的版本/覆盖。

| 用户意图 | 请求选择 | 回答中保留 |
|---|---|---|
| 文号〔2025〕的处罚 | date.basis=document_number_year，2025全年；也可独立用 document_number.years=[2025] | 文号发文年，年精度，不等同真实决定日期 |
| 决定于2025年，缺失时按文号判断 | decision_or_document_year，2025全年 | decision_time 的 value/precision/basis/status；不回退公告日期 |
| 某局监管辖区 | regions.basis=document_jurisdiction，scope及ID从catalog.inference.regions取 | 文号机关辖区，不代表当事人所在地 |
| 按发文机关驻地归入杭州/南京 | regions.basis=issuer_seat，scope=city | 机关驻地推定，不能表述为本市全部处罚或当事人在该市 |
| 公告于某季度且文号为2025年 | date.basis=publication + document_number.years=[2025] | 两条件AND，不把“文号年”当该季度的决定时间 |

年精度只有在整个自然年被区间包含时才纳入；2025Q1不会纳入仅有2025文号年的记录。month/quarter 中此类记录为unknown。document_number.prefixes 为字面前缀列表，不能写正则或用省份首字猜映射。原 jurisdiction.city_id 为空时不填驻地城市；使用 document_number_inference.seat。一个推定部分未知不必否定其他有效部分；有歧义/过期规则时保留状态，不自行选一个候选。

服务端返回 original_party_type、document_number_inference、decision_time、party_associations、person_sanctions及person_identity_groups，直接保留，不能覆盖原始 party/decision_date/jurisdiction。原决定日期为空而decision_time取文号年时，只写“按文号推定为某年”；decision_or_document_year匹配0条只表示本库按该回退口径匹配0条，不能断言实际决定处罚为零。未知推定或规则未就绪时说明覆盖不足；不得在客户端维护第二份省市映射或分类规则。

## 找任职机构相关的个人

affiliations 只检索有证据的 confirmed 任职边；默认不含混合公示，include_mixed=true 时返回涉及机构的原整行。机构列表OR，其他过滤字段AND；不要用 subjects.names 搜机构来替代个人任职查询，也不要通过姓名长度判断个人。

逐案保留 edges 中的 institution、person、relation、status、evidence。unresolved_mentions 是待核提及，不算确认关系；同一人多段任职允许共存。混合公示中的 people 是关联证据，不是可自行拆分的独立case_id。新职务和措辞可能漏识别，未命中不能证明没有处罚。

交付称“已识别任职关联的记录”，不称“全部责任人”。个人处罚只使用后端person_sanctions确认的对应与证据；缺失或不明确时保留原文，不按姓名、任职提及或正则补配。身份采用后端“姓名＋具体机构＋职务”的identity_key，并保留person_identity_groups的scope和逐页原值；不同姓名数不是人数，各页分组不能直接相加成人数。完整规则见[结果交付](delivery.md)。

## 指定集合的权威金额

1. 从成功查询/详情/公告列表取得实际case_id。要全量集合先取完同修订分页；只有首屏时只能称“当前取得的集合”。
2. 明确每个set_id的规则及成员，例如 name_matches、document_seat_candidates；有交集的组各自统计，不相加。原始请求成员保持重复，服务端报告duplicate_input_members；库内标记重复默认排除。
3. 使用 set_stats，顶层回传首个响应的 expected_data_revision；每组可加同修订要求。顶层409重新查询；某组修订不符、未知ID产生partial/failed时保留逐组状态。
4. 从同一 results[] 取 id_resolution.matched_case_ids、statistics.total_records/source_documents/single_explicit_fines。unknown_ids 不计入金额分母；纳入+排除=该组全部有效ID。
5. 金额排除混合公示、非正式决定、不明确/缺失/负金额；以返回exclusion_reasons为准。原行single_explicit也不意味着混合公示金额可纳入，不加回历史版本的旧合计。

普通query/stats金额分母仍按正式决定口径，set_stats分母按全部有效ID。金额只描述min_yuan/max_yuan及对应原文，不累计金额。included_records=0时范围为null/未知；有明确0元时才返回0.00，zero_amount_records反映真实零值。不把综合处罚金额归因于某一业务，不据金额数值评价处罚轻重。

## 内存一致性检查（默认）

普通任务直接调用reconcile(responses, selection)，或将responses数组和可选selection放入一个JSON对象，经stdin传给result_check.py --bundle -。不必为每页、每个选集创建JSON文件。完整示例和stdin单次消费规则见 [内存工作流](memory-workflow.md)。

## 已留档材料的一致性检查（按需）

result_check.py只检查已保存API结果的结构、分页覆盖、同修订/规则版本、同一集合的数量和金额覆盖；直接保留服务端金额和推定字段。它不重算金额、不再解析文号或执行document_rules。旧版r3的本地金额/推定输出应重新请求新版API，不能视为新版统计。

```bash
python3 /安装目录/scripts/result_check.py --input query-page1.json --input query-page2.json --selection selection.json
python3 /安装目录/scripts/penalty_client.py set_stats --input set-stats-request.json --dry-run
python3 /安装目录/scripts/penalty_client.py set_stats --input set-stats-request.json
python3 /安装目录/scripts/result_check.py --input query-page1.json --input query-page2.json --input set-stats-response.json --selection selection.json
```

selection.json格式：`{"sets":[{"set_id":"chosen","case_ids":["实际返回ID"],"status":"inferred","basis":"本次明确的归属规则"}]}`。第一次检查的set_stats_request字段可单独保存为请求文件；status/basis仅为本地说明，不发送给API。没有set_stats响应时sets为空且all_sets_complete=false，工具不会生成金额结论。结果含coverage、source_failures、selection_notes、原样sets和derived_fields；API统计仍是权威来源。
