"""Versioned, deterministic research package with strict extraction."""
import gzip, hashlib, io, json, math, os, pathlib, shutil, tarfile
from .protocol import Policy, is_mvp

MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 512
# Input quotas are independent of frequently published output artifacts.
INPUT_MAX_FILE = int(os.getenv('RESEARCH_RUNNER_INPUT_FILE_BYTES', 8 * 1024**3))
INPUT_MAX_TOTAL = int(os.getenv('RESEARCH_RUNNER_INPUT_TOTAL_BYTES', 64 * 1024**3))
if INPUT_MAX_FILE <= 0 or INPUT_MAX_TOTAL < INPUT_MAX_FILE:
    raise ValueError('input limits require 0 < file bytes <= total bytes')
MAX_ARCHIVE = INPUT_MAX_TOTAL + INPUT_MAX_TOTAL // 100 + 1024**2
CHUNK = 1024 * 1024
ENVELOPE_MAX = 1024 * 1024


def hash_file(path):
    with open(path, 'rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def _copy_hash(source, target=None):
    h = hashlib.sha256()
    size = 0
    while chunk := source.read(CHUNK):
        h.update(chunk)
        size += len(chunk)
        if target is not None:
            target.write(chunk)
    return h.hexdigest(), size


def digest(data): return hashlib.sha256(data).hexdigest()


def relpath(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('invalid package path')
    p = pathlib.PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or '.' in p.parts or str(p) != value or value.startswith('-'):
        raise ValueError('unsafe package path')
    return value


def validate_manifest(m):
    if m.get('format') not in ('research-package/v1','research-package/v2'): raise ValueError('unsupported package format')
    fields = {'format','name','adapter','runtime','brief','agents','skills','inputs','resources','required_outputs'}
    if is_mvp(m): fields.add('policy')
    if is_mvp(m) and 'dependencies' in m: fields.add('dependencies')
    if set(m) != fields: raise ValueError('manifest fields')
    if m['adapter'] not in ('codex','fixture'): raise ValueError('adapter')
    if not isinstance(m['name'], str) or not m['name'] or len(m['name']) > 100: raise ValueError('name')
    if not isinstance(m['runtime'], dict): raise ValueError('runtime')
    if m['runtime'].get('sdk')!='openai-codex==0.159.0':raise ValueError('sdk pin')
    if not is_mvp(m) or 'image' in m['runtime'] or 'image_id' in m['runtime']:
        if not isinstance(m['runtime'].get('image'),str):raise ValueError('runtime image')
        image_id=m['runtime'].get('image_id')
        if not isinstance(image_id,str) or len(image_id)!=71 or not image_id.startswith('sha256:') or any(c not in '0123456789abcdef' for c in image_id[7:]):raise ValueError('image pin')
    for k in ('brief','agents'): relpath(m[k])
    for k in ('skills','inputs','required_outputs'):
        if not isinstance(m[k], list): raise ValueError(k)
        for v in m[k]: relpath(v)
    if len(set([m['brief'],m['agents']]+m['skills']+m['inputs']+m['required_outputs'])) != 2+len(m['skills'])+len(m['inputs'])+len(m['required_outputs']): raise ValueError('duplicate paths')
    if any(not p.startswith('outputs/') for p in m['required_outputs']): raise ValueError('required outputs must be outputs/')
    if any(p.startswith('outputs/') or p == 'package.json' for p in declared(m)): raise ValueError('reserved input path')
    if 'dependencies' in m:
        from .protocol import AssetDependency
        if not isinstance(m['dependencies'], list) or len(m['dependencies']) > 100:
            raise ValueError('dependencies must be a list of at most 100 assets')
        occupied = declared(m) + ['package.json', 'manifest.json', 'outputs']
        for dependency in m['dependencies']:
            asset = AssetDependency.model_validate(dependency)
            dest = relpath(asset.destination)
            if any(dest == p or dest.startswith(p+'/') or p.startswith(dest+'/') for p in occupied):
                raise ValueError('dependency destination overlaps reserved, packaged or dependency path: '+dest)
            occupied.append(dest)
    r=m['resources']
    for k in ('cpu','memory_mb','time_seconds'):
        if type(r.get(k)) not in (int,float) or not math.isfinite(r[k]) or not 0 < r[k] <= {'cpu':8,'memory_mb':32768,'time_seconds':86400}[k]: raise ValueError(k)
    if is_mvp(m):
        policy = Policy.model_validate(m['policy'])
        if policy.finalization_seconds >= r['time_seconds']: raise ValueError('finalization reserve must be smaller than time budget')
        if not {'outputs/result.json', 'outputs/handoff.md'} <= set(m['required_outputs']): raise ValueError('v2 requires result.json and handoff.md')
    return m


def declared(m): return [m['brief'],m['agents']]+m['skills']+m['inputs']


def assemble(src, dest):
    src=pathlib.Path(src).resolve(); dest=pathlib.Path(dest).resolve()
    if dest.is_relative_to(src): raise ValueError('archive must be outside payload directory')
    m=validate_manifest(json.loads((src/'manifest.json').read_text()))
    expected=set(declared(m))|{'manifest.json'}
    actual=set()
    for p in src.rglob('*'):
        rp=p.relative_to(src).as_posix()
        if p.is_symlink(): raise ValueError('links forbidden')
        if p.is_file(): actual.add(rp)
        elif not p.is_dir(): raise ValueError('special input forbidden')
    if actual != expected: raise ValueError(f'undeclared or missing payloads: {sorted(actual ^ expected)}')
    if len(declared(m))+1>MAX_FILES: raise ValueError('too many entries')
    files={};total=0
    for name in sorted(declared(m)):
        p=src/name
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(src): raise ValueError('unsafe input')
        size=p.stat().st_size
        if size>INPUT_MAX_FILE: raise ValueError('oversize input file')
        total+=size
        if total>INPUT_MAX_TOTAL: raise ValueError('oversize package')
        files[name]={'size':size,'sha256':hash_file(p)}
    envelope={'manifest':m,'sha256':{n:d['sha256'] for n,d in files.items()}}
    data=json.dumps(envelope,sort_keys=True,separators=(',',':')).encode()
    if len(data)>ENVELOPE_MAX or total+len(data)>INPUT_MAX_TOTAL: raise ValueError('package limits exceeded')
    dest.parent.mkdir(parents=True,exist_ok=True)
    # Publish only a fully written, verified archive.
    import tempfile
    fd, temporary=tempfile.mkstemp(dir=dest.parent,suffix='.rrpkg.tmp')
    os.close(fd)
    try:
        with open(temporary,'wb') as out, gzip.GzipFile(fileobj=out,mode='wb',filename='',mtime=0,compresslevel=6) as gz, tarfile.open(fileobj=gz,mode='w|') as t:
            for name,meta in [('package.json',{'size':len(data)})]+list(sorted(files.items())):
                info=tarfile.TarInfo(name);info.size=meta['size'];info.mode=0o644
                if name=='package.json': t.addfile(info,io.BytesIO(data))
                else:
                    with (src/name).open('rb') as source: t.addfile(info,source)
        verify(temporary)
        os.replace(temporary,dest)
    finally:
        pathlib.Path(temporary).unlink(missing_ok=True)
    return hash_file(dest)


def _scan(archive, root=None):
    files={};total=0;meta=None
    if pathlib.Path(archive).stat().st_size>MAX_ARCHIVE: raise ValueError('compressed archive too large')
    with tarfile.open(archive,'r|gz') as t:
        seen=set()
        for count,x in enumerate(t,1):
            if count>MAX_FILES: raise ValueError('too many entries')
            name=relpath(x.name)
            limit=ENVELOPE_MAX if name=='package.json' else INPUT_MAX_FILE
            if not x.isfile() or name in seen or x.size<0 or x.size>limit: raise ValueError('unsafe archive member')
            # Prevent overlapping paths from turning extraction into a file/directory conflict.
            if any(name.startswith(n+'/') or n.startswith(name+'/') for n in seen): raise ValueError('overlapping archive paths')
            seen.add(name);total+=x.size
            if total>INPUT_MAX_TOTAL: raise ValueError('archive too large')
            f=t.extractfile(x)
            if f is None: raise ValueError('missing body')
            if name=='package.json':
                meta=json.loads(f.read(ENVELOPE_MAX+1))
                continue
            if root is None:
                sha,size=_copy_hash(f)
            else:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
                with p.open('xb') as out: sha,size=_copy_hash(f,out)
            if size!=x.size: raise ValueError('truncated body')
            files[name]={'sha256':sha,'size':size}
    if meta is None: raise ValueError('missing package.json')
    m=validate_manifest(meta['manifest'])
    if set(files)!=set(declared(m)) or set(meta['sha256'])!=set(files): raise ValueError('payload mismatch')
    if any(d['sha256']!=meta['sha256'][n] for n,d in files.items()): raise ValueError('hash mismatch')
    return m,files


def verify(archive):
    """Stream validation; return manifest and payload hash/size inventory."""
    return _scan(archive)


def read_manifest(archive):
    """Read the envelope of an already verified sealed archive without hashing inputs."""
    with tarfile.open(archive,'r|gz') as t:
        for x in t:
            if x.name=='package.json':
                if not x.isfile() or x.size>ENVELOPE_MAX: raise ValueError('invalid envelope')
                return validate_manifest(json.loads(t.extractfile(x).read(ENVELOPE_MAX+1))['manifest'])
    raise ValueError('missing package.json')


def deploy(archive,root):
    root=pathlib.Path(root)
    if root.exists(): raise FileExistsError(root)
    root.mkdir(parents=True)
    try:
        m,files=_scan(archive,root)
        (root/'package.json').write_text(json.dumps({'manifest':m,'sha256':{n:d['sha256'] for n,d in files.items()}},sort_keys=True))
        (root/'outputs').mkdir(exist_ok=True)
    except BaseException:
        shutil.rmtree(root);raise
    return m
