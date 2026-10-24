"""Installed producer CLI, with optional offline/server administration."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

from .package import assemble, verify, deploy


def resolve_host(host):
    if host != 'tailscale':
        return host
    try:
        result = subprocess.run(['tailscale', 'ip', '-4'], capture_output=True, text=True, check=True, timeout=5)
        address = ipaddress.IPv4Address(result.stdout.strip())
        if address.is_unspecified or address.is_loopback or address.is_multicast:
            raise ValueError('not a usable Tailscale address')
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError('Cannot discover Tailscale IPv4 address. Check that Tailscale is installed and connected, or use serve --host 127.0.0.1 for local access.') from error
    return str(address)


def parser():
    p = argparse.ArgumentParser(prog='research-runner')
    p.add_argument('--version', action='version', version=__import__('runner').__version__)
    p.add_argument('--repo', default='.', help='consuming repo or a directory inside it')
    p.add_argument('--state', default=os.getenv('RESEARCH_RUNNER_STATE'), help='offline/server state only (serve defaults to ./var)')
    p.add_argument('--engine', choices=['docker', 'host'], default='host')
    p.add_argument('--image', default='research-runner-worker:0.2.0')
    p.add_argument('--url', help='explicit remote URL for low-level commands; token from RESEARCH_RUNNER_TOKEN')
    sub = p.add_subparsers(dest='cmd', required=True)
    loaders = sub.add_parser('validate-loaders', help='validate operator registrations and handshake with installed loaders')
    loaders.add_argument('--config', required=True, help='server asset loader TOML file')
    docs = sub.add_parser('docs', help='read the bundled agent quickstart or export docs, schemas and skill')
    docs.add_argument('--output', help='export directory; preserves modified existing files')
    init = sub.add_parser('init', help='scaffold a consuming repo and bundled agent skill')
    init.add_argument('directory', nargs='?', default='.')
    init.add_argument('--url', dest='init_url')
    init.add_argument('--name', default='example')
    init.add_argument('--no-skill', action='store_true')
    new = sub.add_parser('new', help='add a research definition')
    new.add_argument('name')
    package = sub.add_parser('package', help='assemble and verify a repo research definition')
    package.add_argument('name')
    attach = sub.add_parser('attach', help='name a dashboard-submitted job locally')
    attach.add_argument('name')
    attach.add_argument('job')
    sub.add_parser('install-skill', help='install the bundled skill into this repo')
    results = sub.add_parser('results', help='verify and read the most recently pulled structured results')
    results.add_argument('job')
    for command, arguments in [
        ('assemble', ['source', 'archive']), ('verify', ['archive']), ('deploy', ['archive', 'directory']),
        ('submit', ['archive']), ('start', ['job']), ('stop', ['job']), ('finalize', ['job']),
        ('status', ['job']), ('events', ['job']), ('collect', ['job', 'output']), ('pull', ['job']),
        ('monitor', ['job']), ('capabilities', []), ('schemas', []), ('download', ['job', 'name', 'output']),
        ('snapshot', ['job', 'output']), ('restore', ['archive', 'directory']),
    ]:
        item = sub.add_parser(command)
        for argument in arguments:
            item.add_argument(argument)
        if command in ('submit', 'start', 'stop', 'finalize'):
            item.add_argument('--key', help='required in low-level mode; repo mode persists identities automatically')
        if command in ('submit', 'start', 'stop', 'finalize', 'collect', 'pull'):
            item.add_argument('--apply', action='store_true', help='apply; otherwise preview')
        if command == 'submit':
            item.add_argument('--receipt', help='required for low-level remote submission')
            item.add_argument('--run', help='local experiment name (defaults to definition name)')
        if command in ('events', 'monitor'):
            item.add_argument('--after', type=int, default=None if command == 'monitor' else 0)
        if command == 'events':
            item.add_argument('--limit', type=int, default=100)
        if command in ('collect', 'pull'):
            item.add_argument('--generation', type=int)
        if command == 'start':
            item.add_argument('--input')
        if command == 'monitor':
            item.add_argument('--json', action='store_true', help='emit every event and status as NDJSON')
            item.add_argument('--interval', type=float, default=2)
            item.add_argument('--timeout', type=float, help='maximum monitoring seconds; Ctrl-C exits without controlling the job')
    serve = sub.add_parser('serve')
    serve.add_argument('--loader-config', help='operator-owned asset loader TOML file')
    serve.add_argument('--host', default='tailscale')
    serve.add_argument('--port', type=int, default=8765)
    return p


def emit(value):
    print(json.dumps(value, indent=2), flush=True)


def progress(value):
    print(f"{value['files']}/{value['total_files']} files, {value['bytes']}/{value['total_bytes']} bytes: {value['name']}" + (' (verified locally)' if value['reused'] else ''), file=sys.stderr, flush=True)


def monitor_message(message, as_json):
    if as_json:
        print(json.dumps(message), flush=True)
    elif message['kind'] == 'status':
        status = message['status']
        parts = [status['state'], status.get('outcome')]
        if status.get('remaining_seconds') is not None:
            parts.append(str(max(0, int(status['remaining_seconds']))) + 's remaining')
        if status.get('pending_approvals'):
            parts.append('awaiting operator approval')
        if status.get('error'):
            parts.append(status['error'])
        print(' | '.join(str(part) for part in parts if part), flush=True)
    else:
        event = message['event']
        if event.get('kind') == 'artifact':
            return
        data = event.get('data', {})
        detail = data.get('message') or data.get('method') or ''
        if event.get('kind') == 'asset_progress':
            detail = data.get('asset_id', '') + ': ' + detail
            if 'bytes_completed' in data:
                detail += f" ({data['bytes_completed']:,}" + (f" / {data['bytes_total']:,}" if 'bytes_total' in data else '') + ' bytes)'
        print(f"[{event['seq']}] {event.get('kind', 'event')}: {str(detail)[:240]}", flush=True)


def _main(p, a):
    if a.cmd == 'validate-loaders':
        from .assets import read_config, validate_loaders
        return validate_loaders(read_config(a.config))
    if a.cmd == 'docs':
        from .documentation import documents, export_documents
        if a.output:
            return export_documents(a.output)
        print(documents()['docs/agent-quickstart.md'], end='')
        return None
    from .client import Client
    from .repo import ResearchRepo, CONFIG
    if a.cmd == 'init':
        repo = ResearchRepo.init(a.directory, a.init_url or a.url or 'http://127.0.0.1:8765', a.name, not a.no_skill)
        return {'repo': str(repo.root), 'definition': a.name, 'next': f'Edit research/{a.name}/inputs/question.md, then package {a.name}'}
    if a.cmd == 'assemble':
        return assemble(a.source, a.archive)
    if a.cmd == 'verify':
        manifest, inventory = verify(a.archive)
        return {'manifest': manifest, 'files': inventory}
    if a.cmd == 'deploy':
        return deploy(a.archive, a.directory)
    if a.cmd == 'schemas':
        from .protocol import schemas
        return schemas()

    # Explicit --url always selects low-level HTTP. Otherwise discover repo config.
    root = Path(a.repo).resolve()
    configured = any((path / CONFIG).is_file() for path in (root, *root.parents))
    repo_commands = {'new', 'package', 'attach', 'install-skill', 'results', 'submit', 'start', 'stop', 'finalize', 'status', 'events', 'monitor', 'pull', 'capabilities'}
    if not a.url and (configured or a.cmd in {'new', 'package', 'attach', 'install-skill', 'results', 'pull', 'monitor'} ) and a.cmd in repo_commands:
        repo = ResearchRepo(a.repo)
        if a.cmd == 'new':
            return {'definition': repo.new(a.name)}
        if a.cmd == 'package':
            return repo.package(a.name)
        if a.cmd == 'install-skill':
            return {'skill': repo.install_skill()}
        if a.cmd == 'attach':
            return repo.attach(a.name, a.job)
        if a.cmd == 'results':
            return repo.results(a.job)
        if a.cmd == 'submit':
            if a.key or a.receipt:
                p.error('repo submit manages keys/receipts; use --url for raw archive submission')
            return repo.submit(a.archive, a.run, a.apply)
        if a.cmd in ('start', 'stop', 'finalize'):
            body = {'input': a.input} if a.cmd == 'start' and a.input else None
            return repo.control(a.job, a.cmd, a.apply, a.key, body)
        if a.cmd == 'status':
            return repo.status(a.job)
        if a.cmd == 'events':
            return repo.events(a.job, a.after, a.limit)
        if a.cmd == 'monitor':
            for event in repo.monitor(a.job, after=a.after, interval=a.interval, timeout=a.timeout):
                monitor_message(event, a.json)
            return None
        if a.cmd == 'pull':
            return repo.pull(a.job, a.generation, a.apply, progress)
        if a.cmd == 'capabilities':
            return repo.client.capabilities()

    if a.url:
        client = Client(a.url)
        if a.cmd in ('submit', 'start', 'stop', 'finalize') and not a.key:
            p.error('low-level control requires --key')
        if a.cmd == 'submit':
            if not a.receipt:
                p.error('remote submission requires --receipt')
            return client.submit(a.archive, a.key, a.receipt, a.apply)
        if a.cmd in ('start', 'stop', 'finalize'):
            return client.control(a.job, a.cmd, a.key, a.apply, {'input': a.input} if a.cmd == 'start' and a.input else {})
        if a.cmd == 'status':
            return client.status(a.job)
        if a.cmd == 'events':
            return client.events(a.job, a.after, a.limit)
        if a.cmd == 'capabilities':
            return client.capabilities()
        if a.cmd == 'collect':
            return client.collect(a.job, a.output, a.generation, a.apply, progress)
        if a.cmd == 'monitor':
            for event in client.monitor(a.job, after=a.after or 0, interval=a.interval, timeout=a.timeout):
                monitor_message(event, a.json)
            return None
        p.error('command not supported over HTTP')

    if a.cmd == 'capabilities':
        from .protocol import capabilities
        return capabilities()
    if a.cmd == 'collect':
        p.error('collection requires --url; use pull NAME in a configured repo')
    if a.cmd not in ('serve', 'restore') and not a.state:
        p.error('no repo configuration; run init --url URL or use --url. Offline administration requires explicit --state')
    if a.cmd == 'serve' and not a.state:
        a.state = './var'
    # Server-only imports must stay below producer dispatch (including --help).
    try:
        from .service import Service, StateLockedError
        if a.cmd == 'serve':
            host = resolve_host(a.host)
            import uvicorn
            from .web import app
            from .assets import read_config
            application = app(a.state, a.engine, a.image, loaders=read_config(a.loader_config))
            url_host = '[' + host + ']' if ':' in host else host
            application.state.service.set_dashboard_url(f'http://{url_host}:{a.port}')
            print(f'Dashboard: http://{url_host}:{a.port}', flush=True)
            uvicorn.run(application, host=host, port=a.port, timeout_graceful_shutdown=5)
            return None
        if a.cmd == 'restore':
            return str(Service.restore(a.archive, a.directory))
        if a.cmd in ('submit', 'start', 'stop', 'finalize') and not a.key:
            p.error('offline controls require --key')
        svc = Service(a.state, a.engine, a.image)
        try:
            if a.cmd == 'submit':
                return svc.submit(a.archive, a.key)[0]
            if a.cmd == 'start':
                import time
                emit(svc.launch(a.job, a.key, a.input))
                while svc.store.get(a.job)['state'] in ('running', 'finalizing'):
                    time.sleep(.2)
                return svc.store.get(a.job)['state']
            if a.cmd == 'stop':
                return svc.stop(a.job, a.key)
            if a.cmd == 'finalize':
                return svc.finalize(a.job, a.key)
            if a.cmd == 'status':
                return svc.status(a.job)
            if a.cmd == 'events':
                return svc.store.events(a.job, a.after, a.limit)
            if a.cmd == 'download':
                data, meta = svc.download(a.job, a.name)
                Path(a.output).write_bytes(data)
                return meta['sha256']
            if a.cmd == 'snapshot':
                return str(svc.snapshot(a.job, a.output))
        finally:
            svc._owner.close()
    except ModuleNotFoundError as error:
        raise ValueError("server commands require a Unix host and pip install 'research-runner[server]'") from error
    except StateLockedError as error:
        raise ValueError(str(error)) from error


def main():
    p = parser()
    a = p.parse_args()
    try:
        value = _main(p, a)
        if value is not None:
            emit(value)
    except (ValueError, OSError, KeyError) as error:
        p.error(str(error))
    except KeyboardInterrupt:
        print('Interrupted; no additional job control sent.', file=sys.stderr)
        raise SystemExit(130)


if __name__ == '__main__':
    main()
