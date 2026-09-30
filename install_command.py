#!/usr/bin/env python3
"""Install an af3_gui command into the active Conda/venv environment."""
import os
from pathlib import Path
import shlex
import sys
import tempfile


MARKER = '# AF3 Console launcher; managed by install_command.py'
REQUIRED = ('af3_gui', 'af3.py', 'af3_runtime.py', 'af3_pae.py',
            'af3_pae_domains.py', 'af3_networkx_community.py', 'af3_msa_sync.py')


def environment_prefix():
    """Never install accidentally into system Python or a different environment."""
    prefix = Path(sys.prefix).resolve()
    conda = os.environ.get('CONDA_PREFIX', '').strip()
    if conda:
        if Path(conda).resolve() != prefix:
            raise ValueError('Python does not belong to the active Conda environment. '
                             'Run conda activate af3-console, then use its python.')
    elif sys.prefix == sys.base_prefix:
        raise ValueError('Activate the af3-console Conda environment (or a venv) first.')
    return prefix


def launcher_text(application, interpreter):
    target = application / 'af3_gui'
    return ('#!/bin/sh\n' + MARKER + '\n'
            'if [ ! -f ' + shlex.quote(str(target)) + ' ]; then\n'
            '    printf "%s\\n" "AF3 Console was moved or removed. Run install_command.py from its new location." >&2\n'
            '    exit 1\n'
            'fi\n'
            'exec ' + shlex.quote(str(interpreter)) + ' -B ' + shlex.quote(str(target)) + ' "$@"\n')


def install_launcher(application, prefix, interpreter):
    application = Path(application).resolve()
    prefix = Path(prefix).resolve()
    # Preserve the environment's executable path: venv python may be a symlink
    # to a base interpreter, and resolving that symlink would bypass the venv.
    interpreter = Path(os.path.abspath(interpreter))
    for value in (str(application), str(prefix), str(interpreter)):
        if any(c in value for c in ('\0', '\r', '\n')):
            raise ValueError('Installation paths must not contain NUL or newlines.')
    missing = [name for name in REQUIRED if not (application / name).is_file()]
    if missing:
        raise ValueError('Keep the complete AF3 Console folder together. Missing: ' + ', '.join(missing))
    if not interpreter.is_file():
        raise ValueError('Python interpreter is missing: ' + str(interpreter))
    bin_dir = prefix / 'bin'
    if not bin_dir.is_dir():
        raise ValueError('Environment bin directory is missing: ' + str(bin_dir))
    destination = bin_dir / 'af3_gui'
    if destination.is_symlink():
        raise ValueError('Refusing to replace an existing symlink: ' + str(destination))
    text = launcher_text(application, interpreter)
    existed = destination.exists()
    if existed:
        if not destination.is_file():
            raise ValueError('The command path is not a file: ' + str(destination))
        old = destination.read_text(encoding='utf-8')
        if not old.startswith('#!/bin/sh\n' + MARKER + '\n'):
            raise ValueError('An unrelated af3_gui command already exists; it was not changed: ' + str(destination))
        if old == text:
            destination.chmod(0o755)
            return destination
    fd, temporary = tempfile.mkstemp(prefix='.af3_gui-', dir=bin_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o755)
        if existed:
            os.replace(temporary, destination)
        else:
            # Publish a new command without clobbering a file created meanwhile.
            os.link(temporary, destination)
        return destination
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    try:
        if os.name != 'posix':
            raise ValueError('Install this command on your Linux cluster, inside its Conda environment.')
        prefix = environment_prefix()
        command = install_launcher(Path(__file__).parent, prefix, sys.executable)
    except (OSError, ValueError) as exc:
        print('Could not install af3_gui: ' + str(exc), file=sys.stderr)
        return 1
    print('Installed: ' + str(command))
    print('With this environment active, run af3_gui from any directory.')
    print('Keep the application folder in place. Re-run this installer after moving or upgrading it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
