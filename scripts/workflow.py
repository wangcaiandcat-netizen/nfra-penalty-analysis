#!/usr/bin/env python3
"""In-memory query, preview, pagination, reconciliation and optional XLSX delivery.

Uses only the public client and the existing offline exporter/checker. No local
business classification, geography inference or money aggregation is performed.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
folder = str(Path(__file__).resolve().parent)
if folder not in sys.path:
    sys.path.insert(0, folder)

from runtime_check import require_environment, EnvironmentError, error_payload
from penalty_client import Client, ClientError, _read_input
from contract import ValidationError, validate_request, validate_statistics, plan_request
from result_check import reconcile, CheckError
from delivery_view import (penalty_attribution, compact_statistics, compact_case,
                           backend_person_sanctions, decision_time_display, DISCLAIMER, OUTPUT_POLICY)

MAX_PAGES = 50  # Same response-bundle limit as the offline checker.
MAX_BUNDLE_BYTES = 32 * 1024 * 1024


class WorkflowError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def require(condition, message, code="WORKFLOW_RESPONSE_INVALID"):
    if not condition:
        raise WorkflowError(code, message)


def _prepare(request, complete):
    body = validate_request("query", request)
    require(body["page"]["cursor"] is None,
            "workflow从第一页开始；已有游标的续查使用query，不把续页当作完整集合。", "WORKFLOW_INPUT_INVALID")
    if complete and not (isinstance(request, dict) and isinstance(request.get("page"), dict)
                         and "size" in request["page"]):
        body["page"]["size"] = 100
    if body["statistics"] is None:
        body["statistics"] = validate_statistics({"group_by": ["month", "authority_province", "issuer_seat_city", "party_type", "business_type"], "top_n": 50})
    return body


def _case_card(row):
    """Select original fields and API-supplied business evidence, never paraphrase."""
    card = {key: copy.deepcopy(row.get(key)) for key in (
        "case_id", "doc_id", "party", "party_type", "original_party_type", "subject_composition", "authority", "region", "decision_number", "publish_date", "decision_date",
        "decision_time", "violation", "penalty", "stage_code", "fine_yuan", "fine_status", "fine_context", "source_url",
        "party_associations", "classification", "institution_classification")}
    card["person_sanctions"], card["person_sanctions_source"] = backend_person_sanctions(row)
    card["decision_time_display"] = decision_time_display(row)
    classification = row.get("classification") or {}
    require(isinstance(classification, dict), "classification须为对象或空值。")
    card["business_codes"] = copy.deepcopy(classification.get("business_codes", []))
    evidence = classification.get("business_evidence", [])
    require(isinstance(evidence, list), "业务证据须为数组。")
    card["business_evidence"] = copy.deepcopy(evidence)
    card["bills_evidence"] = [copy.deepcopy(item) for item in evidence
                                 if isinstance(item, dict) and isinstance(item.get("code"), str)
                                 and (item["code"] == "bills" or item["code"].startswith("bills."))]
    card["penalty_attribution"] = penalty_attribution(row)
    return card


class WorkflowResult(dict):
    """JSON-compatible report with reusable, process-local source responses."""
    def __init__(self, report, responses, rows, context):
        super().__init__(report)
        self._responses = responses
        self._rows = rows
        self._context = context

    def case(self, case_id):
        for row in self._rows:
            if row["case_id"] == case_id:
                return _case_card(row)
        raise WorkflowError("CASE_NOT_FETCHED", "该ID不在本次已取得记录中；未额外联网。")

    def view(self, style="brief", case_limit=5):
        require(style in {"brief", "full"}, "view须为brief或full。", "WORKFLOW_INPUT_INVALID")
        require(type(case_limit) is int and 1 <= case_limit <= 5000, "case_limit须为1至5000的整数。", "WORKFLOW_INPUT_INVALID")
        if style == "full":
            result = copy.deepcopy(dict(self))
            result["cases"] = [_case_card(row) for row in self._rows]
            result["cases_are_complete_list"] = self["coverage"]["all_pages_fetched"]
            result['view'] = 'full'
            result['case_display'] = {'shown': len(self._rows), 'fetched': len(self._rows),
                                      'is_complete_list': result['cases_are_complete_list']}
            return result
        cases = [compact_case(_case_card(row)) for row in self._rows[:case_limit]]
        prohibitions = []
        for row in self._rows:
            for item in penalty_attribution(row)["items"]:
                if item.get("sanction_type") in {"industry_ban", "disqualification"}:
                    # Ambiguous clauses retain subject=None rather than using party
                    # or the first identified name as the ban recipient.
                    prohibitions.append({"case_id": row["case_id"], "source_url": row.get("source_url"), **copy.deepcopy(item)})
        result = {key: copy.deepcopy(self[key]) for key in ("kind", "data_revision", "result_scope", "applied_filters", "coverage", "warnings", "execution",
                  "person_identity_groups", "person_identity_groups_pages", "identity_note", "output_policy", "disclaimer")}
        result['related_mixed'] = copy.deepcopy(self.get('related_mixed'))
        result.update({
            "view": "brief", "statistics": compact_statistics(self.get("statistics")),
            "cases": cases,
            "case_display": {"shown": len(cases), "fetched": len(self._rows),
                             "is_complete_list": len(cases) == len(self._rows) and self["coverage"]["all_pages_fetched"]},
            "prohibition_items": prohibitions[:max(20, case_limit)],
            "prohibition_display": {"shown": min(len(prohibitions), max(20, case_limit)), "available_clauses": len(prohibitions),
                                    "note": "仅展示后端识别的禁止从业/取消任职处罚；不是人数统计；未识别不代表没有该处罚。"},
            "fine_amount_range": copy.deepcopy((self.get('statistics') or {}).get('single_explicit_fines')),
            "attribution_note": "对应采用后端person_sanctions；未确认时保留原文，不用本地正则或首名重配。",
        })
        if "export" in self:
            result["export"] = copy.deepcopy(self["export"])
        return result

    def export(self, output, *, title=None):
        require(self["coverage"]["all_pages_fetched"], "本次只取了部分页；不能导出为完整台账。")
        from export_xlsx import export_workbook
        context = copy.deepcopy(self._context)
        if title is not None:
            context["title"] = title
        info = export_workbook({"responses": self._responses, "context": context}, output)
        self["export"] = info
        return copy.deepcopy(info)


def run_workflow(request, *, output=None, all_pages=False, top=10, title="金融监管处罚台账",
                 dry_run=False, client=None, on_preview=None, max_pages=MAX_PAGES):
    """Return a compact delivery report; intermediate responses stay in this process.

    on_preview receives the full normalized plan before each HTTP request. It is
    observational: a separate copy is passed so callbacks cannot alter requests.
    Direct callers can reuse one Client and catalog revision across queries.
    """
    require_environment("workflow")
    if output is not None:
        require_environment("export", output)
    require(type(top) is int and 1 <= top <= 50, "top须为1至50的整数。", "WORKFLOW_INPUT_INVALID")
    require(type(max_pages) is int and 1 <= max_pages <= MAX_PAGES,
            "max_pages须为1至50的整数。", "WORKFLOW_INPUT_INVALID")
    require(type(title) is str and 1 <= len(title) <= 100, "title须为1至100字文本。", "WORKFLOW_INPUT_INVALID")
    complete_requested = bool(all_pages or output is not None)
    body = _prepare(request, complete_requested)
    first_plan = plan_request("query", body)
    if dry_run:
        return {"kind": "penalty_workflow_plan", "first_request": first_plan,
                "all_pages": complete_requested, "max_pages": max_pages,
                "output": str(Path(output).expanduser().absolute()) if output is not None else None,
                "top": top, "network_called": False,
                "note": "后续页复用筛选、页大小和首响应修订；每页执行前自动预演。"}

    client = client if client is not None else Client()
    responses, rows, seen_ids, seen_cursors = [], [], set(), set()
    revision = body["expected_data_revision"]
    signature = None
    byte_count = 0
    checked = None
    for page_number in range(1, max_pages + 1):
        # Both calls normalize the same object; callbacks cannot mutate it.
        plan = client.execute("query", body, dry_run=True)
        if on_preview is not None:
            on_preview({"event": "request_preview", "page": page_number, "plan": copy.deepcopy(plan)})
        response = client.execute("query", body)
        require(isinstance(response, dict), "API响应须为对象。")
        require(response.get("completion_status") == "complete" and "error" not in response,
                "查询返回失败或部分成功；本次未导出，先处理API状态。")
        scope = response.get("scope")
        require(isinstance(scope, dict), "响应缺少scope。")
        current_revision = scope.get("data_revision")
        require(type(current_revision) is int and current_revision >= 0, "响应缺少有效数据修订。")
        require(revision is None or current_revision == revision,
                "API返回的修订与本次请求不一致；重新查询，不能混合旧页。", "DATA_REVISION_CHANGED")
        revision = current_revision
        applied = response.get("applied_filters")
        require(isinstance(applied, dict) and isinstance(applied.get("filter_signature"), str)
                and bool(applied["filter_signature"]), "响应缺少筛选签名，无法核对分页。")
        require(signature is None or applied["filter_signature"] == signature, "分页筛选签名发生变化，停止查询。")
        signature = applied["filter_signature"]
        page = response.get("pagination")
        current_rows = response.get("results")
        require(isinstance(page, dict) and isinstance(current_rows, list), "响应缺少分页或结果数组。")
        require(type(page.get("total")) is int and page["total"] >= 0
                and page.get("total_exact") is True and type(page.get("has_more")) is bool,
                "分页缺少精确总数或明确的has_more。")
        require(type(page.get("returned")) is int and page["returned"] == len(current_rows), "分页返回数与明细不一致。")
        require(all(isinstance(row, dict) and isinstance(row.get("case_id"), str)
                    and row["case_id"] for row in current_rows), "结果存在缺少case_id的明细。")
        ids = [row["case_id"] for row in current_rows]
        require(len(set(ids)) == len(ids) and not seen_ids.intersection(ids), "分页重复返回case_id；停止，不能冒充全量。")
        byte_count += len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        require(byte_count <= MAX_BUNDLE_BYTES,
                "本次响应累计超过32MiB；请缩小或拆分筛选范围，未保存中间文件。", "WORKFLOW_LIMIT_REACHED")
        responses.append(response)
        rows.extend(current_rows)
        seen_ids.update(ids)
        # Detect total/version/fact conflicts before sending another page.
        checked = reconcile(responses)
        require(not checked["source_failures"], "响应含失败项，停止查询。")
        if not page["has_more"]:
            require(checked["all_api_filters_complete"], "末页数量未覆盖API精确总数，未导出。")
            break
        require(len(rows) < page["total"], "has_more与已获取数量矛盾，停止查询。")
        if not complete_requested:
            break
        cursor = page.get("next_cursor")
        require(isinstance(cursor, str) and bool(cursor) and cursor not in seen_cursors
                and bool(current_rows), "缺失/重复游标或空中间页，停止查询。")
        require(page_number < max_pages, "已达到分页上限；请拆分范围，未生成不完整Excel。", "WORKFLOW_LIMIT_REACHED")
        seen_cursors.add(cursor)
        body = copy.deepcopy(body)
        body["page"]["cursor"] = cursor
        body["expected_data_revision"] = revision
        # Full-filter statistics already came with page 1; no separate stats query.
        body["statistics"] = None

    complete = checked["all_api_filters_complete"]
    require(not complete_requested or complete, "全量请求未取齐，未导出。")
    first = responses[0]
    scope = first["scope"]
    cards = [_case_card(row) for row in (rows[:top] if output is not None else rows)]
    report = {
        "kind": "penalty_workflow_result", "data_revision": revision,
        "result_scope": "本库当前筛选/分类口径；不等同于官网全部相关处罚。",
        "applied_filters": copy.deepcopy(first["applied_filters"]),
        "coverage": {"api_total": first["pagination"]["total"], "fetched_unique_records": len(rows),
                     "pages": len(responses), "all_pages_fetched": complete,
                     "count_unit": scope.get("count_unit", "官网公示表行或公告事项，不等于独立法律决定数量"),
                     "available_publication_dates": copy.deepcopy(scope.get("available_publication_dates")),
                     "collection_checked_through": scope.get("collection_checked_through"),
                     "declared_publication_coverage": copy.deepcopy(scope.get("declared_publication_coverage")),
                     "pending_review_count": scope.get("pending_review_count"),
                     "pending_review_scope": "数据源全库；不能视为全部属于本次地区/年份/业务。",
                     "date_note": "最新已发布日与目录核查日分开；文号年仅为推定，不能改称确定决定年。决定/文号回退查询为0不证明该年实际作出处罚为0。"},
        "statistics": copy.deepcopy(first.get("statistics")),
        "related_mixed": copy.deepcopy(first.get('related_mixed')),
        "execution": {"query_requests": len(responses), "local_previews": len(responses),
                      "separate_statistics_requests": 0,
                      "reuse": "案例、服务端统计、排名、处罚对应和Excel均来自本次已获取响应。"},
        "warnings": checked["warnings"],
        "cases": cards, "cases_are_complete_list": complete and output is None,
        "fine_amount_range": copy.deepcopy((first.get('statistics') or {}).get('single_explicit_fines')),
        "person_identity_groups": copy.deepcopy(first.get("person_identity_groups")),
        "person_identity_groups_pages": [{"page": i, "person_identity_groups": copy.deepcopy(response["person_identity_groups"])}
                                         for i, response in enumerate(responses, 1) if "person_identity_groups" in response],
        "identity_note": "person_identity_groups为首响应原值，pages保留逐页原值；范围由后端scope说明。姓名去重数不是人数，不跨页或按姓名自行合并；任职查询仅覆盖已识别任职关联的记录。",
        "output_policy": copy.deepcopy(OUTPUT_POLICY), "disclaimer": DISCLAIMER,
    }
    missing_sources = [row["case_id"] for row in rows if not row.get("source_url")]
    if missing_sources:
        report["warnings"].append("存在缺失官方来源的案例，请如实保留，不拼造链接。")
        report["missing_source_case_ids"] = missing_sources
    result = WorkflowResult(report, responses, rows, {
            "title": title, "request": first_plan["body"],
            "notes": report["result_scope"] + "整案罚款不代表票据单项罚款；覆盖日期与待复核范围见scope及warnings。"
        })
    if output is not None:
        result.export(output)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, metavar="FILE|-")
    parser.add_argument("--output", metavar="FILE.xlsx", help="Export all matched pages; only this final file is saved.")
    parser.add_argument("--all", dest="all_pages", action="store_true", help="Fetch all pages even without an XLSX output.")
    parser.add_argument("--dry-run", action="store_true", help="Show the full first request; no network/config/credential reads.")
    parser.add_argument("--top", type=int, default=10, help="兼容旧参数，已取消金额排名；仅展示后台最小/最大金额及依据。")
    parser.add_argument("--title", default="金融监管处罚台账")
    parser.add_argument("--base-url")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--view", choices=("brief", "full"), help="Default: brief; --all without XLSX defaults to full. No extra requests.")
    parser.add_argument("--case-limit", type=int, default=5, help="Representative cases in brief, 1..5000; ban clauses show at least the first 20 if available.")
    parser.add_argument("--show-previews", action="store_true", help="Print every full request preview to stderr. Local previews always run.")
    args = parser.parse_args(argv)
    try:
        # Direct invocation has the same preflight-before-input behavior as run.py.
        require_environment("workflow")
        if args.output is not None:
            require_environment("export", args.output)
        request = _read_input(args.input)
        require(1 <= args.case_limit <= 5000, "case-limit须为1至5000。", "WORKFLOW_INPUT_INVALID")
        client = None if args.dry_run else Client(base_url=args.base_url, timeout=args.timeout)
        def preview(event):
            print(json.dumps(event, ensure_ascii=False, allow_nan=False), file=sys.stderr, flush=True)
        result = run_workflow(request, output=args.output, all_pages=args.all_pages, top=args.top,
                              title=args.title, dry_run=args.dry_run, client=client,
                              on_preview=preview if args.show_previews else None)
        style = args.view or ("full" if args.all_pages and not args.output else "brief")
        rendered = result if args.dry_run else result.view(style, args.case_limit)
        print(json.dumps(rendered, ensure_ascii=False, indent=None if style == "brief" and not args.dry_run else 2, allow_nan=False))
        return 0
    except EnvironmentError as error:
        print(json.dumps(error_payload(error.report), ensure_ascii=False), file=sys.stderr)
        return 3
    except ClientError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False), file=sys.stderr)
        return error.exit_code
    except ValidationError as error:
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error.message, "path": error.path}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except WorkflowError as error:
        print(json.dumps({"error": {"code": error.code, "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (CheckError, ValueError, TypeError, KeyError, OSError, RecursionError):
        print(json.dumps({"error": {"code": "WORKFLOW_FAILED", "message": "响应或导出检查未通过；未完成交付，请处理错误后重新查询。"}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
