# nfra-penalty-analysis

面向 WorkBuddy 等 Skill 宿主的金融监管处罚案例查询工具。支持机构与个人查询、同名证据核查、期间比较、统计及 Excel 导出。

**当前版本：R16（2026-10-24）。默认 API：`https://deepfin.vip`，接口契约：1.2.0。**

这是独立的公开 Skill 客户端项目。实际查询由 DeepFin 处罚服务提供，服务端的账户权限和数据范围仍然适用。本仓库不包含处罚数据库、后台服务或访问凭据。

## 安装到 WorkBuddy

1. 打开 [R16 发布页](https://github.com/wangcaiandcat-netizen/nfra-penalty-analysis/releases/tag/r16)。
2. 在 **Assets** 下载 `nfra-penalty-analysis-workbuddy-20261024-r16.zip`，将此 ZIP 导入 WorkBuddy 的 Skill 安装入口。不要把 GitHub 自动生成的 Source code 压缩包当作安装包。
3. 宿主需要 Python 3.10+；查询与 Excel 导出仅使用标准库，无需安装第三方依赖。

ZIP 根目录包含 `SKILL.md`。需要手动安装时，完整解压到宿主使用的 Skill 目录，保留子目录结构。

## 快速检查

Windows PowerShell，在 Skill 目录运行：

```powershell
py -3 -I -B -X utf8 scripts/run.py check
py -3 -I -B -X utf8 scripts/run.py query --input assets/requests/guogu-2025-bills-query.json --dry-run
py -3 -I -B -X utf8 scripts/run.py catalog
```

Linux / WSL / macOS：

```bash
sh run.sh check
sh run.sh query --input assets/requests/guogu-2025-bills-query.json --dry-run
sh run.sh catalog
```

`check` 和 `--dry-run` 不访问网络；`catalog` 读取生产服务。自然语言调用时，按宿主安装的 Skill 说明发起查询即可。

## API 地址与权限

地址优先级：显式 `--base-url` → 非空环境变量 `DEEPFIN_API_BASE_URL` → `https://deepfin.vip`。

如果旧安装设置过本地地址，请修改或清空 `DEEPFIN_API_BASE_URL`，否则仍会覆盖生产默认地址。远程服务使用 HTTPS，本地联调可明确指定环回地址。

查询凭据通过宿主环境变量 `DEEPFIN_PENALTIES_API_KEY` 提供，解释权限使用 `DEEPFIN_PENALTIES_ASK_API_KEY`。后台要求鉴权时，按返回的账号入口或维护者说明配置；不要把 Key 写入请求、命令参数、日志或仓库。网络沿用已有HTTP(S)代理与NO_PROXY，不更改系统设置；远程HTTPS仍校验证书。不保证SOCKS或PAC配置，若不支持由用户调整既有网络环境。连接失败、限流或权限不足时停止，不扫描地址、创建环回转发或反复重试。

## R16 的变化

- 修复强制直连：使用Python标准库识别已有HTTP(S)代理和NO_PROXY，未配置时直连。
- 保留HTTPS证书校验、重定向阻止、响应完整性与32 MiB上限；失败不自动重试或改用直连。
- Windows默认使用scripts/run.py，即使平台移除run.cmd仍可调用；不创建本地转发或常驻任务。
- 查询条件、统计口径、身份归并、期间比较、接口schema及Excel导出业务代码与R15一致。

[R16版本说明](docs/r16.md) · [R15历史验收](docs/r15.md) · [接口参考](references/api.md) · [连接说明](references/connection.md) · [开发与打包](references/development.md)

本仓库代码公开可见，数据及服务的使用权限由相应服务端决定。
