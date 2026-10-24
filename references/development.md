# 独立项目开发与打包

公开维护仓库：[nfra-penalty-analysis](https://github.com/wangcaiandcat-netizen/nfra-penalty-analysis)。本仓库根目录就是完整 Skill，运行只依赖 Python 3.10+ 标准库。无需安装 DeepFin 后端、数据库或模型 SDK。

## 本地检查

在仓库根目录运行：

```bash
sh run.sh check
sh run.sh query --input assets/requests/guogu-2025-bills-query.json --dry-run
```

Windows 使用 `py -3 -I -B -X utf8 scripts/run.py` 对应入口。`run.cmd`是可选包装，不作为平台安装的必需文件。`--dry-run` 检查和归一化请求，不访问后台。实际查询需要网络，默认生产服务为 `https://deepfin.vip`。

## 连接与鉴权

地址优先级是显式 `--base-url`、非空 `DEEPFIN_API_BASE_URL`、生产默认地址。本地联调可显式传入 `--base-url http://127.0.0.1:8002`。

只读凭据使用宿主环境变量 `DEEPFIN_PENALTIES_API_KEY`；解释权限使用 `DEEPFIN_PENALTIES_ASK_API_KEY`。是否需要凭据由后台决定，遇到鉴权错误按响应停止。不要提交真实凭据、生产响应或导出文件。详细行为见 [连接规则](connection.md)和[服务就绪检查](service-readiness.md)。

## 可复现打包

```bash
python3 tools/package_skill.py
```

Windows 可使用已安装的 Python 3.10+ 执行同一脚本。输出位于 `dist/`，包含 WorkBuddy ZIP、逐文件 SHA-256 清单和 ZIP 校验文件。打包只包含 `SKILL.md`、两个入口，以及 `agents/`、`assets/requests/`、`references/`、`schemas/`、`scripts/` 中支持的静态文件。发布工具、版本记录和项目首页不会混入安装包。

每次变更后检查入口、请求预演、ZIP 完整性及相对链接；涉及接口或业务处理时，还应使用授权测试环境验证分页、同一数据修订、统计和 Excel 导出。维护者可用已安装pytest执行`python3 -m pytest -q tests`，宿主运行不需要pytest。代理测试使用自动清理的本地夹具及临时测试证书，不访问生产后台。R16及历史验收范围与环境限制见仓库`docs/r16.md`、`docs/r15.md`。

分类、身份确认和金额范围由后端提供；客户端沿用现有 `workflow.py`、`comparison.py`、`delivery_view.py` 和标准库 Excel 导出，不另行归类或补算。参见 [内存工作流](memory-workflow.md)和[Excel 说明](excel-export.md)。
