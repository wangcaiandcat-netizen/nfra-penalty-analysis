# 统计维度选择与结果解释

`stats` 针对完整筛选集聚合。先确定用户问题和筛选范围，再选有区分度的维度；默认不机械请求全部15维。实际可用代码与覆盖以本次 `catalog` 为准。

| 维度 | 含义与适用场景 | 已知限制或无区分度场景 |
|---|---|---|
| `year` | 按选定日期基准统计跨年分布 | 单一年份筛选通常只有一组；不能凭公告量判断监管强弱或自动算同比 |
| `quarter` | 比较季度分布 | 单季度筛选通常只有一组；API按filters.date.basis聚合，只有文号年份的推定记录无法确定季度 |
| `month` | 年内或季度内的月度分布，常用时间维度 | 缺失月份不能直接补0；返回只含有记录期间，覆盖未确认不能解释为零处罚 |
| `region` | 按记录原始监管地区字段分组，适合检查原始标注或区分省局与下辖局 | 原字段可能混合省、市、总局等层级，不是规范行政区划，也不是当事人所在地 |
| `authority_province` | 按发文监管机关的规范省级辖区分组，适合跨省比较或省域汇总 | 直辖市在省级维度中表现为上海等省级辖区，不代表区县；单省/单直辖市筛选通常一组，总局与未知仍需展示 |
| `authority_city` | 按已核验的地方监管辖区分组，适合一省内多个地方分局的比较 | 省级局没有下级city_id时落入unknown；上海监管局等直辖市省级映射也如此。此字段还含已核验自治州/地区/万州等监管地方名，不保证都是行政地级市 |
| `party_type` | 比较机构、个人与未知/混合主体的公示行 | unknown可能是机构与责任人合并事项，不能当成个人或强行拆行；筛选仅机构后再按类型分组通常无区分度 |
| `subject_composition` | 按服务端当前口径比较纯个人、纯机构、机构与个人混合及未知公示 | 沿用API原值及分母；纯个人记录也可能涉及多人，记录数不是人数；不据旧party_type自行重分类 |
| `institution_type` | 按后端法人名录快照类别统计，机构取受罚机构、个人取已确认任职机构；筛选使用institution_types | 原样保留multi_label、分母、unknown及覆盖；一条可能涉及多类，不相加成总数；不临时按银行名称分类，不宣称处罚时类别 |
| `business_type` | 展示违法事实规则识别的一级业务关联，适合业务构成及共现概览 | 多标签，每组按case_id去重，组数和占比之和可能超过总数/100%；不能把信贷与票据组相加，不能由规则命中断言分类完整 |
| `document_number_year` | 文号发文年分布，适合查看跨公告期发出的文号年份 | 不等同原决定年；冲突/未知保留unknown，不能派生月或季度 |
| `document_jurisdiction` | 文号推定的具名机关辖区 | 歧义/无映射为unknown；不是机关驻地或当事人所在地 |
| `document_province` | 文号推定省级辖区，跨省比较 | 单省无区分度；全国机关/未知按返回保留，直辖市按省级辖区 |
| `issuer_seat_city` | 文号机关驻地城市，适合把已映射省局按办公驻地归入杭州/南京 | 仅已确认驻地规则；不代表辖区全部在该市、当事人位于该市或违法发生于该市 |
| `stage` | 在明确包含多个阶段的查询中比较正式决定、拟处罚、执行、撤销 | 默认仅decided时只有一组；阶段标签不代表当前法律效力已核实 |

## 上海与直辖市：如何解释unknown

已核对的 `nfra-jurisdictions-2026-10-13-v2` 映射将上海监管局的记录置于 `province:shanghai`，相应省级机关的 `city_id` 为空。历史目录提供 `province:shanghai` 和 `jurisdiction:shanghai:上海`，不提供一个可凭空构造的 `city:shanghai:上海`。

因此仅上海监管局的样本按authority_city统计可能全是unknown，这是该层级字段不适用的表现，不能补成上海、当作全部地区映射失败或认定没有处罚。全国及其他机关的unknown还可能来自未核验/歧义映射，必须结合记录的authority、level、jurisdiction、目录及映射版本判断，不能把所有unknown统一标为“不适用”。

单独查询上海时通常选month、business_type、party_type；跨省比较选authority_province；浙江等一省内多个地方监管辖区比较可选authority_city，同时保留省级机关的unknown。authority_*表示原机关辖区；document_*表示文号推定辖区；issuer_seat_city另表示机关驻地。三者都不代表当事人住所、总部或违法发生地。推定可用ID与状态查catalog.inference，不能沿用旧目录自行拼造城市ID。

若33/33均为unknown，明确报告分母33和unknown33，并说明维度在该样本上无可用区分度；可建议其他维度，但不删除用户明确要求的分布，也不为了画图填造城市。

## 多维、Top N与覆盖

- `group_by=["month","business_type"]` 返回 `statistics.groups.month` 与 `statistics.groups.business_type` 两个**独立分布**，不是“月×业务”的交叉表。需要交叉分析时，说明API没有直接返回交叉表；仅在用户需要、取得当前筛选集完整明细且口径一致时另做交叉分析，并标明客户端派生及多标签去重规则。
- 分组的分母读返回的 `denominator`，总数读 `total_records`。`multi_label=true` 时不要求组和等于总数；单值分组需保留unknown、national及other后核对完整分区，不能只加Top N。
- API时间分组按date.basis选择原日期或服务端文号年/回退值。document_number_year基准的month/quarter为unknown；decision_or_document_year只有真实日精度可分到具体月季。原始日期保留，推定年单列并标明服务端版本与依据；仅知年份时不能分配到月份/季度。候选获取与核对见 [文号推断与集合核对](inference-and-checks.md)。时间分组不自动补零、计算同比环比。
- 当前业务分组保留全部一级业务类别，不受top_n截断；其他非时间分组可能产生other并保留unknown/总局。以实际返回为准，不能丢掉这些组后仍标注“全量”。
- 展示 `classification_coverage` 的未分类/待复核/派生覆盖；503 `DERIVED_DATA_NOT_READY` 时说明统计暂不可用，不改用首屏自行汇总。
- 金额只使用 `single_explicit_fines.min_yuan/max_yuan` 的“已提取明确罚款金额范围”，附minimum/maximum原文依据、样本分母、可提取/排除数及原因。兼容字段名保留，已移除yuan合计。不能由明细自行累计、计算平均数/中位数，也不能把整案金额归于某单项业务或据金额评价处罚轻重。
set_stats的金额分母是本集合全部有效ID；query/stats保留正式决定分母，不能互换。混合公示由后台排除，即使原fine_status是single_explicit也不加回。无样本时min_yuan/max_yuan为null，明确范围未知；有原文明示零金额时才返回0.00。
