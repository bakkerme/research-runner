"""Versioned, deterministic research package with strict extraction."""
import gzip, hashlib, io, json, math, os, pathlib, shutil, tarfile
from .protocol import Policy, is_mvp

MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 512


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
    src=pathlib.Path(src).resolve(); m=validate_manifest(json.loads((src/'manifest.json').read_text()))
    expected=set(declared(m))|{'manifest.json'}
    actual=set()
    for p in src.rglob('*'):
        rp=p.relative_to(src).as_posix()
        if p.is_symlink(): raise ValueError('links forbidden')
        if p.is_file(): actual.add(rp)
    if actual != expected: raise ValueError(f'undeclared or missing payloads: {sorted(actual ^ expected)}')
    files={}
    for name in sorted(declared(m)):
        p=src/name
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(src): raise ValueError('unsafe input')
        data=p.read_bytes()
        if len(data)>MAX_FILE: raise ValueError('oversize file')
        files[name]=data
    if sum(map(len,files.values()))>MAX_TOTAL: raise ValueError('oversize package')
    envelope={'manifest':m,'sha256':{n:digest(d) for n,d in files.items()}}
    files['package.json']=json.dumps(envelope,sort_keys=True,separators=(',',':')).encode()
    if len(files)>MAX_FILES or sum(map(len,files.values()))>MAX_TOTAL: raise ValueError('package limits exceeded')
    dest=pathlib.Path(dest); dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('wb') as out, gzip.GzipFile(fileobj=out,mode='wb',filename='',mtime=0,compresslevel=6) as gz, tarfile.open(fileobj=gz,mode='w') as t:
        for name,data in sorted(files.items()):
            info=tarfile.TarInfo(name); info.size=len(data);info.mtime=0;info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o644
            t.addfile(info,io.BytesIO(data))
    return digest(dest.read_bytes())


def verify(archive):
    files={};total=0
    with tarfile.open(archive,'r:gz') as t:
        for count,x in enumerate(t,1):
            if count>MAX_FILES: raise ValueError('too many entries')
            name=relpath(x.name)
            if not x.isfile() or name in files or x.size>MAX_FILE: raise ValueError('unsafe archive member')
            total+=x.size
            if total>MAX_TOTAL: raise ValueError('archive too large')
            f=t.extractfile(x)
            if f is None: raise ValueError('missing body')
            files[name]=f.read(MAX_FILE+1)
    meta=json.loads(files.pop('package.json'))
    m=validate_manifest(meta['manifest'])
    if set(files)!=set(declared(m)) or set(meta['sha256'])!=set(files): raise ValueError('payload mismatch')
    if any(digest(b)!=meta['sha256'][n] for n,b in files.items()): raise ValueError('hash mismatch')
    return m,files


def deploy(archive,root):
    m,files=verify(archive);root=pathlib.Path(root)
    if root.exists(): raise FileExistsError(root)
    root.mkdir(parents=True)
    try:
        for n,b in files.items():
            p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
        (root/'package.json').write_text(json.dumps({'manifest':m,'sha256':{n:digest(b) for n,b in files.items()}},sort_keys=True))
        (root/'outputs').mkdir(exist_ok=True)
    except BaseException:
        shutil.rmtree(root);raise
    return m
