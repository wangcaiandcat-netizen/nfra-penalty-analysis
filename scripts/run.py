#!/usr/bin/env python3
"""Preflight first, then import the selected business script. No stdin is consumed here."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
folder = str(Path(__file__).resolve().parent)
if folder not in sys.path:
    sys.path.insert(0, folder)
try:
    from runtime_check import require_environment, error_payload, EnvironmentError
except ImportError:
    print('{"error":{"code":"SKILL_FILES_MISSING","message":"runtime_check.py缺失，请完整重新安装Skill。"}}', file=sys.stderr)
    raise SystemExit(3)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: run.py check [--task query|export] [--output FILE.xlsx]\n"
              "       run.py catalog|query|stats|detail|batch|set_stats|document|explain [client options]\n"
              "       run.py verify [result_check options]\n"
              "       run.py workflow --input FILE|- [--all] [--output FILE.xlsx] [--dry-run]\n"
              "       run.py compare --input PERIODS.json|- [--summary-only] [--output FILE.xlsx] [--dry-run]\n"
              "       run.py search --from DATE --to DATE [--province NAME] [--name NAME] [--subject person] [--output FILE.xlsx]\n"
              "       run.py export --input FILE|- --output FILE.xlsx [--allow-partial]")
        return 0
    command, arguments = argv[0], argv[1:]
    if command == "check":
        from runtime_check import main as check_main
        return check_main(arguments)
    if command not in ("catalog", "query", "stats", "detail", "batch", "set_stats", "document", "explain", "verify", "export", "workflow", "compare", "search"):
        print('{"error":{"code":"INVALID_COMMAND","message":"使用run.py --help查看支持的命令。"}}', file=sys.stderr)
        return 2
    output = None
    if command in ("export", "workflow", "compare"):
        if "--help" in arguments or "-h" in arguments:
            if command == "compare":
                print("compare --input PERIODS.json|- [--summary-only] [--output FILE.xlsx] [--dry-run] [--view brief|full] [--case-limit 20] [--show-previews] [--top 10] [--title TEXT] [--base-url URL] [--timeout 60]\n"
                      "Fetch 2..5 comparable periods once at one revision; export one workbook from the same responses.")
            elif command == "workflow":
                print("workflow --input QUERY.json|- [--all] [--output FILE.xlsx] [--dry-run] [--view brief|full] [--case-limit 5] [--show-previews] [--top 10] [--title TEXT] [--base-url URL] [--timeout 60]\n"
                      "In-memory preview/query/pagination/check/export; output implies all pages. No automatic retries.")
            else:
                print("export --input BUNDLE.json|- --output FILE.xlsx [--allow-partial]\nDefault: require complete same-revision data; never overwrite an existing file.")
            return 0
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--output")
        known, _ = parser.parse_known_args(arguments)
        output = known.output
    try:
        require_environment(command if command in ("workflow", "compare", "export", "verify") else "query", output=output)
        if command in ("workflow", "compare") and output is not None:
            require_environment("export", output=output)
    except EnvironmentError as error:
        print(json.dumps(error_payload(error.report), ensure_ascii=False), file=sys.stderr)
        return 3
    if command == "compare":
        from comparison import main as business_main
        return business_main(arguments)
    if command == 'search':
        from search import main as business_main
        return business_main(arguments)
    if command == "workflow":
        from workflow import main as business_main
        return business_main(arguments)
    if command == "export":
        from export_xlsx import main as business_main
        return business_main(arguments)
    if command == "verify":
        from result_check import main as business_main
        return business_main(arguments)
    from penalty_client import main as business_main
    return business_main([command] + arguments)


if __name__ == "__main__":
    raise SystemExit(main())
