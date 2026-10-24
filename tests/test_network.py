"""Bounded, offline transport tests; no persistent proxy or production calls."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import select
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from penalty_client import Client, ClientError


@pytest.fixture(autouse=True)
def clean_network_environment(monkeypatch):
    for key in list(os.environ):
        if key.lower().endswith('_proxy') or key in {
            'REQUEST_METHOD', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
            'DEEPFIN_API_BASE_URL', 'DEEPFIN_PENALTIES_API_KEY', 'DEEPFIN_PENALTIES_ASK_API_KEY'}:
            monkeypatch.delenv(key, raising=False)


class QuietServer(ThreadingHTTPServer):
    daemon_threads = True
    def handle_error(self, *args):
        pass


@contextmanager
def endpoint(*, certificate=None, connect_port=None, status=200, headers=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            calls.append({'method': 'CONNECT', 'path': self.path, 'headers': dict(self.headers)})
            if self.path != f'localhost:{connect_port}':
                self.send_error(502)
                return
            with socket.create_connection(('127.0.0.1', connect_port), timeout=5) as upstream:
                self.send_response(200, 'Connection established')
                self.end_headers()
                streams = [self.connection, upstream]
                while True:
                    ready, _, _ = select.select(streams, [], [], 5)
                    if not ready:
                        break
                    for source in ready:
                        part = source.recv(65536)
                        if not part:
                            self.close_connection = True
                            return
                        (upstream if source is self.connection else self.connection).sendall(part)

        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            calls.append({'method': self.command, 'path': self.path, 'body': body,
                          'headers': dict(self.headers)})
            data = b'{"scope":{"api_contract_version":"1.2.0","data_revision":12}}'
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = respond
        do_POST = respond

    server = QuietServer(('127.0.0.1', 0), Handler)
    if certificate:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certificate[0]), str(certificate[1]))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    scheme = 'https' if certificate else 'http'
    host = 'localhost' if certificate else '127.0.0.1'
    try:
        yield f'{scheme}://{host}:{server.server_port}', calls, server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive(), 'Test listener was not cleaned up'


@pytest.fixture(scope='session')
def certificate(tmp_path_factory):
    directory = tmp_path_factory.mktemp('transport-certificate')
    cert, key = directory/'certificate.pem', directory/'private-key.pem'
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('HTTPS integration tests need the maintainer OpenSSL executable')
    result = subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-days', '1', '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost',
        '-addext', 'subjectAltName=DNS:localhost'], capture_output=True, timeout=20)
    assert result.returncode == 0, 'Unable to create temporary test certificate'
    return cert, key


def test_default_direct_connection():
    with endpoint() as (origin, calls, _):
        assert Client(base_url=origin).execute('catalog')['scope']['data_revision'] == 12
    assert len(calls) == 1 and calls[0]['path'] == '/api/v1/external/penalties/catalog'


@pytest.mark.parametrize('name', ['HTTP_PROXY', 'http_proxy'])
def test_existing_http_proxy_is_used(name, monkeypatch):
    with endpoint() as (proxy, calls, _):
        monkeypatch.setenv(name, proxy)
        result = Client(base_url='http://127.0.0.1:9').execute('catalog')
    assert result['scope']['data_revision'] == 12
    assert len(calls) == 1
    assert calls[0]['path'] == 'http://127.0.0.1:9/api/v1/external/penalties/catalog'


@pytest.mark.parametrize('name', ['HTTPS_PROXY', 'https_proxy'])
def test_https_proxy_connect_keeps_tls_and_api_key_inside_tunnel(name, monkeypatch, certificate):
    monkeypatch.setenv('SSL_CERT_FILE', str(certificate[0]))
    with endpoint(certificate=certificate) as (origin, destination_calls, port):
        with endpoint(connect_port=port) as (proxy, proxy_calls, _):
            monkeypatch.setenv(name, proxy)
            result = Client(base_url=origin, environ={'DEEPFIN_PENALTIES_API_KEY': 'synthetic-read-key'}).execute('catalog')
    assert result['scope']['api_contract_version'] == '1.2.0'
    assert len(proxy_calls) == 1 and proxy_calls[0]['method'] == 'CONNECT'
    assert 'x-api-key' not in {key.lower() for key in proxy_calls[0]['headers']}
    assert len(destination_calls) == 1
    assert destination_calls[0]['headers']['X-Api-Key'] == 'synthetic-read-key'


def test_os_proxy_discovery_path_is_respected(monkeypatch, certificate):
    monkeypatch.setenv('SSL_CERT_FILE', str(certificate[0]))
    with endpoint(certificate=certificate) as (origin, calls, port):
        with endpoint(connect_port=port) as (proxy, proxy_calls, _):
            monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'https': proxy})
            assert Client(base_url=origin).execute('catalog')['scope']['data_revision'] == 12
    assert len(proxy_calls) == 1 and len(calls) == 1


def test_no_proxy_bypasses_configured_proxy(monkeypatch):
    with endpoint() as (origin, destination_calls, _):
        with endpoint() as (proxy, proxy_calls, _):
            monkeypatch.setenv('HTTP_PROXY', proxy)
            monkeypatch.setenv('NO_PROXY', '127.0.0.1')
            Client(base_url=origin).execute('catalog')
    assert len(destination_calls) == 1 and not proxy_calls


def test_proxy_auth_is_not_forwarded_to_https_origin(monkeypatch, certificate):
    monkeypatch.setenv('SSL_CERT_FILE', str(certificate[0]))
    with endpoint(certificate=certificate) as (origin, origin_calls, port):
        with endpoint(connect_port=port) as (proxy, proxy_calls, proxy_port):
            monkeypatch.setenv('HTTPS_PROXY', f'http://test-user:test-pass@127.0.0.1:{proxy_port}')
            Client(base_url=origin).execute('catalog')
    assert 'Proxy-Authorization' in proxy_calls[0]['headers']
    assert 'Proxy-Authorization' not in origin_calls[0]['headers']


def test_unknown_certificate_is_rejected_through_proxy_without_retry(monkeypatch, certificate):
    with endpoint(certificate=certificate) as (origin, origin_calls, port):
        with endpoint(connect_port=port) as (proxy, proxy_calls, _):
            monkeypatch.setenv('HTTPS_PROXY', proxy)
            with pytest.raises(ClientError) as failure:
                Client(base_url=origin, timeout=5).execute('catalog')
    assert failure.value.code == 'NETWORK_ERROR'
    assert len(proxy_calls) == 1 and not origin_calls


def test_failed_proxy_does_not_fallback_to_direct_or_retry(monkeypatch):
    with endpoint() as (origin, origin_calls, _):
        with endpoint(status=502) as (proxy, proxy_calls, _):
            monkeypatch.setenv('HTTP_PROXY', proxy)
            with pytest.raises(ClientError) as failure:
                Client(base_url=origin).execute('catalog')
    assert failure.value.http_status == 502
    assert len(proxy_calls) == 1 and not origin_calls


def test_redirect_through_proxy_is_still_blocked(monkeypatch):
    with endpoint() as (origin, origin_calls, _):
        with endpoint(status=302, headers={'Location': origin+'/redirected'}) as (proxy, proxy_calls, _):
            monkeypatch.setenv('HTTP_PROXY', proxy)
            with pytest.raises(ClientError) as failure:
                Client(base_url=origin).execute('catalog')
    assert failure.value.code == 'REDIRECT_BLOCKED'
    assert len(proxy_calls) == 1 and not origin_calls


def test_post_body_and_destination_are_unchanged(monkeypatch):
    from contract import plan_request
    request = {'page': {'size': 2}, 'statistics': None}
    with endpoint() as (proxy, calls, _):
        monkeypatch.setenv('HTTP_PROXY', proxy)
        Client(base_url='http://127.0.0.1:9').execute('query', request)
    assert len(calls) == 1 and calls[0]['method'] == 'POST'
    assert json.loads(calls[0]['body']) == plan_request('query', request)['body']
    assert calls[0]['path'] == 'http://127.0.0.1:9/api/v1/external/penalties/cases/query'


def test_dry_run_never_discovers_proxy_or_opens_network(monkeypatch):
    def forbidden():
        raise AssertionError('Proxy configuration must not be read during dry-run')
    monkeypatch.setattr(urllib.request, 'getproxies', forbidden)
    assert Client(environ={}).execute('query', {}, dry_run=True)['method'] == 'POST'


def test_python_entrypoint_works_without_cmd_wrapper(tmp_path):
    installed = tmp_path/'installed'
    shutil.copytree(ROOT/'scripts', installed/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    assert not (installed/'run.cmd').exists()
    for arguments in (['check'], ['query', '--input', '-', '--dry-run']):
        result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8',
            str(installed/'scripts/run.py'), *arguments], input='{}', text=True,
            capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert isinstance(json.loads(result.stdout), dict)
