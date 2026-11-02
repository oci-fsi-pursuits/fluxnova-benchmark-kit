"""Interactive functional lab: real engine execution, bounded load, owned resources."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

from .lab_model import parse_model, parameters, variables
from .lab_runtime import LocalRuntime
from .util import ROOT, read_json, write_json, utc_now

ASSETS = Path(__file__).with_name('lab_assets')
EXAMPLES = ROOT / 'providers/local/workflows'
SCOPE = 'Interactive functional exploration; not a controlled performance benchmark or payment verification.'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Engine redirects are disabled; configure the final REST endpoint.')


class Engine:
    """Fixed endpoint, no proxy environment, TLS verification and no credential redirects."""
    def __init__(self, url, username=None, password=None, ca_file=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Configure a clean HTTP(S) engine REST URL without credentials, query or fragment.')
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if parsed.scheme == 'http' and not loopback:
            raise ValueError('Remote engines require HTTPS. Forward private HTTP engines to a loopback IP with SSH/Bastion.')
        self.url = url.rstrip('/')
        self.auth = None
        if username and password:
            self.auth = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()
        elif username or password:
            raise ValueError('Set both engine username and password environment variables.')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def request(self, method, path, body=None, content_type='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, allow_nan=False).encode()
        headers = {'Accept': 'application/json'}
        if self.auth:
            headers['Authorization'] = self.auth
        if body is not None:
            headers['Content-Type'] = content_type
        try:
            with self.opener.open(urllib.request.Request(self.url + path, data=body, headers=headers, method=method), timeout=10) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'Engine returned HTTP {error.code}; check endpoint, permissions and history settings.') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError('Engine connection failed; check the tunnel or configured endpoint.') from None

    def deploy(self, model):
        boundary = 'lab-' + uuid.uuid4().hex
        content = (f'--{boundary}\r\nContent-Disposition: form-data; name="deployment-name"\r\n\r\n{model["key"]}\r\n'
                   f'--{boundary}\r\nContent-Disposition: form-data; name="model.bpmn"; filename="model.bpmn"\r\n'
                   'Content-Type: application/xml\r\n\r\n').encode() + model['xml'] + f'\r\n--{boundary}--\r\n'.encode()
        return self.request('POST', '/deployment/create', content, 'multipart/form-data; boundary=' + boundary)


def connection(path):
    config = read_json(path)
    if config.get('schema_version') != '1.0' or config.get('kind') != 'existing-engine':
        raise ValueError('Use a version 1.0 existing-engine connection file.')
    permitted = {'schema_version', 'kind', 'base_url', 'auth_username_env', 'auth_password_env', 'ca_file'}
    if set(config) - permitted:
        raise ValueError('Connection files accept endpoint, CA file and credential environment-variable names only.')
    user_env, password_env = config.get('auth_username_env', 'BENCH_USERNAME'), config.get('auth_password_env', 'BENCH_PASSWORD')
    if not isinstance(user_env, str) or not isinstance(password_env, str) or not os.environ.get(user_env) or not os.environ.get(password_env):
        raise ValueError('Set the credential environment variables named in the connection file.')
    return Engine(config['base_url'], os.environ[user_env], os.environ[password_env], config.get('ca_file'))


class Lab:
    def __init__(self, engine, output, writable=True, mode='owned-local'):
        self.engine, self.output = engine, Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.writable, self.mode = writable, mode
        self.session = 'lab_' + uuid.uuid4().hex[:12]
        self.worker = self.session + '_worker'
        self.models, self.runs, self.deployments = {}, [], []
        self.lock = threading.RLock()
        self.completion_lock = threading.Lock()
        self.thread = None
        self.stop = threading.Event()
        self.version = engine.request('GET', '/version')

    def require_write(self):
        if not self.writable:
            raise ValueError('Connection is read-only. Restart with --allow-deploy to enable isolated test deployments.')

    def upload(self, xml, example=None):
        self.require_write()
        with self.lock:
            if len(self.models) >= 12:
                raise ValueError('This session is limited to 12 models; restart the lab for more.')
            key = self.session + '_' + uuid.uuid4().hex[:8]
            model = parse_model(xml, key)
            deployed = self.engine.deploy(model)
            self.deployments.append(deployed['id'])  # Record ownership before any later validation.
            definitions = list((deployed.get('deployedProcessDefinitions') or {}).values())
            if len(definitions) != 1 or definitions[0]['key'] != key:
                raise RuntimeError('Engine returned an unexpected deployment; it is recorded for cleanup.')
            model.update(id=key, definition_id=definitions[0]['id'], example=example,
                         simulate=example in ('sequential', 'parallel'), deployment_id=deployed['id'])
            self.models[key] = model
            (self.output / (key + '.bpmn')).write_bytes(model['xml'])
            write_json(self.output / 'owned-deployments.local.json', {'deployment_ids': self.deployments})
            return self.public_model(model)

    @staticmethod
    def public_model(model):
        return {k: v for k, v in model.items() if k not in ('xml', 'definition_id', 'deployment_id')}

    def start(self, data):
        self.require_write()
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('Finish or stop the active run before starting another.')
            if len(self.runs) >= 50:
                raise ValueError('This session is limited to 50 runs; export and restart for more.')
            model = self.models.get(data.get('model'))
            if not model:
                raise ValueError('Upload or choose a model first.')
            params = parameters(data, model)
            run = {'id': uuid.uuid4().hex[:12], 'model': model['id'], 'name': model['name'],
                   'simulated_checks': model['simulate'],
                   'parameters': params, 'created_at': utc_now(), 'scope': SCOPE,
                   'status': 'running', 'instances': [], 'error': None}
            self.runs.append(run)
            self.stop.clear()
            self.thread = threading.Thread(target=self.execute, args=(run, model), daemon=True)
            self.thread.start()
            return {'id': run['id']}

    def execute(self, run, model):
        params = run['parameters']
        try:
            with ThreadPoolExecutor(max_workers=2) as workers:
                for index in range(params['count']):
                    if self.stop.is_set():
                        break
                    begin = time.monotonic()
                    started = self.engine.request('POST', '/process-definition/' + urllib.parse.quote(model['definition_id'], safe='') + '/start',
                        {'businessKey': self.session + '-' + run['id'] + '-' + str(index), 'variables': params['variables']})
                    instance = {'id': started['id'], 'number': index + 1, 'status': 'running', 'elapsed_ms': 0,
                                'tasks': [], 'external_tasks': [], 'activities': [], 'end_events': [], 'incidents': [],
                                'expected_end_matches': None, 'variables_match': None, 'deadline_met': None}
                    with self.lock:
                        run['instances'].append(instance)
                    pid = urllib.parse.quote(instance['id'], safe='')
                    futures = {}
                    while not self.stop.is_set():
                        pid = urllib.parse.quote(instance['id'], safe='')
                        history = self.engine.request('GET', '/history/process-instance/' + pid)
                        activities = self.engine.request('GET', '/history/activity-instance?processInstanceId=' + pid)
                        tasks = self.engine.request('GET', '/task?processInstanceId=' + pid)
                        external = self.engine.request('GET', '/external-task?processInstanceId=' + pid)
                        incidents = self.engine.request('GET', '/incident?processInstanceId=' + pid)
                        elapsed = round((time.monotonic() - begin) * 1000, 1)
                        with self.lock:
                            instance.update(elapsed_ms=elapsed,
                                activities=[{k: a.get(k) for k in ('activityId', 'activityName', 'activityType', 'startTime', 'endTime')} for a in activities],
                                tasks=[{k: t.get(k) for k in ('id', 'name', 'taskDefinitionKey')} for t in tasks],
                                external_tasks=[{k: t.get(k) for k in ('id', 'activityId', 'topicName')} for t in external],
                                incidents=[{'type': i.get('incidentType'), 'activity': i.get('activityId')} for i in incidents])
                        if history and history.get('endTime'):
                            declared_ends = {n['id'] for n in model['nodes'] if n['type'] == 'endEvent'}
                            end_events = [a['activityId'] for a in activities if a['activityId'] in declared_ends and a.get('endTime')]
                            saved = self.engine.request('GET', '/history/variable-instance?processInstanceId=' + pid + '&deserializeValues=false')
                            values = {v['name']: v.get('value') for v in saved}
                            with self.lock:
                                instance.update(status='completed' if history.get('state') == 'COMPLETED' else 'cancelled', end_events=end_events,
                                    deadline_met=elapsed <= params['deadline_s'] * 1000,
                                    expected_end_matches=params['expected_end'] in end_events if params['expected_end'] else None,
                                    variables_match=all(values.get(k) == v['value'] for k, v in params['variables'].items()))
                            break
                        if incidents:
                            instance['status'] = 'incident'
                            break
                        if elapsed >= params['deadline_s'] * 1000:
                            instance['status'] = 'deadline_exceeded'
                            instance['deadline_met'] = False
                            break
                        if model['simulate']:
                            for task in external:
                                if task['id'] not in futures:
                                    futures[task['id']] = workers.submit(self.simulate, task, params['delay_ms'])
                            for future in futures.values():
                                if future.done():
                                    future.result()
                        if self.stop.wait(params['poll_ms'] / 1000):
                            break
                    # Let owned simulator requests finish before deleting this instance.
                    for future in futures.values():
                        future.result()
                    if instance['status'] != 'completed':
                        if instance['status'] == 'running':
                            instance['status'] = 'stopped'
                        if instance['status'] != 'cancelled':
                            self.engine.request('DELETE', '/process-instance/' + pid + '?skipCustomListeners=true&skipIoMappings=true')
                    with self.lock:
                        instance['tasks'], instance['external_tasks'] = [], []
            run['status'] = 'stopped' if self.stop.is_set() else 'finished'
        except Exception as error:
            # Raw engine text/payloads never cross into the browser or a public report.
            run['status'], run['error'] = 'error', str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        finally:
            write_json(self.output / ('run-' + run['id'] + '.json'), run)

    def simulate(self, task, delay_ms):
        path = '/external-task/' + urllib.parse.quote(task['id'], safe='')
        self.engine.request('POST', path + '/lock', {'workerId': self.worker, 'lockDuration': 30000})
        time.sleep(delay_ms / 1000)
        # Work overlaps; serial acknowledgements avoid competing updates to the
        # same parallel-join execution. This is a demo scheduler, not a load driver.
        with self.completion_lock:
            self.engine.request('POST', path + '/complete', {'workerId': self.worker})

    def complete(self, data):
        self.require_write()
        typed = variables(data.get('variables', {}))
        with self.lock:
            if not self.runs or self.runs[-1]['status'] != 'running':
                raise ValueError('No active run.')
            run = self.runs[-1]
            if self.models[run['model']]['simulate']:
                raise ValueError('The starter simulator owns these external tasks.')
            instance = next((i for i in run['instances'] if i['id'] == data.get('instance') and i['status'] == 'running'), None)
            if not instance:
                raise ValueError('Task does not belong to an active instance created by this session.')
            is_external = data.get('kind') == 'external'
            endpoint = '/external-task' if is_external else '/task'
            tasks = self.engine.request('GET', endpoint + '?processInstanceId=' + urllib.parse.quote(instance['id'], safe=''))
            task = next((t for t in tasks if t['id'] == data.get('task')), None)
            if not task:
                raise ValueError('This task is no longer active on the selected instance.')
            path = endpoint + '/' + urllib.parse.quote(task['id'], safe='')
            body = {'variables': typed}
            if is_external:
                self.engine.request('POST', path + '/lock', {'workerId': self.worker, 'lockDuration': 30000})
                body['workerId'] = self.worker
            self.engine.request('POST', path + '/complete', body)
            return {'status': 'completed'}

    def state(self):
        with self.lock:
            return copy.deepcopy({'mode': self.mode, 'writable': self.writable, 'engine_version': self.version,
                'scope': SCOPE, 'models': [self.public_model(m) for m in self.models.values()], 'runs': self.runs})

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=45)
            if self.thread.is_alive():
                raise RuntimeError('Run has not stopped; ownership receipt retained. Inspect it before cleanup.')
        failures = []
        for deployment in self.deployments:
            try:
                self.engine.request('DELETE', '/deployment/' + urllib.parse.quote(deployment, safe='') + '?cascade=true&skipCustomListeners=true&skipIoMappings=true')
            except RuntimeError:
                failures.append(deployment)
        write_json(self.output / 'cleanup.local.json', {'remaining_deployment_ids': failures, 'scope': 'Only deployments created by this session'})
        if failures:
            raise RuntimeError('Some owned deployments could not be removed. See cleanup.local.json; existing unrelated deployments were not targeted.')


def make_server(lab, port=8765):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type='application/json'):
            if content_type == 'application/json':
                data = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            host = '127.0.0.1:' + str(self.server.server_port)
            return self.headers.get('Host') == host and self.headers.get('Sec-Fetch-Site') not in ('cross-site',)

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {'error': 'Use the printed loopback URL.'})
            if self.path == '/api/state':
                return self.reply(200, dict(lab.state(), token=token))
            if self.path == '/api/export':
                return self.reply(200, lab.state())
            asset = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'application/javascript'),
                     '/style.css': ('style.css', 'text/css')}.get(self.path)
            if asset:
                return self.reply(200, (ASSETS / asset[0]).read_bytes(), asset[1])
            return self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            origin = 'http://127.0.0.1:' + str(self.server.server_port)
            if (not self.allowed() or self.headers.get('Origin') != origin
                    or not secrets.compare_digest(self.headers.get('X-Lab-Token', ''), token)
                    or self.headers.get('Content-Type') != 'application/json'):
                return self.reply(403, {'error': 'Only same-origin lab actions are accepted.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 300_000 or self.headers.get('Transfer-Encoding'):
                    raise ValueError('Request size is invalid.')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object.')
                if self.path == '/api/upload':
                    result = lab.upload(data.get('xml'))
                elif self.path == '/api/example':
                    name = data.get('name')
                    if name not in ('sequential', 'parallel', 'approval'):
                        raise ValueError('Unknown starter workflow.')
                    result = lab.upload((EXAMPLES / (name + '.bpmn')).read_text(encoding='utf-8'), name)
                elif self.path == '/api/run':
                    result = lab.start(data)
                elif self.path == '/api/complete':
                    result = lab.complete(data)
                elif self.path == '/api/stop':
                    lab.stop.set()
                    result = {'status': 'stop_requested'}
                elif self.path == '/api/shutdown':
                    result = {'status': 'shutdown_requested'}
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    return self.reply(404, {'error': 'Not found'})
                self.reply(200, result)
            except (ValueError, RuntimeError, KeyError) as error:
                self.reply(400, {'error': str(error)})
            except (TypeError, UnicodeError):
                self.reply(400, {'error': 'Invalid request fields or text encoding.'})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def add_arguments(parser):
    parser.add_argument('--connect', type=Path, help='Existing-engine connection JSON; read-only unless --allow-deploy is supplied')
    parser.add_argument('--allow-deploy', action='store_true', help='Allow isolated test deployment/start/cleanup on the configured existing engine')
    parser.add_argument('--runtime', type=Path, default=ROOT / '.tools/fluxnova-3.0.1')
    parser.add_argument('--distribution-archive', type=Path, default=ROOT / '.tools/fluxnova-3.0.1.zip')
    parser.add_argument('--download-runtime', action='store_true', help='Download the checksum-pinned official Fluxnova ZIP if absent')
    parser.add_argument('--java', type=Path)
    parser.add_argument('--engine-port', type=int, default=18088)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--output', type=Path, help='Fresh output directory; defaults to an isolated folder under runs/')


def serve(args):
    if not all(1024 <= port <= 65535 for port in (args.port, args.engine_port)) or args.port == args.engine_port:
        raise ValueError('Choose different UI and engine ports in 1024–65535.')
    folder = (args.output or ROOT / 'runs' / ('lab-' + uuid.uuid4().hex[:12])).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    owned, lab, server = None, None, None
    try:
        if args.connect:
            engine = connection(args.connect)
        else:
            owned = LocalRuntime(folder / 'runtime', args.runtime, args.distribution_archive,
                                 args.java, args.engine_port, args.download_runtime)
            print('Starting isolated Fluxnova 3.0.1; allow about a minute for first startup.', flush=True)
            local = owned.start()
            engine = Engine(local.base_url, 'lab', owned.password)
        lab = Lab(engine, folder, writable=not args.connect or args.allow_deploy,
                  mode='existing-engine' if args.connect else 'owned-local-h2')
        server = make_server(lab, args.port)
        url = f'http://127.0.0.1:{server.server_port}'
        print(json.dumps({'url': url, 'scope': SCOPE, 'output': str(folder), 'writable': lab.writable}), flush=True)
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever(poll_interval=.25)
        except KeyboardInterrupt:
            pass
    finally:
        if server:
            server.server_close()
        try:
            if lab:
                lab.close()
        finally:
            if owned:
                owned.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return serve(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
