# 宿主连接与运行

本技能使用宿主已有的 Python 3.10+，只依赖标准库。用户查询任务不需要项目虚拟环境、DeepFin源码或开发测试目录。默认从安装目录的run.cmd（Windows）或sh run.sh（WSL/Linux）启动，先选择Python，再检查标准库/文件，最后执行业务脚本。已知解释器可直接运行scripts/run.py。详细报错与导出目录检查见 [环境预检与Excel](excel-export.md)。

## 服务地址

地址选择由客户端完成，优先级为：用户通过 `--base-url` 明确提供的地址 → 非空宿主环境变量 `DEEPFIN_API_BASE_URL` → 生产默认 `https://deepfin.vip`。环境变量缺失、为空或仅含空白都视为未配置。无需先检查环境变量或探测端口，不要询问“用哪个地址”，直接调用客户端。不要搜索配置文件、.env、历史聊天或其他项目来寻找配置。

接受服务根地址或完整 `/api/v1/external/penalties` 前缀。本版默认调用 `https://deepfin.vip/api/v1/external/penalties/*`；需要本地联调时显式传入 `--base-url http://127.0.0.1:8002`。远程服务使用 HTTPS，HTTP仅限本机环回地址。旧版宿主若已经设置地址覆盖值，应由用户确认是否移除或修改，该值仍优先于新的默认地址。

所选服务连接失败时停止并提示检查网络、后台运行状态和地址；生产服务失败时联系服务维护者，本地联调服务失败时由维护者检查本地后台。不要改端口、扫描服务、自动重试或切换地址。非空显式配置错误仍作为配置错误处理，不能悄悄改用默认服务。

```bash
python3 -B -X utf8 scripts/run.py catalog
python3 -B -X utf8 scripts/run.py query --input 请求.json --dry-run
python3 -B -X utf8 scripts/run.py query --input 请求.json
```

请求JSON由宿主按用户条件在内存构建，默认通过--input -输入并从stdout接收响应；上面文件参数用于已有请求或主动留档场景。[无中间文件的调用方法](memory-workflow.md)。安装包不含项目 examples/，不用寻找示例文件。实际执行前完成预演，不在一次任务中自动下载全库。

安装包内已有完整JSON请求，含直接按国股类别筛选的样例，见 [常用查询 recipes](recipes.md) 和 `assets/requests/`。**先用 `--dry-run` 核对完整归一化请求，再执行同一内存请求或已有样例**；预演不检查后台健康。首次访问或故障时按 [服务就绪检查](service-readiness.md) 处理，不自动切换到本地端口。

## 匿名与鉴权

默认免Key，没有对应Key时直接调用配置的服务地址，不先要求用户提供Key。只有后台开启鉴权后才按服务端响应处理：

- `AUTH_REQUIRED`：显示客户端保留的登录/注册链接，让用户自行登录并配置凭据，不自动登录、注册、改地址或重试。
- `API_KEY_INVALID` / `ACCOUNT_DISABLED` / `INSUFFICIENT_SCOPE`：说明对应问题并停止，不删除已配置Key重试匿名访问。

可选凭据只从宿主环境变量读取：查询、公告列表、集合统计类使用 `DEEPFIN_PENALTIES_API_KEY`（`penalties:read`），解释使用 `DEEPFIN_PENALTIES_ASK_API_KEY`（`penalties:ask`）。不得把Key写入提问、URL、请求文件、命令参数、回答或日志。

开发环境登录页可能未启用；显示服务端链接不保证页面可用。缺少有效链接时说明需要服务管理员提供账号入口。
