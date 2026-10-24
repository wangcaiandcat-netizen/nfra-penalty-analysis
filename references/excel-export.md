# 环境预检与Excel导出

## 统一入口先检查，再加载业务代码

Windows从PowerShell用已有Python执行py -3 -I -B -X utf8 scripts/run.py；WSL/Linux用sh run.sh。run.cmd只是可选入口，缺失时仍可直接用scripts/run.py；没有py启动器时使用已有Python 3.10+解释器的明确路径。入口先确认Python>=3.10，再检查标准库和本模式必需文件；通过后才加载业务模块、读取请求、调用API。查询/核对不创建文件。直接Client对象也在构造时预检。

```powershell
py -3 -I -B -X utf8 'C:\已知安装目录\scripts\run.py' check
$requestJson | py -3 -I -B -X utf8 'C:\已知安装目录\scripts\run.py' query --input - --dry-run
$requestJson | py -3 -I -B -X utf8 'C:\已知安装目录\scripts\run.py' query --input -
```

```bash
sh /已知安装目录/run.sh check
python3 -B -X utf8 /已知安装目录/scripts/run.py query --input - --dry-run
```

上例的requestJson来自本次内存构建，仍须核对预演后执行同一请求。脚本不会安装软件、读取.env或调整系统设置；本地预检不检查网络，也不代替真实catalog。Windows入口会尝试已安装的py/python/python3，只选择符合版本的解释器，不更换API地址。

## 先检查本次导出目标

新查询并导出用 `run.py workflow --input - --output 绝对路径.xlsx`；两年或多个期间用`run.py compare --input - --output 绝对路径.xlsx`，一次调用合并交付，各期来源带期间标签。两者同进程完成检查、逐页预演、取全、核对和导出。无需保存请求文件、写分页脚本或build_bundle.py。详见[内存工作流](memory-workflow.md)。下面的手工check与离线export适用于已有响应。

```powershell
py -3 -I -B -X utf8 'C:\已知安装目录\scripts\run.py' check --task export --output 'D:\本次任务\处罚台账.xlsx'
```

选择已存在的任务目录，不能把文件写Skill安装目录。已有同名文件直接拒绝，不覆盖。检查使用一次零字节、自动删除的权限探针，不写案例数据，不遗留中间文件；无需安装Excel、openpyxl或其他第三方包。目录/权限通过不保证后续磁盘状态不变，保存时仍检查错误。

## 输入使用内存中的原始API响应

输入格式为responses数组、可选selection及context。context接受title/request/notes及可选source_labels；source_labels与responses逐项对应，用于期间来源展示，不改变筛选/统计。compare自动生成标签。不要传API Key、认证头或服务端配置。

```json
{"responses":["这里放本次真实query/batch/detail/stats/set_stats响应对象"],"context":{"title":"淮安处罚台账","request":{"filters":{"date":{"from":"2026-01-01","to":"2026-06-30","basis":"publication"}}}}}
```

上例仅说明结构，responses内须为对象。多个分页响应保持同条件、同修订；用户要全量台账时先取完所有页。可直接将现有response、set_response对象组成bundle，不需要先保存JSON。

```powershell
$bundleJson | py -3 -I -B -X utf8 'C:\已知安装目录\scripts\run.py' export --input - --output 'D:\本次任务\处罚台账.xlsx'
```

同进程Python可调用export_xlsx.export_workbook(bundle, output_path)。脚本纯离线处理已返回数据，不自行查询、不翻页、不算罚款、不归并法律主体。

默认遇到缺页、未知ID或失败项拒绝导出，先说明缺失范围。用户只要已取得部分时显式加--allow-partial，工作簿首页与口径页均标为部分结果；混合修订、冲突事实和金额覆盖矛盾即使加此参数也会拒绝。不能通过这个参数把样本改称全量。

## 工作簿内容

| 工作表 | 内容 |
|---|---|
| 统计汇总 | 原样服务端每组数量、公告数、已提取最小/最大金额及原文依据、可提取样本/排除数、有效分母、原因、明确零值；没有统计时留空，不从首屏补算 |
| 明细台账 | 唯一case_id一行、原始当事人/类型、文号、日期、回退时间精度、原辖区/推定辖区/驻地、违法/处罚、原始金额文本、业务标签、官方链接、修订及命中请求；末尾另列后端机构类别名称/代码、分类状态、名录时点、版本及说明 |
| 口径与覆盖 | 实际条件、版本、分页覆盖、全部警告/失败和ID解析；JSON长字段按分段保存 |
| 分布统计（有数据时） | 各维度独立分布，多标签和分母保留，不能拼成未返回的交叉表 |
| 推定与关联证据（有数据时） | 按case_id保存服务端原字段、person_sanctions及institution_classification完整结构、版本、状态、机构/任职关系及证据，长JSON分段，无静默截断 |
| 对象与处罚依据（有处罚原文或后端对应时） | 直接展示后端确认的姓名、处罚、具体机构、职务、身份键、期限原文和证据；缺失或不明确时保留整段原文，不用本地正则补配、不拆案例、不分摊金额 |
| 身份分组（后端提供时） | 按响应来源原样列出person_identity_groups的分组、引用和scope；不按姓名合并、不跨页累加成人数，完整原结构同时保留在口径页 |

表头有筛选和冻结，长内容换行，日期是真实Excel日期；只有年份的回退时间保持文本精度，不补月日。case_id/doc_id及文号按文本保存。服务端最小/最大金额以十进制元值写入，超过Excel15位有效数字时用文本保留；不累计金额，不计算均值或中位数，不加回已排除混合公示。原始罚款字符串始终保留文本，无可确认样本的范围留空并明确未知，不强迫缺失为0。

每张工作表数据区域后独占一行写“AI生成内容，仅供参考”，筛选范围不包含该行，读取台账时不能把提示行计为案例。口径页保留客观输出规则。只展示已返回的事实与数据，不生成处罚轻重、严重程度、机构优劣或无证据的原因判断。原决定日期为空时，文号年仍注明“按文号推定”；不能写成确定决定日期。

来源链接只给NFRA/原银保监官网地址添加超链接，其他地址保留文本。所有原文写为文本值，不执行以=、+等开头的内容。脚本无公式、宏或外部数据连接。

XLSX的XML/ZIP全部在内存中组装，只有最终.xlsx写入任务目录。失败时清理本次创建的不完整输出，不删除用户已有文件。输入JSON仍有32MiB上限；极大导出应按任务拆分范围，并分别说明覆盖。

## 本机报错速查

| 报错 | 判断与下一步 |
|---|---|
| PYTHON_NOT_AVAILABLE / PYTHON_TOO_OLD | run.cmd/run.sh未找到合适解释器；安装完整Python>=3.10或给scripts/run.py指定现有解释器绝对路径，不需要pip补版本 |
| STDLIB_UNAVAILABLE / RUNTIME_SELF_CHECK_FAILED | 解释器裁剪或损坏；更换完整Python，不向Skill目录复制零散依赖 |
| SKILL_FILES_MISSING | 安装包缺文件或混版本；完整重装同版本，不能只拷贝客户端一个.py |
| INPUT_UNREADABLE / 找不到文件 | 普通请求改走--input -；已有输入用绝对路径。含空格路径加引号，确认当前目录 |
| OUTPUT_DIRECTORY_MISSING / OUTPUT_NOT_WRITABLE | 选择已存在的可写任务目录；检查路径引号、权限和空间；不要写安装目录 |
| OUTPUT_EXISTS | 选择新文件名；程序不覆盖原文件 |
| XLSX_EXPORT_FAILED，提示缺页/失败 | 补齐同修订分页；只要部分时显式--allow-partial并保留范围，不清空失败项 |
| 中文JSON解析失败 | 使用UTF-8；PowerShell设$OutputEncoding为UTF8，已有Python调用加-X utf8，不用系统默认编码重新保存中间文件 |
| NETWORK_ERROR / TIMEOUT | 本地预检通过不表示后台可达，按[服务就绪检查](service-readiness.md)在同一实际运行环境检查 |

预检失败退出3，输入/导出失败退出2，HTTP/网络仍沿用客户端退出码。成功输出JSON包含实际output路径、字节数、明细数、修订、partial和工作表；只有文件真实保存后才返回成功。
