"""Standalone stdlib client must use server ranges and explicit display counts."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import comparison
import delivery_view
import search
from result_check import CheckError
from workflow import run_workflow, WorkflowError


@pytest.mark.parametrize('environment,explicit,expected', [
    ({}, None, 'https://deepfin.vip'),
    ({'DEEPFIN_API_BASE_URL': ''}, None, 'https://deepfin.vip'),
    ({'DEEPFIN_API_BASE_URL': '  '}, None, 'https://deepfin.vip'),
    ({'DEEPFIN_API_BASE_URL': 'http://127.0.0.1:8002'}, None, 'http://127.0.0.1:8002'),
    ({'DEEPFIN_API_BASE_URL': 'https://example.invalid'}, 'http://127.0.0.1:8002', 'http://127.0.0.1:8002'),
])
def test_production_default_and_explicit_development_override(environment, explicit, expected):
    from penalty_client import Client
    calls = []

    class Response:
        status = 200
        def __init__(self): self.body = io.BytesIO(b'{}')
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, size): return self.body.read(size)

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return Response()

    client = Client(base_url=explicit, environ=environment)
    client._opener = Opener()
    client.execute('catalog')
    assert calls == [expected + '/api/v1/external/penalties/catalog']


@pytest.mark.parametrize('truncated,limit,expected_code', [
    (False, 1024, None), (True, 1024, 'NETWORK_ERROR'),
    (False, 16, 'RESPONSE_TOO_LARGE'),
])
def test_response_fragments_are_consumed_once_with_length_and_size_guards(monkeypatch, truncated, limit, expected_code):
    import penalty_client
    payload = b'{"scope":{"api_contract_version":"1.2.0","data_revision":12}}'
    calls = []

    class Response:
        status = 200
        headers = {'Content-Length': str(len(payload))}
        def __init__(self): self.body = io.BytesIO(payload[:-4] if truncated else payload)
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, size): return self.body.read(min(size, 7))

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return Response()

    monkeypatch.setattr(penalty_client, 'MAX_RESPONSE_BYTES', limit)
    client = penalty_client.Client(environ={})
    client._opener = Opener()
    if expected_code:
        with pytest.raises(penalty_client.ClientError) as error:
            client.execute('catalog')
        assert error.value.code == expected_code
    else:
        assert client.execute('catalog') == json.loads(payload)
    assert len(calls) == 1


@pytest.mark.parametrize('wrapper,code,status', [
    ('error', 'FINE_DATA_NOT_READY', 503), ('detail', 'FINE_DATA_NOT_READY', 503),
    ('error', 'DERIVED_DATA_NOT_READY', 503), ('error', 'INSTITUTION_CLASSIFICATION_NOT_READY', 503),
    ('detail', 'DATA_REVISION_CHANGED', 409),
])
def test_http_readiness_codes_are_preserved_without_remote_text_or_retries(wrapper, code, status):
    from urllib.error import HTTPError
    from penalty_client import Client, ClientError
    client = Client(base_url='https://example.invalid', environ={'DEEPFIN_PENALTIES_API_KEY': 'synthetic-secret'})
    calls = []
    class FailingOpener:
        def open(self, request, timeout):
            calls.append(request)
            body = json.dumps({wrapper: {'code': code, 'message': 'sensitive synthetic-secret database details'}}).encode()
            raise HTTPError(request.full_url, status, 'unavailable', {'Retry-After': '30'}, io.BytesIO(body))
    client._opener = FailingOpener()
    with pytest.raises(ClientError) as error:
        client.execute('stats', {})
    assert error.value.code == code and error.value.http_status == status
    assert error.value.retry_after_seconds == (30 if status == 503 else None)
    assert len(calls) == 1
    assert 'sensitive' not in json.dumps(error.value.as_dict())
    assert 'synthetic-secret' not in json.dumps(error.value.as_dict())


def response(year=2026, count=8):
    proof = {'case_id': f'{year}-0', 'party': '测试机构', 'penalty': '罚款3万元', 'source_url': 'https://www.nfra.gov.cn/test'}
    fine = {'statistic': 'extracted_fine_range', 'aggregation_enabled': False,
        'min_yuan': '30000.00', 'max_yuan': '30000.00', 'included_records': count,
        'excluded_records': 0, 'denominator': count, 'zero_amount_records': 0, 'is_complete_total': False,
        'minimum': {'yuan': '30000.00', 'matched_records': count, 'returned_records': 1, 'truncated': count > 1, 'records': [proof]},
        'maximum': {'yuan': '30000.00', 'matched_records': count, 'returned_records': 1, 'truncated': count > 1, 'records': [proof]}}
    return {'scope': {'data_revision': 11}, 'completion_status': 'complete', 'warnings': [],
        'applied_filters': {'filter_signature': f'test-{year}'},
        'pagination': {'total': count, 'returned': count, 'has_more': False, 'total_exact': True},
        'statistics': {'total_records': count, 'source_documents': count, 'single_explicit_fines': fine, 'groups': {}},
        'results': [{**proof, 'case_id': f'{year}-{i}', 'doc_id': year * 100 + i, 'fine_yuan': '30000.00',
            'fine_status': 'single_explicit', 'subject_composition': 'institution'} for i in range(count)]}


class FakeClient:
    def __init__(self, values):
        self.values = copy.deepcopy(values)
        self.calls = []
    def execute(self, mode, payload=None, dry_run=False):
        if dry_run:
            from contract import plan_request
            return plan_request(mode, payload)
        self.calls.append((mode, copy.deepcopy(payload)))
        return self.values.pop(0)


@pytest.mark.parametrize('args,shown,style', [(['--all'], 8, 'full'), (['--all','--view','brief'], 5, 'brief'),
    (['--all','--view','brief','--case-limit','7'], 7, 'brief'), (['--view','full'], 8, 'full')])
def test_search_display_flags_make_fetched_and_displayed_counts_explicit(monkeypatch, args, shown, style):
    client = FakeClient([response()])
    monkeypatch.setattr(search, 'Client', lambda **kw: client)
    with contextlib.redirect_stdout(io.StringIO()) as output:
        assert search.main(args) == 0
    result = json.loads(output.getvalue())
    assert result['view'] == style and len(result['cases']) == shown
    assert result['case_display']['shown'] == shown and result['case_display']['fetched'] == 8
    assert result['coverage']['api_total'] == 8
    assert 'amount_ranking' not in result
    assert result['fine_amount_range'] == response()['statistics']['single_explicit_fines']
    assert len(client.calls) == 1


@pytest.mark.parametrize('field,value', [('yuan','240000'), ('sum_yuan','240000'), ('mean_yuan','30000'), ('median_yuan','30000')])
def test_old_backend_totals_fail_closed_instead_of_leaking_into_views(field, value):
    node = response()
    node['statistics']['single_explicit_fines'][field] = value
    with pytest.raises(CheckError, match='契约'):
        run_workflow({}, client=FakeClient([node]))


def test_amounts_missing_are_unknown_and_real_zero_is_valid():
    node = response()
    fine = node['statistics']['single_explicit_fines']
    fine.update(included_records=0, min_yuan=None, max_yuan=None, minimum=None, maximum=None)
    delivery_view.validate_range_contract(node)
    fine['min_yuan'] = '0.00'
    with pytest.raises(ValueError):
        delivery_view.validate_range_contract(node)
    fine.update(included_records=1, min_yuan='0.00', max_yuan='0.00', minimum={'records': [{'penalty': '罚款0元'}]}, maximum={'records': [{'penalty': '罚款0元'}]})
    delivery_view.validate_range_contract(node)


def test_summary_uses_only_two_stats_calls_no_truncation_and_no_subjective_ranking(tmp_path):
    request = {'periods': [{'label': str(y), 'request': {'filters': {'date': {'from': f'{y}-01-01', 'to': f'{y}-06-30'}},
        'statistics': {'group_by': ['subject_composition']}}} for y in (2025, 2026)]}
    responses = [response(y) for y in (2025, 2026)]
    for r in responses:
        r.update(results=[], pagination=None)
    client = FakeClient(responses)
    result = comparison.run_comparison(request, summary_only=True, client=client)
    assert [mode for mode, _ in client.calls] == ['stats','stats']
    assert client.calls[1][1]['expected_data_revision'] == 11
    assert result.view('brief') == result.view('full')
    assert result['periods'][0]['statistics'] == responses[0]['statistics']
    assert result['output_policy']['mode'] == 'facts_only'
    output = tmp_path/'range.xlsx'
    result.export(output)
    with zipfile.ZipFile(output) as book:
        assert book.testzip() is None
        data = ''.join(book.read(name).decode() for name in book.namelist() if name.endswith('.xml'))
        assert '最小金额（元）' in data and '最大金额（元）' in data
        assert '罚款3万元' in data and 'min_yuan' in data and 'max_yuan' in data
        assert not any(k in data for k in ('sum_yuan', 'mean_yuan', 'median_yuan', 'amount_ranking'))
        assert '已识别单一罚款合计' not in data


def test_summary_rejects_revision_changes():
    request = {'periods': [{'label': str(y), 'request': {'filters': {'date': {'from': f'{y}-01-01', 'to': f'{y}-06-30'}}}} for y in (2025,2026)]}
    values = [response(y) for y in (2025,2026)]
    values[1]['scope']['data_revision'] = 12
    with pytest.raises(WorkflowError, match='修订'):
        comparison.run_comparison(request, summary_only=True, client=FakeClient(values))


def test_long_range_evidence_is_split_without_silent_truncation(tmp_path):
    from export_xlsx import export_workbook
    node = response()
    node.update(results=[], pagination=None)
    original = '长原文证据' * 10000
    for key in ('minimum', 'maximum'):
        node['statistics']['single_explicit_fines'][key]['records'][0]['penalty'] = original
    output = tmp_path / 'long-evidence.xlsx'
    export_workbook({'responses': [node]}, output)
    with zipfile.ZipFile(output) as book:
        from xml.etree import ElementTree as ET
        cells = [n.text or '' for name in book.namelist() if name.startswith('xl/worksheets/sheet') and name.endswith('.xml')
                 for n in ET.fromstring(book.read(name)).iter('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t')]
        assert all(len(t.encode('utf-16-le')) // 2 <= 32767 for t in cells)
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rows = ET.fromstring(book.read('xl/worksheets/sheet3.xml')).findall('m:sheetData/m:row', ns)
        chunks = []
        for row in rows:
            values = [''.join(c.itertext()) for c in row.findall('m:c', ns)]
            if len(values) == 4 and values[1] == 'statistics':
                chunks.append((int(values[2]), values[3]))
        stats = json.loads(''.join(value for _, value in sorted(chunks)))
        assert stats['single_explicit_fines']['minimum']['records'][0]['penalty'] == original
        assert stats['single_explicit_fines']['maximum']['records'][0]['penalty'] == original
