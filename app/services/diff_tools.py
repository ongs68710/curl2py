import bisect
import difflib
import json

# 单个 difflib 匹配块的规模上限（裁剪公共前后缀后的笛卡尔积）。
# difflib 的匹配复杂度与两侧行数之积正相关；在 armv7 这类弱 CPU 上
# 无上限的输入会把单次请求拖到分钟级。超限的块改走锚点切分，
# 把大问题拆成若干小块，保证总能算完且不丢粒度。
MAX_MATCH_WORK = 4_000_000

_UNIFIED_CONTEXT = 3


def _display(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _logical_lines(text):
    """Normalize line endings and return logical lines without terminators."""
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def _longest_increasing(pairs):
    """Longest strictly-increasing subsequence of ``pairs`` ordered by first item."""
    if not pairs:
        return []
    tails = []
    tail_index = []
    previous = [-1] * len(pairs)
    for index, (_left, right) in enumerate(pairs):
        position = bisect.bisect_left(tails, right)
        if position == len(tails):
            tails.append(right)
            tail_index.append(index)
        else:
            tails[position] = right
            tail_index[position] = index
        previous[index] = tail_index[position - 1] if position else -1
    chain = []
    cursor = tail_index[-1]
    while cursor != -1:
        chain.append(pairs[cursor])
        cursor = previous[cursor]
    chain.reverse()
    return chain


def _unique_anchors(left_lines, right_lines, left_start, left_stop, right_start, right_stop):
    """Lines occurring exactly once on both sides, ordered as a longest common chain."""
    seen_left = {}
    for index in range(left_start, left_stop):
        line = left_lines[index]
        seen_left[line] = -1 if line in seen_left else index
    seen_right = {}
    for index in range(right_start, right_stop):
        line = right_lines[index]
        seen_right[line] = -1 if line in seen_right else index

    pairs = []
    for line, left_index in seen_left.items():
        if left_index < 0:
            continue
        right_index = seen_right.get(line, -1)
        if right_index < 0:
            continue
        pairs.append((left_index, right_index))
    pairs.sort()
    return _longest_increasing(pairs)


def _opcodes(left_lines, right_lines):
    """Return ``SequenceMatcher`` opcodes, bounded for large inputs.

    Two optimizations keep this fast without changing the result for
    ordinary inputs:

    * common prefix/suffix are trimmed before matching, so a handful of
      edits inside a long file costs almost nothing;
    * a block whose matching cost would exceed :data:`MAX_MATCH_WORK` is
      split at lines that are unique on both sides (patience-style
      anchoring) and each piece is matched separately.
    """
    opcodes = []
    stack = [("block", 0, len(left_lines), 0, len(right_lines))]

    while stack:
        item = stack.pop()
        if item[0] == "emit":
            opcodes.append(item[1])
            continue
        if item[0] == "batch":
            opcodes.extend(item[1])
            continue

        _kind, left_start, left_stop, right_start, right_stop = item

        if left_start >= left_stop:
            if right_start < right_stop:
                opcodes.append(("insert", left_start, left_start, right_start, right_stop))
            continue
        if right_start >= right_stop:
            opcodes.append(("delete", left_start, left_stop, right_start, right_start))
            continue

        limit = min(left_stop - left_start, right_stop - right_start)
        prefix = 0
        while prefix < limit and left_lines[left_start + prefix] == right_lines[right_start + prefix]:
            prefix += 1
        remaining = limit - prefix
        suffix = 0
        while suffix < remaining and left_lines[left_stop - 1 - suffix] == right_lines[right_stop - 1 - suffix]:
            suffix += 1

        middle_left_start = left_start + prefix
        middle_left_stop = left_stop - suffix
        middle_right_start = right_start + prefix
        middle_right_stop = right_stop - suffix
        middle_left = middle_left_stop - middle_left_start
        middle_right = middle_right_stop - middle_right_start

        pending = []
        if prefix:
            pending.append(("emit", ("equal", left_start, middle_left_start, right_start, middle_right_start)))

        if middle_left and middle_right:
            if middle_left * middle_right <= MAX_MATCH_WORK:
                matcher = difflib.SequenceMatcher(
                    None,
                    left_lines[middle_left_start:middle_left_stop],
                    right_lines[middle_right_start:middle_right_stop],
                    autojunk=False,
                )
                pending.append(("batch", [
                    (tag, a1 + middle_left_start, a2 + middle_left_start,
                     b1 + middle_right_start, b2 + middle_right_start)
                    for tag, a1, a2, b1, b2 in matcher.get_opcodes()
                ]))
            else:
                anchors = _unique_anchors(
                    left_lines, right_lines,
                    middle_left_start, middle_left_stop,
                    middle_right_start, middle_right_stop,
                )
                if not anchors:
                    # 两侧没有可对齐的唯一定位点（如整段都是重复行），
                    # 整块按替换处理：粒度粗但结果正确且必然终止。
                    pending.append(("emit", ("replace", middle_left_start, middle_left_stop,
                                             middle_right_start, middle_right_stop)))
                else:
                    cursor_left = middle_left_start
                    cursor_right = middle_right_start
                    index = 0
                    total = len(anchors)
                    while index < total:
                        anchor_left, anchor_right = anchors[index]
                        # 连续锚点合并成一个 equal 区间，避免把整段相同的行
                        # 拆成逐行 opcode，下游就不必空转上万次。
                        run_stop = index + 1
                        while (run_stop < total
                               and anchors[run_stop][0] == anchors[run_stop - 1][0] + 1
                               and anchors[run_stop][1] == anchors[run_stop - 1][1] + 1):
                            run_stop += 1
                        last_left, last_right = anchors[run_stop - 1]
                        if cursor_left < anchor_left or cursor_right < anchor_right:
                            pending.append(("block", cursor_left, anchor_left,
                                            cursor_right, anchor_right))
                        pending.append(("emit", ("equal", anchor_left, last_left + 1,
                                                 anchor_right, last_right + 1)))
                        cursor_left = last_left + 1
                        cursor_right = last_right + 1
                        index = run_stop
                    if cursor_left < middle_left_stop or cursor_right < middle_right_stop:
                        pending.append(("block", cursor_left, middle_left_stop,
                                        cursor_right, middle_right_stop))
        elif middle_left:
            pending.append(("emit", ("delete", middle_left_start, middle_left_stop,
                                     middle_right_start, middle_right_start)))
        elif middle_right:
            pending.append(("emit", ("insert", middle_left_start, middle_left_start,
                                     middle_right_start, middle_right_stop)))

        if suffix:
            pending.append(("emit", ("equal", middle_left_stop, left_stop, middle_right_stop, right_stop)))

        for entry in reversed(pending):
            stack.append(entry)

    return opcodes


def _grouped_opcodes(opcodes, context=_UNIFIED_CONTEXT):
    """Mirror of ``SequenceMatcher.get_grouped_opcodes`` over precomputed opcodes."""
    codes = list(opcodes)
    if not codes:
        codes = [("equal", 0, 1, 0, 1)]
    if codes[0][0] == "equal":
        tag, left_start, left_stop, right_start, right_stop = codes[0]
        codes[0] = (
            tag,
            max(left_start, left_stop - context),
            left_stop,
            max(right_start, right_stop - context),
            right_stop,
        )
    if codes[-1][0] == "equal":
        tag, left_start, left_stop, right_start, right_stop = codes[-1]
        codes[-1] = (
            tag,
            left_start,
            min(left_stop, left_start + context),
            right_start,
            min(right_stop, right_start + context),
        )

    window = context + context
    group = []
    for tag, left_start, left_stop, right_start, right_stop in codes:
        if tag == "equal" and left_stop - left_start > window:
            group.append((
                tag,
                left_start,
                min(left_stop, left_start + context),
                right_start,
                min(right_stop, right_start + context),
            ))
            yield group
            group = []
            left_start = max(left_start, left_stop - context)
            right_start = max(right_start, right_stop - context)
        group.append((tag, left_start, left_stop, right_start, right_stop))
    if group and not (len(group) == 1 and group[0][0] == "equal"):
        yield group


def _format_range_unified(start, stop):
    beginning = start + 1
    length = stop - start
    if length == 1:
        return str(beginning)
    if not length:
        beginning -= 1
    return f"{beginning},{length}"


def _unified_diff(opcodes, left_lines, right_lines, fromfile, tofile, lineterm=""):
    """Render a unified diff from precomputed opcodes (byte-identical to difflib)."""
    started = False
    lines = []
    for group in _grouped_opcodes(opcodes):
        if not started:
            started = True
            lines.append(f"--- {fromfile}{lineterm}")
            lines.append(f"+++ {tofile}{lineterm}")
        first, last = group[0], group[-1]
        file1_range = _format_range_unified(first[1], last[2])
        file2_range = _format_range_unified(first[3], last[4])
        lines.append(f"@@ -{file1_range} +{file2_range} @@{lineterm}")
        for tag, left_start, left_stop, right_start, right_stop in group:
            if tag == "equal":
                for line in left_lines[left_start:left_stop]:
                    lines.append(" " + line)
                continue
            if tag in {"replace", "delete"}:
                for line in left_lines[left_start:left_stop]:
                    lines.append("-" + line)
            if tag in {"replace", "insert"}:
                for line in right_lines[right_start:right_stop]:
                    lines.append("+" + line)
    return "\n".join(lines)


def _char_segments(left_text, right_text):
    """Return stable character-level segments for one aligned line pair."""
    if left_text == right_text:
        if not left_text:
            return [], []
        return (
            [{"text": left_text, "type": "equal"}],
            [{"text": right_text, "type": "equal"}],
        )

    matcher = difflib.SequenceMatcher(None, left_text or "", right_text or "", autojunk=False)
    left_segments = []
    right_segments = []
    for tag, left_start, left_stop, right_start, right_stop in matcher.get_opcodes():
        left_value = left_text[left_start:left_stop]
        right_value = right_text[right_start:right_stop]
        if tag == "equal":
            left_segments.append({"text": left_value, "type": "equal"})
            right_segments.append({"text": right_value, "type": "equal"})
        elif tag == "delete":
            left_segments.append({"text": left_value, "type": "delete"})
        elif tag == "insert":
            right_segments.append({"text": right_value, "type": "insert"})
        else:
            if left_value:
                left_segments.append({"text": left_value, "type": "delete"})
            if right_value:
                right_segments.append({"text": right_value, "type": "insert"})
    return left_segments, right_segments


def structured_text_diff(left_text, right_text, opcodes=None):
    """Return line-aligned records for the text comparison view."""
    left_lines = _logical_lines(left_text)
    right_lines = _logical_lines(right_text)
    ops = _opcodes(left_lines, right_lines) if opcodes is None else opcodes
    records = []
    stats = {"equal": 0, "insert": 0, "delete": 0, "replace": 0}

    def add(kind, left_number=None, right_number=None, left_value=None, right_value=None):
        records.append({
            "type": kind,
            "left_line": left_number,
            "right_line": right_number,
            "left_text": left_value,
            "right_text": right_value,
        })
        stats[kind] += 1

    for tag, left_start, left_stop, right_start, right_stop in ops:
        if tag == "equal":
            for offset in range(left_stop - left_start):
                add("equal", left_start + offset + 1, right_start + offset + 1,
                    left_lines[left_start + offset], right_lines[right_start + offset])
        elif tag == "delete":
            for index in range(left_start, left_stop):
                add("delete", index + 1, None, left_lines[index], None)
        elif tag == "insert":
            for index in range(right_start, right_stop):
                add("insert", None, index + 1, None, right_lines[index])
        else:
            left_count = left_stop - left_start
            right_count = right_stop - right_start
            overlap = min(left_count, right_count)
            for offset in range(overlap):
                add("replace", left_start + offset + 1, right_start + offset + 1,
                    left_lines[left_start + offset], right_lines[right_start + offset])
            for offset in range(overlap, left_count):
                index = left_start + offset
                add("delete", index + 1, None, left_lines[index], None)
            for offset in range(overlap, right_count):
                index = right_start + offset
                add("insert", None, index + 1, None, right_lines[index])

    stats.update({
        "added": stats["insert"],
        "removed": stats["delete"],
        "modified": stats["replace"],
        "unchanged": stats["equal"],
    })
    return records, stats


def visual_text_diff(left_text, right_text, opcodes=None):
    """Return three-column, line-aligned data for the visual diff renderer."""
    left_lines = _logical_lines(left_text)
    right_lines = _logical_lines(right_text)
    ops = _opcodes(left_lines, right_lines) if opcodes is None else opcodes
    visual_lines = []
    index = 0

    def add(kind, left_number, right_number, left_value, right_value):
        nonlocal index
        left_segments, right_segments = _char_segments(left_value, right_value)
        visual_lines.append({
            "id": f"diff-line-{index}",
            "left_line": left_number,
            "right_line": right_number,
            "left_text": left_value,
            "right_text": right_value,
            "left_segments": left_segments,
            "right_segments": right_segments,
            "connector": {"type": kind, "width": 28},
            "merge_actions": (["left_to_right", "right_to_left"] if kind != "equal" else []),
        })
        index += 1

    for tag, left_start, left_stop, right_start, right_stop in ops:
        if tag == "equal":
            for offset in range(left_stop - left_start):
                add("equal", left_start + offset + 1, right_start + offset + 1,
                    left_lines[left_start + offset], right_lines[right_start + offset])
        elif tag == "delete":
            for offset in range(left_start, left_stop):
                add("delete", offset + 1, None, left_lines[offset], "")
        elif tag == "insert":
            for offset in range(right_start, right_stop):
                add("insert", None, offset + 1, "", right_lines[offset])
        else:
            left_count = left_stop - left_start
            right_count = right_stop - right_start
            for offset in range(max(left_count, right_count)):
                left_index = left_start + offset
                right_index = right_start + offset
                add("replace", left_index + 1 if left_index < left_stop else None,
                    right_index + 1 if right_index < right_stop else None,
                    left_lines[left_index] if left_index < left_stop else "",
                    right_lines[right_index] if right_index < right_stop else "")
    return visual_lines


def text_diff(left_text, right_text, opcodes=None):
    """Return the legacy unified diff view."""
    left_lines = _logical_lines(left_text)
    right_lines = _logical_lines(right_text)
    ops = _opcodes(left_lines, right_lines) if opcodes is None else opcodes
    return _unified_diff(ops, left_lines, right_lines, "original", "new")


def build_text_diff(left_text, right_text):
    """Compute every text-diff representation from a single match pass."""
    left_lines = _logical_lines(left_text)
    right_lines = _logical_lines(right_text)
    ops = _opcodes(left_lines, right_lines)
    _records, stats = structured_text_diff(left_text, right_text, opcodes=ops)
    return {
        "diff": _unified_diff(ops, left_lines, right_lines, "original", "new"),
        "lines": _records,
        "stats": stats,
        "visual_lines": visual_text_diff(left_text, right_text, opcodes=ops),
    }


def _json_diff(left, right, path, lines):
    if type(left) is not type(right):
        lines.append(f"~ {path}: {_display(left)} -> {_display(right)}")
        return

    if isinstance(left, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}"
            if key not in left:
                lines.append(f"+ {child}: {_display(right[key])}")
            elif key not in right:
                lines.append(f"- {child}: {_display(left[key])}")
            else:
                _json_diff(left[key], right[key], child, lines)
        return

    if isinstance(left, list):
        left_tokens = [_display(item) for item in left]
        right_tokens = [_display(item) for item in right]
        matcher = difflib.SequenceMatcher(None, left_tokens, right_tokens, autojunk=False)
        for tag, left_start, left_stop, right_start, right_stop in matcher.get_opcodes():
            if tag == "equal":
                continue
            if tag == "delete":
                for index in range(left_start, left_stop):
                    lines.append(f"- {path}[{index}]: {_display(left[index])}")
            elif tag == "insert":
                for index in range(right_start, right_stop):
                    lines.append(f"+ {path}[{index}]: {_display(right[index])}")
            else:
                overlap = min(left_stop - left_start, right_stop - right_start)
                for offset in range(overlap):
                    _json_diff(
                        left[left_start + offset],
                        right[right_start + offset],
                        f"{path}[{right_start + offset}]",
                        lines,
                    )
                for index in range(left_start + overlap, left_stop):
                    lines.append(f"- {path}[{index}]: {_display(left[index])}")
                for index in range(right_start + overlap, right_stop):
                    lines.append(f"+ {path}[{index}]: {_display(right[index])}")
        return

    if left != right:
        lines.append(f"~ {path}: {_display(left)} -> {_display(right)}")


def json_diff(left_text, right_text):
    try:
        left = json.loads(left_text)
        right = json.loads(right_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）") from exc

    lines = []
    _json_diff(left, right, "$", lines)
    return "\n".join(lines)


def _json_visual_lines(value, path="$", level=0):
    """Render JSON with the same two-space layout used by json.dumps and paths."""
    indent = "  " * level
    if isinstance(value, dict):
        if not value:
            return [(indent + "{}", path)]
        entries = []
        for key in sorted(value):
            child_path = f"{path}.{key}"
            entries.append((key, child_path, value[key]))
        lines = [(indent + "{", path)]
        for index, (key, child_path, child) in enumerate(entries):
            child_lines = _json_visual_lines(child, child_path, level + 1)
            prefix = "  " * (level + 1) + json.dumps(key, ensure_ascii=False) + ": "
            child_lines[0] = (prefix + child_lines[0][0].lstrip(), child_lines[0][1])
            if index < len(entries) - 1:
                child_lines[-1] = (child_lines[-1][0] + ",", child_lines[-1][1])
            lines.extend(child_lines)
        lines.append((indent + "}", path))
        return lines
    if isinstance(value, list):
        if not value:
            return [(indent + "[]", path)]
        lines = [(indent + "[", path)]
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            child_lines = _json_visual_lines(child, child_path, level + 1)
            if index < len(value) - 1:
                child_lines[-1] = (child_lines[-1][0] + ",", child_lines[-1][1])
            lines.extend(child_lines)
        lines.append((indent + "]", path))
        return lines
    return [(indent + json.dumps(value, ensure_ascii=False), path)]


def visual_json_diff(left_text, right_text):
    """Return a stable, formatted JSON visual diff with field path metadata."""
    try:
        left = json.loads(left_text)
        right = json.loads(right_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）") from exc
    left_entries = _json_visual_lines(left)
    right_entries = _json_visual_lines(right)
    left_formatted = "\n".join(line for line, _path in left_entries)
    right_formatted = "\n".join(line for line, _path in right_entries)
    opcodes = _opcodes(_logical_lines(left_formatted), _logical_lines(right_formatted))
    _records, stats = structured_text_diff(left_formatted, right_formatted, opcodes=opcodes)
    visual_lines = visual_text_diff(left_formatted, right_formatted, opcodes=opcodes)
    for line in visual_lines:
        left_number = line.get("left_line")
        right_number = line.get("right_line")
        line["left_path"] = left_entries[left_number - 1][1] if left_number else None
        line["right_path"] = right_entries[right_number - 1][1] if right_number else None
        line["path"] = line["left_path"] or line["right_path"] or "$"
    return visual_lines, stats
