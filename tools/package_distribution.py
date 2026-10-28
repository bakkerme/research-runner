"""Check wheel contents and assemble separately downloadable docs and checksums."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
import zipfile


def package_distribution(directory):
    directory = Path(directory)
    wheels = list(directory.glob('research_runner-*-py3-none-any.whl'))
    if len(wheels) != 1:
        raise ValueError('expected exactly one Research Runner wheel in a clean distribution directory')
    wheel = wheels[0]
    version = wheel.name.removeprefix('research_runner-').removesuffix('-py3-none-any.whl')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?', version):
        raise ValueError('unsupported release version')
    source = directory / f'research_runner-{version}.tar.gz'
    if not source.is_file():
        raise ValueError('matching source distribution is missing')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        docs = {name.removeprefix('runner/_bundle/'): archive.read(name)
                for name in names if name.startswith('runner/_bundle/') and not name.endswith('/')}
        required = {'README.md', 'AGENTS.md', 'docs/agent-quickstart.md', 'docs/client-workflow.md',
                    'docs/job-runner-spec.md', 'docs/asset-loaders.md', 'examples/asset-loader/loader.py',
                    'schemas/asset-loader.json', 'schemas/research.json', 'schemas/manifest-v2.json',
                    'schemas/manifest-v3.json', 'examples/tool-image/manifest.json',
                    'examples/tool-image/tools/demo/Containerfile', 'examples/tool-image/inputs/note.txt'}
        if not required <= docs.keys():
            raise ValueError('wheel documentation is incomplete: ' + str(sorted(required - docs.keys())))
        docs['skills/research-runner/SKILL.md'] = archive.read('runner/skills/research-runner/SKILL.md')
    with tarfile.open(source, 'r:gz') as archive:
        prefix = 'research_runner-' + version + '/'
        for name, data in docs.items():
            source_name = 'runner/' + name if name.startswith('skills/') else name
            member = archive.extractfile(prefix + source_name)
            if member is None or member.read() != data:
                raise ValueError('sdist documentation differs from wheel: ' + name)
    docs_name = f'research-runner-docs-{version}.zip'
    with zipfile.ZipFile(directory / docs_name, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(docs.items()):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED)
    assets = [wheel, source, directory / docs_name]
    inventory = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    (directory / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in sorted(inventory.items())), encoding='utf-8')
    return {'version': version, 'assets': inventory}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', default='dist')
    args = parser.parse_args()
    print(json.dumps(package_distribution(args.dist), indent=2))
