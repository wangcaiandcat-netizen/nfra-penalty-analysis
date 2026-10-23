#!/usr/bin/env python3
"""Offline XLSX export from an in-memory API response bundle; stdlib only."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import sys
import math
import unicodedata
from urllib.parse import urlsplit
import zipfile
from xml.etree import ElementTree as ET

sys.dont_write_bytecode = True
folder = str(Path(__file__).resolve().parent)
if folder not in sys.path:
    sys.path.insert(0, folder)
from runtime_check import require_environment, EnvironmentError, error_payload
from delivery_view import DISCLAIMER, OUTPUT_POLICY, backend_person_sanctions

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
XML = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("", MAIN)
ET.register_namespace("r", REL)


class ExportError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ExportError(message)


def text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return str(value)


def get(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def xml_bytes(node):
    return ET.tostring(node, encoding="utf-8", xml_declaration=True)


def sub(node, name, attrs=None, value=None):
    child = ET.SubElement(node, "{" + MAIN + "}" + name, attrs or {})
    if value is not None:
        child.text = str(value)
    return child


def col_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def safe_link(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        p = urlsplit(value)
        host = p.hostname or ""
        return (p.scheme in {"http", "https"} and p.username is None and p.password is None
                and (host == "nfra.gov.cn" or host.endswith(".nfra.gov.cn")
                     or host == "cbirc.gov.cn" or host.endswith(".cbirc.gov.cn")))
    except ValueError:
        return False


def validate_text(value):
    require(len(value.encode("utf-16-le")) // 2 <= 32767, "单元格文本超过Excel长度限制；请拆分字段或交付JSON。")
    require(all(c in "\t\n\r" or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD
                or 0x10000 <= ord(c) <= 0x10FFFF for c in value), "文本包含XML无法表示的控制字符；未生成文件。")


def worksheet(title, subtitle, headers, rows, widths, *, date_columns=(), money_columns=(), links=()):
    root = ET.Element("{" + MAIN + "}worksheet")
    end = max(4, 4 + len(rows))
    footer = end + 2
    sub(root, "dimension", {"ref": "A1:" + col_name(len(headers)) + str(footer)})
    views = sub(root, "sheetViews")
    view = sub(views, "sheetView", {"workbookViewId": "0", "showGridLines": "0"})
    sub(view, "pane", {"xSplit": "1", "ySplit": "4", "topLeftCell": "B5", "activePane": "bottomRight", "state": "frozen"})
    sub(view, "selection", {"pane": "bottomRight", "activeCell": "B5", "sqref": "B5"})
    sub(root, "sheetFormatPr", {"defaultRowHeight": "22"})
    cols = sub(root, "cols")
    for index, width in enumerate(widths, 1):
        sub(cols, "col", {"min": str(index), "max": str(index), "width": str(width), "customWidth": "1"})
    data = sub(root, "sheetData")
    relations = []
    for row_index, values in [(1, [title]), (2, [subtitle]), (4, headers)] + [(i + 5, r) for i, r in enumerate(rows)] + [(footer, [DISCLAIMER])]:
        attrs = {"r": str(row_index)}
        lines = max([1] + [sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text(v)) / max(1, widths[i]) for i, v in enumerate(values)])
        height = "28" if row_index in (1, 4) else "32" if row_index == 2 else str(min(140, max(32, math.ceil(lines) * 14 + 8)))
        attrs.update({"ht": height, "customHeight": "1"})
        row = sub(data, "row", attrs)
        for index, value in enumerate(values, 1):
            if value is None or value == "":
                continue
            address = col_name(index) + str(row_index)
            style = 1 if row_index == 4 else 2 if row_index == 1 else 3 if row_index == 2 else 0
            numeric = type(value) in {int, Decimal}
            if row_index > 4 and index in date_columns and isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                try:
                    value = (date.fromisoformat(value) - date(1899, 12, 30)).days
                    numeric, style = True, 5
                except ValueError:
                    pass
            if row_index > 4 and index in money_columns and value != "":
                amount = Decimal(str(value))
                require(amount.is_finite(), "金额不是有限十进制数。")
                # Excel has 15 significant digits. Preserve large originals as text.
                if len(amount.as_tuple().digits) <= 15:
                    value, numeric, style = amount, True, 4
                else:
                    value, numeric = str(value), False
            cell = sub(row, "c", {"r": address, "s": str(style)})
            if type(value) is bool:
                cell.set("t", "b")
                sub(cell, "v", value="1" if value else "0")
            elif numeric:
                sub(cell, "v", value=value)
            else:
                value = text(value)
                validate_text(value)
                cell.set("t", "inlineStr")
                string = sub(cell, "is")
                sub(string, "t", { "{" + XML + "}space": "preserve"}, value)
            if row_index > 4 and index in links and safe_link(value):
                relations.append((address, value))
    if rows:
        sub(root, "autoFilter", {"ref": "A4:" + col_name(len(headers)) + str(end)})
    # Title/context/footer rows span empty cells outside the data table.
    merges = sub(root, "mergeCells", {"count": "3"})
    for row in (1, 2, footer):
        sub(merges, "mergeCell", {"ref": "A" + str(row) + ":" + col_name(min(8, len(headers))) + str(row)})
    if relations:
        hyperlinks = sub(root, "hyperlinks")
        relroot = ET.Element("Relationships", {"xmlns": PKG})
        for index, (address, target) in enumerate(relations, 1):
            sub(hyperlinks, "hyperlink", {"ref": address, "{" + REL + "}id": "rId" + str(index)})
            ET.SubElement(relroot, "Relationship", {"Id": "rId" + str(index), "Type": REL + "/hyperlink", "Target": target, "TargetMode": "External"})
    else:
        relroot = None
    sub(root, "pageMargins", {"left": "0.25", "right": "0.25", "top": "0.4", "bottom": "0.4", "header": "0.2", "footer": "0.2"})
    return xml_bytes(root), xml_bytes(relroot) if relroot is not None else None


STYLES = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="yyyy-mm-dd"/></numFmts>
<fonts count="3"><font><sz val="10"/><name val="Arial"/><color rgb="FF172B4D"/></font><font><b/><sz val="10"/><name val="Arial"/><color rgb="FFFFFFFF"/></font><font><b/><sz val="14"/><name val="Arial"/><color rgb="FF172B4D"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF24476A"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="6">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center"/></xf>
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>
<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="right" vertical="top"/></xf>
</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def build_workbook(bundle, allow_partial=False):
    from result_check import reconcile
    from delivery_view import penalty_attribution
    require(type(bundle) is dict and "responses" in bundle and not set(bundle) - {"responses", "selection", "context"}, "输入须为responses及可选selection/context对象。")
    context = bundle.get("context", {})
    require(type(context) is dict and not set(context) - {"title", "request", "notes", "source_labels"}, "context仅接受title/request/notes/source_labels。")
    labels = context.get("source_labels")
    require(labels is None or (type(labels) is list and len(labels) == len(bundle["responses"])
            and all(type(label) is str and 1 <= len(label) <= 100 for label in labels)),
            "source_labels须与responses逐一对应，每项1至100字。")
    checked = reconcile(bundle["responses"], bundle.get("selection"))
    require(checked["data_revision"] is not None, "响应缺少数据修订，不能核对或导出；请重新取得同修订API响应。")
    def reject_credentials(value):
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                require(normalized not in {"api_key", "apikey", "x_api_key", "authorization", "cookie", "access_token", "password", "client_secret"},
                        "输入包含认证或凭据字段；仅传公开API响应和无凭据查询条件。")
                reject_credentials(item)
        elif isinstance(value, list):
            for item in value:
                reject_credentials(item)
    reject_credentials(context)
    coverage = checked["coverage"]
    partial = (bool(checked["source_failures"]) or any(not c["api_filter_complete"] for c in coverage)
               or any(s["status"] != "succeeded" for s in checked["sets"])
               or (bundle.get("selection") is not None and not checked["all_sets_complete"]))
    require(not partial or allow_partial, "结果存在缺页、未知ID或失败项；补齐后再导出，或显式--allow-partial导出并标注。")
    cases, membership, summaries, distributions, meta, identities, personal_stats, mixed_preview = {}, {}, [], [], [], [], [], []
    def visit(node, label):
        if type(node) is not dict:
            return
        stats = node.get("statistics")
        if type(stats) is dict:
            personal = stats.get('personal_sanctions')
            if isinstance(personal, dict):
                # Preserve backend units and values; no local arithmetic or
                # extraction of headcounts from mixed publications.
                for field in ('rule_version', 'scope', 'record_counts', 'pure_person', 'mixed_policy'):
                    value = text(personal.get(field))
                    for offset in range(0, max(1, len(value)), 12000):
                        personal_stats.append([label, field, offset // 12000 + 1, value[offset:offset+12000]])
            fine = stats.get("single_explicit_fines") or {}
            money_scope = fine.get("denominator_scope") or fine.get("scope") or ""
            if fine and fine.get("included_records") == 0:
                money_scope = "没有可确认金额，范围未知，不视为零。" + money_scope
            status = node.get("status", node.get("completion_status", "complete"))
            status = {"succeeded":"成功", "complete":"完成", "partial":"部分成功", "failed":"失败"}.get(status, status)
            reasons = {"mixed_publication":"机构与个人混合公示", "not_formal_decision":"非正式决定",
                       "missing_amount":"缺少金额", "invalid_negative_amount":"原金额为负",
                       "fine_status:ambiguous":"金额不明确", "fine_status:not_stated":"未列明罚款"}
            reason_text = "；".join("%s：%s条（%s）" % (reasons.get(x.get("reason"),x.get("reason")),x.get("count"),x.get("reason")) for x in fine.get("exclusion_reasons",[]))
            summaries.append([label, status,
                stats.get("total_records", stats.get("union_records")), stats.get("source_documents"),
                fine.get("min_yuan"), fine.get("max_yuan"), fine.get("included_records"), fine.get("excluded_records"),
                fine.get("denominator"), reason_text, fine.get("zero_amount_records"),
                money_scope, text([r['case_id'] for r in (fine.get('minimum') or {}).get('records', [])]),
                text([r['case_id'] for r in (fine.get('maximum') or {}).get('records', [])])])
            for dimension, group in (stats.get("groups") or {}).items():
                if not isinstance(group, dict):
                    continue
                for item in group.get("items", []):
                    distributions.append([label, dimension, item.get("key"), item.get("name") or item.get("label") or item.get("key"), item.get("count"),
                                          group.get("denominator"), group.get("multi_label"), group.get("coverage_note", group.get("note"))])
        for key in ("scope", "applied_filters", "document", "pagination", "statistics", "status", "completion_status", "warnings", "error", "id_resolution", "selection_signature", "person_identity_groups"):
            if key in node:
                meta.append([label, key, text(node[key])])
        identity_groups = node.get("person_identity_groups")
        related = node.get('related_mixed')
        if isinstance(related, dict):
            meta.append([label, 'related_mixed', text(related)])
            for row in related.get('results', []):
                mixed_preview.append([label, row.get('case_id'), row.get('party'), row.get('penalty'),
                    row.get('source_url'), '后端另列混合预览；不计入主清单，不拆人数或分摊金额'])
        if isinstance(identity_groups, dict) and isinstance(identity_groups.get("groups"), list):
            for group in identity_groups["groups"]:
                if not isinstance(group, dict):
                    continue
                references = text(group.get("references"))
                for offset in range(0, max(1, len(references)), 12000):
                    identities.append([label, group.get("identity_key"), group.get("name"), group.get("institution"),
                                       group.get("position"), offset//12000+1, references[offset:offset+12000],
                                       identity_groups.get("scope")])
        if "set_id" in node:
            return
        for item in node.get("results", []):
            if not isinstance(item, dict):
                continue
            if "request_id" in item or "set_id" in item:
                visit(item, str(item.get("set_id") or item.get("request_id")))
            elif "item" in item:
                if item.get("status") == "succeeded":
                    row = item["item"]
                    cases[row["case_id"]] = {**cases.get(row["case_id"], {}), **row}
                    membership.setdefault(row["case_id"], set()).add(label)
            elif "case_id" in item:
                cases[item["case_id"]] = {**cases.get(item["case_id"], {}), **item}
                membership.setdefault(item["case_id"], set()).add(label)
    for index, response in enumerate(bundle["responses"], 1):
        visit(response, labels[index-1] if labels is not None else "响应" + str(index))
    revision = checked["data_revision"]
    state = "部分结果" if partial else "完整筛选明细" if coverage and cases else "已提供数据"
    if cases and not coverage:
        state = "已提供ID明细"
    caption = "修订%s；%s；明细%s行。数量单位为公示记录；金额仅描述已提取样本的最小/最大值，不表示处罚轻重。" % (revision, state, len(cases))
    title = context.get("title", "金融监管处罚台账")
    require(type(title) is str and 1 <= len(title) <= 100, "title须为1–100字文本。")
    meta = [["导出", "导出时间（北京时间）", datetime.now(timezone(timedelta(hours=8))).isoformat()],
            ["导出", "结果范围", state], ["导出", "已导出唯一case_id数", len(cases)],
            ["导出", "分页覆盖", text(coverage)], ["导出", "失败/部分项", text(checked["source_failures"])],
            ["导出", "金额口径", "仅采用API金额范围及原文依据；混合公示等由服务端排除，不累计金额、不计算均值或中位数。"],
            ["导出", "数字精度", "金额超过Excel15位有效数字时保留文本；原始金额字符串保留。"],
            ["导出", "多标签", "不同业务/重叠集合不可加总；多个统计维度为独立分布。"],
            ["导出", "客观输出规则", text(OUTPUT_POLICY)],
            ["导出", "提示", DISCLAIMER]] + meta
    for key in ("request", "notes"):
        if key in context:
            meta.append(["用户上下文", key, text(context[key])])
    if not summaries:
        summaries = [["未提供服务端统计", state, None, None, None, None, None, None, None, "", None,
                      "仅导出明细，不从当前页推算总体数量或金额。", "", ""]]
    detail_rows, evidence, attributions = [], [], []
    for identifier, row in cases.items():
        inference = row.get("document_number_inference") or {}
        timing = row.get("decision_time") or {}
        classification = row.get("classification") or {}
        institution = row.get("institution_classification")
        institution = institution if isinstance(institution, dict) else {}
        # Display only backend-supplied names; never infer types from party text.
        matches = institution.get("matches")
        institution_names = list(dict.fromkeys(item["type_name"] for item in matches
            if isinstance(item, dict) and isinstance(item.get("type_name"), str))) if isinstance(matches, list) else []
        business = classification.get("business_codes", classification.get("business_types", classification.get("business_tags", classification.get("business", []))))
        detail_rows.append([str(identifier), text(row.get("doc_id")), row.get("party"), row.get("subject_composition", row.get("party_type")),
            row.get("original_party_type"), row.get("decision_number"), row.get("publish_date"), row.get("decision_date"),
            timing.get("value"), timing.get("precision"), timing.get("basis"), row.get("authority"),
            " / ".join(str(v) for k,v in (row.get("jurisdiction") or {}).items() if k.endswith("_id") and v),
            get(inference,"issuer","name"), get(inference,"seat","name"),
            row.get("violation"), row.get("penalty"), row.get("stage_code"), row.get("fine_yuan"),
            row.get("fine_status"), text(business), row.get("source_url"), revision,
            ", ".join(sorted(membership.get(identifier, []))),
            " / ".join(institution_names), text(institution.get("type_codes")), institution.get("status"),
            institution.get("as_of_date"), institution.get("version"), institution.get("note"),
            get(row, 'fine_context', 'amount_scope'), get(row, 'fine_context', 'includes_multiple_violations'),
            get(row, 'fine_context', 'aggregation', 'included'), get(row, 'fine_context', 'aggregation', 'reason_note'),
            text(get(row, 'fine_context', 'amount_attribution'))])
        for item in penalty_attribution(row)["items"]:
            source = item["evidence"]
            state = {"backend_confirmed": "后端确认对应", "unresolved": "未明确对应，保留原文"}[item["attribution"]]
            identity = item.get("identity") or {}
            attributions.append([identifier, item["subject_text"], item["action_text"], state,
                                 source.get("field"), source.get("start"), source.get("end"), source.get("text"), item["note"], row.get("source_url"),
                                 get(item,"institution","value"), get(item,"position","value"), identity.get("key") if isinstance(identity,dict) else None,
                                 get(item,"duration","original_text"), item.get("sanction_type"), item.get("source")])
        person_sanctions, person_source = backend_person_sanctions(row)
        if person_source is not None:
            raw = text(person_sanctions)
            for part in range(0, max(1,len(raw)), 12000):
                evidence.append([identifier, "person_sanctions", part//12000+1, raw[part:part+12000]])
        for field in ("jurisdiction", "document_number_inference", "decision_time", "party_associations", "party_type_normalization", "subject_composition", "classification", "institution_classification", "fine_context"):
            if field in row:
                raw = text(row[field])
                # Split by characters well below Excel's UTF-16 cell limit.
                for part in range(0, max(1, len(raw)), 12000):
                    evidence.append([identifier, field, part // 12000 + 1, raw[part:part+12000]])
    # Large context/warning JSON is split rather than silently truncated.
    metadata = []
    for source, field, value in meta:
        value = text(value)
        for part in range(0, max(1, len(value)), 12000):
            metadata.append([source, field, part // 12000 + 1, value[part:part+12000]])
    sheets = [
        ("统计汇总", title, caption,
       ["来源/集合", "状态", "记录数", "公告数", "最小金额（元）", "最大金额（元）", "可提取样本", "样本排除", "金额分母", "排除原因", "明确零值", "金额口径", "最小值案例ID（完整依据见口径与覆盖）", "最大值案例ID（完整依据见口径与覆盖）"],
       summaries, [20,16,12,12,23,23,12,12,12,44,12,55,90,90], (), (5,6), ()),
        ("明细台账", title, caption,
         ["case_id","doc_id","原始当事人","主体构成（后端）","原始类型","文号","公告日期","原始决定日","回退时间值","时间精度","时间依据","发文机关","原始辖区","文号推定机关辖区","发文机关驻地","违法事实","处罚内容","阶段","后端罚款元（文本）","金额状态","业务标签","官方来源","数据修订","命中请求",
        "机构类别（后端名称）","机构类别代码","机构分类状态","名录时点","分类版本","分类说明", "金额用途", "含多项违规", "是否为金额范围样本", "金额样本说明", "金额归属依据JSON"],
         detail_rows, [24,16,35,20,14,30,14,14,16,14,25,30,48,55,55,65,65,16,23,20,35,45,12,28,32,32,20,14,34,65,26,16,18,70,90], (7,8), (), (22,)),
        ("口径与覆盖", title, "保留查询条件、版本、范围、失败项与排除原因；长JSON按分段号拼接。",
         ["来源","字段","分段","内容"], metadata, [23,35,10,110], (), (), ())]
    if personal_stats:
        sheets.append(('个人处罚统计', title, '完整筛选集的后端统计，保留指标单位及未知/样本覆盖；混合记录不拆人数。',
                       ['来源/期间', '后端字段', '分段', '后端原值JSON'], personal_stats, [28,32,10,110], (), (), ()))
    if mixed_preview:
        sheets.append(('另列混合预览', title, '仅为后端预览，可能截断；完整查询方式见口径与覆盖的related_mixed。',
                       ['来源/期间', 'case_id', '当事人原文', '处罚原文', '官方来源', '口径'], mixed_preview,
                       [26,26,65,95,50,75], (), (), (5,)))
    if distributions:
        sheets.append(("分布统计",title,"各维度是独立分布；业务多标签不可加总。",
                       ["来源/集合","维度","分组代码","分组名称","数量","分母","多标签","说明"],
                       distributions,[22,25,32,32,12,12,12,65],(),(),()))
    if evidence:
        sheets.append(("推定与关联证据",title,"按case_id保留服务端原字段；长JSON按分段号拼接。",
                       ["case_id","字段","分段","原字段JSON"],evidence,[25,35,10,110],(),(),()))
    if attributions:
        sheets.append(("对象与处罚依据",title,"直接展示后端person_sanctions；未知对应保留原文，不用本地正则重配，不按姓名计人数。",
                       ["case_id","原文对象","处罚片段","对应状态","依据字段","起始位置","结束位置","完整依据原文","说明","官方来源",
                        "具体机构（后端值）","职务（后端值）","后端身份键","期限原文（后端值）","处罚类型原值","结构来源"],
                       attributions,[25,44,65,24,14,12,12,80,65,45,55,35,45,65,24,38],(),(),(10,)))
    if identities:
        sheets.append(("身份分组",title,"逐响应保留后端身份分组及范围，不按姓名合并、不跨页累加分组数为人数。",
                       ["来源","后端身份键","姓名","具体机构","职务","分段","关联引用JSON","后端覆盖范围"],
                       identities,[25,45,24,55,35,12,80,65],(),(),()))
    stream = io.BytesIO()
    content = ET.Element("Types", {"xmlns":"http://schemas.openxmlformats.org/package/2006/content-types"})
    for ext, mime in (("rels","application/vnd.openxmlformats-package.relationships+xml"),("xml","application/xml")):
        ET.SubElement(content,"Default",{"Extension":ext,"ContentType":mime})
    for name, kind in (("/xl/workbook.xml","sheet.main"),("/xl/styles.xml","styles")):
        ET.SubElement(content,"Override",{"PartName":name,"ContentType":"application/vnd.openxmlformats-officedocument.spreadsheetml."+kind+"+xml"})
    workbook=ET.Element("{"+MAIN+"}workbook")
    views=sub(workbook,"bookViews");sub(views,"workbookView",{"activeTab":"0"})
    worksheet_nodes=sub(workbook,"sheets")
    relationships=ET.Element("Relationships",{"xmlns":PKG})
    package_rels=ET.Element("Relationships",{"xmlns":PKG})
    ET.SubElement(package_rels,"Relationship",{"Id":"rId1","Type":REL+"/officeDocument","Target":"xl/workbook.xml"})
    with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        for index, (name, heading, subtitle, headers, rows, widths, dates, monies, links) in enumerate(sheets,1):
            require(len(rows)<=1048572,"工作表超过Excel行数上限。")
            sub(worksheet_nodes,"sheet",{"name":name,"sheetId":str(index),"{"+REL+"}id":"rId"+str(index)})
            ET.SubElement(relationships,"Relationship",{"Id":"rId"+str(index),"Type":REL+"/worksheet","Target":"worksheets/sheet"+str(index)+".xml"})
            ET.SubElement(content,"Override",{"PartName":"/xl/worksheets/sheet"+str(index)+".xml","ContentType":"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
            data, rels=worksheet(heading,subtitle,headers,rows,widths,date_columns=dates,money_columns=monies,links=links)
            archive.writestr("xl/worksheets/sheet"+str(index)+".xml",data)
            if rels:
                archive.writestr("xl/worksheets/_rels/sheet"+str(index)+".xml.rels",rels)
        ET.SubElement(relationships,"Relationship",{"Id":"rIdStyles","Type":REL+"/styles","Target":"styles.xml"})
        archive.writestr("[Content_Types].xml",xml_bytes(content))
        archive.writestr("_rels/.rels",xml_bytes(package_rels))
        archive.writestr("xl/workbook.xml",xml_bytes(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels",xml_bytes(relationships))
        archive.writestr("xl/styles.xml",STYLES)
    return stream.getvalue(), {"data_revision":revision,"detail_records":len(cases),"partial":partial,
                              "sheets":[s[0] for s in sheets],"server_money_used":True}


def export_workbook(bundle, output, allow_partial=False):
    require_environment("export", output)
    data, info = build_workbook(bundle, allow_partial)
    path = Path(output).expanduser().absolute()
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            stream.write(data)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise ExportError("无法保存.xlsx；检查目录权限、磁盘空间或文件名。") from None
    return {"output":str(path),"bytes":len(data),**info}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",required=True,metavar="BUNDLE.json|-")
    parser.add_argument("--output",required=True,metavar="FILE.xlsx")
    parser.add_argument("--allow-partial",action="store_true",help="Explicitly export incomplete/failed results with visible coverage.")
    args=parser.parse_args(argv)
    try:
        require_environment("export",args.output)
        from result_check import read_json, CheckError
        bundle=read_json(args.input)
        report=export_workbook(bundle,args.output,args.allow_partial)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return 0
    except EnvironmentError as error:
        print(json.dumps(error_payload(error.report),ensure_ascii=False),file=sys.stderr)
        return 3
    except (ExportError, ValueError, OSError, TypeError, KeyError, ArithmeticError, RecursionError) as error:
        message=str(error) if isinstance(error, ExportError) else "输入或响应一致性检查未通过，未生成Excel。"
        print(json.dumps({"error":{"code":"XLSX_EXPORT_FAILED","message":message}},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
