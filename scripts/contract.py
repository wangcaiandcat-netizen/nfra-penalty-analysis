"""Standalone JSON validation for the public NFRA penalty API (Python 3.10+).

No DeepFin, database, Pydantic or model dependency. Catalog code existence and
signed cursor/revision checks remain authoritative on the API server.
"""
from __future__ import annotations

import copy
import re
from datetime import date

COMMANDS = ("catalog", "query", "detail", "batch", "stats", "set_stats", "document", "explain")
PREFIX = "/api/v1/external/penalties"
DEFAULT_QUESTION = "仅依据已获取原文和数据，客观列明违法事实、处罚、日期口径与可核对的数量变化；不作严重程度、优劣、监管强弱或无证据的因果判断。末尾独占一行写：AI生成内容，仅供参考"
STAGES = {"decided", "proposed", "enforcement", "revoked"}
DIMENSIONS = {"year", "quarter", "month", "region", "authority_province",
              "authority_city", "party_type", "subject_composition", "business_type", "stage",
              "document_number_year", "document_jurisdiction", "document_province", "issuer_seat_city",
              "institution_type"}
CASE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CODE_RE = re.compile(r"^[a-z][a-z0-9_.:-]*$")


class ValidationError(ValueError):
    def __init__(self, message: str, path: str = "request"):
        self.message = message
        self.path = path
        super().__init__(f"{path}: {message}")


def _object(value, fields, path):
    if type(value) is not dict:
        raise ValidationError("must be a JSON object", path)
    if any(type(key) is not str or key not in fields for key in value):
        raise ValidationError("contains unsupported fields", path)
    return value


def _string(value, path, minimum=0, maximum=200, pattern=None):
    if type(value) is not str:
        raise ValidationError("must be a string", path)
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ValidationError(f"string length must be {minimum}..{maximum}", path)
    if any(ord(char) < 32 for char in value) and path != "question":
        raise ValidationError("control characters are not allowed", path)
    if pattern and not pattern.fullmatch(value):
        raise ValidationError("invalid string format", path)
    return value


def _enum(value, choices, path):
    value = _string(value, path, minimum=1, maximum=160)
    if value not in choices:
        raise ValidationError("unsupported value", path)
    return value


def _integer(value, path, minimum, maximum=None):
    if type(value) is not int:
        raise ValidationError("must be an integer, not a boolean or string", path)
    if value < minimum or (maximum is not None and value > maximum):
        raise ValidationError("integer is outside the allowed range", path)
    return value


def _array(value, path, maximum, item_validator, minimum=0, unique=True):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValidationError(f"must be an array with {minimum}..{maximum} items", path)
    values = [item_validator(item, f"{path}[{index}]") for index, item in enumerate(value)]
    return list(dict.fromkeys(values)) if unique else values


def _date(value, path):
    if value is None:
        return None
    value = _string(value, path, minimum=10, maximum=10)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValidationError("date must use YYYY-MM-DD", path)
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValidationError("invalid calendar date", path) from None
    return value


def validate_filters(value=None, path="filters"):
    raw = _object({} if value is None else value,
        {"date", "subjects", "regions", "business", "themes", "stages", "include_duplicates", "keyword",
         "document_number", "affiliations", "doc_ids", "institution_types"}, path)
    dates = _object(raw.get("date", {}), {"from", "to", "basis"}, path + ".date")
    date_from = _date(dates.get("from"), path + ".date.from")
    date_to = _date(dates.get("to"), path + ".date.to")
    if date_from and date_to and date_from > date_to:
        raise ValidationError("start date must not follow end date", path + ".date")
    subjects = _object(raw.get("subjects", {}), {"names", "match", "party_types", "compositions"}, path + ".subjects")
    if subjects.get('party_types') and subjects.get('compositions'):
        raise ValidationError('party_types and compositions cannot both be selected', path + '.subjects')
    regions = _object(raw.get("regions", {}), {"ids", "scope", "basis"}, path + ".regions")
    extra = {}
    if "institution_types" in raw:
        institution = _object(raw["institution_types"], {"codes", "match", "basis"}, path + ".institution_types")
        extra["institution_types"] = {
            "codes": _array(institution.get("codes", []), path + ".institution_types.codes", 30,
                lambda v, p: _string(v, p, 1, 100, CODE_RE)),
            "match": _enum(institution.get("match", "any"), {"any", "all"}, path + ".institution_types.match"),
            "basis": _enum(institution.get("basis", "respondent_or_employer"),
                {"respondent_or_employer", "respondent", "employer"}, path + ".institution_types.basis")}
    if "document_number" in raw:
        number = _object(raw["document_number"], {"years", "prefixes"}, path + ".document_number")
        extra["document_number"] = {
            "years": _array(number.get("years", []), path + ".document_number.years", 50,
                lambda v, p: _integer(v, p, 1900, 2100), unique=False),
            "prefixes": _array(number.get("prefixes", []), path + ".document_number.prefixes", 50,
                lambda v, p: _string(v, p, 1, 60), unique=False)}
    if "affiliations" in raw:
        affiliation = _object(raw["affiliations"], {"institutions", "match", "include_mixed"}, path + ".affiliations")
        mixed = affiliation.get("include_mixed", False)
        if type(mixed) is not bool:
            raise ValidationError("must be a boolean", path + ".affiliations.include_mixed")
        extra["affiliations"] = {
            "institutions": _array(affiliation.get("institutions", []), path + ".affiliations.institutions", 50,
                lambda v, p: _string(v, p, 1, 200), unique=False),
            "match": _enum(affiliation.get("match", "contains"), {"exact", "contains"}, path + ".affiliations.match"),
            "include_mixed": mixed}
    if "doc_ids" in raw:
        extra["doc_ids"] = _array(raw["doc_ids"], path + ".doc_ids", 50,
            lambda v, p: _integer(v, p, 1), unique=False)
    tags = {}
    for name in ("business", "themes"):
        tag = _object(raw.get(name, {}), {"codes", "match"}, path + "." + name)
        tags[name] = {
            "codes": _array(tag.get("codes", []), path + "." + name + ".codes", 30,
                lambda v, p: _string(v, p, 1, 100, CODE_RE)),
            "match": _enum(tag.get("match", "any"), {"any", "all"}, path + "." + name + ".match"),
        }
    duplicates = raw.get("include_duplicates", False)
    if type(duplicates) is not bool:
        raise ValidationError("must be a boolean, not a string or integer", path + ".include_duplicates")
    keyword = _string(raw.get("keyword", ""), path + ".keyword", maximum=200)
    if len(keyword.split()) > 8:
        raise ValidationError("at most 8 literal keyword tokens are allowed", path + ".keyword")
    return {
        "date": {"from": date_from, "to": date_to,
                 "basis": _enum(dates.get("basis", "publication"), {"publication", "decision", "document_number_year", "decision_or_document_year"}, path + ".date.basis")},
        "subjects": {
            "names": _array(subjects.get("names", []), path + ".subjects.names", 50,
                lambda v, p: _string(v, p, 1, 200)),
            "match": _enum(subjects.get("match", "contains"), {"exact", "contains"}, path + ".subjects.match"),
            "party_types": _array(subjects.get("party_types", []), path + ".subjects.party_types", 3,
                lambda v, p: _enum(v, {"institution", "person", "unknown"}, p)),
            **({'compositions': _array(subjects['compositions'], path + '.subjects.compositions', 4,
                lambda v, p: _enum(v, {'institution', 'person', 'mixed', 'unknown'}, p))} if 'compositions' in subjects else {}),
        },
        "regions": {
            "ids": _array(regions.get("ids", []), path + ".regions.ids", 50,
                lambda v, p: _string(v, p, 1, 160)),
            "scope": _enum(regions.get("scope", "jurisdiction"), {"jurisdiction", "province", "city"}, path + ".regions.scope"),
            **({"basis": _enum(regions["basis"], {"authority_jurisdiction", "document_jurisdiction", "issuer_seat"}, path + ".regions.basis")} if "basis" in regions else {}),
        },
        **tags,
        **extra,
        "stages": _array(raw.get("stages", ["decided"]), path + ".stages", 4,
            lambda v, p: _enum(v, STAGES, p), minimum=1),
        "include_duplicates": duplicates,
        "keyword": keyword,
    }


def validate_page(value=None, path="page"):
    raw = _object({} if value is None else value, {"size", "cursor"}, path)
    cursor = raw.get("cursor")
    if cursor is not None:
        cursor = _string(cursor, path + ".cursor", maximum=3000)
    return {"size": _integer(raw.get("size", 20), path + ".size", 1, 100), "cursor": cursor}


def validate_statistics(value=None, path="statistics"):
    raw = _object({} if value is None else value, {"group_by", "top_n", "include_personal_sanctions"}, path)
    if 'include_personal_sanctions' in raw and type(raw['include_personal_sanctions']) is not bool:
        raise ValidationError('must be a boolean', path + '.include_personal_sanctions')
    return {
        "group_by": _array(raw.get("group_by", ["month", "authority_province", "business_type"]),
            path + ".group_by", 15, lambda v, p: _enum(v, DIMENSIONS, p), minimum=1),
        "top_n": _integer(raw.get("top_n", 10), path + ".top_n", 1, 50),
        **({'include_personal_sanctions': raw['include_personal_sanctions']} if 'include_personal_sanctions' in raw else {}),
    }


def _case_id(value, path):
    return _string(value, path, 1, 128, CASE_RE)


def _revision(raw, path="expected_data_revision"):
    value = raw.get("expected_data_revision")
    return None if value is None else _integer(value, path, 0)


def _case_set(value, path):
    raw = _object(value, {"set_id", "case_ids", "expected_data_revision", "include_duplicates"}, path)
    duplicates = raw.get("include_duplicates", False)
    if type(duplicates) is not bool:
        raise ValidationError("must be a boolean", path + ".include_duplicates")
    return {"set_id": _string(raw.get("set_id"), path + ".set_id", 1, 100, CASE_RE),
            "case_ids": _array(raw.get("case_ids"), path + ".case_ids", 1000, _case_id, minimum=1, unique=False),
            "expected_data_revision": _revision(raw, path + ".expected_data_revision"),
            "include_duplicates": duplicates}


def _batch_item(value, path):
    raw = _object(value, {"request_id", "case_id", "filters", "page", "statistics"}, path)
    request_id = _string(raw.get("request_id"), path + ".request_id", 1, 100, CASE_RE)
    case_id = raw.get("case_id")
    filters = raw.get("filters")
    if (case_id is None) == (filters is None):
        raise ValidationError("provide exactly one of case_id or filters", path)
    page = validate_page(raw.get("page"), path + ".page")
    statistics = None if raw.get("statistics") is None else validate_statistics(raw["statistics"], path + ".statistics")
    if case_id is not None:
        case_id = _case_id(case_id, path + ".case_id")
        if page["cursor"] or statistics is not None:
            raise ValidationError("single-case items do not accept cursor or statistics", path)
    return {"request_id": request_id, "case_id": case_id,
            "filters": None if case_id is not None else validate_filters(filters, path + ".filters"),
            "page": page, "statistics": statistics}


def validate_request(command: str, payload=None):
    """Return the command-specific public API JSON body, without mode/output.

    Accept either the public endpoint body or the native AnalysisRequest envelope.
    A supplied envelope mode must match the command; unrelated nonempty fields
    never silently disappear. Detail IDs retain input order and repetitions.
    """
    if command not in COMMANDS:
        raise ValidationError("unsupported command", "command")
    raw = copy.deepcopy({} if payload is None else payload)
    if command == "catalog":
        _object(raw, set(), "request")
        return None
    fields = {
        "query": {"filters", "page", "statistics", "expected_data_revision"},
        "stats": {"filters", "statistics", "expected_data_revision"},
        "detail": {"case_ids", "expected_data_revision"},
        "batch": {"requests", "expected_data_revision"},
        "explain": {"case_ids", "question", "expected_data_revision"},
        "set_stats": {"sets", "statistics", "expected_data_revision"},
        "document": {"doc_id", "page", "expected_data_revision"},
    }
    envelope = type(raw) is dict and ("mode" in raw or "output" in raw)
    allowed = ({"mode", "output", "filters", "case_ids", "page", "statistics",
                "requests", "sets", "doc_id", "expected_data_revision", "question"} if envelope else fields[command])
    _object(raw, allowed, "request")
    if envelope:
        mode = _enum(raw.get("mode", command), set(COMMANDS) - {"catalog"}, "mode")
        if mode != command:
            raise ValidationError("mode must match the selected command", "mode")
        _enum(raw.get("output", "json"), {"chat", "json"}, "output")
        page = validate_page(raw.get("page"))
        if command not in {"query", "document"} and page != {"size": 20, "cursor": None}:
            raise ValidationError("this mode does not accept pagination", "page")
        if command not in {"query", "stats"} and raw.get("filters") is not None:
            raise ValidationError("this mode does not accept filters", "filters")
        if command not in {"query", "stats", "set_stats"} and raw.get("statistics") is not None:
            raise ValidationError("this mode does not accept statistics", "statistics")
        if command not in {"detail", "explain"} and raw.get("case_ids", []) != []:
            raise ValidationError("this mode does not accept case_ids", "case_ids")
        if command != "batch" and raw.get("requests", []) != []:
            raise ValidationError("this mode does not accept requests", "requests")
        if command != "set_stats" and raw.get("sets", []) != []:
            raise ValidationError("this mode does not accept sets", "sets")
        if command != "document" and raw.get("doc_id") is not None:
            raise ValidationError("this mode does not accept doc_id", "doc_id")
        if command != "explain" and raw.get("question", "") != "":
            raise ValidationError("question is only valid for explain", "question")
        # Validate empty placeholders as well, rather than accepting null or wrong
        # container types and ignoring them just because they appear falsy.
        if "case_ids" in raw:
            _array(raw["case_ids"], "case_ids", 50, _case_id, unique=False)
        if "requests" in raw:
            _array(raw["requests"], "requests", 50, _batch_item, unique=False)
        if "sets" in raw:
            _array(raw["sets"], "sets", 10, _case_set, unique=False)
        if "question" in raw:
            _string(raw["question"], "question", maximum=1000)
    revision = _revision(raw)
    if command == "document":
        return {"doc_id": _integer(raw.get("doc_id"), "doc_id", 1),
                "page": validate_page(raw.get("page")), "expected_data_revision": revision}
    if command == "set_stats":
        sets = _array(raw.get("sets"), "sets", 10, _case_set, minimum=1, unique=False)
        if len({item["set_id"] for item in sets}) != len(sets):
            raise ValidationError("set_id values must be unique", "sets")
        if sum(len(item["case_ids"]) for item in sets) > 5000:
            raise ValidationError("at most 5000 raw ID members across all sets", "sets")
        return {"sets": sets, "statistics": validate_statistics(
                    {"group_by": ["stage"]} if raw.get("statistics") is None else raw["statistics"]),
                "expected_data_revision": revision}
    if command == "query":
        return {"filters": validate_filters(raw.get("filters")),
                "page": validate_page(raw.get("page")),
                "statistics": None if raw.get("statistics") is None else validate_statistics(raw["statistics"]),
                "expected_data_revision": revision}
    if command == "stats":
        return {"filters": validate_filters(raw.get("filters")),
                "statistics": validate_statistics(raw.get("statistics")),
                "expected_data_revision": revision}
    if command in {"detail", "explain"}:
        ids = _array(raw.get("case_ids"), "case_ids", 10 if command == "explain" else 50,
                     _case_id, minimum=1, unique=False)
        body = {"case_ids": ids, "expected_data_revision": revision}
        if command == "explain":
            question = raw.get("question", DEFAULT_QUESTION)
            if envelope and question == "":
                question = DEFAULT_QUESTION
            body["question"] = _string(question, "question", 1, 1000)
        return body
    requests = _array(raw.get("requests"), "requests", 50, _batch_item, minimum=1, unique=False)
    ids = [item["request_id"] for item in requests]
    if len(ids) != len(set(ids)):
        raise ValidationError("request_id values must be unique", "requests")
    return {"requests": requests, "expected_data_revision": revision}


def plan_request(command: str, payload=None):
    body = validate_request(command, payload)
    if command == "document":
        query = {"size": body["page"]["size"]}
        if body["page"]["cursor"] is not None:
            query["cursor"] = body["page"]["cursor"]
        if body["expected_data_revision"] is not None:
            query["expected_data_revision"] = body["expected_data_revision"]
        return {"method": "GET", "path": PREFIX + f'/documents/{body["doc_id"]}/cases',
                "query": query, "body": None}
    suffix = {"catalog": "/catalog", "query": "/cases/query", "detail": "/cases/batch-query",
              "batch": "/cases/batch-query", "stats": "/stats/query", "set_stats": "/stats/sets", "explain": "/explain"}[command]
    if command == "detail":
        body = {"requests": [
            {"request_id": f"detail-{index + 1}", "case_id": case_id}
            for index, case_id in enumerate(body["case_ids"])
        ], "expected_data_revision": body["expected_data_revision"]}
    return {"method": "GET" if command == "catalog" else "POST", "path": PREFIX + suffix, "body": body}
