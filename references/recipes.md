# 贴近用户问题的常用查询 recipes

下面展示底层接口的请求口径。案例清单、列全和新建Excel优先把同一query请求交给 `run.py workflow --input -`；加--all取全，加--output自动取全并导出。动态条件保持内存对象，不复制样例或另写分页脚本。已有目录只在内存复用，不为看不同字段重复调用catalog。

下列文件参数读取随包已有样例，不产生新请求文件。动态查询默认在内存构建JSON，通过--input -传入，预演和执行复用同一份内容；见 [内存工作流](memory-workflow.md)。从安装后的Skill目录执行，Windows可将python3换成python。样例是**固定2025年条件**，不是永久“去年”或当前季度。先运行catalog确认本次目录仍支持地区和业务代码；再按用户需求改日期、主体和代码。没有目录时保留待确认条件，不悄悄改成keyword检索。

## 按银行类别查询：国股2025年票据处罚

完整请求：[guogu-2025-bills-query.json](../assets/requests/guogu-2025-bills-query.json)。用`institution_types={"codes":["guogu"],"match":"any","basis":"respondent_or_employer"}`，不附银行简称或主体类型限制。一次覆盖按后端名录匹配的国有大型商业银行、股份制商业银行及已识别任职关联记录。

```bash
python3 -B -X utf8 scripts/run.py workflow --input assets/requests/guogu-2025-bills-query.json --dry-run
python3 -B -X utf8 scripts/run.py workflow --input assets/requests/guogu-2025-bills-query.json --all
```

需要Excel时将同一请求交给workflow并加`--output 绝对路径.xlsx`。动态条件仍在内存构建；2025是固定样例，实际“去年”按本次北京时间换年。国股是后端组合码，不在客户端展开18家名单。其他行类别取catalog.institution_types.types；明确只查受罚机构时basis=respondent，只查任职机构时basis=employer。机构类型统计用group_by中的institution_type，不由名称字符串重分类。

brief/full与Excel保留institution_classification及名录as_of_date、状态和证据。类别来自该时点法人名录快照，不代表当前或处罚发生时类别；分支只继承类别，不归并法律主体。foreign_legal_entity_bank仅指外资法人银行；未命中不补作其他银行。失败时说明失败，不改用名称拼单或全文关键词并声称类别全量。

## 1. 某地区、某季度、某业务：上海2025年第三季度票据处罚

完整请求：[shanghai-2025-q3-bills-query.json](../assets/requests/shanghai-2025-q3-bills-query.json)。

```bash
python3 scripts/penalty_client.py catalog
python3 scripts/penalty_client.py query --input assets/requests/shanghai-2025-q3-bills-query.json --dry-run
python3 scripts/penalty_client.py query --input assets/requests/shanghai-2025-q3-bills-query.json
```

预演核对：2025-07-01至2025-09-30、publication、province:shanghai、scope=province、bills、decided、include_duplicates=false、page.size=20。province范围包括目录确认的省域下辖机关；若只要某具名机关辖区，应改为该目录的jurisdiction ID和scope，不能仅替换一个中文城市名。

把样例作为完整JSON结构参考，动态条件在内存中修改，不直接照抄期望数量。`bills`是含子业务的父码；“票据贴现”应按目录改为 `bills.discount`。不要额外限定party_types，否则可能排除混合主体公示。结果总数读pagination.total，前5条展示可从默认首屏20条中选取，不自动把size改成100。

成功返回0条是当前筛选集为空，不是连接失败；保留用户条件、修订、覆盖和warnings，说明零结果不代表从未受罚。若建议放宽时间或业务，先明确这是另一个查询，不能静默改条件或借用其他范围的结果。

## 2. 同一筛选口径：月度、业务关联、主体类型统计

完整请求：[shanghai-2025-q3-bills-stats.json](../assets/requests/shanghai-2025-q3-bills-stats.json)。日期与地区业务条件和recipe1一致。

```bash
python3 scripts/penalty_client.py stats --input assets/requests/shanghai-2025-q3-bills-stats.json --dry-run
python3 scripts/penalty_client.py stats --input assets/requests/shanghai-2025-q3-bills-stats.json
```

读取statistics.total_records和groups.month/business_type/party_type，分别展示三个独立分布；业务是多标签，不能加总为处罚总数。这份上海样例不把authority_city当默认分布；适用情形见 [统计维度选择](statistics.md)。

需要与recipe1保持同一数据快照时，将第一份真实响应的scope.data_revision填入后续请求的expected_data_revision，再预演执行；不要写死历史修订1。409时重新开始同口径查询，不能静默丢掉修订限制。

## 3. 多家机构核查并交付台账：工行与建行2025年票据处罚

完整请求：[banks-2025-bills-batch.json](../assets/requests/banks-2025-bills-batch.json)。

```bash
python3 scripts/penalty_client.py batch --input assets/requests/banks-2025-bills-batch.json --dry-run
python3 scripts/penalty_client.py batch --input assets/requests/banks-2025-bills-batch.json
```

核对两个子请求各有完整filters，名称包含匹配，不限制主体类型和辖区，每项默认20条。逐项报告成功/失败/成功空结果及pagination.total，合计直接读取外层statistics.union_records，保留successful_requests/failed_requests/overlap_memberships；不能相加子项数量。

用户要完整台账时按各子项的同条件、同修订游标取得完整筛选明细，合并台账按case_id保留一条并记录命中了哪些request_id；**并集合计仍以服务端字段为准**。银行名称匹配不等于已核实集团或法人关系，不擅自将分行处罚改写成总行处罚。交付字段与归并映射见 [结果交付](delivery.md)。

## 4. “按文号年份查2025年，发文机关驻地在南京的处罚”

完整请求：[nanjing-2025-document-year-query.json](../assets/requests/nanjing-2025-document-year-query.json)。使用document_number_year全年与issuer_seat城市口径；query、stats、batch可使用同一filters。机关辖区和机关驻地区分展示，不能回答成“南京地区全部处罚”。

```bash
python3 scripts/penalty_client.py query --input assets/requests/nanjing-2025-document-year-query.json --dry-run
python3 scripts/penalty_client.py query --input assets/requests/nanjing-2025-document-year-query.json
```

“杭州2025年有多少条”若目录没有可用的杭州地方辖区，可直接按已允许的文号机关驻地推定完成，用catalog.inference里的city:zhejiang:杭州及issuer_seat，回答注明实际口径；不以浙江全省冒充杭州市。用户明确指定其他口径时遵循该口径。只有年份的记录不进入2025Q1，也不填成1月1日。

## 5. “任职机构带淮安的责任人，有哪些处罚？”

完整请求：[huaian-affiliation-query.json](../assets/requests/huaian-affiliation-query.json)。使用affiliations，不加subjects.names，不加用户未要求的日期/辖区。默认不含混合公示；若需要含合并事项，明确改include_mixed=true并保留整行。

```bash
python3 scripts/penalty_client.py query --input assets/requests/huaian-affiliation-query.json --dry-run
python3 scripts/penalty_client.py query --input assets/requests/huaian-affiliation-query.json
```

回答是“已识别的任职关系中命中N条公示记录”，保留institution/person/evidence；不是全部淮安处罚，也不能推断未命中的人从未受罚。

## 6. “这篇公告具体有哪几条案例？”

完整请求：[document-cases.json](../assets/requests/document-cases.json)。doc_id=1221496是一个公告示例，实际任务使用用户链接中的docId；先预演再执行document。

```bash
python3 scripts/penalty_client.py document --input assets/requests/document-cases.json --dry-run
python3 scripts/penalty_client.py document --input assets/requests/document-cases.json
```

取返回的真实case_id，不猜行号；需要全部时跟随同doc_id、同修订游标。该列表覆盖全部已发布阶段及已标记重复行，不等于默认query口径；空列表不证明官网没有公告。

## 7. “我选中的这些案例共几条、几篇公告、罚款多少？”

完整请求：[selected-cases-stats.json](../assets/requests/selected-cases-stats.json)。样例仅选一个已知真实ID；实际从本次查询取得ID再替换，并写入该次scope.data_revision。多个用户集合各设set_id，不能相加交叠组。

```bash
python3 scripts/penalty_client.py set_stats --input assets/requests/selected-cases-stats.json --dry-run
python3 scripts/penalty_client.py set_stats --input assets/requests/selected-cases-stats.json
```

逐组取matched_case_ids和该组statistics，不从本页重算或套用历史数字。若未知ID/部分失败、金额排除混合行，说明有效集合、失败项和纳入覆盖；没有可纳入金额时不说确认零罚款。进一步流程见 [集合核对](inference-and-checks.md)。

本包仅提供请求样例和规范，不含历史案例结果、Key或固定处罚条数。完整字段见 [接口契约](api.md)。
