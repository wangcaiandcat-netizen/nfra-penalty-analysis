#!/usr/bin/env python3
"""Audit API pages and server set_stats responses from memory or files.

Use --bundle - for a stdin object with responses and optional selection.
Optional selection prepares a set_stats request for explicit case_id sets.
No HTTP, geography, association or fine classification rules are implemented.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024
ID = re.compile(r"^[A-Za-z0-9_-]+$")
MONEY = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$")


class CheckError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise CheckError(message)


def read_json(filename):
    if filename == "-":
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    else:
        with Path(filename).open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, "Input exceeds 32 MiB.")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON field.")
            result[key] = value
        return result
    def constant(_):
        raise CheckError("Non-finite JSON number.")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=constant)


def normalize_selection(selection):
    if selection is None:
        return None
    require(type(selection) is dict and set(selection) == {"sets"}, "Selection accepts only sets; use API inference fields, not document_rules.")
    sets = selection["sets"]
    require(type(sets) is list and 1 <= len(sets) <= 10, "Provide 1 to 10 sets.")
    names, total = set(), 0
    for item in sets:
        require(type(item) is dict and not set(item) - {"set_id", "case_ids", "status", "basis", "include_duplicates"}, "Invalid selected set.")
        name, ids = item.get("set_id"), item.get("case_ids")
        require(type(name) is str and 1 <= len(name) <= 100 and ID.fullmatch(name) and name not in names, "Set IDs must be valid and unique.")
        names.add(name)
        require(type(ids) is list and 1 <= len(ids) <= 1000 and all(type(x) is str and 1 <= len(x) <= 128 and ID.fullmatch(x) for x in ids), "Each set needs 1 to 1000 real case_ids.")
        require(type(item.get("include_duplicates", False)) is bool, "include_duplicates must be boolean.")
        require(item.get("status", "observed") in {"observed", "inferred", "unresolved"}, "Invalid selection status.")
        require(type(item.get("basis", "")) is str, "Invalid selection basis.")
        require(item.get("status", "observed") == "observed" or bool(item.get("basis", "").strip()), "Inferred/unresolved sets need a basis.")
        total += len(ids)
    require(total <= 5000, "At most 5000 raw members across all sets.")
    return copy.deepcopy(sets)


def audit_set(group):
    require(type(group.get("set_id")) is str, "Missing set_id.")
    require(group.get("status") in {"succeeded", "partial", "failed"}, "Invalid set status.")
    stats = group.get("statistics")
    if stats is None:
        require(group["status"] == "failed", "Successful set lacks statistics.")
        return
    resolution = group.get("id_resolution")
    require(type(stats) is dict and type(resolution) is dict, "Invalid server set statistics.")
    ids = resolution.get("matched_case_ids")
    require(type(ids) is list and all(type(x) is str for x in ids) and len(ids) == len(set(ids)), "Invalid matched_case_ids.")
    total = stats.get("total_records")
    require(type(total) is int and total == len(ids), "Set count differs from matched IDs.")
    require(resolution.get("amount_denominator") == total, "ID amount denominator differs from count.")
    fines = stats.get("single_explicit_fines")
    require(type(fines) is dict, "Missing server money coverage.")
    for key in ("denominator", "included_records", "excluded_records", "zero_amount_records"):
        require(type(fines.get(key)) is int and fines[key] >= 0, "Invalid money coverage count.")
    require(fines["denominator"] == total and fines["included_records"] + fines["excluded_records"] == total, "Money coverage differs from same set count.")
    require(fines["zero_amount_records"] <= fines["included_records"], "Invalid explicit-zero count.")
    require(fines.get('statistic') == 'extracted_fine_range' and fines.get('aggregation_enabled') is False,
            '后台未支持金额范围契约；请更新后台，不能自行求和或改算范围。')
    require(not any(k in fines for k in ('yuan', 'sum_yuan', 'mean_yuan', 'median_yuan')), 'Unexpected aggregate money.')
    low, high = fines.get('min_yuan'), fines.get('max_yuan')
    if fines['included_records']:
        from decimal import Decimal
        require(all(type(v) is str and MONEY.fullmatch(v) for v in (low, high)) and Decimal(low) <= Decimal(high), 'Invalid fine range.')
    else:
        require(low is None and high is None, 'Missing amounts must be unknown, not zero.')
    require(fines.get("is_complete_total") is False, "Money must retain incomplete-total qualification.")


def reconcile(responses, selection=None):
    require(type(responses) is list and 1 <= len(responses) <= 50, "Provide 1 to 50 response objects.")
    from delivery_view import validate_range_contract
    for response in responses:
        try:
            validate_range_contract(response)
        except ValueError as exc:
            raise CheckError(str(exc)) from None
    selected = normalize_selection(selection)
    cases, versions, populations, failures, warnings, sets, identity_groups = {}, {}, {}, [], [], {}, []

    def visit(node, label, inherited_revision=None):
        require(type(node) is dict and type(node.get("scope", {})) is dict, "Invalid response/group.")
        scope = node.get("scope", {})
        if "person_identity_groups" in node:
            identity_groups.append({"source": label, "person_identity_groups": copy.deepcopy(node["person_identity_groups"])})
        revision = scope.get("data_revision", node.get("data_revision", inherited_revision))
        if revision is not None:
            require(type(revision) is int and revision >= 0, "Invalid data revision.")
            versions.setdefault("data_revision", set()).add(revision)
        for key, value in scope.items():
            if key.endswith("_version") and value is not None:
                require(type(value) is str, "Invalid rule version.")
                versions.setdefault(key, set()).add(value)
        for warning in node.get("warnings", []):
            if type(warning) is str and warning not in warnings:
                warnings.append(warning)
        if "set_id" in node:
            audit_set(node)
            name = node["set_id"]
            require(name not in sets or sets[name] == node, "Conflicting responses for one set_id.")
            sets[name] = copy.deepcopy(node)
            if node["status"] != "succeeded":
                failures.append({"source": label, "set_id": name, "status": node["status"]})
            return
        if node.get("status") == "failed" or "error" in node:
            failures.append({"source": label, "status": "failed"})
            return
        if node.get("completion_status", "complete") != "complete":
            failures.append({"source": label, "status": node["completion_status"]})
        results = node.get("results")
        require(type(results) is list, "Response needs results array.")
        if any(type(r) is dict and ("request_id" in r or "set_id" in r) for r in results):
            for index, group in enumerate(results):
                visit(group, label + "/" + str(index), revision)
            return
        ids = []
        for item in results:
            require(type(item) is dict, "Invalid result item.")
            if "item" in item or item.get("status") == "failed":
                if item.get("status") != "succeeded":
                    failures.append({"source": label, "status": "failed"})
                    continue
                item = item["item"]
            require(type(item) is dict and type(item.get("case_id")) is str, "Invalid case.")
            identifier = item["case_id"]
            old = cases.get(identifier, {})
            require(all(old[k] == item[k] for k in old.keys() & item.keys()), "Conflicting versions of same case_id.")
            cases[identifier] = {**old, **copy.deepcopy(item)}
            ids.append(identifier)
        require(len(ids) == len(set(ids)), "Duplicate case_id within page.")
        page = node.get("pagination")
        if type(page) is dict:
            total = page.get("total")
            require(type(total) is int and total >= 0, "Invalid API total.")
            require(page.get("returned", len(ids)) == len(ids), "Returned count differs from page.")
            applied = node.get("applied_filters", {})
            require(type(applied) is dict, "Invalid applied_filters.")
            identity = {"filters": applied, "document": node.get("document")}
            signature = applied.get("filter_signature") or (hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest() if applied or node.get("document") else label)
            pop = populations.setdefault((signature, revision), {"sources": [], "ids": set(), "total": total, "ended": False, "exact": True, "revision": revision})
            require(pop["total"] == total, "Same-filter totals changed.")
            pop["sources"].append(label)
            pop["ids"].update(ids)
            pop["ended"] |= page.get("has_more") is False
            pop["exact"] &= page.get("total_exact") is True

    for index, response in enumerate(responses):
        visit(response, "input-" + str(index + 1))
    require(all(len(v) <= 1 for v in versions.values()), "Mixed data revisions/rule versions; query again.")
    revision = next(iter(versions.get("data_revision", [])), None)
    if selected:
        for item in selected:
            group = sets.get(item["set_id"])
            if group and group.get("id_resolution"):
                resolution = group["id_resolution"]
                require(resolution.get("submitted_members") == len(item["case_ids"]) and resolution.get("unique_requested") == len(set(item["case_ids"])), "Server set differs from selected raw members.")
                resolved = resolution["matched_case_ids"] + resolution.get("unknown_ids", []) + resolution.get("excluded_marked_duplicate_ids", [])
                require(set(resolved) == set(item["case_ids"]), "Server set differs from requested IDs.")
    coverage = [{"sources": p["sources"], "fetched_unique_records": len(p["ids"]), "api_total": p["total"],
                 "data_revision": p["revision"], "api_filter_complete": len(p["ids"]) == p["total"] and p["ended"] and p["exact"] and p["revision"] is not None} for p in populations.values()]
    request = None if selected is None else {"mode": "set_stats", "sets": [
        {"set_id": s["set_id"], "case_ids": s["case_ids"], "include_duplicates": s.get("include_duplicates", False)} for s in selected],
        "expected_data_revision": revision, "statistics": {"group_by": ["stage"]}, "output": "json"}
    return {"kind": "api_result_consistency_check", "data_revision": revision,
            "fetched_unique_records": len(cases), "coverage": coverage, "source_failures": failures,
            "all_api_filters_complete": bool(coverage) and all(p["api_filter_complete"] for p in coverage) and not failures,
            "all_sets_complete": bool(sets) and all(s["status"] == "succeeded" for s in sets.values()) and not failures and (selected is None or all(s["set_id"] in sets for s in selected)),
            "sets": list(sets.values()), "selection_notes": selected, "set_stats_request": request, "warnings": warnings,
            "person_identity_groups_by_source": identity_groups,
            "derived_fields": [{"case_id": c["case_id"], **{k: c[k] for k in ("document_number_inference", "decision_time", "party_associations", "original_party_type", "subject_composition", "person_sanctions", "institution_classification") if k in c}} for c in cases.values()],
            "note": "数量和金额采用同一个set_stats结果；本工具只核对结构及覆盖，不计算金额、不推断地区或时间。未返回的集合没有统计结论。"}


def main(argv=None):
    if __name__ == "__main__":
        sys.dont_write_bytecode = True
    script_folder = str(Path(__file__).resolve().parent)
    if script_folder not in sys.path:
        sys.path.insert(0, script_folder)
    from runtime_check import require_environment, EnvironmentError, error_payload
    try:
        require_environment("verify")
    except EnvironmentError as error:
        print(json.dumps(error_payload(error.report), ensure_ascii=False), file=sys.stderr)
        return 3
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", action="append", metavar="RESPONSE.json|-",
                        help="One API response per input; at most one stdin input.")
    inputs.add_argument("--bundle", metavar="BUNDLE.json|-",
                        help="An object with responses array and optional selection; use - for memory-only stdin.")
    parser.add_argument("--selection", metavar="SELECTION.json|-", help="With --input only; stdin can be consumed once.")
    args = parser.parse_args(argv)
    try:
        if args.bundle is not None:
            require(args.selection is None, "Bundle already contains selection; do not also use --selection.")
            bundle = read_json(args.bundle)
            require(type(bundle) is dict and "responses" in bundle and not set(bundle) - {"responses", "selection"},
                    "Bundle must contain responses and optional selection only.")
            result = reconcile(bundle["responses"], bundle.get("selection"))
        else:
            require(args.input.count("-") + int(args.selection == "-") <= 1, "Stdin can be consumed only once; use --bundle - for multiple objects.")
            result = reconcile([read_json(name) for name in args.input], read_json(args.selection) if args.selection else None)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except CheckError as error:
        print(json.dumps({"error": {"code": "RESULT_CHECK_FAILED", "message": str(error)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, RecursionError, TypeError, KeyError):
        print(json.dumps({"error": {"code": "RESULT_CHECK_FAILED", "message": "输入、集合、金额覆盖或修订核对失败；未输出统计结论。"}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
