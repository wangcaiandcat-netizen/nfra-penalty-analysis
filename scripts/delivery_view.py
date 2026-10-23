"""Present backend facts; no local person/identity/sanction classification."""
from __future__ import annotations
import copy
from decimal import Decimal, InvalidOperation

DISCLAIMER = "AI生成内容，仅供参考"
OUTPUT_POLICY = {
    "mode": "facts_only", "final_line": DISCLAIMER,
    "requirements": [
        "仅陈述已获取的事实、数据、原文及范围；未知、未识别、推定分别说明。",
        "禁止以模型自己的口吻输出最差、最好、更差、更好、更严重、最严重、最轻/最重、雷区、处罚变轻/变重等主观判断；不得作机构或人员优劣排名。",
        "金额仅描述后端已提取样本的min_yuan、max_yuan、对应原文和样本数；不累计金额、不计算平均数或中位数，不按fine_yuan或fine_status自行求和。最大/最小金额不能改称最重/最轻处罚。",
        "数量、占比、差额只按同口径数据说明；无证据不解释原因、推断监管意图或评价处罚轻重。",
        "姓名数不是人数；身份分组沿用后端姓名、具体机构、职务及identity_key，不另行归并。",
        "任职关联查询称已识别任职关联的记录，不能声称全部责任人。",
        "文号推定年不是已核实决定年；回退查询为零不等于实际作出处罚为零。",
        "零结果只说本库按本次条件未检出；跨年/放宽条件的结果不能证明原查询绝对没有处罚，不为零结果强制扩查。公示形式和样本数变化不足以证明变化原因。",
        "brief/full只控制案例明细展示，不截断统计；匹配/获取/展示数分别说明，不能把5条预览称为全量。top_n上限50。历史任务记忆与当前接口契约冲突时以本契约和实际响应为准。",
        "行分类只使用后端已确认字段，不按银行名称临时分类。",
        "原文中的评价措辞只能带来源明确引用，不能转成模型自己的判断。",
    ],
}


def validate_range_contract(node):
    """Fail closed against a stale backend; never translate totals locally."""
    if not isinstance(node, dict):
        return
    stats = node.get('statistics')
    if isinstance(stats, dict):
        fine = stats.get('single_explicit_fines')
        personal = ((stats.get('personal_sanctions') or {}).get('pure_person') or {}).get('fine_amounts')
        for value, count_key in ((fine, 'included_records'), (personal, 'confirmed_entries')):
            if value is None:
                continue
            if (not isinstance(value, dict) or value.get('statistic') != 'extracted_fine_range'
                    or value.get('aggregation_enabled') is not False
                    or any(k in value for k in ('yuan', 'sum_yuan', 'mean_yuan', 'median_yuan', 'threshold_100000_yuan'))):
                raise ValueError('后台未支持金额范围契约；请更新后台，不自行求和或改算范围。')
            count = value.get(count_key)
            low, high = value.get('min_yuan'), value.get('max_yuan')
            if type(count) is not int or count < 0:
                raise ValueError('金额样本数无效。')
            if not count:
                if any(value.get(k) is not None for k in ('min_yuan', 'max_yuan', 'minimum', 'maximum')):
                    raise ValueError('无可确认金额时范围必须为未知。')
            else:
                try:
                    valid = all(isinstance(v, str) and Decimal(v).is_finite() for v in (low, high)) and Decimal(0) <= Decimal(low) <= Decimal(high)
                except (InvalidOperation, TypeError):
                    valid = False
                if not valid or any(not isinstance(value.get(k), dict) or not value[k].get('records') for k in ('minimum', 'maximum')):
                    raise ValueError('金额范围或原文依据无效。')
    for child in node.get('results') or []:
        validate_range_contract(child)


def backend_person_sanctions(row):
    if "person_sanctions" in row:
        return copy.deepcopy(row["person_sanctions"]), "person_sanctions"
    associations = row.get("party_associations")
    if isinstance(associations, dict) and "person_sanctions" in associations:
        return copy.deepcopy(associations["person_sanctions"]), "party_associations.person_sanctions"
    return None, None


def _original_evidence(row):
    original = row.get("penalty")
    return {"field": "penalty", "text": original if isinstance(original, str) else "",
            "start": 0 if isinstance(original, str) else None,
            "end": len(original) if isinstance(original, str) else None}


def penalty_attribution(row):
    """Display backend mappings; missing/unknown mappings stay unassigned."""
    structured, source = backend_person_sanctions(row)
    original, items, incomplete = _original_evidence(row), [], False
    if isinstance(structured, dict):
        subjects = structured.get("subjects")
        if not isinstance(subjects, list):
            subjects, incomplete = [], True
        for subject in subjects:
            if not isinstance(subject, dict) or not isinstance(subject.get("sanctions"), list):
                incomplete = True
                continue
            if not subject["sanctions"]:
                incomplete = True
            for sanction in subject["sanctions"]:
                if not isinstance(sanction, dict):
                    incomplete = True
                    continue
                name, evidence = subject.get("name"), sanction.get("evidence")
                proofs = [e for e in evidence if isinstance(e, dict) and isinstance(e.get("field"), str)
                          and bool(e.get("text", e.get("quote")))] if isinstance(evidence, list) else []
                confirmed = (isinstance(name, dict) and name.get("status") == "confirmed"
                             and bool(name.get("value")) and sanction.get("status") == "confirmed"
                             and sanction.get("attribution_status") == "confirmed" and bool(proofs))
                incomplete = incomplete or not confirmed
                selected = next((e for e in proofs if e.get("field") == "penalty"), proofs[0] if proofs else original)
                proof = copy.deepcopy(selected)
                proof["text"] = selected.get("text", selected.get("quote", ""))
                item = {"subject_text": name["value"] if confirmed else None,
                        "action_text": sanction.get("text"),
                        "attribution": "backend_confirmed" if confirmed else "unresolved",
                        "source": source, "subject_ref": subject.get("subject_ref"),
                        "sanction_type": sanction.get("type"), "sanction_label": sanction.get("label"),
                        "backend_status": sanction.get("status"),
                        "backend_attribution_status": sanction.get("attribution_status"),
                        "evidence": proof, "backend_evidence": copy.deepcopy(evidence),
                        "attribution_evidence": copy.deepcopy(sanction.get("attribution_evidence")),
                        "note": "展示后端已确认对象与处罚，不新增身份、期限或金额推断。" if confirmed
                                else "后端未确认对应，保留依据，不由本地规则补配姓名。"}
                for key in ("institution", "position", "identity"):
                    item[key] = copy.deepcopy(subject.get(key))
                for key in ("duration", "amount"):
                    item[key] = copy.deepcopy(sanction.get(key))
                items.append(item)
        incomplete = incomplete or bool(structured.get("unresolved") or structured.get("unrecognized"))
        incomplete = incomplete or structured.get("status") in {"unknown", "partial", "conflict", "unresolved"}
    if not items or incomplete:
        items.append({"subject_text": None, "action_text": None, "attribution": "unresolved",
                      "source": source or "original_penalty_only", "evidence": original,
                      "note": "保留完整处罚原文；结构缺失、未确认或有待核项，不使用本地正则重配对象。"})
    return {"items": items, "has_unresolved": incomplete or any(x["attribution"] == "unresolved" for x in items),
            "source": source or "original_penalty_only",
            "note": "对象对应沿用后端person_sanctions；依据段数、姓名数、身份分组数和公示记录数不能互相替代。"}


def decision_time_display(row):
    """Label supplied dates/inferences without extracting a year locally."""
    raw, timing = row.get("decision_date"), row.get("decision_time")
    if raw:
        return {"text": f"原始决定日期：{raw}", "basis": "decision_date", "is_inferred": False}
    if isinstance(timing, dict) and timing.get("value"):
        if timing.get("status") == "inferred" or timing.get("basis") == "document_number_year":
            return {"text": f"按文号推定为{timing['value']}；原始决定日期未载明", "basis": timing.get("basis"), "is_inferred": True}
        return {"text": "原始决定日期未载明；仅保留API时间值与状态，不能改称已核实决定日",
                "basis": timing.get("basis"), "is_inferred": None}
    return {"text": "决定日期未载明，不能确定实际作出处罚的年份", "basis": None, "is_inferred": None}


def compact_statistics(statistics):
    if not isinstance(statistics, dict):
        return None
    # Preserve the documented API object shape; coverage/unknown notes are facts.
    return copy.deepcopy(statistics)


def compact_case(card):
    fields = ("case_id", "party", "party_type", "subject_composition", "authority", "publish_date",
              "decision_number", "decision_date", "decision_time", "decision_time_display", "source_url",
              "violation", "penalty", "fine_yuan", "fine_status", "fine_context", "business_codes",
              "person_sanctions", "person_sanctions_source", "penalty_attribution", "institution_classification")
    result = {key: copy.deepcopy(card.get(key)) for key in fields}
    facts = list(dict.fromkeys(e.get("text") for e in card.get("bills_evidence", [])
                              if isinstance(e, dict) and isinstance(e.get("text"), str)))
    result["violation_evidence"] = facts or ([card["violation"]] if card.get("violation") else [])
    return result
