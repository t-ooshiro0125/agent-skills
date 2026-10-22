#!/usr/bin/env python3
"""Keep local, versioned editing records; never call a model or judge prose."""
import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


class RevisionError(ValueError):
    pass


STATES = {"pending", "recheck", "confirmed", "unchanged", "held"}
TERMINAL = {"confirmed", "unchanged", "held"}
OPAQUE = {"ok", "okay", "問題なし", "確認済み", "完了", "変更なし", "修正済み"}
TICK = chr(96)
FENCE = re.compile(r"^ {0,3}(" + TICK + r"{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
LIST = re.compile(r"^( {0,3})(?:[-+*]|\d+[.)])\s+")


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_text(path):
    return Path(path).read_bytes().decode("utf-8")


def read_json(path):
    return json.loads(read_text(path))


def stage_file(path, data):
    path = Path(path)
    if path.is_symlink():
        raise RevisionError("refusing to replace a symbolic link; specify the intended regular file")
    if path.exists() and not path.is_file():
        raise RevisionError(f"destination must be a regular file: {path}")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            name = stream.name
            stream.write(data)
            stream.flush()
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
        return name
    except BaseException:
        if name and os.path.exists(name):
            os.unlink(name)
        raise


def write_many_atomic(outputs):
    """Stage every output first and restore committed files on replacement errors."""
    staged, backups, committed, recovery = [], {}, [], set()
    try:
        for path, text in outputs:
            path = Path(path)
            staged.append((path, stage_file(path, text.encode("utf-8"))))
        if len(staged) > 1:
            for path, _ in staged:
                backups[path] = stage_file(path, path.read_bytes()) if path.exists() else None
        for path, name in staged:
            os.replace(name, path)
            committed.append(path)
    except OSError as error:
        failures = []
        for path in reversed(committed):
            backup = backups.get(path)
            try:
                if backup:
                    os.replace(backup, path)
                else:
                    path.unlink()
            except OSError as restore_error:
                if backup:
                    recovery.add(backup)
                failures.append(f"{path}: {restore_error}; recovery copy: {backup}")
        if failures:
            raise RevisionError("output restoration failed: " + "; ".join(failures)) from error
        raise
    finally:
        for name in [name for _, name in staged] + list(backups.values()):
            if name and name not in recovery and os.path.exists(name):
                os.unlink(name)


def write_atomic(path, text):
    write_many_atomic([(path, text)])


def table_start(lines, i):
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    cells = lines[i + 1].strip().strip("|").split("|")
    return len(cells) >= 2 and all(re.fullmatch(r"\s*:?-{3,}:?\s*", c) for c in cells)


def fence_end(lines, i):
    marker = FENCE.match(lines[i]).group(1)
    closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$")
    i += 1
    while i < len(lines):
        if closing.match(lines[i]):
            return i + 1
        i += 1
    return i


def blocks(text):
    """Return exact text slices. Boundaries are candidates, not semantic units."""
    lines = text.splitlines(keepends=True)
    result, i = [], 0
    while i < len(lines):
        start = i
        if not lines[i].strip():
            while i < len(lines) and not lines[i].strip():
                i += 1
            if result:
                result[-1]["text"] += "".join(lines[start:i])
                continue
            kind = "spacing"
        elif FENCE.match(lines[i]):
            kind, i = "code", fence_end(lines, i)
        elif table_start(lines, i):
            kind, i = "table", i + 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                i += 1
        elif HEADING.match(lines[i].lstrip("\ufeff")):
            kind, i = "heading", i + 1
        elif i + 1 < len(lines) and re.fullmatch(r" {0,3}(=+|-+)\s*", lines[i + 1]):
            kind, i = "heading", i + 2
        elif LIST.match(lines[i]):
            kind, i = "list", i + 1
            while i < len(lines):
                if FENCE.match(lines[i]) and lines[i].startswith(" "):
                    i = fence_end(lines, i)
                elif LIST.match(lines[i]) or lines[i].startswith((" ", "\t")):
                    i += 1
                elif not lines[i].strip():
                    look = i + 1
                    while look < len(lines) and not lines[look].strip():
                        look += 1
                    if look < len(lines) and (LIST.match(lines[look]) or lines[look].startswith((" ", "\t"))):
                        i = look
                    else:
                        break
                else:
                    break
        elif lines[i].startswith(("    ", "\t")):
            kind, i = "code", i + 1
            while i < len(lines) and (not lines[i].strip() or lines[i].startswith(("    ", "\t"))):
                i += 1
        elif lines[i].lstrip().startswith(">"):
            kind, i = "quote", i + 1
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                i += 1
        elif lines[i].lstrip().startswith("<!--"):
            kind, i = "html", i + 1
            while "-->" not in lines[i - 1] and i < len(lines):
                i += 1
        else:
            kind, i = "paragraph", i + 1
            while i < len(lines) and lines[i].strip():
                if HEADING.match(lines[i]) or LIST.match(lines[i]) or FENCE.match(lines[i]) or table_start(lines, i):
                    break
                i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        result.append({"kind": kind, "text": "".join(lines[start:i])})
    assert "".join(b["text"] for b in result) == text
    return result


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise RevisionError(f"{name}: nonempty text is required")


def validate_brief(brief):
    if not isinstance(brief, dict):
        raise RevisionError("brief must be an object")
    for name in ("purpose", "reader", "scope", "allowed_changes"):
        require_text(brief.get(name), name)


def make_unit(state, text, kind, parents=()):
    uid = f"u{state['next_id']:04d}"
    state["next_id"] += 1
    return {"id": uid, "kind": kind, "initial_text": text, "text": text,
            "inherited_change": False,
            "generation": 0, "state": "pending", "attempts": 0, "awaiting_review": False,
            "notes": {}, "context_ids": [], "history": [], "review": None,
            "parents": list(parents)}


def initialize(original, working, brief, max_iterations=3):
    validate_brief(brief)
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise RevisionError("max_iterations must be a positive integer")
    state = {"format": 1, "original": original, "original_hash": digest(original),
             "initial_working": working, "brief": copy.deepcopy(brief),
             "revision": 1, "next_id": 1, "max_iterations": max_iterations,
             "units": [], "retired": [], "events": [], "final_review": None}
    state["units"] = [make_unit(state, b["text"], b["kind"]) for b in blocks(working)]
    return state


def unit_by_id(state, uid):
    for unit in state["units"]:
        if unit["id"] == uid:
            return unit
    raise RevisionError(f"unknown active unit: {uid}")


def render(state):
    return "".join(u["text"] for u in state["units"])


def context(state, unit):
    units = state["units"]
    index = units.index(unit)
    headings = []
    for previous in units[:index]:
        for block in blocks(previous["text"]):
            if block["kind"] != "heading":
                continue
            line = block["text"].splitlines()[0].lstrip("\ufeff")
            match = HEADING.match(line)
            level = len(match.group(1)) if match else (1 if "=" in block["text"].splitlines()[1] else 2)
            headings = [h for h in headings if h["level"] < level]
            headings.append({"level": level, "text": block["text"]})
    refs = [{"id": uid, "text": unit_by_id(state, uid)["text"]} for uid in unit["context_ids"]]
    return {"brief": state["brief"], "headings": headings,
            "before": units[index - 1]["text"] if index else "",
            "after": units[index + 1]["text"] if index + 1 < len(units) else "",
            "related": refs, "notes": unit["notes"]}


def token(state, unit):
    return digest({"id": unit["id"], "text": unit["text"], "generation": unit["generation"],
                   "context": context(state, unit)})


def refresh(state):
    for unit in state["units"]:
        review = unit.get("review")
        if unit["state"] in TERMINAL and (not review or review.get("token") != token(state, unit)):
            unit["state"] = "recheck"
            unit["awaiting_review"] = False


def validate_state(state):
    if state.get("format") != 1 or digest(state["original"]) != state["original_hash"]:
        raise RevisionError("unsupported state or modified original snapshot")
    validate_brief(state["brief"])
    ids = [u["id"] for u in state["units"]]
    if len(ids) != len(set(ids)):
        raise RevisionError("duplicate active unit identifiers")
    for unit in state["units"]:
        if unit["state"] not in STATES or not isinstance(unit["text"], str):
            raise RevisionError("invalid unit state")
        if any(uid not in ids or uid == unit["id"] for uid in unit["context_ids"]):
            raise RevisionError("invalid context reference")
    refresh(state)


def load(path):
    if Path(path).is_symlink():
        raise RevisionError("state must be a regular file, not a symbolic link")
    state = read_json(path)
    validate_state(state)
    return state


def save(path, state):
    validate_state(state)
    write_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def document_token(state):
    return digest({"revision": state["revision"], "text": render(state),
                   "brief": state["brief"], "units": [token(state, u) for u in state["units"]]})


def touch(state, reason):
    state["revision"] += 1
    state["final_review"] = None
    state["events"].append({"revision": state["revision"], "reason": reason})
    refresh(state)


def check_token(state, unit, expected):
    if expected != token(state, unit):
        raise RevisionError("stale unit token: retrieve the target and its context again")


def view(state, unit):
    original = unit["notes"].get("original_lines")
    if original:
        lines = state["original"].splitlines(keepends=True)
        original_text = "".join(lines[original[0] - 1:original[1]])
        original_scope = "specified_lines"
    elif unit["initial_text"] in state["original"]:
        original_text, original_scope = unit["initial_text"], "matching_excerpt"
    else:
        original_text, original_scope = state["original"], "whole_document"
    return {"target": {"id": unit["id"], "kind": unit["kind"], "token": token(state, unit),
                       "text": unit["text"], "initial_working_text": unit["initial_text"],
                       "original_text": original_text, "original_scope": original_scope,
                       "state": unit["state"], "attempts": unit["attempts"],
                       "max_iterations": state["max_iterations"]},
            "reference_only": context(state, unit)}


def history(state, uid):
    """Include retired ancestors so regrouping does not hide earlier findings."""
    units = {u["id"]: u for u in state["retired"] + state["units"]}
    if uid not in units:
        raise RevisionError(f"unknown unit: {uid}")
    result, pending, seen = [], [uid], set()
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        unit = units[current]
        result.append(copy.deepcopy(unit))
        pending.extend(unit["parents"])
    return {"unit": uid, "records": result}


def status(state):
    refresh(state)
    return {"document_token": document_token(state), "revision": state["revision"],
            "counts": dict(Counter(u["state"] for u in state["units"])),
            "units": [{"id": u["id"], "kind": u["kind"], "state": u["state"],
                       "token": token(state, u), "attempts": u["attempts"]} for u in state["units"]],
            "final_review_current": bool(state["final_review"] and
                                         state["final_review"]["token"] == document_token(state))}


def next_target(state):
    refresh(state)
    unit = next((u for u in state["units"] if u["state"] not in TERMINAL), None)
    return view(state, unit) if unit else {"target": None, **status(state)}


def annotate(state, uid, expected, data):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    allowed = {"findings", "preservation", "criteria", "context_note", "original_lines", "context_ids"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise RevisionError("unsupported annotation fields")
    refs = data.get("context_ids", unit["context_ids"])
    if not isinstance(refs, list) or any(r == uid for r in refs):
        raise RevisionError("context_ids must name other active units")
    for ref in refs:
        unit_by_id(state, ref)
    if "original_lines" in data:
        bounds = data["original_lines"]
        if (not isinstance(bounds, list) or len(bounds) != 2 or
                any(type(n) is not int for n in bounds) or
                not 1 <= bounds[0] <= bounds[1] <= len(state["original"].splitlines())):
            raise RevisionError("original_lines must be an inclusive range in the original snapshot")
    unit["context_ids"] = list(dict.fromkeys(refs))
    unit["history"].append({"action": "annotate", "notes": copy.deepcopy(unit["notes"])})
    unit["notes"].update({k: copy.deepcopy(v) for k, v in data.items() if k != "context_ids"})
    touch(state, f"annotate {uid}")


def invalidate(state, ids, reason):
    require_text(reason, "invalidation reason")
    targets = [unit_by_id(state, uid) for uid in ids]
    for unit in targets:
        unit["generation"] += 1
        unit["state"], unit["awaiting_review"] = "recheck", False
    touch(state, reason)


def edit(state, uid, expected, text, affected=(), all_affected=False):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    if text == unit["text"]:
        return
    if unit["state"] == "held":
        raise RevisionError("held unit: invalidate with the new information/decision before editing")
    if unit["attempts"] >= state["max_iterations"]:
        raise RevisionError("iteration limit reached: record a held result with remaining issue and proposal")
    if unit["awaiting_review"]:
        raise RevisionError("record the check of the last edit before editing again")
    impacted = [unit_by_id(state, item) for item in affected]
    unit["history"].append({"action": "edit", "text": unit["text"], "token": expected})
    unit["text"], unit["state"], unit["awaiting_review"] = text, "recheck", True
    if all_affected:
        impacted = state["units"]
    for other in impacted:
        if other is not unit:
            other["generation"] += 1
    touch(state, f"edit {uid}")


def validate_record(record):
    if not isinstance(record, dict):
        raise RevisionError("review record must be an object")
    if record.get("outcome") not in {"confirmed", "unchanged", "needs_revision", "held"}:
        raise RevisionError("invalid outcome")
    for name in ("problem", "evidence"):
        require_text(record.get(name), name)
    for name in ("preservation", "criteria"):
        if not isinstance(record.get(name), list) or not record[name]:
            raise RevisionError(f"{name}: a nonempty list is required")
        for value in record[name]:
            require_text(value, name)
    if not isinstance(record.get("checks"), list) or not record["checks"]:
        raise RevisionError("checks: record what was checked and what was observed")
    for check in record["checks"]:
        if not isinstance(check, dict):
            raise RevisionError("each check must be an object")
        for name in ("criterion", "observation"):
            value = check.get(name)
            require_text(value, name)
            if value.strip().casefold().strip(".。!！") in OPAQUE:
                raise RevisionError("bare OK/change labels are not review observations")


def held_details(record):
    for name in ("reason", "proposal", "required_decision"):
        require_text(record.get(name), name)


def record_review(state, uid, expected, record):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    validate_record(record)
    outcome = record["outcome"]
    if unit["attempts"] >= state["max_iterations"] and outcome != "held":
        raise RevisionError("iteration limit reached: only a documented hold may be recorded")
    if outcome == "unchanged" and (unit["text"] != unit["initial_text"] or unit["inherited_change"]):
        raise RevisionError("modified text cannot be recorded as unchanged")
    attempts = unit["attempts"] + 1
    if outcome == "needs_revision" and attempts >= state["max_iterations"]:
        outcome = "held"
    if outcome == "held":
        held_details(record)
    review = copy.deepcopy(record)
    review.update({"token": expected, "outcome": outcome,
                   "iteration": attempts, "limit_reached": attempts >= state["max_iterations"]})
    unit["attempts"] = attempts
    unit["review"] = review
    unit["history"].append({"action": "review", **copy.deepcopy(review)})
    unit["awaiting_review"] = False
    unit["state"] = {"needs_revision": "recheck", "held": "held",
                     "unchanged": "unchanged", "confirmed": "confirmed"}[outcome]
    state["final_review"] = None


def safe_cuts(text, cuts, kind):
    # Regroup a compound target at block boundaries before subdividing a table/code block.
    offset = 0
    for block in blocks(text):
        end = offset + len(block["text"])
        if block["kind"] in {"code", "heading"} and any(offset < c < end for c in cuts):
            raise RevisionError("keep code blocks and headings together")
        if block["kind"] == "table" and kind not in {"table", "table_rows"}:
            if any(offset < c < end for c in cuts):
                raise RevisionError("separate the whole table before splitting its rows")
        if block["kind"] == "list":
            list_lines = block["text"].splitlines(keepends=True)
            indent = len(LIST.match(list_lines[0]).group(1))
            boundaries, position = set(), offset
            for line in list_lines:
                item = LIST.match(line)
                if item and len(item.group(1)) == indent:
                    boundaries.add(position)
                position += len(line)
            if any(offset < cut < end and cut not in boundaries for cut in cuts):
                raise RevisionError("split lists only between complete top-level items")
        offset = end
    offset = 0
    lines = text.splitlines(keepends=True)
    unquoted = []
    for line in lines:
        line = line.expandtabs(4)
        # A fence may be nested in one or more blockquotes.
        prefix = re.match(r"^ {0,3}>[ \t]?", line)
        while prefix:
            line = line[prefix.end():]
            prefix = re.match(r"^ {0,3}>[ \t]?", line)
        unquoted.append(line)
    i = 0
    while i < len(lines):
        if FENCE.match(unquoted[i].lstrip(" ")):
            # Lists can contain indented fences which must also stay whole.
            indent = len(unquoted[i]) - len(unquoted[i].lstrip(" "))
            normalized = [line[indent:] if line.startswith(" " * indent) else line
                          for line in unquoted]
            end = fence_end(normalized, i)
            bound = offset + sum(len(s) for s in lines[i:end])
            if any(offset < cut < bound for cut in cuts):
                raise RevisionError("cannot split inside a fenced code block")
            offset, i = bound, end
        else:
            offset += len(lines[i])
            i += 1
    if kind == "code" and cuts:
        raise RevisionError("keep an indented code block together")
    if kind in {"table", "table_rows"}:
        boundaries = {0}
        offset = 0
        for line in lines:
            offset += len(line)
            boundaries.add(offset)
        if any(cut not in boundaries for cut in cuts):
            raise RevisionError("split tables only between complete rows")
        if table_start(lines, 0) and any(0 < cut < len(lines[0]) + len(lines[1]) for cut in cuts):
            raise RevisionError("keep table column names and delimiter together")


def replace_units(state, old, new, reason):
    ids = [u["id"] for u in old]
    positions = [state["units"].index(u) for u in old]
    if positions != list(range(positions[0], positions[0] + len(old))):
        raise RevisionError("units must be contiguous and in document order")
    before = render(state)
    if "".join(u["text"] for u in old) != "".join(u["text"] for u in new):
        raise RevisionError("regrouping must preserve text exactly")
    state["retired"].extend(copy.deepcopy(old))
    state["units"][positions[0]:positions[-1] + 1] = new
    for unit in state["units"]:
        refs = []
        for ref in unit["context_ids"]:
            refs.extend([n["id"] for n in new] if ref in ids else [ref])
        unit["context_ids"] = list(dict.fromkeys(r for r in refs if r != unit["id"]))
        unit["generation"] += 1
    assert render(state) == before
    touch(state, reason)


def split_unit(state, uid, expected, parts):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    if (not isinstance(parts, list) or len(parts) < 2 or
            any(not isinstance(p, str) or not p for p in parts) or "".join(parts) != unit["text"]):
        raise RevisionError("split parts must be nonempty and concatenate exactly to the current target")
    cuts, offset = [], 0
    for part in parts[:-1]:
        offset += len(part)
        cuts.append(offset)
    safe_cuts(unit["text"], cuts, unit["kind"])
    children = []
    position = state["units"].index(unit)
    neighbors = [u["id"] for u in state["units"][max(0, position - 1):position + 2]
                 if u is not unit]
    for part in parts:
        candidates = blocks(part)
        kind = candidates[0]["kind"] if len(candidates) == 1 else "group"
        child = make_unit(state, part, kind, [uid])
        child["notes"] = copy.deepcopy(unit["notes"])
        child["context_ids"] = list(dict.fromkeys(unit["context_ids"] + neighbors))
        child["attempts"] = unit["attempts"]
        child["inherited_change"] = unit["inherited_change"] or unit["text"] != unit["initial_text"]
        children.append(child)
    if unit["kind"] == "table":
        for child in children[1:]:
            child["kind"] = "table_rows"
            child["context_ids"].append(children[0]["id"])
    elif unit["kind"] == "table_rows":
        for child in children:
            child["kind"] = "table_rows"
    replace_units(state, [unit], children, f"split {uid}")
    return [u["id"] for u in children]


def merge_units(state, ids):
    if len(ids) < 2 or len(ids) != len(set(ids)):
        raise RevisionError("merge requires at least two distinct units")
    old = [unit_by_id(state, uid) for uid in ids]
    positions = [state["units"].index(u) for u in old]
    if positions != list(range(positions[0], positions[0] + len(old))):
        raise RevisionError("merge requires contiguous units in document order")
    text = "".join(u["text"] for u in old)
    new = make_unit(state, text, "group", ids)
    new["notes"]["inherited"] = [copy.deepcopy(u["notes"]) for u in old]
    new["context_ids"] = list(dict.fromkeys(r for u in old for r in u["context_ids"] if r not in ids))
    new["attempts"] = max(u["attempts"] for u in old)
    new["inherited_change"] = any(u["inherited_change"] or u["text"] != u["initial_text"] for u in old)
    replace_units(state, old, [new], f"merge {','.join(ids)}")
    return new["id"]


def reorder(state, ids):
    current = [u["id"] for u in state["units"]]
    if len(ids) != len(current) or set(ids) != set(current) or len(ids) != len(set(ids)):
        raise RevisionError("reorder must contain every active identifier exactly once")
    if ids == current:
        return
    state["units"] = [unit_by_id(state, uid) for uid in ids]
    for unit in state["units"]:
        unit["generation"] += 1
    touch(state, "reorder document")


def blocking_issues(state):
    refresh(state)
    issues = []
    for unit in state["units"]:
        uid, review = unit["id"], unit.get("review")
        if unit["state"] not in TERMINAL:
            issues.append(f"{uid}: {unit['state']}")
            continue
        if not review or review.get("token") != token(state, unit):
            issues.append(f"{uid}: missing/current-version review required")
            continue
        try:
            validate_record(review)
            if unit["state"] == "held":
                held_details(review)
        except RevisionError as error:
            issues.append(f"{uid}: {error}")
    return issues


def final_review(state, expected, record):
    if expected != document_token(state):
        raise RevisionError("stale document token")
    issues = blocking_issues(state)
    if issues:
        raise RevisionError("unfinished targets: " + "; ".join(issues))
    for name in ("original_comparison", "standalone_read"):
        require_text(record.get(name), name)
        if record[name].strip().casefold().strip(".。") in OPAQUE:
            raise RevisionError("describe the full-document check, not a bare OK")
    state["final_review"] = {**copy.deepcopy(record), "token": expected}


def finish(state):
    issues = blocking_issues(state)
    if issues:
        raise RevisionError("unfinished targets: " + "; ".join(issues))
    if not state["final_review"] or state["final_review"]["token"] != document_token(state):
        raise RevisionError("current-version full-document review is required")
    held = [{"id": u["id"], **u["review"]} for u in state["units"] if u["state"] == "held"]
    return {"text": render(state), "document_token": document_token(state),
            "fully_resolved": not held, "held": held, "final_review": state["final_review"]}


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state", required=True, type=Path)
    sub = cli.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--original", required=True, type=Path)
    init.add_argument("--working", type=Path)
    init.add_argument("--brief", required=True, type=Path)
    init.add_argument("--max-iterations", type=int, default=3)
    sub.add_parser("next")
    sub.add_parser("status")
    show = sub.add_parser("show")
    pick = show.add_mutually_exclusive_group(required=True)
    pick.add_argument("--unit")
    pick.add_argument("--history")
    pick.add_argument("--document", choices=["original", "initial_working", "working"])
    for name in ("edit", "record", "annotate", "split"):
        item = sub.add_parser(name)
        item.add_argument("--unit", required=True)
        item.add_argument("--token", required=True)
        item.add_argument("--file", required=True, type=Path)
        if name == "edit":
            item.add_argument("--affected", nargs="*", default=[])
            item.add_argument("--all-affected", action="store_true")
    for name in ("merge", "reorder"):
        sub.add_parser(name).add_argument("--units", nargs="+", required=True)
    inv = sub.add_parser("invalidate")
    group = inv.add_mutually_exclusive_group(required=True)
    group.add_argument("--units", nargs="+")
    group.add_argument("--all", action="store_true")
    inv.add_argument("--reason", required=True)
    brief = sub.add_parser("brief")
    brief.add_argument("--file", required=True, type=Path)
    final = sub.add_parser("final-review")
    final.add_argument("--token", required=True)
    final.add_argument("--file", required=True, type=Path)
    done = sub.add_parser("finish")
    done.add_argument("--output", required=True, type=Path)
    done.add_argument("--report", type=Path)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            if args.state.exists():
                raise RevisionError("state already exists; resume it instead of overwriting")
            if args.state.resolve() in {args.original.resolve(), (args.working or args.original).resolve(),
                                        args.brief.resolve()}:
                raise RevisionError("state must not overwrite an input")
            state = initialize(read_text(args.original), read_text(args.working or args.original),
                               read_json(args.brief), args.max_iterations)
            result = status(state)
        else:
            state = load(args.state)
            command = args.command
            if command == "next":
                result = next_target(state)
            elif command == "status":
                result = status(state)
            elif command == "show":
                if args.document:
                    print(render(state) if args.document == "working" else state[args.document], end="")
                    return 0
                result = history(state, args.history) if args.history else view(state, unit_by_id(state, args.unit))
            elif command == "edit":
                edit(state, args.unit, args.token, read_text(args.file), args.affected, args.all_affected)
                result = view(state, unit_by_id(state, args.unit))
            elif command == "record":
                record_review(state, args.unit, args.token, read_json(args.file))
                result = status(state)
            elif command == "annotate":
                annotate(state, args.unit, args.token, read_json(args.file))
                result = view(state, unit_by_id(state, args.unit))
            elif command == "split":
                result = {"units": split_unit(state, args.unit, args.token, read_json(args.file))}
            elif command == "merge":
                result = {"unit": merge_units(state, args.units)}
            elif command == "reorder":
                reorder(state, args.units)
                result = status(state)
            elif command == "invalidate":
                invalidate(state, [u["id"] for u in state["units"]] if args.all else args.units, args.reason)
                result = status(state)
            elif command == "brief":
                brief = read_json(args.file)
                validate_brief(brief)
                state["brief"] = brief
                invalidate(state, [u["id"] for u in state["units"]], "document brief changed")
                result = status(state)
            elif command == "final-review":
                final_review(state, args.token, read_json(args.file))
                result = status(state)
            elif command == "finish":
                outputs = [args.output] + ([args.report] if args.report else [])
                if any(path.is_symlink() for path in outputs + [args.state]):
                    raise RevisionError("refusing symbolic-link output/state paths; specify regular files")
                if len({p.resolve() for p in outputs + [args.state]}) != len(outputs) + 1:
                    raise RevisionError("output, report, and state paths must be distinct")
                result = finish(state)
                outputs = [(args.output, result.pop("text"))]
                if args.report:
                    outputs.append((args.report, json.dumps(result, ensure_ascii=False, indent=2) + "\n"))
                write_many_atomic(outputs)
            else:
                raise RevisionError("unsupported command")
        if args.command != "finish":
            save(args.state, state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        print(f"revision: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
