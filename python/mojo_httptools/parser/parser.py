from __future__ import annotations

import ctypes
import struct
from array import array
from typing import Any

from .._lib import lib
from .errors import (
    HttpParserCallbackError,
    HttpParserError,
    HttpParserInvalidMethodError,
    HttpParserInvalidURLError,
    HttpParserUpgrade,
)


_CALLBACKS = (
    "on_message_begin",
    "on_url",
    "on_header",
    "on_headers_complete",
    "on_body",
    "on_message_complete",
    "on_chunk_header",
    "on_chunk_complete",
)

_ERROR_MESSAGES = {
    1: "invalid HTTP request",
    2: "Invalid method encountered",
    3: "Invalid URL encountered",
    4: "Invalid HTTP version",
    5: "Invalid header token",
    6: "Invalid character in Content-Length",
    7: "Invalid Transfer-Encoding",
    8: "Transfer-Encoding can't be present with Content-Length",
    9: "Invalid character in chunk size",
    10: "Expected CRLF after chunk data",
    11: "invalid parser state",
}

_EVENT_CAPACITY = 8192
_EVENT_STRUCT = struct.Struct("=5q")
_INT64_MAX = (1 << 63) - 1
_PY_BYTES_AS_STRING = ctypes.pythonapi.PyBytes_AsString
_PY_BYTES_AS_STRING.argtypes = [ctypes.py_object]
_PY_BYTES_AS_STRING.restype = ctypes.c_void_p


class HttpRequestParser:
    """Incremental HTTP/1 request parser with the httptools callback API."""

    def __init__(self, protocol: Any):
        self._callbacks = tuple(
            getattr(protocol, name, None) if protocol is not None else None
            for name in _CALLBACKS
        )
        self._state = array("q", [0]) * 16
        self._state_address = self._state.buffer_info()[0]
        self._events = array("q", [0]) * (_EVENT_CAPACITY * 5)
        self._events_address = self._events.buffer_info()[0]
        self._event_bytes = memoryview(self._events).cast("B")
        self._parse_request = lib().mht_parse_request
        self._pending = bytearray()
        self._method = b"DELETE"
        self._http_major = 0
        self._http_minor = 0
        self._keep_alive = False
        self._upgrade = False
        self._failed: HttpParserError | None = None
        self._leniencies: dict[str, bool] = {}

    def set_dangerous_leniencies(
        self,
        lenient_headers: bool | None = None,
        lenient_chunked_length: bool | None = None,
        lenient_keep_alive: bool | None = None,
        lenient_transfer_encoding: bool | None = None,
        lenient_version: bool | None = None,
        lenient_data_after_close: bool | None = None,
        lenient_optional_lf_after_cr: bool | None = None,
        lenient_optional_cr_before_lf: bool | None = None,
        lenient_optional_crlf_after_chunk: bool | None = None,
        lenient_spaces_after_chunk_size: bool | None = None,
    ) -> None:
        values = locals()
        enabled = [
            name
            for name, value in values.items()
            if name != "self" and value is True
        ]
        if enabled:
            raise NotImplementedError(
                "dangerous leniencies are not implemented; "
                "mojo-httptools is a strict HTTP/1 parser"
            )
        self._leniencies.update(
            {
                name: bool(value)
                for name, value in values.items()
                if name != "self" and value is not None
            }
        )

    def get_http_version(self) -> str:
        return f"{self._http_major}.{self._http_minor}"

    def get_method(self) -> bytes:
        return self._method

    def should_keep_alive(self) -> bool:
        return self._keep_alive

    def should_upgrade(self) -> bool:
        return self._upgrade

    def _parser_error(self, result: int) -> HttpParserError:
        detail = int(self._state[11])
        message = _ERROR_MESSAGES.get(detail, "invalid HTTP request")
        if result == -2:
            return HttpParserInvalidMethodError(message)
        if result == -3:
            return HttpParserInvalidURLError(message)
        return HttpParserError(message)

    def feed_data(
        self, data: bytes | bytearray | memoryview | array[int]
    ) -> None:
        if self._failed is not None:
            raise self._failed
        old_length = len(self._pending)
        using_pending = bool(old_length)
        data_buffer = None
        view = None
        if using_pending:
            incoming = memoryview(data)
            if not incoming.c_contiguous:
                raise BufferError(
                    "memoryview: underlying buffer is not C-contiguous"
                )
            if not incoming.nbytes:
                incoming.release()
                return
            self._pending.extend(incoming.cast("B"))
            incoming.release()
            view = memoryview(self._pending)
            snapshot = view
            data_buffer = ctypes.c_uint8.from_buffer(snapshot)
            data_address = ctypes.addressof(data_buffer)
        elif isinstance(data, bytes):
            snapshot = data
            data_address = _PY_BYTES_AS_STRING(snapshot)
        else:
            view = memoryview(data)
            if not view.c_contiguous:
                raise BufferError(
                    "memoryview: underlying buffer is not C-contiguous"
                )
            if not view.nbytes:
                view.release()
                return
            view = view.cast("B")
            if view.readonly:
                array_interface = getattr(data, "__array_interface__", None)
                if array_interface is None:
                    snapshot = view.tobytes()
                    data_address = _PY_BYTES_AS_STRING(snapshot)
                    view.release()
                    view = None
                else:
                    snapshot = view
                    data_address = int(array_interface["data"][0])
            else:
                snapshot = view
                data_buffer = ctypes.c_uint8.from_buffer(snapshot)
                data_address = ctypes.addressof(data_buffer)
        if not snapshot:
            if view is not None:
                view.release()
            return

        snapshot_length = len(snapshot)
        if snapshot_length > _INT64_MAX:
            if view is not None:
                view.release()
            raise OverflowError("buffer is too large for the native parser")
        (
            on_message_begin,
            on_url,
            on_header,
            on_headers_complete,
            on_body,
            on_message_complete,
            on_chunk_header,
            on_chunk_complete,
        ) = self._callbacks
        parse_request = self._parse_request
        state_address = self._state_address
        events_address = self._events_address
        consumed_total = 0
        upgrade_at: int | None = None
        callback_name = ""
        remainder = b""

        try:
            while consumed_total < snapshot_length:
                result = int(
                    parse_request(
                        data_address + consumed_total,
                        snapshot_length - consumed_total,
                        state_address,
                        events_address,
                        _EVENT_CAPACITY,
                    )
                )
                if result < 0:
                    error = self._parser_error(result)
                    self._failed = error
                    raise error
                if result > _EVENT_CAPACITY:
                    raise RuntimeError("native parser returned too many events")

                event_bytes = self._event_bytes[: result * _EVENT_STRUCT.size]
                for tag, a, b, c, d in _EVENT_STRUCT.iter_unpack(
                    event_bytes
                ):
                    kind = tag & 255
                    if kind == 1:
                        e = (tag >> 8) & 255
                        f = (tag >> 16) & 255
                        a += consumed_total
                        c += consumed_total
                        self._method = bytes(snapshot[a : a + b])
                        self._http_major, self._http_minor = e, f
                        self._upgrade = False
                        if on_message_begin is not None:
                            callback_name = _CALLBACKS[0]
                            on_message_begin()
                        if on_url is not None:
                            callback_name = _CALLBACKS[1]
                            on_url(bytes(snapshot[c : c + d]))
                    elif kind == 2:
                        a += consumed_total
                        c += consumed_total
                        if on_header is not None:
                            callback_name = _CALLBACKS[2]
                            on_header(
                                bytes(snapshot[a : a + b]),
                                bytes(snapshot[c : c + d]),
                            )
                    elif kind == 3:
                        self._keep_alive = bool(a)
                        self._upgrade = bool(b)
                        if on_headers_complete is not None:
                            callback_name = _CALLBACKS[3]
                            on_headers_complete()
                    elif kind == 4:
                        a += consumed_total
                        if on_body is not None:
                            callback_name = _CALLBACKS[4]
                            on_body(bytes(snapshot[a : a + b]))
                    elif kind == 5:
                        if on_chunk_header is not None:
                            callback_name = _CALLBACKS[6]
                            on_chunk_header()
                    elif kind == 6:
                        if on_chunk_complete is not None:
                            callback_name = _CALLBACKS[7]
                            on_chunk_complete()
                    elif kind == 7:
                        if on_message_complete is not None:
                            callback_name = _CALLBACKS[5]
                            on_message_complete()
                        self._keep_alive = (
                            self._http_major == 1 and self._http_minor == 1
                        )
                    elif kind == 8:
                        upgrade_at = consumed_total + a
                    elif kind == 9:
                        self._keep_alive = bool(a)
                        self._upgrade = bool(b)
                        if on_headers_complete is not None:
                            callback_name = _CALLBACKS[3]
                            on_headers_complete()
                        if on_message_complete is not None:
                            callback_name = _CALLBACKS[5]
                            on_message_complete()
                        self._keep_alive = (
                            self._http_major == 1 and self._http_minor == 1
                        )
                    elif kind == 10:
                        a += consumed_total
                        self._keep_alive = bool(c)
                        self._upgrade = bool(d)
                        if on_headers_complete is not None:
                            callback_name = _CALLBACKS[3]
                            on_headers_complete()
                        if on_body is not None:
                            callback_name = _CALLBACKS[4]
                            on_body(bytes(snapshot[a : a + b]))
                        if on_message_complete is not None:
                            callback_name = _CALLBACKS[5]
                            on_message_complete()
                        self._keep_alive = (
                            self._http_major == 1 and self._http_minor == 1
                        )
                    elif kind == 11:
                        a += consumed_total
                        if on_body is not None:
                            callback_name = _CALLBACKS[4]
                            on_body(bytes(snapshot[a : a + b]))
                        if on_message_complete is not None:
                            callback_name = _CALLBACKS[5]
                            on_message_complete()
                        self._keep_alive = (
                            self._http_major == 1 and self._http_minor == 1
                        )
                    elif kind == 12:
                        a += consumed_total
                        if on_chunk_header is not None:
                            callback_name = _CALLBACKS[6]
                            on_chunk_header()
                        if on_body is not None:
                            callback_name = _CALLBACKS[4]
                            on_body(bytes(snapshot[a : a + b]))
                        if on_chunk_complete is not None:
                            callback_name = _CALLBACKS[7]
                            on_chunk_complete()

                consumed = int(self._state[9])
                remaining = snapshot_length - consumed_total
                if consumed < 0 or consumed > remaining:
                    raise RuntimeError("native parser returned an invalid byte count")
                if self._state[12] and consumed == 0:
                    raise RuntimeError("native parser made no progress")
                consumed_total += consumed
                if not self._state[12]:
                    break
            if (
                upgrade_at is None
                and not using_pending
                and consumed_total < snapshot_length
            ):
                remainder = bytes(snapshot[consumed_total:])
        except BaseException as exc:
            if isinstance(exc, HttpParserError) and exc is self._failed:
                raise
            error = HttpParserCallbackError(f"`{callback_name}` callback error")
            self._failed = error
            raise error from exc
        finally:
            del data_buffer
            if view is not None:
                view.release()

        if upgrade_at is not None:
            self._pending.clear()
            offset = max(0, upgrade_at - old_length)
            raise HttpParserUpgrade(offset)
        if using_pending:
            del self._pending[:consumed_total]
        else:
            self._pending.extend(remainder)
