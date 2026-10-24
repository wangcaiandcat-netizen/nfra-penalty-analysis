---
name: nfra-penalty-analysis
description: 通过DeepFin查询金融监管处罚案例，支持银行类别、个人处罚与同名证据核查、同口径期间统计和Excel导出。用于已配置的处罚库。
metadata:
  version: "2026.10.24-r16"
---

Windows使用已有Python 3.10+执行`py -3 -I -B -X utf8 scripts/run.py`，Linux/WSL使用`sh run.sh`；`run.cmd`仅为可选便捷入口，平台移除它不影响直接调用Python入口。只需Python 3.10+标准库，查询和Excel导出均不安装依赖。默认生产后台`https://deepfin.vip`，显式地址或`DEEPFIN_API_BASE_URL`可覆盖；只读Key仅从`DEEPFIN_PENALTIES_API_KEY`读取。不寻找.env或历史凭据，不扫描/切换后台地址。环境检查由入口自动完成。网络沿用宿主已配置的HTTP(S)代理与NO_PROXY，未配置时直连；不修改代理设置、不创建本地转发服务，连接失败停止并报告，不额外加重试。本版适配后台接口契约1.2.0；后端就绪与错误处理见[接口参考](references/api.md)。

## 查询与文件

简单查询直接用参数；相对日期按本次北京时间换成具体范围，未指定日期时不默认查两年。默认公示日期、正式决定、排除已标记重复、前20条；`--all`取全且无导出时默认完整展示；`--view brief/full`只控制展示，brief默认5条，可用`--case-limit`调整。分别说明匹配数、已获取数和已展示数。`--output`自动取全并只保存最终xlsx。

```powershell
py -3 -I -B -X utf8 'C:\本技能目录\scripts\run.py' search --from 2025-01-01 --to 2025-12-31 --province 四川 --institution-type guogu --keyword 保证金
py -3 -I -B -X utf8 'C:\本技能目录\scripts\run.py' search --name 张明 --exact --employer 广发银行 --subject person
py -3 -I -B -X utf8 'C:\本技能目录\scripts\run.py' search --from 2026-01-01 --to 2026-06-30 --subject person --personal-stats --output 'C:\任务目录\个人处罚.xlsx'
```

类别、业务、辖区代码不明确时查一次`catalog`并复用。行分类只用后端`institution_types`，国股为`guogu`，不拉全库按银行名称自行分类。地区默认发文机关监管辖区。姓名匹配受罚主体；任职关系使用`--employer`。用户查个人默认`--subject person`，查机构默认`--subject institution`；混合公示由后端另列，不并入主结果。`--include-mixed`仅用于用户明确要求将任职相关混合记录纳入查询。

复杂查询使用JSON入口：`workflow --input - [--all] [--output 台账.xlsx]`；详情、批次、指定集合、公告案例分别用`detail/batch/set_stats/document --input -`。只传实际查得的case_id，不能由doc_id拼造案例ID。请求结构见[接口参考](references/api.md)，例子在`assets/requests/`。复用同一份内存响应生成摘要、证据和文件，不另写取数/统计脚本，不用head/grep截断后猜字段。

## 比较与个人统计

只需期间数量、措施、期限或金额样本比较时，用`compare --summary-only --input - [--output 对比.xlsx]`。每期只调用一次全筛选集统计，不为同比拉取全部明细。输入示例见[个人统计](references/personal-statistics.md)；需要完整案例台账时才用原`compare`取全分页。

个人统计设置`statistics.include_personal_sanctions=true`（简单查询为`--personal-stats`）。直接使用`statistics.personal_sanctions`：纯个人记录、明确个人主体提及次数、完整身份归并数量、身份不足数量分别说明。混合公示只另列记录/公告数，不拆人数；不按姓名去重或把主体提及称为真实人数。处罚类型可叠加；期限按后端明确归属的个人措施，不按整条公示的字样数人。金额只描述后端已提取样本的最小值、最大值、对应原文和覆盖；不累计金额，不计算平均数或中位数，不自行从金额明细补算。无法确认明确写未知。

身份沿用后端“完整姓名＋同一具体机构＋同一职务”一致可认定同一人的规则；缺失/冲突不自行合并。逐人处罚只读`person_sanctions`及原文依据，不用本地正则、机构任职关系或首名补配。

## 回答与交付口径

- 记录行、公告、独立案件、姓名提及和身份归并分别表达。零结果只说“本库按本次条件未检出”，不自动扩大到全国或其他年份后声称无遗漏。
- 分开说最新已发布公告日、目录已核查至日期、待复核公告数。待复核是全库口径，不能直接归给本次地区。
- 保留原决定日期；`decision_time.status=inferred`只称文号年推定，不能改写为确定决定年份。
- `fine_context`说明金额用途、是否含多项违规及是否为金额范围样本（兼容字段`aggregation.included`）；`aggregation_enabled=false`，不再汇总罚款。整案金额不能称为命中业务的单项罚款；机构人员任职关系不能证明机构自身也受罚。
- 金额只读`single_explicit_fines.min_yuan/max_yuan`及`minimum/maximum`依据，个人金额读`personal_sanctions.pure_person.fine_amounts`同名范围字段；说明样本数、排除数及并列依据是否截断。无样本为null/未知，不能写0。旧后台仍返回`yuan/sum_yuan`时停止，不能改用本地求和或计算范围。
- 禁止用自己的口吻输出“最差、最好、更差、更好、更严重、最严重、最轻、最重、雷区、处罚变轻/变重”等主观判断，不作机构或人员优劣排名。最大/最小只指已提取金额数值；不得据此评价处罚轻重、监管强弱或个人风险。原文中的“严重”等词仅可带来源明确引用。
- 不把公示形式、记录数或金额样本变化解释为原因、监管意图或趋势结论；用户要求原因而证据不足时明确“当前数据无法确认原因”。零结果不强制跨年扩查，放宽条件的结果不能证明原条件下实际没有处罚。
- 历史任务记忆与本版规则冲突时以当前契约和响应为准。`top_n`上限50；summary-only的brief/full均保留完整统计，不因宿主截断打印而重查。
- 分页和期间锁定相同数据修订；409重新开始，失败不能当零。429展示Retry-After，停止自动重试；401/403交由用户处理权限。文件交付到任务目录，不覆盖已有文件。
- `FINE_DATA_NOT_READY`表示金额主体识别数据未就绪，统计结果未知；保留已成功取得的案例，说明可另用不含统计的`query --input -`查询原文，先预演后执行。不能本地补算、声称零罚款或自动反复重试。分类/个人派生数据未就绪同样停止对应统计。

Excel保留后端主体构成、金额用途/纳入依据、逐人原文证据、身份分组和统计原值。混合预览在独立表，并标明可能截断；未取得的混合明细不冒充完整台账。更多导出方式见[Excel说明](references/excel-export.md)。

所有最终答复基于实际返回事实，列明实际条件、数量和局限；需要逐案引用时给case_id与官方链接。每次最终自然语言答复末尾独占一行写：AI生成内容，仅供参考。机器JSON使用disclaimer字段，不在JSON后追加散文。
