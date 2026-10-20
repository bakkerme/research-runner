"""Include maintained documentation in wheels, including builds from an sdist."""
from pathlib import Path
import shutil

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        source = Path(__file__).parent
        target = Path(self.build_lib) / 'runner' / '_bundle'
        for name in ('README.md', 'AGENTS.md'):
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target / name)
        for folder in ('docs', 'schemas'):
            shutil.copytree(source / folder, target / folder, dirs_exist_ok=True)
