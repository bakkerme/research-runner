"""Set package and runtime versions from a release tag in a disposable checkout."""
import argparse
from pathlib import Path
import re
import tomllib


NUMBER = r'(?:0|[1-9][0-9]*)'
RELEASE_TAG = rf'v({NUMBER}\.{NUMBER}\.{NUMBER}(?:(?:a|b|rc){NUMBER})?)'


def prepare_release(source, tag):
    match = re.fullmatch(RELEASE_TAG, tag)
    if not match:
        raise ValueError('release tag must be vMAJOR.MINOR.PATCH, optionally followed by aN, bN, or rcN')
    version = match[1]
    source = Path(source)
    project_path = source / 'pyproject.toml'
    project = project_path.read_text(encoding='utf-8')
    if not isinstance(tomllib.loads(project)['project'].get('version'), str):
        raise ValueError('expected a static [project].version to prepare for release')
    section = re.search(r'(?ms)^\[project\][ \t]*\n(.*?)(?=^\[|\Z)', project)
    if section is None:
        raise ValueError('missing [project] table')
    body, count = re.subn(r'(?m)^(version[ \t]*=[ \t]*)[^\n]+',
                          lambda m: m[1] + '"' + version + '"', section[1])
    if count != 1:
        raise ValueError('expected one version assignment in [project]')
    project = project[:section.start(1)] + body + project[section.end(1):]
    if tomllib.loads(project)['project']['version'] != version:
        raise ValueError('package version was not updated')

    runtime_path = source / 'runner' / '__init__.py'
    runtime, count = re.subn(r'(?m)^__version__[ \t]*=[ \t]*[^\n]+',
                            '__version__ = "' + version + '"',
                            runtime_path.read_text(encoding='utf-8'))
    if count != 1:
        raise ValueError('expected one runtime __version__ assignment')
    # Validate both files before changing either. Never commit this build-only edit.
    project_path.write_text(project, encoding='utf-8')
    runtime_path.write_text(runtime, encoding='utf-8')
    return version


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='.')
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    print(prepare_release(args.source, args.tag))
