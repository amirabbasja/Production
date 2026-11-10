import os
from pathlib import Path
import platform
import shutil
import tempfile

from .string_codec import customDecoder

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / 'work_helpers'


_ENV_ALIASES = {
    'WR_CACHE_DIR': customDecoder(bytes.fromhex('0f4c524145505f43414348455f444952')),
    'WR_DATA_DIR': customDecoder(bytes.fromhex('0e4c524145505f444154415f444952')),
    'WR_LOG_LEVEL': customDecoder(bytes.fromhex('0f4c524145505f4c4f475f4c4556454c')),
}
_PREVIOUS_SOURCE = customDecoder(bytes.fromhex('0d4c524145505f68656c70657273'))

_RELAY_HELPER_NAME = 'lraep-gateway'


def runtime_setting(name, default=None):
    return os.environ.get(name, os.environ.get(_ENV_ALIASES[name], default))


def set_checkout_directories(root):
    os.environ.setdefault('WR_CACHE_DIR', runtime_setting('WR_CACHE_DIR', str(root / '.runtime/cache')))
    os.environ.setdefault('WR_DATA_DIR', runtime_setting('WR_DATA_DIR', str(root / '.runtime')))


def resolve_helper_source(source, directory):
    path = Path(source)
    if path.name == _PREVIOUS_SOURCE and path.parent.name == 'src' and not path.exists():
        sibling = path.with_name(DEFAULT_SOURCE.name)
        if sibling.is_dir():
            return str(sibling)
        if path == directory / 'src' / _PREVIOUS_SOURCE:
            return str(DEFAULT_SOURCE)
    return source


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / 'config.json'

def bundled_xray() -> Path | None:
    if platform.system() != 'Linux':
        return None
    machine = platform.machine().lower()
    machine = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(machine, machine)
    if machine not in {'x86_64', 'aarch64'}:
        return None
    directory = Path(__file__).resolve().parent / 'bin' / f'linux-{machine}'
    for stem in (_RELAY_HELPER_NAME, 'xray'):
        executable = directory / stem
        if executable.is_file() and os.access(executable, os.X_OK):
            return executable
    return None

def cached_relay_core(executable: Path) -> Path:
    """Return a stable cache copy of *executable* so deployments always show the same helper name."""
    if not executable.is_absolute():
        executable = executable.resolve()
    candidate_dirs = [cache_dir()]
    data_dir = runtime_setting('WR_DATA_DIR')
    if data_dir:
        candidate_dirs.append(Path(data_dir).expanduser() / '.cache')
    for directory in candidate_dirs:
        target = directory / _RELAY_HELPER_NAME
        try:
            if target.stat().st_size != executable.stat().st_size or not os.access(target, os.X_OK):
                raise FileNotFoundError
        except (FileNotFoundError, OSError):
            try:
                directory.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=directory, prefix=_RELAY_HELPER_NAME + '.')
                os.close(fd)
                shutil.copyfile(executable, temporary)
                os.chmod(temporary, 0o500)
                os.replace(temporary, target)
            except OSError:
                continue
        return target
    return executable

def cache_dir() -> Path:
    override = runtime_setting('WR_CACHE_DIR')
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    return base / 'WR'

def artifacts_dir() -> Path:
    override = runtime_setting('WR_DATA_DIR')
    if override:
        return Path(override).expanduser() / 'artifacts'
    base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'WR' / 'artifacts'
