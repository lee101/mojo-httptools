from std.sys import simd_width_of


comptime BPtr = UnsafePointer[UInt8, AnyOrigin[mut=True]]
comptime IPtr = UnsafePointer[Int, AnyOrigin[mut=True]]


def packed(data: BPtr, start: Int, n: Int) -> UInt64:
    var value = UInt64(0)
    for i in range(n):
        value |= UInt64(data[start + i]) << UInt64(i * 8)
    return value


def packed_lower(data: BPtr, start: Int, n: Int) -> UInt64:
    var value = UInt64(0)
    for i in range(n):
        var c = data[start + i]
        if c >= UInt8(65) and c <= UInt8(90):
            c += UInt8(32)
        value |= UInt64(c) << UInt64(i * 8)
    return value


def equals_ascii(
    data: BPtr, start: Int, n: Int, p0: UInt64, p1: UInt64
) -> Bool:
    if n > 16:
        return False
    var first = min(n, 8)
    if packed(data, start, first) != p0:
        return False
    if n > 8 and packed(data, start + 8, n - 8) != p1:
        return False
    return True


def equals_lower(
    data: BPtr, start: Int, n: Int, p0: UInt64, p1: UInt64
) -> Bool:
    if n > 16:
        return False
    var first = min(n, 8)
    if packed_lower(data, start, first) != p0:
        return False
    if n > 8 and packed_lower(data, start + 8, n - 8) != p1:
        return False
    return True


def is_token_char(c: UInt8) -> Bool:
    if (
        (c >= UInt8(48) and c <= UInt8(57))
        or (c >= UInt8(65) and c <= UInt8(90))
        or (c >= UInt8(97) and c <= UInt8(122))
    ):
        return True
    return (
        c == UInt8(33)
        or c == UInt8(35)
        or c == UInt8(36)
        or c == UInt8(37)
        or c == UInt8(38)
        or c == UInt8(39)
        or c == UInt8(42)
        or c == UInt8(43)
        or c == UInt8(45)
        or c == UInt8(46)
        or c == UInt8(94)
        or c == UInt8(95)
        or c == UInt8(96)
        or c == UInt8(124)
        or c == UInt8(126)
    )


def method_key(data: BPtr, start: Int, n: Int) -> UInt64:
    var a = packed(data, start, min(n, 8))
    var b = UInt64(0)
    if n > 8 and n <= 16:
        b = packed(data, start + 8, n - 8)
    if n == 3:
        return a if (a == 5522759 or a == 5526864 or a == 4997953) else 0
    if n == 4:
        return a if (
            a == 1145128264
            or a == 1414745936
            or a == 1498435395
            or a == 1262702412
            or a == 1163284301
            or a == 1145981250
            or a == 1263421772
        ) else 0
    if n == 5:
        return a if (
            a == 297481097812
            or a == 327747324749
            or a == 297549317453
            or a == 310367240528
            or a == 297549321552
            or a == 383632364881
        ) else 0
    if n == 6:
        return a if (
            a == 76228242195780
            or a == 79453980018003
            or a == 82752465292885
            or a == 75103027217746
            or a == 75103027220053
            or a == 92712494712146
            or a == 98158412844878
            or a == 82799609269845
            or a == 76155446447955
        ) else 0
    if n == 7:
        return a if (
            a == 23717862989254467 or a == 23448525506629711
        ) else 0
    if n == 8:
        return a if (
            a == 4921952009106444880
            or a == 6076850456876107843
            or a == 5207096034459856205
        ) else 0
    if n == 9:
        return a if (
            (a == 4851574511785431632 and b == 72)
            or (a == 4776439328916264275 and b == 69)
        ) else 0
    if n == 10:
        return a if (
            (a == 5284491839020288845 and b == 22868)
            or (a == 4921947636577291085 and b == 21057)
        ) else 0
    if n == 11:
        return a if (a == 5927673078914174549 and b == 4538953) else 0
    return 0


def find_line_end(data: BPtr, start: Int, n: Int) -> Int:
    comptime W = simd_width_of[DType.float64]()
    comptime BYTE_W = W * 8
    var i = start
    var prefix_end = min(n, start + BYTE_W)
    while i < prefix_end:
        if data[i] == UInt8(10):
            if i == start or data[i - 1] != UInt8(13):
                return -2
            return i - 1
        if data[i] == UInt8(13) and i + 1 < n and data[i + 1] != UInt8(10):
            return -2
        i += 1
    while i + BYTE_W <= n:
        var chars = data.load[width=BYTE_W](i)
        var special = chars.eq(UInt8(10)) | chars.eq(UInt8(13))
        if special.reduce_or():
            break
        i += BYTE_W
    while i < n:
        if data[i] == UInt8(10):
            if i == start or data[i - 1] != UInt8(13):
                return -2
            return i - 1
        if data[i] == UInt8(13) and i + 1 < n and data[i + 1] != UInt8(10):
            return -2
        i += 1
    return -1


def emit(
    events: IPtr,
    index: Int,
    kind: Int,
    a: Int = 0,
    b: Int = 0,
    c: Int = 0,
    d: Int = 0,
) -> Int:
    var base = index * 5
    events[base] = kind
    events[base + 1] = a
    events[base + 2] = b
    events[base + 3] = c
    events[base + 4] = d
    return index + 1


def fail(state: IPtr, pos: Int, detail: Int, code: Int) -> Int:
    state[0] = -1
    state[10] = pos
    state[11] = detail
    return -code


def trim_value(data: BPtr, start: Int, end: Int) -> Tuple[Int, Int]:
    var left = start
    while left < end and (data[left] == UInt8(32) or data[left] == UInt8(9)):
        left += 1
    var right = end
    while right > left and (
        data[right - 1] == UInt8(32) or data[right - 1] == UInt8(9)
    ):
        right -= 1
    return (left, right)


def token_is(
    data: BPtr, start: Int, end: Int, p0: UInt64, p1: UInt64
) -> Bool:
    var left = start
    var right = end
    while left < right and (data[left] == UInt8(32) or data[left] == UInt8(9)):
        left += 1
    while right > left and (
        data[right - 1] == UInt8(32) or data[right - 1] == UInt8(9)
    ):
        right -= 1
    return equals_lower(data, left, right - left, p0, p1)


def csv_has_token(
    data: BPtr, start: Int, end: Int, p0: UInt64, p1: UInt64
) -> Bool:
    var item = start
    var i = start
    while i <= end:
        if i == end or data[i] == UInt8(44):
            if token_is(data, item, i, p0, p1):
                return True
            item = i + 1
        i += 1
    return False


def csv_last_is_token(
    data: BPtr, start: Int, end: Int, p0: UInt64, p1: UInt64
) -> Bool:
    var item = start
    for i in range(start, end):
        if data[i] == UInt8(44):
            item = i + 1
    return token_is(data, item, end, p0, p1)


def parse_decimal(data: BPtr, start: Int, end: Int) -> Int:
    if start == end:
        return -1
    var value = Int(0)
    for i in range(start, end):
        var c = data[i]
        if c < UInt8(48) or c > UInt8(57):
            return -1
        var digit = Int(c) - 48
        if value > (9223372036854775807 - digit) // 10:
            return -1
        value = value * 10 + digit
    return value


def parse_hex(data: BPtr, start: Int, end: Int) -> Int:
    var stop = start
    while stop < end and data[stop] != UInt8(59):
        stop += 1
    if stop == start:
        return -1
    var value = Int(0)
    for i in range(start, stop):
        var c = data[i]
        var digit = Int(-1)
        if c >= UInt8(48) and c <= UInt8(57):
            digit = Int(c) - 48
        elif c >= UInt8(65) and c <= UInt8(70):
            digit = Int(c) - 55
        elif c >= UInt8(97) and c <= UInt8(102):
            digit = Int(c) - 87
        if digit < 0 or value > (9223372036854775807 - digit) // 16:
            return -1
        value = value * 16 + digit
    return value


def process_header(
    data: BPtr, start: Int, end: Int, state: IPtr, events: IPtr, count: Int
) -> Int:
    var colon = start
    while colon < end and data[colon] != UInt8(58):
        if not is_token_char(data[colon]):
            return -1
        colon += 1
    if colon == start or colon == end:
        return -1
    var bounds = trim_value(data, colon + 1, end)
    var value_start = bounds[0]
    var value_end = bounds[1]
    for i in range(value_start, value_end):
        if (
            (data[i] < UInt8(32) and data[i] != UInt8(9))
            or data[i] == UInt8(127)
        ):
            return -1

    var name_len = colon - start
    if name_len == 14 and equals_lower(
        data, start, name_len, 3275364211029340003, 114849160783212
    ):
        var length = parse_decimal(data, value_start, value_end)
        if length < 0:
            return -2
        if (state[7] & 1) != 0:
            return -2
        state[7] |= 1
        state[1] = length
    elif name_len == 17 and (
        packed_lower(data, start, 8) == UInt64(8243107338930713204)
        and packed_lower(data, start + 8, 8) == UInt64(7956000646299018541)
        and packed_lower(data, start + 16, 1) == UInt64(103)
    ):
        if not csv_last_is_token(
            data, value_start, value_end, 28259009760159843, 0
        ):
            return -3
        state[7] |= 2
    elif name_len == 10 and equals_lower(
        data, start, name_len, 7598807758576447331, 28271
    ):
        if csv_has_token(data, value_start, value_end, 435728378979, 0):
            state[7] |= 4
        if csv_has_token(data, value_start, value_end, 7596553519254300011, 25974):
            state[7] |= 8
        if csv_has_token(data, value_start, value_end, 28539342341763189, 0):
            state[7] |= 16
    elif name_len == 7 and equals_lower(
        data, start, name_len, 28539342341763189, 0
    ):
        if value_end > value_start:
            state[7] |= 32

    return emit(
        events,
        count,
        2,
        start,
        name_len,
        value_start,
        value_end - value_start,
    )


@export("mht_parse_request")
def mht_parse_request(
    data_addr: Int,
    n: Int,
    state_addr: Int,
    events_addr: Int,
    max_events: Int,
) abi("C") -> Int:
    # Reject malformed external calls before constructing non-nullable pointers.
    if n < 0 or state_addr == 0 or events_addr == 0 or max_events < 5:
        return -1
    if n > 0 and data_addr == 0:
        return -1
    var data = BPtr(unsafe_from_address=data_addr)
    var state = IPtr(unsafe_from_address=state_addr)
    var events = IPtr(unsafe_from_address=events_addr)
    var pos = Int(0)
    var count = Int(0)

    if state[0] < 0:
        return -1
    state[12] = 0

    while pos < n and count + 4 < max_events:
        var phase = Int(state[0])
        if phase == 0:
            var end = find_line_end(data, pos, n)
            if end == -2:
                return fail(state, pos, 1, 1)
            if end < 0:
                var first_space = pos
                while first_space < n and data[first_space] != UInt8(32):
                    first_space += 1
                if first_space < n and method_key(
                    data, pos, first_space - pos
                ) == 0:
                    return fail(state, pos, 2, 2)
                if first_space < n and n - first_space - 1 >= 5 and equals_ascii(
                    data, first_space + 1, 5, 203211166792, 0
                ):
                    return fail(state, first_space + 1, 3, 3)
                break

            var first_space = pos
            while first_space < end and data[first_space] != UInt8(32):
                first_space += 1
            var request_method = method_key(data, pos, first_space - pos)
            if first_space == end or request_method == 0:
                return fail(state, pos, 2, 2)
            var second_space = first_space + 1
            while second_space < end and data[second_space] != UInt8(32):
                var uc = data[second_space]
                if uc <= UInt8(32) or uc == UInt8(127):
                    return fail(state, second_space, 3, 3)
                second_space += 1
            if second_space == first_space + 1 or second_space == end:
                return fail(state, first_space + 1, 3, 3)
            var version_start = second_space + 1
            if end - version_start != 8:
                return fail(state, version_start, 4, 1)
            if not (
                data[version_start] == UInt8(72)
                and data[version_start + 1] == UInt8(84)
                and data[version_start + 2] == UInt8(84)
                and data[version_start + 3] == UInt8(80)
                and data[version_start + 4] == UInt8(47)
                and data[version_start + 5] == UInt8(49)
                and data[version_start + 6] == UInt8(46)
                and (
                    data[version_start + 7] == UInt8(48)
                    or data[version_start + 7] == UInt8(49)
                )
            ):
                return fail(state, version_start, 4, 1)

            state[1] = 0
            state[3] = 1
            state[4] = Int(data[version_start + 7]) - 48
            state[6] = 0
            state[7] = 0
            if request_method == 23717862989254467:
                state[7] |= 64
            count = emit(
                events,
                count,
                1 + state[3] * 256 + state[4] * 65536,
                Int(request_method),
                first_space - pos,
                first_space + 1,
                second_space - first_space - 1,
            )
            state[0] = 1
            pos = end + 2
            continue

        if phase == 1 or phase == 6:
            var end = find_line_end(data, pos, n)
            if end == -2:
                return fail(state, pos, 5, 1)
            if end < 0:
                break
            if end != pos:
                var next_count = process_header(data, pos, end, state, events, count)
                if next_count == -2:
                    return fail(state, pos, 6, 1)
                if next_count == -3:
                    return fail(state, pos, 7, 1)
                if next_count < 0:
                    return fail(state, pos, 5, 1)
                count = next_count
                pos = end + 2
                continue

            pos = end + 2
            if phase == 6:
                count = emit(events, count, 6)
                count = emit(events, count, 7)
                state[0] = 0
                continue

            if (state[7] & 1) != 0 and (state[7] & 2) != 0:
                return fail(state, pos, 8, 1)
            var keep_alive = Int(0)
            if state[4] == 1:
                keep_alive = 1 if (state[7] & 4) == 0 else 0
            else:
                keep_alive = 1 if (state[7] & 8) != 0 else 0
            state[5] = keep_alive
            state[6] = 1 if (
                (state[7] & 64) != 0
                or ((state[7] & 16) != 0 and (state[7] & 32) != 0)
            ) else 0
            if state[6] == 1:
                count = emit(events, count, 3, state[5], state[6])
                count = emit(events, count, 7)
                count = emit(events, count, 8, pos)
                state[0] = 0
                state[9] = pos
                return count
            if (state[7] & 2) != 0:
                count = emit(events, count, 3, state[5], state[6])
                state[0] = 3
            elif (state[7] & 1) != 0 and state[1] > 0:
                if state[1] <= n - pos:
                    var body_length = Int(state[1])
                    count = emit(
                        events, count, 10, pos, body_length, state[5], state[6]
                    )
                    pos += body_length
                    state[1] = 0
                    state[0] = 0
                else:
                    count = emit(events, count, 3, state[5], state[6])
                    state[0] = 2
            else:
                count = emit(events, count, 9, state[5], state[6])
                state[0] = 0
            continue

        if phase == 2:
            var take = min(Int(state[1]), n - pos)
            if take > 0:
                if take == state[1]:
                    count = emit(events, count, 11, pos, take)
                else:
                    count = emit(events, count, 4, pos, take)
                pos += take
                state[1] -= take
            if state[1] == 0:
                state[0] = 0
                continue
            break

        if phase == 3:
            var end = find_line_end(data, pos, n)
            if end == -2:
                return fail(state, pos, 9, 1)
            if end < 0:
                break
            var chunk_size = parse_hex(data, pos, end)
            if chunk_size < 0:
                return fail(state, pos, 9, 1)
            state[2] = chunk_size
            pos = end + 2
            if chunk_size == 0:
                count = emit(events, count, 5)
                state[0] = 6
            else:
                if chunk_size + 2 <= n - pos:
                    state[13] = 1
                else:
                    count = emit(events, count, 5)
                    state[13] = 0
                state[0] = 4
            continue

        if phase == 4:
            var take = min(Int(state[2]), n - pos)
            if take > 0:
                if (
                    state[13] == 1
                    and take == state[2]
                    and n - pos - take >= 2
                ):
                    state[14] = pos
                    state[15] = take
                    state[13] = 2
                else:
                    if state[13] == 1:
                        count = emit(events, count, 5)
                        state[13] = 0
                    count = emit(events, count, 4, pos, take)
                pos += take
                state[2] -= take
            if state[2] == 0:
                state[0] = 5
                continue
            break

        if phase == 5:
            if n - pos < 2:
                break
            if data[pos] != UInt8(13) or data[pos + 1] != UInt8(10):
                return fail(state, pos, 10, 1)
            pos += 2
            if state[13] == 2:
                count = emit(events, count, 12, state[14], state[15])
                state[13] = 0
            else:
                count = emit(events, count, 6)
            state[0] = 3
            continue

        return fail(state, pos, 11, 1)

    state[9] = pos
    if pos < n and count + 4 >= max_events:
        state[12] = 1
    return count
