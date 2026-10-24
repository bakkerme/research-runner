"""Documentation and schemas travel with the client, available without a checkout."""
from importlib.resources import files
from pathlib import Path

from .repo import bundled_skill


def documents():
    root = files('runner').joinpath('_bundle')
    if not root.is_dir():
        # Editable/source installs read the canonical sources; wheels use _bundle.
        root = Path(__file__).resolve().parent.parent
    result = {}
    for name in ('README.md', 'AGENTS.md'):
        result[name] = root.joinpath(name).read_text(encoding='utf-8')
    for folder, suffix in (('docs', '.md'), ('schemas', '.json')):
        for path in root.joinpath(folder).iterdir():
            if path.is_file() and path.name.endswith(suffix):
                result[folder + '/' + path.name] = path.read_text(encoding='utf-8')
    for path in root.joinpath('examples/asset-loader').iterdir():
        if path.is_file() and path.name.endswith(('.py', '.json', '.toml', '.md')):
            result['examples/asset-loader/' + path.name] = path.read_text(encoding='utf-8')
    result['skills/research-runner/SKILL.md'] = bundled_skill()
    return result


def export_documents(directory):
    directory = Path(directory).absolute()
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('documentation destination must not contain links')
    content = documents()
    # Check every destination before writing; preserve user edits on reruns.
    for name, text in content.items():
        target = directory / name
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError('documentation destination must not contain links')
        if target.exists() and (not target.is_file() or target.read_text(encoding='utf-8') != text):
            raise ValueError('refusing to overwrite modified documentation: ' + str(target))
    for name, text in content.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    return {'directory': str(directory), 'files': sorted(content)}
