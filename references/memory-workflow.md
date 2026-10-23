# 默认内存工作流

## 两年或多个期间：直接用compare

对比优先`run.py compare --input -`，不分别调用workflow再写临时脚本合并。一次调用完成2至5个期间的分页、摘要及可选合并Excel；不创建请求/响应JSON。以下为固定2024/2025示例，实际相对年份按本次北京时间解释。

```powershell
$entryPath = 'C:\已知安装目录\run.cmd'
$periods = foreach ($year in 2024,2025) {
    @{ label = "$year"; request = @{
        filters = @{
            date = @{ from = "$year-01-01"; to = "$year-12-31"; basis = 'publication' }
            regions = @{ ids = @('province:shanxi'); scope = 'province' }
            business = @{ codes = @('bills') }
        }
        statistics = @{ group_by = @('month','business_type','subject_composition','issuer_seat_city'); top_n = 50 }
    }}
}
$compareJson = @{ periods = @($periods) } | ConvertTo-Json -Depth 15
$compareJson | & $entryPath compare --input - --output 'D:\本次任务\两年对比.xlsx' --dry-run
$compareJson | & $entryPath compare --input - --output 'D:\本次任务\两年对比.xlsx'
```

各期起止日期须完整、按先后排列且不重叠；日期基准、非日期筛选一致。全部请求先在本地检查，首期返回修订后锁定后续期间；各期只取必要页，首次附统计。累计最多50页/32MiB，失败不重试、不生成部分Excel。目录需要时只查一次，已有目录修订可写入每期request.expected_data_revision。

默认brief每期最多20条代表案例，自带case_id、官网链接、处罚依据及显示范围；`--view full`展示全量。不要用head/tail、grep或临时Python再裁剪。各期独立保留API金额、纳入/排除及业务分布，不相加跨期金额，也不把记录数当人数。`execution`列总查询数；一页一期间时两年共2次，导出不追加查询。Excel汇总及明细来源明确标注期间。

持久进程中用`from comparison import run_comparison`取得`result`；`result.view()`、`result.period('2024').case(case_id)`和`result.export(path)`复用原响应。跨进程不能恢复内存对象，优先在初次compare调用指定最终文件。历史会话若记有“重新查询后拼bundle导出”的做法，改用当前入口。

## 查清单或全量：直接用workflow

普通单页查询、用户要求列全、或宿主选择Excel时，使用统一入口，避免在任务目录创建请求JSON、分页脚本和bundle。输入是原有query结构，不增加workflow专用JSON字段。

按银行类别时在同一filters中使用institution_types，国股用codes=["guogu"]、basis=respondent_or_employer。无需拼18家银行简称，也不额外加subjects.names。brief/full、case()、金额排序卡片和export()均保留原institution_classification及名录时点、状态、版本和证据；未知不补分类。完整样例见[recipes](recipes.md)。

```powershell
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$requestJson = @'
{"filters":{"date":{"from":"2025-01-01","to":"2025-12-31","basis":"publication"},"business":{"codes":["bills"]}}}
'@
$entryPath = 'C:\已知安装目录\run.cmd'
# 普通查询：默认20条，不创建任何结果文件。
$requestJson | & $entryPath workflow --input - --dry-run
$requestJson | & $entryPath workflow --input -
# 用户要完整台账：先检查目标，完整预演后执行同一内存请求。
$requestJson | & $entryPath workflow --input - --output 'D:\本次任务\台账.xlsx' --dry-run
$requestJson | & $entryPath workflow --input - --output 'D:\本次任务\台账.xlsx' --title '票据处罚台账'
```

日期2025只是样例，不代表任何时候的“去年”。--all表示取全；--output自动取全，未明确page.size时用100。普通查询仍默认20。CLI默认brief（--all无文件时默认full），stdout仅一份有效JSON；实际执行始终逐页本地预演，只有--show-previews才在stderr输出预演事件。首次--dry-run仍显示完整计划，不读地址/凭据。不要合并stderr后再grep、head/tail或写Python后处理。

同进程已有Client时可直接调用，无需临时.py：

```python
from workflow import run_workflow
# request与client均为已有内存对象；catalog只查一次并保留其修订。
request["expected_data_revision"] = catalog["scope"]["data_revision"]
report = run_workflow(request, client=client, all_pages=True,
                      output="/本次任务/台账.xlsx", title="票据处罚台账")
# 下列操作使用同批内存响应，不发API请求、不创建中间文件。
summary = report.view("brief")  # 默认5条代表案例、最多20段禁业原文，范围明确。
all_cards = report.view("full")["cases"]
one_card = report.case(all_cards[0]["case_id"]) if all_cards else None
# 已取全后，如用户随后要求另一份文件，也复用report：
# report.export("/本次任务/另一份台账.xlsx")
```

workflow首请求附完整筛选集statistics，默认含month/authority_province/issuer_seat_city/party_type/business_type，top_n=50；显式statistics原样保留。用户需要其他维度时在首次请求指定，不为看分布重新查同一批数据。后续页只取明细。固定筛选/修订，用reconcile核对再导出。最多50页、累计32MiB；超限停止。409/429/503不重试。

brief默认5条代表案例，compare每期20条；--case-limit可调，case_display明确显示数与覆盖。两种视图均保留violation、penalty、decision_time及原始person_sanctions；不再因brief缺案由而读Excel。统计groups.*.items沿用API对象数组，使用name/count，不按旧版二元数组解析。金额只用服务端fine_amount_range的最小/最大值及原文依据，已取消金额排名；prohibition_items只取后端industry_ban/disqualification，不是人数。full看所有已取卡片；search --all且无导出时默认full；view/case/export均不重查，execution记录查询数。summary-only的brief/full均保留完整统计。跨进程不假定内存变量存在。

penalty_attribution只投影后端person_sanctions，不再解析“对某人/禁止某人”重建对应。缺失、未识别或待核结构保留完整原文与未知状态；原始结构在brief/full及Excel证据页保留。首响应person_identity_groups与逐页person_identity_groups_pages均保留原scope，不按姓名或页内分组数合并人数。所有展示按output_policy客观陈述，最终自然语言末尾加disclaimer。

仅需统计、batch、明确ID集合等模式仍用下面的底层内存调用。已有响应可直接离线export，不为使用workflow重新取数。

## 默认不生成中间文件

普通问答把请求、预演、分页响应、选集和核对结果保存在当前进程变量里，不创建请求JSON、响应JSON、日志、核对文件或临时脚本。客户端接收stdin、输出stdout；不要用重定向、tee或循环写文件为每页生成副本。随包assets请求可直接读取，无需复制。

用户要求Excel/CSV/JSON、留档/审计材料或可恢复快照时，才保存必要交付物。Excel使用随包export_xlsx.py：在内存组装XML/ZIP，最终只保存一个.xlsx；入口run.py export会前置检查环境和目标目录。中间对象可留在内存，不为每一步附送JSON或脚本。

## CLI：stdin输入，stdout接收

PowerShell示例只使用变量。clientPath换成当前已知的Skill安装路径，不创建临时.py或.json。UTF-8设置仅在当前执行进程生效。

```powershell
$clientPath = 'C:\已知安装目录\scripts\run.py'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$requestJson = @'
{"filters":{"date":{"from":"2025-01-01","to":"2025-12-31","basis":"publication"},"business":{"codes":["bills"]}},"page":{"size":20}}
'@
$planJson = $requestJson | python -B -X utf8 $clientPath query --input - --dry-run
if ($LASTEXITCODE -ne 0) { throw '请求预演失败，停止执行' }
$planJson
# 宿主核对上述条件后，执行同一requestJson。
$responseJson = $requestJson | python -B -X utf8 $clientPath query --input -
if ($LASTEXITCODE -ne 0) { throw '请求失败，按stderr错误处理' }
$responseJson
```

变量只在同一Shell进程有效；工具每次启动新进程时，不假定变量跨调用保存。宿主有持久运行时可使用它，否则同一次Python执行完成连续查询、分页和核对。输出仍可能由Workbuddy记录在聊天/执行日志，Skill不管理宿主自身记录。

## 连续查询：直接使用Python对象

用python -B启动，或导入Skill模块前设置sys.dont_write_bytecode=True，将已知安装目录的scripts加入sys.path。无需创建临时脚本，可使用宿主的内存执行或python -c。

```python
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, "/已知安装目录/scripts")
from penalty_client import Client
from result_check import reconcile

client = Client()
request = {"filters": {"business": {"codes": ["bills"]}}, "page": {"size": 20}}
plan = client.execute("query", request, dry_run=True)
# 宿主先核对plan，再执行同一request。
response = client.execute("query", request)
case_ids = [row["case_id"] for row in response["results"]]
if case_ids:
    set_request = {"sets": [{"set_id": "fetched", "case_ids": case_ids}],
                   "expected_data_revision": response["scope"]["data_revision"]}
    set_plan = client.execute("set_stats", set_request, dry_run=True)
    set_response = client.execute("set_stats", set_request)
    checked = reconcile([response, set_response])
```

这里只统计已获取首屏集合。要全部明细时按原条件、同一修订及返回游标逐页读取，不用首屏冒充全量。每页处理后释放不再需要的完整对象，仅保留必要字段/ID；仍遵守默认20/最大100条、单响应32MiB上限及API限流，不在一次任务下载全库。金额由set_stats取得，不改成本地累加或跨组相加。已有bundle可调用export_xlsx.export_workbook(bundle, output_path)，或经run.py export --input - --output传入；无须为导出另存JSON。详见 [Excel导出](excel-export.md)。

## 核对也能走stdin

result_check.py --input -接收一个API响应；多个响应和选集使用--bundle -，输入对象格式：

```json
{"responses":[{"scope":{"data_revision":3},"results":[],"pagination":{"total":0,"returned":0,"has_more":false,"total_exact":true}}],"selection":null}
```

```powershell
$bundleJson | python -B -X utf8 $checkerPath --bundle -
```

bundle仅接受responses和可选selection，总输入最多32MiB；更大的已获取数据可在同进程中调用reconcile并合理控制所保留对象。stdin一次只消费一份JSON；继续支持--input 文件、--selection 文件，供主动留档后复核。内存输入同样拒绝重复JSON键、非有限数字、混合修订/规则版本和集合覆盖矛盾。示例中的修订3仅为结构示意，真实请求始终取当次API修订。

## 必须使用临时磁盘时

优先io.BytesIO/StringIO等内存对象。导出工具必须接收路径、数据超出可用内存或用户要求断点恢复时，可用任务专属系统临时目录，例如TemporaryDirectory上下文，成功和异常退出时清理本次创建的中间文件。强制终止可能留下残余，不承诺所有工具绝对零落盘。

不在Skill安装目录或工作目录散落tmp_*.json，不删除用户已有文件。最终交付物写入用户指定位置并提供真实链接；日志和原始JSON按任务需要保留，不附认证头或凭据。
