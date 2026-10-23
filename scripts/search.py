"""Concise CLI over the same validated in-memory workflow, stdlib only."""
import argparse
import json
import re
import sys
from contract import ValidationError
from penalty_client import Client, ClientError
from runtime_check import require_environment, EnvironmentError, error_payload
from workflow import run_workflow, WorkflowError
from result_check import CheckError


def request_from(args, client):
    filters = {}
    if args.date_from or args.date_to:
        filters['date'] = {'from': args.date_from, 'to': args.date_to, 'basis': args.date_basis}
    if args.name or args.subject:
        filters['subjects'] = {'names': args.name or [], 'match': 'exact' if args.exact else 'contains'}
        if args.subject:
            filters['subjects']['compositions'] = [args.subject]
    if args.employer:
        filters['affiliations'] = {'institutions': args.employer, 'include_mixed': args.include_mixed}
    elif args.include_mixed:
        raise WorkflowError('INVALID_FILTER', '--include-mixed须与--employer一起使用；默认另列混合公示。')
    if args.region or args.province:
        code = args.region
        if args.province:
            if args.dry_run:
                raise WorkflowError('REGION_CODE_REQUIRED', '离线预演请用--region目录代码；--province需先在线解析目录。')
            catalog = client.execute('catalog')
            normalize = lambda s: re.sub(r'(省|市|特别行政区|壮族自治区|回族自治区|维吾尔自治区|自治区)$', '', s)
            ids = {r['id'] for r in catalog.get('regions', []) if r.get('id', '').startswith('province:') and normalize(r.get('name', '')) == normalize(args.province)}
            if len(ids) != 1:
                raise WorkflowError('INVALID_PROVINCE', '目录无法唯一匹配该省名，请用--region传目录代码。')
            code = ids.pop()
        filters['regions'] = {'ids': [code], 'scope': 'province' if code.startswith('province:') else 'jurisdiction'}
    if args.business:
        filters['business'] = {'codes': args.business}
    if args.institution_type:
        filters['institution_types'] = {'codes': args.institution_type}
    if args.keyword:
        filters['keyword'] = args.keyword
    request = {'filters': filters, 'page': {'size': args.limit}}
    if args.personal_stats:
        request['statistics'] = {'group_by': ['subject_composition'], 'include_personal_sanctions': True}
    return request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from', dest='date_from')
    parser.add_argument('--to', dest='date_to')
    parser.add_argument('--date-basis', default='publication', choices=['publication', 'decision', 'document_number_year', 'decision_or_document_year'])
    parser.add_argument('--name', action='append')
    parser.add_argument('--exact', action='store_true')
    parser.add_argument('--subject', choices=['person', 'institution', 'mixed', 'unknown'])
    parser.add_argument('--employer', action='append')
    parser.add_argument('--include-mixed', action='store_true')
    region = parser.add_mutually_exclusive_group()
    region.add_argument('--province')
    region.add_argument('--region')
    parser.add_argument('--business', action='append')
    parser.add_argument('--institution-type', action='append')
    parser.add_argument('--keyword')
    parser.add_argument('--personal-stats', action='store_true')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--view', choices=['brief', 'full'])
    parser.add_argument('--case-limit', type=int, default=5)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--base-url')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.case_limit <= 5000:
            raise WorkflowError('INVALID_REQUEST', '--case-limit须为1至5000。')
        require_environment('workflow')
        if args.output:
            require_environment('export', args.output)
        client = Client(base_url=args.base_url)
        request = request_from(args, client)
        if args.all or args.output:
            request['page']['size'] = 100
        result = run_workflow(request, output=args.output, all_pages=args.all, dry_run=args.dry_run, client=client)
        style = args.view or ('full' if args.all and not args.output else 'brief')
        print(json.dumps(result if args.dry_run else result.view(style, args.case_limit), ensure_ascii=False, allow_nan=False))
        return 0
    except EnvironmentError as exc:
        error = error_payload(exc.report)
    except ClientError as exc:
        error = exc.as_dict()
    except (ValidationError, WorkflowError, CheckError) as exc:
        error = {'error': {'code': getattr(exc, 'code', 'INVALID_REQUEST'), 'message': str(exc)}}
    print(json.dumps(error, ensure_ascii=False), file=sys.stderr)
    return 2
