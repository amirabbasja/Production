from contextlib import contextmanager, redirect_stderr, redirect_stdout
import logging
import os
import sys
import threading


_output_lock = threading.RLock()
_artifacts_enabled = True


def artifacts_enabled():
    return _artifacts_enabled


@contextmanager
def training_output(generate_artifacts):
    """Apply the output policy to the run and its worker threads."""
    global _artifacts_enabled
    with _output_lock:
        if generate_artifacts or not _artifacts_enabled:
            yield
            return

        bytecode = sys.dont_write_bytecode
        log_level = logging.root.manager.disable
        cuda_cache = os.environ.get('CUDA_CACHE_DISABLE')
        with open(os.devnull, 'w', encoding='utf-8') as sink:
            _artifacts_enabled = False
            sys.dont_write_bytecode = True
            os.environ['CUDA_CACHE_DISABLE'] = '1'
            logging.disable(sys.maxsize)
            try:
                with redirect_stdout(sink), redirect_stderr(sink):
                    yield
            finally:
                logging.disable(log_level)
                if cuda_cache is None:
                    os.environ.pop('CUDA_CACHE_DISABLE', None)
                else:
                    os.environ['CUDA_CACHE_DISABLE'] = cuda_cache
                sys.dont_write_bytecode = bytecode
                _artifacts_enabled = True
