"""Local runtime preflight. Compatible syntax lets older Python fail clearly."""
import argparse
import importlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

MIN_VERSION = (3, 10)
MODULES = ("json", "ssl", "urllib.request", "decimal", "zipfile", "zlib", "xml.etree.ElementTree")


class EnvironmentError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("Environment preflight failed")


def check_environment(task="query", output=None, skill_root=None):
    root = Path(skill_root) if skill_root else Path(__file__).resolve().parent.parent
    failures = []
    def fail(code, message, hint):
        failures.append({"code": code, "message": message, "hint": hint})
    if sys.version_info[:2] < MIN_VERSION:
        fail("PYTHON_TOO_OLD", "需要Python 3.10或更高版本。", "使用py -3、python3或明确的合适解释器路径；不要用pip修复Python版本。")
    missing = []
    for name in MODULES:
        try:
            importlib.import_module(name)
        except (ImportError, OSError):
            missing.append(name)
    if missing:
        fail("STDLIB_UNAVAILABLE", "必要标准库不可用：" + ", ".join(missing), "改用完整Python安装；本Skill不要求安装第三方Python包。")
    task_files = {"query": ["scripts/penalty_client.py", "scripts/contract.py"],
                "workflow": ["scripts/workflow.py", "scripts/penalty_client.py", "scripts/contract.py",
                             "scripts/result_check.py", "scripts/export_xlsx.py", "scripts/delivery_view.py"],
                "verify": ["scripts/result_check.py"],
                "export": ["scripts/export_xlsx.py", "scripts/result_check.py", "scripts/delivery_view.py"]}
    task_files["compare"] = task_files["workflow"] + ["scripts/comparison.py"]
    required = task_files[task]
    absent = [name for name in required if not (root / name).is_file()]
    if absent:
        fail("SKILL_FILES_MISSING", "安装包文件缺失：" + ", ".join(absent), "完整重新解压/安装同一版本Skill，不要只复制一个脚本。")
    if not missing and sys.version_info[:2] >= MIN_VERSION:
        try:
            import zipfile
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("check.txt", "中文")
            with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as archive:
                if archive.read("check.txt").decode("utf-8") != "中文":
                    raise ValueError("roundtrip")
        except (ImportError, OSError, ValueError, RuntimeError):
            fail("RUNTIME_SELF_CHECK_FAILED", "内存ZIP/UTF-8自检未通过。", "更换完整的Python解释器；不创建临时文件或安装依赖。")
    if task == "export":
        if output is None:
            fail("OUTPUT_REQUIRED", "导出必须指定.xlsx输出路径。", "使用export --output 完整路径；选择任务输出目录。")
        else:
            try:
                target = Path(output).expanduser().absolute()
                parent = target.parent.resolve()
                if target.suffix.lower() != ".xlsx":
                    fail("OUTPUT_EXTENSION", "输出扩展名须为.xlsx。", "例如--output 处罚台账.xlsx。")
                elif target.exists() or target.is_symlink():
                    fail("OUTPUT_EXISTS", "输出文件已存在，不覆盖。", "选择另一个文件名或由用户处理原文件。")
                elif parent == root.resolve() or root.resolve() in parent.parents:
                    fail("OUTPUT_IN_SKILL", "导出文件不能写入Skill安装目录。", "使用本次任务工作目录的绝对路径。")
                elif not parent.is_dir():
                    fail("OUTPUT_DIRECTORY_MISSING", "输出目录不存在。", "先建立任务输出目录，或指定已有目录。")
                else:
                    # A zero-byte permission probe is immediately removed, including
                    # exceptional exits. No request/case data are written to it.
                    with tempfile.TemporaryFile(dir=str(parent), prefix=".nfra-check-") as probe:
                        probe.write(b"")
                        probe.flush()
            except (OSError, ValueError, TypeError):
                fail("OUTPUT_NOT_WRITABLE", "无法写入或检查输出目录。", "确认目录权限、磁盘空间及路径引号；选择可写的任务目录。")
    return {"ready": not failures, "task": task, "python_version": ".".join(map(str, sys.version_info[:3])),
            "python_executable": sys.executable, "third_party_dependencies": [],
            "network_checked": False, "output_checked": task == "export", "issues": failures}


def require_environment(task="query", output=None, skill_root=None):
    report = check_environment(task, output, skill_root)
    if not report["ready"]:
        raise EnvironmentError(report)
    return report


def error_payload(report):
    return {"error": {"code": "ENVIRONMENT_NOT_READY", "message": "环境检查未通过，业务脚本尚未执行。",
                      "issues": report["issues"]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("query", "verify", "export"), default="query")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    report = check_environment(args.task, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stdout if report["ready"] else sys.stderr)
    return 0 if report["ready"] else 3


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
