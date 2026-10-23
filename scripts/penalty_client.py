#!/usr/bin/env python3
"""Read-only standalone client for DeepFin's scoped public penalty APIs.

Example: python penalty_client.py query --input query.json --dry-run
Configuration: --base-url, DEEPFIN_API_BASE_URL, or default https://deepfin.vip;
optional DEEPFIN_PENALTIES_API_KEY (read),
DEEPFIN_PENALTIES_ASK_API_KEY (explain only). No administrator JWT support.
"""
from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import math
import os
import socket
from http.client import HTTPException
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, urljoin, unquote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

if __name__ == "__main__":
    # Running the installed CLI must not populate its scripts/ directory.
    sys.dont_write_bytecode = True

try:
    from .contract import PREFIX, COMMANDS, ValidationError, plan_request, validate_request
except ImportError:
    sibling_dir = str(Path(__file__).resolve().parent)
    if sibling_dir not in sys.path:
        sys.path.insert(0, sibling_dir)
    from contract import PREFIX, COMMANDS, ValidationError, plan_request, validate_request

MAX_INPUT_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
DEFAULT_BASE_URL = "https://deepfin.vip"


def _read_response(response):
    """Consume one bounded response even when individual reads return short."""
    body = bytearray()
    while len(body) <= MAX_RESPONSE_BYTES:
        part = response.read(min(64 * 1024, MAX_RESPONSE_BYTES + 1 - len(body)))
        if not part:
            break
        body.extend(part)
    return bytes(body)


class ClientError(Exception):
    """Safe errors intentionally omit URLs, keys and remote response contents."""
    def __init__(self, code, message, *, http_status=None, retry_after_seconds=None, exit_code=4):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.exit_code = exit_code
        self.account_links = {}
        super().__init__(message)

    def as_dict(self):
        error = {"code": self.code, "message": self.message}
        if self.http_status is not None:
            error["http_status"] = self.http_status
        if self.retry_after_seconds is not None:
            error["retry_after_seconds"] = self.retry_after_seconds
        error.update(self.account_links)
        return {"error": error}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value):
    if type(value) is not str or not value.strip():
        raise ClientError("MISSING_BASE_URL", "Set DEEPFIN_API_BASE_URL to the service root or public penalties prefix.", exit_code=3)
    value = value.strip()
    try:
        parts = urlsplit(value)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        raise ClientError("INVALID_BASE_URL", "API base URL is invalid.", exit_code=3) from None
    if (parts.scheme not in {"http", "https"} or not hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)
            or (port is not None and not 1 <= port <= 65535)):
        raise ClientError("INVALID_BASE_URL", "Use an HTTP(S) service root or public penalties prefix without credentials, query or fragment.", exit_code=3)
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    if parts.scheme == "http" and not loopback:
        raise ClientError("INSECURE_BASE_URL", "Remote API services require HTTPS; plain HTTP is allowed only for loopback development.", exit_code=3)
    path = parts.path.rstrip("/")
    if path not in {"", PREFIX}:
        raise ClientError("INVALID_BASE_PATH", "API base path must be empty or /api/v1/external/penalties.", exit_code=3)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _retry_after(value):
    if not value:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit() and len(value) <= 12:
        return int(value)
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, math.ceil((parsed - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _http_error(status, headers):
    names = {400: ("BAD_REQUEST", "The API rejected the request."),
             401: ("UNAUTHORIZED", "The API key is invalid or expired."),
             403: ("FORBIDDEN", "The API key lacks the required penalties scope."),
             404: ("NOT_FOUND", "The requested published resource was not found."),
             409: ("DATA_REVISION_CHANGED", "The data revision changed; refresh scope and start a new query."),
             422: ("VALIDATION_FAILED", "The API rejected a filter, catalog code or request field."),
             429: ("RATE_LIMITED", "The API is rate limited. Do not retry before retry_after_seconds; no automatic retry was made."),
             503: ("SERVICE_UNAVAILABLE", "The analysis service is unavailable; try later.")}
    if 300 <= status < 400:
        code, message = "REDIRECT_BLOCKED", "API redirects are blocked to prevent forwarding credentials."
    else:
        code, message = names.get(status, ("HTTP_ERROR", "The API request failed."))
    return ClientError(code, message, http_status=status,
        retry_after_seconds=_retry_after(headers.get("Retry-After")) if status in {429, 503} else None)


def _account_error(error, body, base, key):
    """Retain only authentication codes and safe user-facing account links."""
    if error.http_status not in {401, 403}:
        return error
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            return error
        fields = data.get('error') or data.get('detail')
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
        return error
    if not isinstance(fields, dict):
        return error
    codes = {
        'AUTH_REQUIRED': '接口现已要求鉴权，请打开登录或注册链接，注册/登录后配置对应权限的 API Key。',
        'API_KEY_INVALID': 'API Key 无效或已过期，请登录检查。',
        'ACCOUNT_DISABLED': '账号不可用，请登录检查或联系管理员。',
        'INSUFFICIENT_SCOPE': 'API Key 缺少此接口所需权限，请登录检查。',
    }
    if fields.get('code') not in codes:
        return error
    error.code = fields['code']
    error.message = codes[error.code]
    error.args = (error.message,)
    for field in ('login_url', 'register_url'):
        value = fields.get(field)
        if not isinstance(value, str) or not value or len(value) > 2048:
            continue
        if any(char.isspace() or ord(char) < 32 for char in value) or '\\' in value:
            continue
        if key and (key in value or key in unquote(value)):
            continue
        try:
            # Relative account links use this configured service's origin.
            if value.startswith('/') and not value.startswith('//'):
                value = urljoin(base + '/', value)
            parts = urlsplit(value)
            if (parts.scheme not in {'http', 'https'} or not parts.hostname or
                    parts.username is not None or parts.password is not None or parts.query or parts.fragment):
                continue
            if parts.scheme == 'http':
                try:
                    loopback = ipaddress.ip_address(parts.hostname).is_loopback
                except ValueError:
                    loopback = parts.hostname.lower() == 'localhost'
                if not loopback:
                    continue
            parts.port  # reject malformed port numbers
        except ValueError:
            continue
        error.account_links[field] = value
    return error


def _availability_error(error, body):
    """Keep reviewed query codes with local messages, never remote error text."""
    try:
        data = json.loads(body)
        fields = (data.get('error') or data.get('detail')) if isinstance(data, dict) else None
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
        return error
    if not isinstance(fields, dict):
        return error
    codes = {
        'FINE_DATA_NOT_READY': (503, '金额统计所需的主体识别数据尚未就绪；统计未知，案例原文仍可查询。请稍后重试统计。'),
        'DERIVED_DATA_NOT_READY': (503, '分类或个人处罚派生数据尚未就绪，未生成完整统计；请稍后重试。'),
        'INSTITUTION_CLASSIFICATION_NOT_READY': (503, '机构类别派生数据尚未就绪，未生成完整分类结果；请稍后重试。'),
        'DATA_REVISION_CHANGED': (409, '数据修订已变化，请重新开始查询，不能混用原结果和统计。'),
    }
    entry = codes.get(fields.get('code'))
    if entry and error.http_status == entry[0]:
        error.code, error.message = fields['code'], entry[1]
        error.args = (error.message,)
    return error


class Client:
    def __init__(self, base_url=None, timeout=60, environ=None):
        try:
            from .runtime_check import require_environment, EnvironmentError
        except ImportError:
            from runtime_check import require_environment, EnvironmentError
        try:
            require_environment("query")
        except EnvironmentError as error:
            raise ClientError("ENVIRONMENT_NOT_READY", " ".join(issue["message"] + " " + issue["hint"] for issue in error.report["issues"]), exit_code=3) from None
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 600:
            raise ClientError("INVALID_TIMEOUT", "Timeout must be a positive number at most 600 seconds.", exit_code=3)
        self.base_url = base_url
        self.timeout = timeout
        self.environ = os.environ if environ is None else environ
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def execute(self, command, payload=None, dry_run=False):
        try:
            plan = plan_request(command, payload)
        except ValidationError:
            raise
        if dry_run:
            # Deliberately before all environment/config/credential reads.
            return plan
        configured_base = self.base_url
        if configured_base is None:
            configured_base = self.environ.get("DEEPFIN_API_BASE_URL")
            if configured_base is None or (isinstance(configured_base, str) and not configured_base.strip()):
                configured_base = DEFAULT_BASE_URL
        base = _base_url(configured_base)
        key_name = "DEEPFIN_PENALTIES_ASK_API_KEY" if command == "explain" else "DEEPFIN_PENALTIES_API_KEY"
        key = self.environ.get(key_name)
        if key is not None and type(key) is not str:
            raise ClientError("INVALID_API_KEY", "The configured API key has an invalid format.", exit_code=3)
        key = key.strip() if key else ''
        if key and (len(key) > 8192 or any(ord(char) < 33 or ord(char) > 126 for char in key)):
            raise ClientError("INVALID_API_KEY", "The configured API key has an invalid format.", exit_code=3)
        data = None if plan["body"] is None else json.dumps(plan["body"], ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if key:
            headers['x-api-key'] = key
        query = urlencode(plan.get("query", {}))
        request = Request(base + plan["path"] + ("?" + query if query else ""), data=data, method=plan["method"],
                          headers=headers)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                if not 200 <= response.status < 300:
                    raise _http_error(response.status, response.headers)
                raw = _read_response(response)
                length = getattr(response, 'headers', {}).get('Content-Length')
                expected_length = int(length) if isinstance(length, str) and length.isascii() and length.isdigit() and len(length) <= 12 else None
        except HTTPError as error:
            safe = _http_error(error.code, error.headers)
            try:
                if error.code in {401, 403, 409, 503}:
                    body = error.read(16385)
                    if len(body) <= 16384:
                        safe = _account_error(safe, body, base, key)
                        safe = _availability_error(safe, body)
            except (OSError, HTTPException):
                pass
            finally:
                error.close()
            raise safe from None
        except (TimeoutError, socket.timeout):
            raise ClientError("TIMEOUT", "The API request timed out; no automatic retry was made.", exit_code=5) from None
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise ClientError("TIMEOUT", "The API request timed out; no automatic retry was made.", exit_code=5) from None
            raise ClientError("NETWORK_ERROR", "The API service could not be reached; no automatic retry was made.", exit_code=5) from None
        except (OSError, ValueError, HTTPException):
            raise ClientError("NETWORK_ERROR", "The API connection failed; no automatic retry was made.", exit_code=5) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ClientError("RESPONSE_TOO_LARGE", "The API response exceeded the client limit.", exit_code=5)
        if expected_length is not None and len(raw) != expected_length:
            raise ClientError("NETWORK_ERROR", "The API response was incomplete; no automatic retry was made.", exit_code=5)
        try:
            result = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError, ValidationError):
            raise ClientError("INVALID_RESPONSE", "The API returned invalid JSON.", exit_code=5) from None
        if type(result) is not dict:
            raise ClientError("INVALID_RESPONSE", "The API response must be a JSON object.", exit_code=5)
        from delivery_view import validate_range_contract
        try:
            validate_range_contract(result)
        except ValueError as exc:
            raise ClientError('BACKEND_CONTRACT_OUTDATED', str(exc), exit_code=5) from None
        if command == "detail":
            original_ids = validate_request(command, payload)["case_ids"]
            result = copy.deepcopy(result)
            result["requested_case_ids"] = original_ids
            mapping = {f"detail-{index + 1}": case_id for index, case_id in enumerate(original_ids)}
            for item in result.get("results", []):
                if type(item) is dict:
                    item["requested_case_id"] = mapping.get(item.get("request_id"))
        return result


def _reject_constant(value):
    raise ValidationError("JSON NaN and Infinity are not supported")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate JSON fields are not supported")
        result[key] = value
    return result


def _read_input(path):
    try:
        if path == "-":
            content = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(path).open("rb") as stream:
                content = stream.read(MAX_INPUT_BYTES + 1)
    except OSError:
        raise ClientError("INPUT_UNREADABLE", "Cannot read the input JSON file.", exit_code=2) from None
    if len(content) > MAX_INPUT_BYTES:
        raise ClientError("INPUT_TOO_LARGE", "Input JSON must not exceed 1 MiB.", exit_code=2)
    try:
        return json.loads(content.decode("utf-8-sig"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except ValidationError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError, ValidationError):
        raise ValidationError("input must be a valid UTF-8 JSON object") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--base-url", help=f"Explicit service root or penalties prefix; overrides DEEPFIN_API_BASE_URL. Default when unset: {DEFAULT_BASE_URL}. Never put an API key in this URL.")
    parser.add_argument("--input", metavar="FILE|-", help="Read a JSON object from a file or stdin (-).")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show method/path/body only; never read an API key or send a request.")
    parser.add_argument("--timeout", type=float, default=60, help="Request timeout in seconds, maximum 600 (default: 60).")
    args = parser.parse_args(argv)
    try:
        client = Client(base_url=args.base_url, timeout=args.timeout)
        if args.input is None and args.command not in {"query", "catalog"}:
            raise ValidationError("this command requires --input FILE or --input -")
        payload = {} if args.input is None else _read_input(args.input)
        result = client.execute(args.command, payload, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ValidationError as error:
        print(json.dumps({"error": {"code": "INVALID_REQUEST", "message": error.message, "path": error.path}},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    except ClientError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
