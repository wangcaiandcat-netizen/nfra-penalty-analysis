# 外部 API 契约

r16默认连接生产HTTPS后台，查询、金额范围与请求结构沿用r15及后台1.2.0契约。`scope.api_contract_version`标识接口契约，`scope.data_revision`标识案例数据修订，两者不能混用。接口补充服务端响应校验；客户端继续复用第一页的全筛选集统计，后续页携带相同修订并设置`statistics=null`。

地址优先级：--base-url → 非空 DEEPFIN_API_BASE_URL → https://deepfin.vip；空环境直接默认调用。显式错误/失败不切换地址。HTTP只允许环回，远程用HTTPS；沿用宿主已有的HTTP(S)代理与NO_PROXY，保留系统证书校验，不跟随重定向，不创建转发服务。[连接说明](connection.md)。

所有路径相对于 /api/v1/external/penalties；默认匿名，鉴权开启后如下：

| CLI | 方法与路径 | 权限 |
|---|---|---|
| catalog | GET /catalog | penalties:read |
| query | POST /cases/query | penalties:read |
| stats | POST /stats/query | penalties:read |
| batch | POST /cases/batch-query | penalties:read |
| detail | 单次POST /cases/batch-query，每个ID一项 | penalties:read |
| set_stats | POST /stats/sets | penalties:read |
| document | GET /documents/{doc_id}/cases | penalties:read |
| explain | POST /explain | penalties:ask |

接受模式专用对象或带mode/output的 [AnalysisRequest](../schemas/input.json)。子命令须与mode一致；mode/output不发送给专用接口。客户端输出JSON；宿主负责自然语言理解和结果呈现。

## 过滤条件（query/stats及batch子项共用）

| 字段 | 值及上限 | 语义 |
|---|---|---|
| date | from/to为ISO日期；basis=publication（默认）/decision/document_number_year/decision_or_document_year | 首尾包含；年精度仅在整个自然年被包含时纳入；回退优先原决定日，再文号年，不回退公告日 |
| subjects | names最多50个；match=contains/exact；party_types=institution/person/unknown | 名称OR；个人只匹配姓名，不包含括号任职机构 |
| regions | ids最多50个；scope=jurisdiction/province/city；basis=authority_jurisdiction（默认）/document_jurisdiction/issuer_seat | issuer_seat仅province/city；新推定ID查catalog.inference.regions，原目录不一定有驻地城市 |
| document_number | years最多50个整数1900–2100；prefixes最多50个1–60字的字面前缀 | 各列表内部OR，与日期及其他条件AND |
| affiliations | institutions最多50个；match=contains（默认）/exact；include_mixed=false（默认） | 机构OR，仅confirmed任职关系；true显式包含合并公示原行 |
| institution_types | codes最多30个、单码1–100字；match=any（默认）/all；basis=respondent_or_employer（默认）/respondent/employer | 类别/组合码查catalog.institution_types；guogu为国股组合，unknown为未知；受罚机构与明确任职机构按basis匹配，不用名称列表替代 |
| doc_ids | 最多50个正整数 | 限定来源公告；仍应用其他默认过滤，不同于document接口 |
| business/themes | codes最多30个目录码；match=any/all | 父业务包含子业务，多标签 |
| stages | 默认[decided]；可选proposed/enforcement/revoked | 至少1个 |
| include_duplicates | 默认false，必须布尔值 | 排除已标记重复 |
| keyword | 最多200字、8个空白分隔字面词 | 多词AND |

未知代码/地域组合及规则就绪由API校验，客户端不复制目录映射。document_number.years是独立条件；同时指定公告日期时取交集。

## 各模式参数

- query：filters、page（size默认20，1–100；cursor为原样签名串）、可选statistics、expected_data_revision。
- stats：filters、statistics、expected_data_revision；无分页。
- statistics：group_by从15维选1–15个，含subject_composition、institution_type，top_n=1–50。多个维度是独立分布；完整语义见 [统计维度](statistics.md)。
- detail：case_ids=1–50个；explain=1–10个，另可提供question=1–1000字；都可提供expected_data_revision。
- batch：requests=1–50个，唯一request_id；每项case_id或filters二选一，条件项可带page/statistics。合计取服务端完整筛选集并集。
- set_stats：sets=1–10项，每项唯一set_id（字母数字下划线连字符，1–100字符）、case_ids=1–1000个、可选expected_data_revision及include_duplicates=false；全请求最多5000原始成员（含重复）。顶层可带statistics（默认group_by=[stage]）和expected_data_revision；无filters或page。
- document：doc_id为正整数，page及expected_data_revision。客户端将page.size/cursor和修订编码为GET查询参数，body=null；默认20行，最大100；不自动翻页。

## 返回与业务边界

原样保留 scope/applied_filters/results/pagination/statistics/completion_status/warnings及返回的person_identity_groups。scope包含data_revision、inference_version、association_version和inference_coverage；保留每案original_party_type、document_number_inference、decision_time、party_associations、person_sanctions及原始字段。[文号/关系/集合解释](inference-and-checks.md)。

每案institution_classification为后端法人名录分类原结构，包含status、matches、type_codes、respondent_type_codes、employer_type_codes、version、as_of_date、unresolved和note等。原样保留关系、继承标志和来源/案例证据；未知字段也不能删。按名录快照解释，不把分支继承类别当同一法人，不补算未匹配机构。query/stats/batch及workflow/compare沿用相同institution_types语义；没有显式筛选时保持旧默认。

person_sanctions含subjects、姓名/具体机构/职务确认状态、identity、逐项sanctions及证据、期限与未解析项。顶层字段存在时保留原值（包括null/unknown），不拿嵌套结果覆盖；顶层缺失时可采用party_associations.person_sanctions，并注明来源。不得本地重解原文补配处罚。person_identity_groups的policy/scope/groups/references等按原结构保留，范围可能仅本次返回记录，不是全库去重人数；跨页分别保留，不按姓名归并或累加分组数。

detail 外层results为request_id子响应，实际对象在 `results[i].results[j].item`；分别检查外层和内层status。客户端另加requested_case_ids/requested_case_id，以实际成功item读取字段。

query.pagination.total及statistics.total_records是完整匹配数，returned是本页数。批次union_records为成功子集完整并集，overlap_memberships不是独立重叠案例数。业务多标签不可加总。

set_stats逐组包含set_id/status/data_revision/id_resolution/selection_signature/statistics/error：
- id_resolution保留submitted_members、unique_requested、duplicate_input_members、found_records、unknown_ids、excluded_marked_duplicate_ids、matched_case_ids、amount_denominator、exclusion_reasons。
- statistics的数量、公告数、金额样本绑定matched_case_ids；single_explicit_fines只返回min_yuan/max_yuan十进制元字符串及minimum/maximum原文依据；无样本为null。aggregation_enabled=false，不返回合计yuan。保留denominator、included_records（可提取样本数）、excluded_records、exclusion_reasons、zero_amount_records、is_complete_total=false及denominator_scope。
- 有未知ID的组可partial，全部未知为failed（即使统计为0仍保留错误）。某组修订失败不影响其他组；顶层修订不符HTTP409，整个请求不计算。
- 各集合独立统计，不产生跨组金额总和。默认排除库内标记重复，同ID重复提交始终只计一次。

document覆盖该公告当前修订下全部已发布阶段及已标记重复行，返回标准案例字段；detail额外提供original_fields/content_hash，核对时比较共有字段。需要原始表格字段时再读取detail。unknown doc_id为成功空列表，不能据此断言官网公告不存在。游标绑定doc_id、修订及规则；doc_id不能拼成case_id。

## 错误处理

stderr结构化错误并非零退出：输入2、配置3、HTTP4、网络5。客户端不输出认证头或原始远端错误正文；HTTP422可能统一显示VALIDATION_FAILED，应回查请求/游标条件。401 AUTH_REQUIRED保留安全login_url/register_url供用户打开，403不升级权限；409重新查询；429尊重Retry-After；503可能为派生缓存未就绪。客户端不自动重试或移除修订。explain的model_analysis_available=false为原文字段整理回退。默认匿名及可选read/ask Key环境变量见 [connection.md](connection.md)。

1.2.0的`FINE_DATA_NOT_READY`（503）表示金额统计依赖的主体识别缺失、过期或内容哈希不匹配，不能当作无记录/零罚款。`DERIVED_DATA_NOT_READY`、`INSTITUTION_CLASSIFICATION_NOT_READY`也保留为明确错误码。客户端只保留白名单机器码与本地固定说明，兼容`error.code`和`detail.code`包装，不透出服务端原始正文。可用不含统计的query读取案例；简单search/workflow默认含统计，遇到此错误不会自动降级或重算金额。数据修订冲突仍以`DATA_REVISION_CHANGED`（409）报告，重新查询时由宿主明确重新开始。

explain的模型分析文字不能代替原文事实。最终答复仍须遵守SKILL.md的客观输出规则，不转述无证据的轻重、优劣或因果判断，并以“AI生成内容，仅供参考”结尾；机器JSON使用disclaimer字段，不追加破坏JSON的尾行。
