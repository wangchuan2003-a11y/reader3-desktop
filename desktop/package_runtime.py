"""Package only the reader's source and Python runtime; never include a user's library.

Run with the project's virtualenv interpreter. Every copied symlink is resolved
inside its source tree, so the result has no dependency on the build machine's
Codex runtime path. Native dependency checks and a relocated import smoke test
run before the build can replace an existing application.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


MACHO_MAGIC = {b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf'}


def collect_local_modules(project: Path, entry='server') -> list[Path]:
    """Follow source imports rather than copying arbitrary files in the workspace."""
    queue = [entry]
    seen = set()
    result = []
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        path = project / f'{name}.py'
        if not path.is_file():
            continue
        result.append(path)
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue.extend(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                queue.append(node.module.split('.')[0])
    return sorted(result)


def checked_copytree(source: Path, target: Path, *, exclude=()):
    source = source.resolve()

    def ignore(directory, names):
        ignored = []
        for name in names:
            path = Path(directory) / name
            if name in {'__pycache__', '.DS_Store', *exclude} or name.endswith(('.pyc', '.pyo')):
                ignored.append(name)
                continue
            if path.is_symlink() and not path.resolve().is_relative_to(source):
                raise ValueError(f'Runtime symlink leaves its source tree: {path.relative_to(source)}')
        return ignored

    shutil.copytree(source, target, ignore=ignore, symlinks=False)


def native_files(root: Path):
    for path in root.rglob('*'):
        if path.is_file():
            with path.open('rb') as source:
                if source.read(4) in MACHO_MAGIC:
                    yield path


def load_commands(path: Path):
    output = subprocess.check_output(['/usr/bin/otool', '-l', str(path)], text=True)
    command = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('cmd '):
            command = line[4:]
        elif command in {'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB'} and line.startswith('name '):
            yield 'dependency', line[5:].rsplit(' (offset ', 1)[0]
        elif command == 'LC_RPATH' and line.startswith('path '):
            yield 'rpath', line[5:].rsplit(' (offset ', 1)[0]


def audit_native_links(runtime: Path):
    checked = []
    for path in native_files(runtime):
        commands = list(load_commands(path))
        rpaths = [value for kind, value in commands if kind == 'rpath']
        for kind, value in commands:
            if value.startswith(('/System/', '/usr/lib/')):
                continue
            if value == '@loader_path' or value.startswith('@loader_path/'):
                resolved = (path.parent / value[len('@loader_path'):].lstrip('/')).resolve()
                if not resolved.is_relative_to(runtime.resolve()):
                    raise ValueError(f'Native {kind} escapes bundle: {path.name}: {value}')
                if kind == 'dependency' and not resolved.exists():
                    raise ValueError(f'Missing bundled dependency: {path.name}: {value}')
            elif value.startswith('@rpath/'):
                suffix = value[len('@rpath/'):]
                candidates = []
                for rpath in rpaths:
                    expanded = rpath.replace('@loader_path', str(path.parent)).replace('@executable_path', str(runtime / 'bin'))
                    if not expanded.startswith('@'):
                        candidates.append(Path(expanded) / suffix)
                if not any(p.exists() and p.resolve().is_relative_to(runtime.resolve()) for p in candidates):
                    raise ValueError(f'Unresolved bundled rpath dependency: {path.name}: {value}')
            elif value == '@executable_path' or value.startswith('@executable_path/'):
                resolved = (runtime / 'bin' / value[len('@executable_path'):].lstrip('/')).resolve()
                if not resolved.is_relative_to(runtime.resolve()):
                    raise ValueError(f'Executable-relative {kind} escapes bundle: {value}')
            else:
                raise ValueError(f'Non-portable native {kind}: {path.name}: {value}')
        checked.append(path.relative_to(runtime).as_posix())
    return sorted(checked)


def smoke_check(resources: Path):
    with tempfile.TemporaryDirectory(prefix='reader3-bundled-smoke-') as temporary:
        # Rename the assembled resources within the staging app, so no build-location dependency can be hidden.
        moved = resources.with_name('Relocated resources 测试')
        resources.rename(moved)
        try:
            python = moved / 'runtime/bin/python3.12'
            backend = moved / 'backend'
            env = {'PATH': '/usr/bin:/bin', 'HOME': temporary, 'PYTHONNOUSERSITE': '1',
                   'PYTHONDONTWRITEBYTECODE': '1', 'READER3_LIBRARY_DIR': str(Path(temporary) / 'Library')}
            code = '''
import pathlib,sys,sqlite3,ssl,zlib,lzma,hashlib
sys.dont_write_bytecode=True
sys.path.insert(0,sys.argv[1])
import fastapi,uvicorn,ebooklib,bs4,lxml.etree,pydantic_core,jinja2
import server
runtime=pathlib.Path(sys.executable).resolve().parents[1]
assert pathlib.Path(sys.prefix).resolve()==runtime,(sys.prefix,runtime)
for module in [fastapi,uvicorn,ebooklib,bs4,lxml.etree,pydantic_core,jinja2]:
    assert pathlib.Path(module.__file__).resolve().is_relative_to(runtime),module.__file__
assert pathlib.Path(server.BOOKS_DIR).resolve()==pathlib.Path(sys.argv[2]).resolve(),server.BOOKS_DIR
print('Relocated bundled Python and reader imports passed.')
'''
            subprocess.run([str(python), '-I', '-B', '-c', code, str(backend), env['READER3_LIBRARY_DIR']],
                           env=env, cwd=temporary, check=True, timeout=30)
        finally:
            moved.rename(resources)


def package(project: Path, app: Path):
    resources = app / 'Contents/Resources'
    runtime = resources / 'runtime'
    backend = resources / 'backend'
    base = Path(sys.base_prefix).resolve()
    version = f'python{sys.version_info.major}.{sys.version_info.minor}'
    if version != 'python3.12':
        raise ValueError('The current desktop runtime expects Python 3.12; update the native runtime path before changing versions.')
    (runtime / 'bin').mkdir(parents=True)
    shutil.copy2(Path(sys.executable).resolve(), runtime / 'bin/python3.12')
    checked_copytree(base / 'lib', runtime / 'lib', exclude={'site-packages', 'pkgconfig'})
    dependencies = Path(sys.prefix) / 'lib' / version / 'site-packages'
    excluded = {p.name for p in dependencies.glob('__editable__*')}
    for metadata in dependencies.glob('*.dist-info/direct_url.json'):
        if json.loads(metadata.read_text()).get('dir_info', {}).get('editable'):
            if not metadata.parent.name.startswith('reader3-'):
                raise ValueError(f'Another editable dependency requires an explicit packaging rule: {metadata.parent.name}')
            excluded.add(metadata.parent.name)
    checked_copytree(dependencies, runtime / 'lib' / version / 'site-packages', exclude=excluded)
    for pth in (runtime / 'lib' / version / 'site-packages').glob('*.pth'):
        for line in pth.read_text().splitlines():
            if not line.strip() or line.startswith(('#', 'import ', 'import\t')):
                continue
            target = (pth.parent / line).resolve()
            if not target.is_relative_to(runtime.resolve()):
                raise ValueError(f'Site-package path leaves bundled runtime: {pth.name}')

    backend.mkdir()
    modules = collect_local_modules(project)
    for source in modules:
        shutil.copy2(source, backend / source.name)
    shutil.copy2(project / 'desktop/server_host.py', backend / 'server_host.py')
    for directory in ['templates', 'static']:
        checked_copytree(project / directory, backend / directory)
    shutil.copy2(project / 'docs/桌面版.md', resources / '桌面版.md')
    if (project / 'LICENSE').is_file():
        shutil.copy2(project / 'LICENSE', resources / 'Reader3-LICENSE')

    native = audit_native_links(runtime)
    smoke_check(resources)
    for relative in native:
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(runtime / relative)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    manifest = {
        'python_version': sys.version.split()[0],
        'source_modules': [p.name for p in modules],
        'native_files_checked': native,
        'books_included': False,
        'relocated_import_test': 'passed',
        'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in modules},
    }
    (resources / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(f'Packaged Python, {len(modules)} reader modules, and {len(native)} checked native files; no books included.')


if __name__ == '__main__':
    package(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
