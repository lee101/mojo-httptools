# mojo-httptools

`mojo-httptools` is a standalone Mojo implementation of the compute-heavy
HTTP/1 request state machine exposed through a Python API modeled on
[`httptools`](https://github.com/MagicStack/httptools). The parser is
incremental and strict: bytes are scanned and framing state is maintained in
Mojo, while Python protocol callbacks retain the familiar upstream interface.

This is a real parser rather than a request-line splitter. It handles partial
feeds, binary bodies, pipelined messages, chunk extensions, trailer fields,
connection persistence, protocol upgrades, callback failures, and request
smuggling checks for conflicting framing.

## Coverage

The covered upstream API is:

- `HttpRequestParser(protocol)`
- `feed_data(data)` for `bytes`, `bytearray`, contiguous `memoryview`, and
  `array`
- `get_method()`, `get_http_version()`, `should_keep_alive()`, and
  `should_upgrade()`
- `on_message_begin`, `on_url`, `on_header`, `on_headers_complete`, `on_body`,
  `on_message_complete`, `on_chunk_header`, and `on_chunk_complete`
- the upstream request-parser exception hierarchy and upgrade offset behavior
- HTTP/1.0 and HTTP/1.1 requests, fixed-length and chunked bodies, trailers,
  pipelining, `Connection` semantics, `CONNECT`, and header-based upgrades

The parser recognizes llhttp's HTTP request methods, including the standard,
WebDAV, subscription, and Icecast methods. Strict validation rejects
unknown methods, malformed targets and versions, invalid header tokens,
duplicate or non-decimal `Content-Length`, `Content-Length` combined with
`Transfer-Encoding`, invalid transfer codings, and malformed chunks.

Not covered:

- `HttpResponseParser`
- `parse_url` and the `URL` result type
- HTTP/0.9 or HTTP/2 request prefaces
- enabling the unsafe options in `set_dangerous_leniencies`

`set_dangerous_leniencies` has the upstream signature. Passing `False` or
`None` is accepted; enabling an option raises `NotImplementedError` instead of
silently weakening validation.

Upstream can report `on_url` in several pieces when the request target itself
is split across input buffers. This implementation reports the complete target
once its request line is complete. Header and body values, callback order, and
accumulated message semantics match upstream for the covered subset.

## Install

Install the pinned Mojo toolchain, Python, pytest, and upstream `httptools`
reference package:

```bash
pixi install
pixi run build
```

The build task creates `dist/libmojo-httptools.so`. The Python wrapper also
rebuilds a missing or stale library on first use when `mojo` is available.

## Usage

```python
from mojo_httptools import HttpRequestParser


class Protocol:
    def on_url(self, url):
        print("url:", url)

    def on_header(self, name, value):
        print("header:", name, value)

    def on_body(self, body):
        print("body:", body)

    def on_message_complete(self):
        print("complete")


parser = HttpRequestParser(Protocol())
parser.feed_data(
    b"POST /items HTTP/1.1\r\n"
    b"Host: example.com\r\n"
    b"Content-Length: 4\r\n\r\n"
    b"data"
)
print(parser.get_method(), parser.get_http_version())
```

This prints the URL, headers, body, completion callback, then `b'POST' 1.1`.

## How it works

`src/parser.mojo` is one compilation unit exported as a C-compatible shared
library. Python passes the input address, its length, a 16-element `Int64`
state block, and a reusable `Int64` event buffer through ctypes. Addresses cross the C
ABI as Mojo `Int` values and are reconstructed as
`UnsafePointer[..., AnyOrigin[mut=True]]` inside the non-parametric export.

The Mojo state machine retains the current phase, remaining fixed or chunk
body length, HTTP version, framing flags, persistence decision, upgrade flag,
and consumed byte count. It writes compact five-word event records containing
only a tagged kind and byte offsets. Common completion sequences share one
record, and large pipelines drain through bounded batches rather than
allocating an event array proportional to the input. Python unpacks each batch
in C and slices the original contiguous byte buffer into callback arguments.
Immutable `bytes` inputs use their slices directly, and method events use a
packed identifier to reuse constant method objects, so no Python object or
callback crosses into Mojo and one method allocation per request is avoided.

Completed bytes are discarded after every feed. Only an incomplete request
line, header line, chunk delimiter, or body tail is retained between calls.
For an immutable `bytes` feed with no pending fragment, ctypes points directly
at Python's byte storage. Contiguous NumPy arrays and other writable buffer
inputs also cross the FFI boundary without a copy. Line-ending searches keep
one native-width prefix scalar to avoid SIMD setup overhead on typical short
HTTP lines, then scan full native SIMD blocks and finish with a bounded scalar
tail.

## Tests

```bash
pixi run build
pixi run test
```

The 91-test suite compares callback sequences, metadata, upgrade offsets,
fragmentation, bodies, chunks, trailers, pipelining, keep-alive behavior, input
buffer types, error categories, and callback exception chaining against real
`httptools` 0.8.0. It also covers byte-at-a-time input, strict framing, SIMD
boundaries and tails, event-buffer rollover, and zero-copy NumPy addresses.

## Benchmarks

Run benchmarks only through the locked Pixi task:

```bash
pixi run bench
```

Measured on an Intel Xeon E5-2697 v4 at 2.30 GHz, Linux x86-64. Times are the
best of three measured runs after one warm-up. Both parsers use the same
Python callback sink. Speedup is `httptools time / mojo-httptools time`, so a
value below 1 means the Mojo port is slower.

| workload | mojo-httptools | httptools 0.8 | speedup |
|---|---:|---:|---:|
| 100k pipelined GET requests | 240.98 ms | 109.36 ms | 0.45x |
| 50k pipelined POST requests | 175.83 ms | 80.18 ms | 0.46x |
| single POST, 16 MiB body | 1.37 ms | 1.16 ms | 0.84x |
| chunked POST, 8 MiB / 2048 chunks | 2.12 ms | 1.22 ms | 0.57x |
| 20k separate `feed_data` calls | 110.44 ms | 23.35 ms | 0.21x |

Upstream remains faster on every measured case. Relative to the optimization
baseline from the same machine, the pipelined, chunked, and fragmented Mojo
times improved; the 16 MiB fixed-body case moved from 1.34 ms to 1.37 ms and is
reported as a small regression. The remaining gap on small and fragmented
requests includes ordered Python callback and FFI dispatch around a mature
llhttp implementation.

There is intentionally no parallel or GPU parser path. HTTP parsing updates one
ordered state machine and callbacks must retain wire order, so requests within
a feed are not independent work items. These workloads are primarily byte
scanning and copying, for which host/device transfers and launch overhead are
not useful. CPU therefore remains the only execution path, with no `max`
dependency or device allocation.
