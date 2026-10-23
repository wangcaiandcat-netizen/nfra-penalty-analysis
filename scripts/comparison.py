"""Fetch comparable periods once and reuse their responses for one XLSX."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

from workflow import (WorkflowError, require, _prepare, run_workflow,
                      MAX_PAGES, MAX_BUNDLE_BYTES)
from runtime_check import require_environment, EnvironmentError, error_payload
from penalty_client import Client, ClientError, _read_input
from contract import ValidationError, plan_request
from result_check import reconcile, CheckError
from delivery_view import DISCLAIMER, OUTPUT_POLICY


def _periods(request):
    require(type(request) is dict and set(request) == {"periods"},
            "compare输入仅含periods，每项为label和query请求request。", "COMPARISON_INPUT_INVALID")
    periods = request["periods"]
    require(type(periods) is list and 2 <= len(periods) <= 5,
            "比较须有2至5个期间。", "COMPARISON_INPUT_INVALID")
    prepared, labels, filters, previous_end, basis = [], set(), None, None, None
    revisions = set()
    for period in periods:
        require(type(period) is dict and set(period) == {"label", "request"},
                "每个期间只含label和request。", "COMPARISON_INPUT_INVALID")
        label = period["label"]
        require(type(label) is str and bool(label.strip()) and len(label) <= 80 and label not in labels,
                "期间label须为1至80字且不重复。", "COMPARISON_INPUT_INVALID")
        labels.add(label)
        body = _prepare(period["request"], True)
        date = body["filters"]["date"]
        require(date["from"] is not None and date["to"] is not None,
                "每个比较期间须有起止日期。", "COMPARISON_INPUT_INVALID")
        require(previous_end is None or date["from"] > previous_end,
                "期间须按时间先后排列且不能重叠。", "COMPARISON_INPUT_INVALID")
        require(basis is None or date["basis"] == basis,
                "比较期间须使用同一日期基准。", "COMPARISON_INPUT_INVALID")
        non_date = {k: v for k, v in body["filters"].items() if k != "date"}
        require(filters is None or non_date == filters,
                "比较期间除日期外须使用相同筛选条件。", "COMPARISON_INPUT_INVALID")
        if body["expected_data_revision"] is not None:
            revisions.add(body["expected_data_revision"])
        previous_end, basis, filters = date["to"], date["basis"], non_date
        prepared.append((label, body))
    require(len(revisions) <= 1, "比较请求不能指定不同数据修订。", "COMPARISON_INPUT_INVALID")
    if revisions:
        for _, body in prepared:
            body["expected_data_revision"] = next(iter(revisions))
    return prepared


class ComparisonResult(dict):
    def __init__(self, periods, title):
        self._periods = periods
        self._title = title
        bundle = self._bundle()
        checked = reconcile(bundle["responses"])
        require(checked["all_api_filters_complete"] and not checked["source_failures"],
                "比较数据缺页或含失败项，不能交付。")
        self._revision = checked["data_revision"]
        super().__init__({"kind": "penalty_comparison_result", "data_revision": self._revision})

    def _bundle(self):
        responses, labels = [], []
        for label, result in self._periods:
            responses.extend(result._responses)
            labels.extend(f"{label} / 第{n}页" for n in range(1, len(result._responses) + 1))
        return {"responses": responses, "context": {"title": self._title, "source_labels": labels,
                "request": {"periods": [{"label": label, "filters": result["applied_filters"]}
                                         for label, result in self._periods]},
                "notes": "同修订、同筛选口径、不同期间；统计金额逐期间沿用API，未跨期累加或重算。"}}

    def period(self, label):
        for name, result in self._periods:
            if name == label:
                return result
        raise WorkflowError("PERIOD_NOT_FETCHED", "该期间不在本次结果中；未额外联网。")

    def view(self, style="brief", case_limit=20):
        periods = [{"label": label, **result.view(style, case_limit)} for label, result in self._periods]
        rendered = {**copy.deepcopy(dict(self)), "view": style,
                    "result_scope": "本库当前筛选/分类口径，不等同官网全部处罚；记录数不等于人数。",
                    "periods": periods,
                    "execution": {"query_requests": sum(result["execution"]["query_requests"] for _, result in self._periods),
                                  "separate_statistics_requests": 0, "export_query_requests": 0,
                                  "reuse": "各期仅查询必要页面；摘要、详情及合并Excel均复用原响应。"},
                    "interpretation": copy.deepcopy(OUTPUT_POLICY["requirements"]),
                    "output_policy": copy.deepcopy(OUTPUT_POLICY), "disclaimer": DISCLAIMER}
        return rendered

    def export(self, output, *, title=None):
        from export_xlsx import export_workbook
        bundle = self._bundle()
        if title is not None:
            bundle["context"]["title"] = title
        info = export_workbook(bundle, output)
        self["export"] = info
        return copy.deepcopy(info)


def run_comparison(request, *, output=None, title="金融监管处罚期间对比", top=10,
                   dry_run=False, client=None, on_preview=None, summary_only=False):
    require_environment("compare")
    if output is not None:
        require_environment("export", output)
    require(type(top) is int and 1 <= top <= 50, "top须为1至50。", "COMPARISON_INPUT_INVALID")
    require(type(title) is str and 1 <= len(title) <= 100, "title须为1至100字。", "COMPARISON_INPUT_INVALID")
    prepared = _periods(request)
    if summary_only:
        return run_summary_comparison(prepared, output=output, title=title, dry_run=dry_run, client=client)
    if dry_run:
        return {"kind": "penalty_comparison_plan", "network_called": False,
                "periods": [{"label": label, "first_request": plan_request("query", body)} for label, body in prepared],
                "output": str(Path(output).expanduser().absolute()) if output is not None else None,
                "note": "首期取回修订后锁定后续期间；逐页本地预演，全部取齐后仅导出一个xlsx。"}
    client = client if client is not None else Client()
    results, revision, ids, size, pages = [], None, set(), 0, 0
    for label, body in prepared:
        if revision is not None:
            body["expected_data_revision"] = revision
        def preview(event):
            if on_preview is not None:
                on_preview({"label": label, **event})
        require(pages < MAX_PAGES, "比较累计达到50页上限；请缩小范围。", "WORKFLOW_LIMIT_REACHED")
        result = run_workflow(body, all_pages=True, client=client, top=top, title=title,
                              on_preview=preview, max_pages=MAX_PAGES-pages)
        require(revision is None or result["data_revision"] == revision,
                "比较期间的数据修订不一致。", "DATA_REVISION_CHANGED")
        revision = result["data_revision"]
        period_ids = {row["case_id"] for row in result._rows}
        require(not ids.intersection(period_ids), "不重叠期间返回了相同case_id，停止比较。")
        ids.update(period_ids)
        size += len(json.dumps(result._responses, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        pages += len(result._responses)
        require(size <= MAX_BUNDLE_BYTES, "比较响应累计超过32MiB；请缩小范围。", "WORKFLOW_LIMIT_REACHED")
        results.append((label, result))
    result = ComparisonResult(results, title)
    if output is not None:
        result.export(output)
    return result


class SummaryComparisonResult(dict):
    def __init__(self, responses, labels, title):
        self._responses, self._labels, self._title = responses, labels, title
        check = reconcile(responses)
        require(not check['source_failures'], '统计比较存在失败项，未完成交付。')
        super().__init__({'kind': 'penalty_statistics_comparison', 'data_revision': check['data_revision'],
            'periods': [{'label': label, 'scope': copy.deepcopy(response['scope']),
                         'applied_filters': copy.deepcopy(response['applied_filters']),
                         'statistics': copy.deepcopy(response['statistics']),
                         'related_mixed': copy.deepcopy(response.get('related_mixed')),
                         'warnings': copy.deepcopy(response.get('warnings', []))}
                        for label, response in zip(labels, responses)],
            'execution': {'statistics_requests': len(responses), 'detail_page_requests': 0},
            'output_policy': copy.deepcopy(OUTPUT_POLICY),
            'interpretation': copy.deepcopy(OUTPUT_POLICY['requirements']),
            'result_scope': '同修订、同筛选口径的完整筛选集统计；未拉取案例明细，不据此计算总体真实人数或推断处罚轻重。',
            'disclaimer': DISCLAIMER})

    def view(self, style='brief', case_limit=20):
        return copy.deepcopy(dict(self))

    def export(self, output):
        from export_xlsx import export_workbook
        info = export_workbook({'responses': self._responses, 'context': {'title': self._title,
            'source_labels': self._labels, 'notes': self['result_scope'] + '个人统计直接保留服务端值与单位，混合公示不拆人数。'}}, output)
        self['export'] = info
        return copy.deepcopy(info)


def run_summary_comparison(prepared, *, output=None, title='', dry_run=False, client=None):
    statistics = [body['statistics'] for _, body in prepared]
    require(all(s == statistics[0] for s in statistics), '统计比较的指标和开关须一致。', 'COMPARISON_INPUT_INVALID')
    plans = [{'label': label, 'request': {'filters': body['filters'], 'statistics': body['statistics'],
             'expected_data_revision': body['expected_data_revision']}} for label, body in prepared]
    if dry_run:
        return {'kind': 'penalty_statistics_comparison_plan', 'network_called': False,
                'periods': [{'label': p['label'], 'request': plan_request('stats', p['request'])} for p in plans]}
    client = client if client is not None else Client()
    responses, revision = [], None
    for plan in plans:
        body = copy.deepcopy(plan['request'])
        if revision is not None:
            body['expected_data_revision'] = revision
        response = client.execute('stats', body)
        require(response.get('completion_status') == 'complete' and isinstance(response.get('statistics'), dict), '统计响应未完成。')
        current = response.get('scope', {}).get('data_revision')
        require(type(current) is int and (revision is None or revision == current) and
                (body['expected_data_revision'] is None or body['expected_data_revision'] == current), '统计期间修订不一致。')
        if body['statistics'].get('include_personal_sanctions'):
            require(isinstance(response['statistics'].get('personal_sanctions'), dict), '后台未返回个人统计；请更新后台，不能自行拆人补算。')
        revision = current
        responses.append(response)
    result = SummaryComparisonResult(responses, [p['label'] for p in plans], title)
    if output is not None:
        result.export(output)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, metavar="FILE|-")
    parser.add_argument("--output", metavar="FILE.xlsx")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument('--summary-only', action='store_true', help='仅调用各期全筛选集统计，不拉案例分页。')
    parser.add_argument("--view", choices=("brief", "full"), default="brief")
    parser.add_argument("--case-limit", type=int, default=20)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--title", default="金融监管处罚期间对比")
    parser.add_argument("--show-previews", action="store_true")
    parser.add_argument("--base-url")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        require_environment("compare")
        if args.output is not None:
            require_environment("export", args.output)
        require(1 <= args.case_limit <= 5000, "case-limit须为1至5000。", "COMPARISON_INPUT_INVALID")
        request = _read_input(args.input)
        def preview(event):
            print(json.dumps(event, ensure_ascii=False, allow_nan=False), file=sys.stderr, flush=True)
        result = run_comparison(request, output=args.output, title=args.title, top=args.top,
                                dry_run=args.dry_run, summary_only=args.summary_only,
                                client=None if args.dry_run else Client(base_url=args.base_url, timeout=args.timeout),
                                on_preview=preview if args.show_previews else None)
        print(json.dumps(result if args.dry_run else result.view(args.view, args.case_limit),
                         ensure_ascii=False, allow_nan=False))
        return 0
    except EnvironmentError as error:
        payload, status = error_payload(error.report), 3
    except ClientError as error:
        payload, status = error.as_dict(), error.exit_code
    except ValidationError as error:
        payload, status = {"error": {"code": "INVALID_REQUEST", "message": error.message, "path": error.path}}, 2
    except WorkflowError as error:
        payload, status = {"error": {"code": error.code, "message": str(error)}}, 2
    except (CheckError, ValueError, TypeError, KeyError, OSError, RecursionError):
        payload, status = {"error": {"code": "COMPARISON_FAILED", "message": "比较或导出核对失败，未完成交付。"}}, 2
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
