





from contextlib import contextmanager
import ctypes
from functools import lru_cache
import json
import secrets
import threading

from .native_text_codec import library_path


class WireError(ValueError):
    pass


class WireLimitError(WireError):
    pass


_PROFILES = {'rpc': 0, 's3': 1, 'wr': 2}
MAX_FRAME = 8 * 1024 * 1024


@lru_cache(maxsize=1)
def _library():
    try:
        library = ctypes.CDLL(str(library_path().with_name('libwire_codec.so')))
    except OSError as error:
        raise RuntimeError('native wire codec unavailable; build it from the trainer repository') from error
    pointer, size = ctypes.c_void_p, ctypes.c_size_t
    signatures = {
        'wr_wire_abi_version': ([], ctypes.c_uint),
        'wr_wire_source_id': ([], ctypes.c_char_p),
        'wr_wire_buffer_new': ([], pointer),
        'wr_wire_buffer_free': ([pointer], None),
        'wr_wire_buffer_data': ([pointer], pointer),
        'wr_wire_buffer_size': ([pointer], size),
        'wr_wire_buffer_capacity': ([pointer], size),
        'wr_wire_buffer_reserve': ([pointer, size], ctypes.c_int),
        'wr_wire_buffer_resize': ([pointer, size], ctypes.c_int),
        'wr_wire_buffer_append': ([pointer, pointer, size], ctypes.c_int),
        'wr_wire_buffer_clear': ([pointer], None),
        'wr_wire_buffer_consume': ([pointer, size], ctypes.c_int),
        'wr_wire_buffer_boundary': ([pointer], size),
        'wr_wire_transform': ([pointer, size, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint32, pointer], ctypes.c_int),
        'wr_wire_live_buffers': ([], size),
        'wr_wire_wipe_calls': ([], size),
        'wr_wire_wiped_bytes': ([], size),
    }
    for name, (arguments, result) in signatures.items():
        operation = getattr(library, name)
        operation.argtypes, operation.restype = arguments, result
    if library.wr_wire_abi_version() != 1:
        raise RuntimeError('unsupported native wire codec ABI')
    return library


def _check(status):
    if status == 2:
        raise WireLimitError('wire message exceeds the configured size or nesting limit')
    if status == 3:
        raise MemoryError('native wire buffer allocation failed')
    if status:
        raise WireError('wire message is not valid bounded JSON or framing')


def _profile(value):
    try:
        return _PROFILES[value]
    except (KeyError, TypeError):
        raise ValueError('profile must be rpc, s3 or wr') from None


class _Allocation:
    def __init__(self, library):
        self.library = library
        self.handle = library.wr_wire_buffer_new()
        self.exports = 0
        if not self.handle:
            raise MemoryError('native wire buffer allocation failed')

    def __del__(self):
        handle, self.handle = self.handle, None
        if handle:
            self.library.wr_wire_buffer_free(handle)


class _Lease:
    def __init__(self, allocation):
        self.allocation = allocation
        allocation.exports += 1

    def __del__(self):
        self.allocation.exports -= 1


class _Buffer:


    def __init__(self):
        self.library = _library()
        self._allocation = _Allocation(self.library)

    @property
    def handle(self):
        return self._allocation.handle if self._allocation is not None else None

    @property
    def size(self):
        return self.library.wr_wire_buffer_size(self.handle)

    @property
    def pointer(self):
        return self.library.wr_wire_buffer_data(self.handle)

    def reserve(self, size):
        if self._allocation is None:
            raise RuntimeError('native wire buffer is closed')
        if self._allocation.exports:
            raise RuntimeError('native wire buffer still has a borrowed view')
        _check(self.library.wr_wire_buffer_reserve(self.handle, size))

    def resize(self, size):
        if self._allocation is None:
            raise RuntimeError('native wire buffer is closed')
        if self._allocation.exports and size > self.library.wr_wire_buffer_capacity(self.handle):
            raise RuntimeError('native wire buffer still has a borrowed view')
        _check(self.library.wr_wire_buffer_resize(self.handle, size))

    def append_pointer(self, pointer, size):
        if self._allocation is None:
            raise RuntimeError('native wire buffer is closed')
        if self._allocation.exports and self.size + size > self.library.wr_wire_buffer_capacity(self.handle):
            raise RuntimeError('native wire buffer still has a borrowed view')
        _check(self.library.wr_wire_buffer_append(self.handle, pointer, size))

    def clear(self):
        self.library.wr_wire_buffer_clear(self.handle)

    def consume(self, count):
        _check(self.library.wr_wire_buffer_consume(self.handle, count))

    @contextmanager
    def view(self, size=None):
        count = self.size if size is None else size
        self.reserve(count)
        array = (ctypes.c_ubyte * count).from_address(self.pointer) if count else (ctypes.c_ubyte * 0)()


        array._native_lease = _Lease(self._allocation)
        value = memoryview(array).cast('B')
        try:
            yield value
        finally:
            value.release()
            del value, array

    def close(self):
        if self._allocation is not None:
            self.clear()
            self._allocation = None

    def __del__(self):
        if getattr(self, '_allocation', None) is not None:
            self.close()


@contextmanager
def _input(data):


    encoded = None
    view = array = None
    try:
        if isinstance(data, str):
            encoded = data.encode('utf-8')
            data = encoded
        if isinstance(data, bytes):
            pointer = ctypes.c_char_p(data)
            yield pointer, len(data)
        else:
            view = memoryview(data)
            if not view.contiguous:
                raise TypeError('wire input must be a contiguous buffer')
            if view.readonly:
                raise TypeError('readonly wire input must be bytes')
            array = (ctypes.c_ubyte * view.nbytes).from_buffer(view)
            yield array, view.nbytes
    finally:
        if view is not None:
            view.release()
        del data, encoded, view, array


def _transform(pointer, count, profile, outgoing=False, framed=False, seed=None):
    result = _Buffer()
    try:
        _check(result.library.wr_wire_transform(pointer, count, _profile(profile), int(outgoing),
            int(framed), secrets.randbits(32) if seed is None else seed, result.handle))
        return result
    except BaseException:
        result.close()
        raise


def _neutral_object(buffer):


    neutral = ctypes.string_at(buffer.pointer, buffer.size)
    try:
        return json.loads(neutral)
    except (ValueError, UnicodeError, RecursionError):
        raise WireError('translated JSON could not be read') from None
    finally:
        del neutral


def loads(data, profile='rpc'):

    result = None
    try:
        with _input(data) as (pointer, count):
            result = _transform(pointer, count, profile)
        return _neutral_object(result)
    finally:
        if result is not None:
            result.close()
        del data, result


@contextmanager
def dumps_buffer(message, profile='rpc', *, framed=False, seed=None, newline=False):

    result = encoded = None
    try:
        encoded = json.dumps(message, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')
        with _input(encoded) as (pointer, count):
            result = _transform(pointer, count, profile, outgoing=True, framed=framed, seed=seed)
        if newline:
            result.append_pointer(ctypes.c_char_p(b'\n'), 1)
        with result.view() as view:
            yield view
    finally:
        if result is not None:
            result.close()
        del message, encoded, result


def read_json_body(response, limit, profile='rpc'):

    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError('body limit must be a positive integer')
    raw, chunk, translated = _Buffer(), _Buffer(), None
    try:
        while True:
            count = min(65536, limit + 1 - raw.size)
            with chunk.view(count) as view:
                received = response.readinto(view)
            if type(received) is not int or not 0 <= received <= count:
                raise WireError('HTTP reader returned an invalid byte count')
            chunk.resize(received)
            raw.append_pointer(chunk.pointer, received)
            chunk.clear()
            if raw.size > limit:
                raise WireLimitError('wire response exceeds the configured size limit')
            if not received:
                break
        translated = _transform(raw.pointer, raw.size, profile)
        raw.clear()
        return _neutral_object(translated)
    finally:
        raw.close()
        chunk.close()
        if translated is not None:
            translated.close()
        del response, translated


class NativeChannel:








    def __init__(self, connection, profile, max_frame=MAX_FRAME):
        _profile(profile)
        if profile == 'rpc':
            raise ValueError('RPC bodies use read_json_body')
        if isinstance(max_frame, bool) or not isinstance(max_frame, int) or not 1 <= max_frame <= MAX_FRAME:
            raise ValueError('frame limit must be between one and eight MiB')
        self.connection, self.profile, self.max_frame = connection, profile, max_frame
        self._raw, self._chunk, self._last = _Buffer(), _Buffer(), _Buffer()
        self._read_lock, self._write_lock = threading.RLock(), threading.RLock()
        self._state = threading.RLock()
        self._active = 0
        self._closed = False

    @contextmanager
    def _operation(self):
        with self._state:
            if self._closed:
                raise ConnectionError('native message channel is closed')
            self._active += 1
        try:
            yield
        finally:
            with self._state:
                self._active -= 1
                if self._closed and not self._active:
                    self._free()

    def _free(self):
        self._raw.close()
        self._chunk.close()
        self._last.close()

    def close(self):
        with self._state:
            self._closed = True
            if not self._active:
                self._free()

    def discard_frame(self):
        with self._read_lock, self._state:
            if not self._closed:
                self._last.clear()

    def last_frame(self):
        with self._read_lock, self._state:
            if self._closed or self.profile != 'wr':
                return b''
            return ctypes.string_at(self._last.pointer, self._last.size)

    def _remember(self, count):
        if self.profile == 'wr':
            self._last.clear()
            self._last.append_pointer(self._raw.pointer, count)

    def receive(self):
        with self._read_lock, self._operation():
            translated = None
            self._last.clear()
            try:
                while True:
                    boundary = self._raw.library.wr_wire_buffer_boundary(self._raw.handle)
                    if boundary:
                        self._remember(boundary)
                        if boundary > self.max_frame:
                            raise WireLimitError('wire frame exceeds the configured size limit')
                        if self.profile == 's3':

                            with self._raw.view(boundary) as view:
                                blank = all(value in (9, 10, 13, 32) for value in view)
                            if blank:
                                self._raw.consume(boundary)
                                return None
                        translated = _transform(self._raw.pointer, boundary, self.profile,
                            framed=self.profile == 'wr')
                        self._raw.consume(boundary)
                        result = _neutral_object(translated)
                        if not isinstance(result, dict):
                            raise WireError('protocol message must be a JSON object')
                        return result
                    if self._raw.size >= self.max_frame:
                        self._remember(self._raw.size)
                        raise WireLimitError('wire frame exceeds the configured size limit')
                    count = min(65536, self.max_frame - self._raw.size)
                    with self._chunk.view(count) as view:
                        received = self.connection.recv_into(view)
                    if type(received) is not int or not 0 <= received <= count:
                        raise WireError('socket returned an invalid byte count')
                    self._chunk.resize(received)
                    if not received:
                        self._remember(self._raw.size)
                        raise ConnectionError('peer closed the message connection')
                    self._raw.append_pointer(self._chunk.pointer, received)
                    self._chunk.clear()
            except BaseException:
                self._raw.clear()
                self._chunk.clear()
                raise
            finally:
                if translated is not None:
                    translated.close()
                del translated

    def send(self, message):
        with self._write_lock, self._operation():
            try:
                if not isinstance(message, dict):
                    raise WireError('protocol message must be a JSON object')
                with dumps_buffer(message, self.profile, framed=self.profile == 'wr',
                        newline=self.profile != 'wr') as view:
                    if len(view) > self.max_frame:
                        raise WireLimitError('wire frame exceeds the configured size limit')
                    self.connection.sendall(view)
            finally:
                del message

    def __del__(self):
        if hasattr(self, '_state'):
            self.close()
