#!/usr/bin/env python3
"""Keep local, versioned editing records; never call a model or judge prose."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, OrderedDict
from functools import lru_cache
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys


try:
    from markdown_it import MarkdownIt
except ImportError:
    raise SystemExit("revision: install this skill's requirements.txt into the current Python environment")


class RevisionError(ValueError):
    pass


STATES = {"pending", "recheck", "confirmed", "unchanged", "held"}
TERMINAL = {"confirmed", "unchanged", "held"}
OPAQUE = {"ok", "okay", "問題なし", "確認済み", "完了", "変更なし", "修正済み"}
MARKDOWN = MarkdownIt("commonmark").enable("table")
SPLIT_MARKDOWN = MarkdownIt("commonmark", {"inline_definitions": True}).enable("table")
LAYOUTS = OrderedDict()


def source_lines(text):
    # Match CommonMark line endings without rewriting CRLF, BOM, or Unicode text.
    return re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)[:-1]


def markdown_source(text):
    # Only parser input drops the leading BOM; source slices and offsets stay raw.
    return text.removeprefix("\ufeff")


@lru_cache(maxsize=128)
def syntax(text):
    return tuple(MARKDOWN.parse(markdown_source(text)))


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_text(path):
    return Path(path).read_bytes().decode("utf-8")


def read_json(path):
    return json.loads(read_text(path))


def stage_file(path, data, *, new_mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise RevisionError("refusing to replace a symbolic link; specify the intended regular file")
    if path.exists() and not path.is_file():
        raise RevisionError(f"destination must be a regular file: {path}")
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    else:
        # Read the inherited umask in a child without changing the parent's mask.
        mask = int(subprocess.check_output(
            [sys.executable, "-I", "-S", "-B", "-c", "import os; print(os.umask(0))"], text=True))
        mode = new_mode & ~mask
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        candidate = path.parent / f".revision-{secrets.token_hex(16)}.tmp"
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        name = str(candidate)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
        return name
    except BaseException:
        if name and os.path.exists(name):
            os.unlink(name)
        raise


def write_many_atomic(outputs, *, new_mode=0o600):
    """Stage every output first and restore committed files on replacement errors."""
    staged, backups, recovery = [], {}, set()
    try:
        for path, text in outputs:
            path = Path(path)
            staged.append((path, stage_file(path, text.encode("utf-8"), new_mode=new_mode)))
        for path, _ in staged:
            backups[path] = stage_file(path, path.read_bytes()) if path.exists() else None
        for path, name in staged:
            os.replace(name, path)
    except BaseException as error:
        # A signal can arrive after replace succeeds but before Python records it.
        committed = [path for path, name in staged if not os.path.exists(name)]
        failures = []
        for path in reversed(committed):
            backup = backups.get(path)
            try:
                if backup:
                    os.replace(backup, path)
                else:
                    path.unlink()
            except BaseException as restore_error:
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


def blocks(text):
    """Slice the original source at root block boundaries, including unrendered text."""
    lines = source_lines(text)
    result, position = [], 0
    kinds = {"fence": "code", "code_block": "code", "heading_open": "heading",
             "bullet_list_open": "list", "ordered_list_open": "list",
             "blockquote_open": "quote", "table_open": "table", "html_block": "html",
             "hr": "separator"}
    for node in syntax(text):
        if node.level != 0 or node.map is None or node.nesting == -1:
            continue
        start, end = node.map
        if start > position:
            gap = "".join(lines[position:start])
            if result:
                result[-1]["text"] += gap
            else:
                result.append({"kind": "spacing" if not gap.strip() else "paragraph", "text": gap})
        block = {"kind": kinds.get(node.type, "paragraph"), "text": "".join(lines[start:end])}
        if node.type == "heading_open":
            block["level"] = int(node.tag[1:])
        result.append(block)
        position = end
    tail = "".join(lines[position:])
    if tail:
        if result:
            result[-1]["text"] += tail
        else:
            result.append({"kind": "spacing" if not tail.strip() else "paragraph", "text": tail})
    assert "".join(block["text"] for block in result) == text
    return result



def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise RevisionError(f"{name}: nonempty text is required")


def validate_brief(brief):
    if not isinstance(brief, dict):
        raise RevisionError("brief must be an object")
    for name in ("purpose", "reader", "reader_difficulties", "desired_understanding",
                 "scope", "allowed_changes"):
        require_text(brief.get(name), name)
    criteria = brief.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise RevisionError("register the required criteria before processing targets")
    ids, stages = set(), set()
    for criterion in criteria:
        if not isinstance(criterion, dict) or set(criterion) != {"id", "stage", "description"}:
            raise RevisionError("each criterion requires only id, stage, and description")
        uid = criterion["id"]
        if not isinstance(uid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", uid) or uid in ids:
            raise RevisionError("criterion IDs must be unique stable identifiers")
        if not isinstance(criterion["stage"], str) or criterion["stage"] not in {"unit", "document"}:
            raise RevisionError("criterion stage must be unit or document")
        require_text(criterion["description"], "criterion description")
        ids.add(uid)
        stages.add(criterion["stage"])
    if stages != {"unit", "document"}:
        raise RevisionError("register criteria for both unit and document checks")


def criteria_for(state, stage, plan=None):
    conditions = plan.get("criteria", {}) if isinstance(plan, dict) else {}
    if not isinstance(conditions, dict):
        conditions = {}
    result = []
    for definition in state["brief"]["criteria"]:
        if definition["stage"] == stage:
            item = {**definition, "condition": conditions.get(definition["id"])}
            result.append({**item, "version": digest(item)})
    return result


def validate_plan(plan, criteria):
    fields = {"problem", "evidence", "proposal", "preservation", "criteria"}
    if not isinstance(plan, dict) or set(plan) != fields:
        raise RevisionError("plan requires problem, evidence, proposal, preservation, and criteria; no results")
    for name in ("problem", "evidence", "proposal"):
        require_text(plan[name], name)
    if not isinstance(plan["preservation"], list) or not plan["preservation"]:
        raise RevisionError("plan preservation must be a nonempty list")
    for value in plan["preservation"]:
        require_text(value, "preservation")
    conditions = plan["criteria"]
    if not isinstance(conditions, dict) or set(conditions) != {c["id"] for c in criteria}:
        raise RevisionError("plan must bind every registered criterion ID to a completion condition")
    for value in conditions.values():
        require_text(value, "completion condition")


def set_plan(state, uid, expected, data):
    unit = unit_by_id(state, uid) if uid else None
    if unit:
        check_token(state, unit, expected)
    elif expected != document_token(state):
        raise RevisionError("stale document token")
    stage = "unit" if unit else "document"
    validate_plan(data, criteria_for(state, stage))
    previous = unit["plan"] if unit else state["document_plan"]
    if data == previous:
        return
    entry = {"action": "plan", "plan": copy.deepcopy(data)}
    if unit:
        unit["plan"] = copy.deepcopy(data)
        unit["history"].append(entry)
        unit["generation"] += 1
    else:
        state["document_plan"] = copy.deepcopy(data)
        state["document_history"].append(entry)
    touch(state, f"plan {uid or 'document'}")


def update_brief(state, brief):
    validate_brief(brief)
    old = {c["id"]: c for c in state["brief"]["criteria"]}
    new = {c["id"]: c for c in brief["criteria"]}
    if old.keys() - new.keys() or any(new[key]["stage"] != value["stage"] for key, value in old.items()):
        raise RevisionError("do not silently drop or move registered criteria; record not_applicable with a reason")
    if brief == state["brief"]:
        return
    state["events"].append({"reason": "previous brief", "brief": copy.deepcopy(state["brief"])})
    state["brief"] = copy.deepcopy(brief)
    invalidate(state, [u["id"] for u in state["units"]], "document brief/criteria changed",
               reopen_holds=False)


def extend_limit(state, maximum, reason):
    require_text(reason, "iteration limit extension reason")
    previous = state["max_iterations"]
    if type(maximum) is not int or maximum <= previous:
        raise RevisionError("new iteration limit must be an integer greater than the current limit")
    state["max_iterations"] = maximum
    touch(state, reason)
    state["events"][-1].update({"action": "extend_limit", "previous_max_iterations": previous,
                                "max_iterations": maximum})


def expression_definition(expression):
    return {key: expression[key] for key in ("id", "source", "goal", "origin", "owners")}


def expressions_for(state, unit):
    return layout(state)["expressions"].get(unit["id"], [])


def quote_position(text, quote, start=None):
    require_text(quote, "quote")
    if start is None:
        start = text.find(quote)
        if start < 0 or start != text.rfind(quote):
            raise RevisionError("quote must exist uniquely in the current text, or specify start")
    if type(start) is not int or start < 0 or text[start:start + len(quote)] != quote:
        raise RevisionError("quote does not exist at the specified position")
    return start


def register_expressions(state, uid, expected, entries):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    if not isinstance(entries, list) or not entries:
        raise RevisionError("register a nonempty list of expressions")
    additions = {}
    for entry in entries:
        fields = {"id", "quote", "goal", "origin"}
        if not isinstance(entry, dict) or set(entry) - {"start"} != fields:
            raise RevisionError("expression requires id, quote, goal, origin, and optional start; no results")
        eid = entry["id"]
        if (not isinstance(eid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", eid)
                or eid in additions or eid in state.get("expressions", {})):
            raise RevisionError("expression IDs must be new, unique stable identifiers")
        require_text(entry["goal"], "expression understanding goal")
        if not isinstance(entry["origin"], str) or entry["origin"] not in {"user", "inspection"}:
            raise RevisionError("expression origin must be user or inspection")
        start = quote_position(unit["text"], entry["quote"], entry.get("start"))
        additions[eid] = {"id": eid, "source": {"unit_id": uid, "quote": entry["quote"], "start": start},
                          "source_text": unit["text"], "goal": entry["goal"], "origin": entry["origin"],
                          "owners": [uid], "review": None, "history": [], "hold_active": False}
    state.setdefault("expressions", {}).update(additions)
    touch(state, f"registered expressions for {uid}")


def expression_by_id(state, eid):
    if eid not in state.get("expressions", {}):
        raise RevisionError(f"unknown expression: {eid}")
    return state["expressions"][eid]


def expression_version(state, expression):
    return digest({"definition": expression_definition(expression),
                   "owners": [content_token(state, unit_by_id(state, uid)) for uid in expression["owners"]]})


def expression_has_hold(expression):
    review = expression.get("review")
    return expression.get("hold_active", False) or bool(
        review and review["record"]["outcome"] == "held"
        and expression.get("hold_released_version") != review["version"])


def unit_has_hold(state, unit):
    return has_active_hold(unit) or any(expression_has_hold(e) for e in expressions_for(state, unit))


def expression_fingerprints(state, unit):
    return {e["id"]: digest(e["review"]) for e in expressions_for(state, unit)}


def validate_expression_record(state, record, *, stored=False):
    fields = {"outcome", "observation", "evidence"}
    if not isinstance(record, dict) or not fields <= record.keys():
        raise RevisionError("expression result requires outcome, observation, and current-text evidence")
    outcome = record["outcome"]
    if not isinstance(outcome, str) or outcome not in {"explained", "unchanged", "needs_revision", "held"}:
        raise RevisionError("invalid expression outcome")
    require_text(record["observation"], "expression observation")
    if record["observation"].strip().casefold().strip(".。!！") in OPAQUE:
        raise RevisionError("describe the expression check, not a bare OK")
    if outcome == "unchanged":
        fields.add("reason")
        require_text(record.get("reason"), "why the current explanation is sufficient")
    elif outcome == "held" or (outcome == "needs_revision" and
                               set(record) & {"reason", "proposal", "required_decision"}):
        fields.update({"reason", "proposal", "required_decision"})
        held_details(record)
    if set(record) != fields:
        raise RevisionError("unsupported or missing expression result fields")
    evidence = record["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise RevisionError("expression result requires quoted evidence in the current document")
    normalized = copy.deepcopy(record)
    normalized["evidence"] = []
    for item in evidence:
        if not isinstance(item, dict) or set(item) - {"start"} != {"unit_id", "token", "quote"}:
            raise RevisionError("evidence requires unit_id, token, quote, and optional start")
        unit = unit_by_id(state, item["unit_id"])
        if stored:
            if item["token"] != content_token(state, unit):
                raise RevisionError("stale expression evidence; retrieve the text and context again")
        elif item["token"] not in (content_token(state, unit), token(state, unit)):
            raise RevisionError("stale expression evidence; retrieve the text and context again")
        start = quote_position(unit["text"], item["quote"], item.get("start"))
        normalized["evidence"].append({**item, "token": content_token(state, unit), "start": start})
    return normalized


def expression_review_current(state, expression):
    review = expression["review"]
    if not review or review["version"] != expression_version(state, expression):
        return False
    try:
        validate_expression_record(state, review["record"], stored=True)
    except RevisionError:
        return False
    return True


def expression_statuses(state, unit):
    statuses = set()
    for expression in expressions_for(state, unit):
        if not expression_review_current(state, expression):
            raise RevisionError(f"{expression['id']}: current-version expression result is required")
        statuses.add(expression["review"]["record"]["outcome"])
    return statuses


def record_expression(state, eid, expected, record):
    expression = expression_by_id(state, eid)
    if expected != expression_version(state, expression):
        raise RevisionError("stale expression version; retrieve its owners and context again")
    if expression_has_hold(expression):
        raise RevisionError("held expression: invalidate an owner with new information/decision before recording")
    normalized = validate_expression_record(state, record)
    expression["review"] = {"version": expected, "record": normalized}
    expression["history"].append({"action": "review", **copy.deepcopy(expression["review"])})
    expression["hold_active"] = normalized["outcome"] == "held"
    expression["hold_released_version"] = None
    for uid in expression["owners"]:
        unit = unit_by_id(state, uid)
        unit["expression_review_revision"] = unit.get("expression_review_revision", 0) + 1
    state["final_review"] = None
    refresh(state)


def change_expression_goal(state, eid, expected, goal):
    expression = expression_by_id(state, eid)
    if expected != expression_version(state, expression):
        raise RevisionError("stale expression version")
    require_text(goal, "expression understanding goal")
    if goal != expression["goal"]:
        expression["history"].append({"action": "previous_goal", "goal": expression["goal"]})
        expression["goal"] = goal
        for uid in expression["owners"]:
            unit_by_id(state, uid)["generation"] += 1
        touch(state, f"expression goal changed: {eid}")


def expression_view(state, expression):
    return {**expression_definition(expression), "version": expression_version(state, expression),
            "hold_active": expression_has_hold(expression), "review": expression["review"],
            "review_current": expression_review_current(state, expression)}


def make_unit(state, text, kind, parents=()):
    uid = f"u{state['next_id']:04d}"
    state["next_id"] += 1
    return {"id": uid, "kind": kind, "initial_text": text, "text": text,
            "inherited_change": False,
            "generation": 0, "state": "pending", "attempts": 0, "awaiting_review": False,
            "hold_active": False,
            "notes": {}, "context_ids": [], "history": [], "review": None, "plan": None,
            "parents": list(parents)}


def initialize(original, working, brief, max_iterations=3):
    validate_brief(brief)
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise RevisionError("max_iterations must be a positive integer")
    state = {"format": 2, "original": original, "original_hash": digest(original),
             "initial_working": working, "brief": copy.deepcopy(brief),
             "revision": 1, "next_id": 1, "max_iterations": max_iterations,
             "units": [], "retired": [], "events": [], "final_review": None,
             "document_plan": None, "document_history": []}
    state["units"] = [make_unit(state, b["text"], b["kind"]) for b in blocks(working)]
    return state


def upgrade(state, brief, max_iterations=None):
    if not isinstance(state, dict) or state.get("format") != 1:
        raise RevisionError("upgrade requires a format 1 state")
    if digest(state["original"]) != state["original_hash"]:
        raise RevisionError("modified original snapshot")
    validate_brief(brief)
    limit = state["max_iterations"] if max_iterations is None else max_iterations
    if type(limit) is not int or limit < state["max_iterations"]:
        raise RevisionError("upgrade must preserve or explicitly increase the previous iteration limit")
    result = copy.deepcopy(state)
    result.update({"format": 2, "brief": copy.deepcopy(brief), "document_plan": None,
                   "document_history": [], "final_review": None, "max_iterations": limit})
    if state["final_review"]:
        result["document_history"].append({"action": "legacy_review", "review": copy.deepcopy(state["final_review"])})
    result["events"].append({"reason": "upgraded from format 1; previous brief",
                             "brief": copy.deepcopy(state["brief"]),
                             "previous_max_iterations": state["max_iterations"],
                             "max_iterations": limit})
    for unit in result["units"]:
        unit["hold_active"] = has_active_hold(unit)
        unit["history"].append({"action": "legacy_review", "review": copy.deepcopy(unit["review"])})
        unit["plan"], unit["review"], unit["state"] = None, None, "recheck"
        unit["generation"] += 1
    result["revision"] += 1
    validate_state(result)
    return result


def layout(state):
    """Build heading stacks and lookups once per document revision."""
    key = id(state)
    cached = LAYOUTS.get(key)
    if cached is not None and cached["state"] is state and cached["revision"] == state["revision"]:
        LAYOUTS.move_to_end(key)
        return cached
    cached = {"state": state, "revision": state["revision"], "units": {}, "indices": {},
              "headings": {}, "expressions": {}}
    heading_blocks, offset = [], 0
    for block in blocks(render(state)):
        offset += len(block["text"])
        if block["kind"] == "heading":
            heading_blocks.append((offset, block))
    headings, heading_index, position = [], 0, 0
    for index, unit in enumerate(state["units"]):
        while heading_index < len(heading_blocks) and heading_blocks[heading_index][0] <= position:
            block = heading_blocks[heading_index][1]
            level = block["level"]
            headings = [h for h in headings if h["level"] < level]
            headings.append({"level": level, "text": block["text"]})
            heading_index += 1
        uid = unit["id"]
        cached["units"][uid], cached["indices"][uid] = unit, index
        cached["headings"][uid] = list(headings)
        position += len(unit["text"])
    for expression in state.get("expressions", {}).values():
        for uid in expression["owners"]:
            cached["expressions"].setdefault(uid, []).append(expression)
    LAYOUTS[key] = cached
    LAYOUTS.move_to_end(key)
    while len(LAYOUTS) > 8:
        LAYOUTS.popitem(last=False)
    return cached


def unit_by_id(state, uid):
    try:
        return layout(state)["units"][uid]
    except KeyError:
        raise RevisionError(f"unknown active unit: {uid}") from None


def render(state):
    return "".join(u["text"] for u in state["units"])


def context(state, unit):
    units = state["units"]
    current = layout(state)
    index = current["indices"][unit["id"]]
    headings = copy.deepcopy(current["headings"][unit["id"]])
    refs = [{"id": uid, "text": unit_by_id(state, uid)["text"]} for uid in unit["context_ids"]]
    result = {"brief": state["brief"], "headings": headings,
            "before": units[index - 1]["text"] if index else "",
            "after": units[index + 1]["text"] if index + 1 < len(units) else "",
            "related": refs, "notes": unit["notes"]}
    expressions = expressions_for(state, unit)
    if expressions:
        result["expressions"] = [expression_definition(e) for e in expressions]
    return result


def content_token(state, unit):
    return digest({"id": unit["id"], "text": unit["text"], "generation": unit["generation"],
                   "context": context(state, unit), "plan": unit["plan"]})


def token(state, unit):
    content = content_token(state, unit)
    # Keep legacy tokens valid until an expression result is saved. Expression
    # evidence depends on content, while aggregate reviews also depend on results.
    if unit.get("expression_review_revision", 0):
        return digest({"content": content, "expression_review_revision": unit["expression_review_revision"]})
    return content


def refresh(state):
    for unit in state["units"]:
        review = unit.get("review")
        if unit["state"] in TERMINAL and (not isinstance(review, dict) or
                review.get("token") != token(state, unit)
                or review.get("expression_reviews", {}) != expression_fingerprints(state, unit)
                or any(not expression_review_current(state, e) for e in expressions_for(state, unit))):
            unit["state"] = "recheck"
            unit["awaiting_review"] = False


def validate_state(state):
    if not isinstance(state, dict):
        raise RevisionError("state must be an object")
    LAYOUTS.pop(id(state), None)
    if state.get("format") != 2:
        raise RevisionError("unsupported state format; upgrade format 1 with a new brief and registered criteria")
    if digest(state["original"]) != state["original_hash"]:
        raise RevisionError("modified original snapshot")
    validate_brief(state["brief"])
    ids = [u["id"] for u in state["units"]]
    if len(ids) != len(set(ids)):
        raise RevisionError("duplicate active unit identifiers")
    for unit in state["units"]:
        if unit["state"] not in STATES or not isinstance(unit["text"], str):
            raise RevisionError("invalid unit state")
        if any(uid not in ids or uid == unit["id"] for uid in unit["context_ids"]):
            raise RevisionError("invalid context reference")
    expressions = state.get("expressions", {})
    if not isinstance(expressions, dict):
        raise RevisionError("expressions must be a registry object")
    all_ids = set(ids) | {u["id"] for u in state["retired"]}
    for eid, expression in expressions.items():
        if not isinstance(expression, dict):
            raise RevisionError("expression must be an object")
        if expression["id"] != eid or not isinstance(eid, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", eid):
            raise RevisionError("invalid expression identifier")
        require_text(expression["goal"], "expression understanding goal")
        source = expression["source"]
        if not isinstance(source, dict) or not isinstance(expression["source_text"], str):
            raise RevisionError("expression source requires a text snapshot and location")
        if source["unit_id"] not in all_ids:
            raise RevisionError("expression source lost from active/retired history")
        quote_position(expression["source_text"], source["quote"], source["start"])
        owners = expression["owners"]
        if not isinstance(owners, list) or not owners or len(set(owners)) != len(owners) or set(owners) - set(ids):
            raise RevisionError("expression must retain active owners")
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
                   "brief": state["brief"], "plan": state["document_plan"],
                   "units": [token(state, u) for u in state["units"]]})


def touch(state, reason, *, previous_contexts=None):
    state["revision"] += 1
    state["final_review"] = None
    state["events"].append({"revision": state["revision"], "reason": reason})
    if previous_contexts:
        for unit in state["units"]:
            previous = previous_contexts.get(unit["id"])
            if previous is not None and previous != digest(context(state, unit)):
                unit["generation"] += 1
    refresh(state)


def check_token(state, unit, expected):
    if expected != token(state, unit):
        raise RevisionError("stale unit token: retrieve the target and its context again")


def has_active_hold(unit):
    review = unit.get("review")
    record = review.get("record", review) if isinstance(review, dict) else {}
    checks = record.get("checks", []) if isinstance(record, dict) else []
    previous_hold = (isinstance(record, dict) and record.get("outcome") == "held") or (
        isinstance(checks, list) and any(isinstance(check, dict) and check.get("status") == "held"
                                        for check in checks))
    released_token = unit.get("hold_released_token")
    released = (released_token is not None and isinstance(review, dict)
                and released_token == review.get("token"))
    return unit["state"] == "held" or unit.get("hold_active", False) or (previous_hold and not released)


def view(state, unit):
    original = unit["notes"].get("original_lines")
    if original:
        lines = source_lines(state["original"])
        original_text = "".join(lines[original[0] - 1:original[1]])
        original_scope = "specified_lines"
    elif (unit["initial_text"] in state["original"] and
          state["original"].find(unit["initial_text"]) == state["original"].rfind(unit["initial_text"])):
        original_text, original_scope = unit["initial_text"], "matching_excerpt"
    else:
        original_text, original_scope = state["original"], "whole_document"
    return {"target": {"id": unit["id"], "kind": unit["kind"], "token": token(state, unit),
                       "content_token": content_token(state, unit),
                       "text": unit["text"], "initial_working_text": unit["initial_text"],
                       "original_text": original_text, "original_scope": original_scope,
                       "state": unit["state"], "hold_active": unit_has_hold(state, unit),
                       "attempts": unit["attempts"],
                       "max_iterations": state["max_iterations"], "plan": unit["plan"],
                       "required_criteria": criteria_for(state, "unit", unit["plan"])},
            "required_expressions": [expression_view(state, e) for e in expressions_for(state, unit)],
            "reference_only": context(state, unit), "previous_review": unit["review"]}


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
    document_hold = current_document_hold(state)
    return {"document_token": document_token(state), "revision": state["revision"],
            "max_iterations": state["max_iterations"],
            "document_plan": state["document_plan"],
            "document_hold_active": bool(document_hold), "document_hold": copy.deepcopy(document_hold),
            "document_criteria": criteria_for(state, "document", state["document_plan"]),
            "expressions": [expression_view(state, e) for e in state.get("expressions", {}).values()],
            "counts": dict(Counter(u["state"] for u in state["units"])),
            "units": [{"id": u["id"], "kind": u["kind"], "state": u["state"],
                       "hold_active": unit_has_hold(state, u),
                       "token": token(state, u), "attempts": u["attempts"]} for u in state["units"]],
            "final_review_current": bool(state["final_review"] and
                                         state["final_review"]["token"] == document_token(state))}


def next_target(state):
    refresh(state)
    unit = next((u for u in state["units"] if u["state"] not in TERMINAL and not unit_has_hold(state, u)), None)
    return view(state, unit) if unit else {"target": None, **status(state)}


def migrate_release(state, uid, expected, review_token, evidence):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    require_text(evidence, "evidence of the previous explicit invalidation")
    review = unit.get("review")
    if ("hold_released_token" in unit or unit.get("hold_active", False) is not False
            or unit["state"] != "recheck" or not has_active_hold(unit)):
        raise RevisionError("release migration requires a legacy, cleared hold without release metadata")
    if not isinstance(review, dict) or not review_token or review.get("token") != review_token:
        raise RevisionError("stale held review token")
    if expected == review_token:
        raise RevisionError("the held review is still current; use invalidate with new information/decision")
    unit["hold_active"] = False
    unit["hold_released_token"] = review_token
    unit["history"].append({"action": "migrate_release", "token": expected, "review_token": review_token,
                            "evidence": evidence})
    touch(state, f"migrated previous explicit hold release for {uid}")


def annotate(state, uid, expected, data):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    allowed = {"findings", "context_note", "original_lines", "context_ids"}
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
                not 1 <= bounds[0] <= bounds[1] <= len(source_lines(state["original"]))):
            raise RevisionError("original_lines must be an inclusive range in the original snapshot")
    unit["context_ids"] = list(dict.fromkeys(refs))
    unit["history"].append({"action": "annotate", "notes": copy.deepcopy(unit["notes"])})
    unit["notes"].update({k: copy.deepcopy(v) for k, v in data.items() if k != "context_ids"})
    unit["generation"] += 1
    touch(state, f"annotate {uid}")


def invalidate(state, ids, reason, *, reopen_holds=True):
    require_text(reason, "invalidation reason")
    targets = [unit_by_id(state, uid) for uid in ids]
    if not targets and state["units"]:
        raise RevisionError("invalidate requires at least one affected target")
    target_ids = {unit["id"] for unit in targets}
    document_hold = current_document_hold(state) if reopen_holds else None
    for unit in targets:
        unit["generation"] += 1
        unit["state"] = "recheck"
        if reopen_holds:
            unit["hold_active"] = False
            review = unit.get("review")
            unit["hold_released_token"] = review.get("token") if isinstance(review, dict) else None
    if reopen_holds:
        for expression in state.get("expressions", {}).values():
            if set(expression["owners"]) & target_ids:
                expression["hold_active"] = False
                review = expression["review"]
                expression["hold_released_version"] = review["version"] if review else None
        if document_hold:
            state["document_history"].append({"action": "release_hold", "review_token": document_hold["token"],
                                              "reason": reason})
    touch(state, reason)


def edit(state, uid, expected, text, affected=(), all_affected=False):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    if text == unit["text"]:
        return
    validate_plan(unit["plan"], criteria_for(state, "unit"))
    if unit_has_hold(state, unit):
        raise RevisionError("held unit: invalidate with the new information/decision before editing")
    if unit["attempts"] >= state["max_iterations"]:
        raise RevisionError("iteration limit reached: record a held result with remaining issue and proposal")
    if unit["awaiting_review"]:
        raise RevisionError("record the check of the last edit before editing again")
    impacted = [unit_by_id(state, item) for item in affected]
    proposed = [{**u, "text": text} if u is unit else u for u in state["units"]]
    if split_structure_shapes(state["units"]) != split_structure_shapes(proposed):
        raise RevisionError("preserve split table/list structure; merge the whole structure before changing it")
    if all_affected:
        impacted = state["units"]
    changed_ids = {uid, *(other["id"] for other in impacted)}
    previous_contexts = {other["id"]: digest(context(state, other)) for other in state["units"]
                         if other["id"] not in changed_ids}
    unit["history"].append({"action": "edit", "text": unit["text"], "token": expected})
    unit["text"], unit["state"], unit["awaiting_review"] = text, "recheck", True
    unit["generation"] += 1
    for other in impacted:
        if other is not unit:
            other["generation"] += 1
    touch(state, f"edit {uid}", previous_contexts=previous_contexts)


def held_details(record):
    for name in ("reason", "proposal", "required_decision"):
        require_text(record.get(name), name)


def validate_checks(checks, criteria):
    if not isinstance(checks, list):
        raise RevisionError("checks must be a list covering every registered criterion")
    expected = {c["id"]: c for c in criteria}
    seen = set()
    for check in checks:
        fields = {"criterion_id", "criterion_version", "status", "observation"}
        if not isinstance(check, dict) or fields - check.keys():
            raise RevisionError("each check requires criterion_id, criterion_version, status, and observation")
        uid = check["criterion_id"]
        if not isinstance(uid, str) or uid not in expected or uid in seen:
            raise RevisionError("unknown or duplicate criterion ID")
        seen.add(uid)
        if check["criterion_version"] != expected[uid]["version"]:
            raise RevisionError(f"{uid}: stale criterion version")
        status_value = check["status"]
        if not isinstance(status_value, str) or status_value not in {"passed", "needs_revision", "held", "not_applicable"}:
            raise RevisionError("invalid criterion status")
        require_text(check["observation"], "observation")
        if check["observation"].strip().casefold().strip(".。!！") in OPAQUE:
            raise RevisionError("bare OK/change labels are not review observations")
        if status_value == "not_applicable":
            fields.add("reason")
            require_text(check.get("reason"), "not_applicable reason")
        elif status_value == "held" or (status_value == "needs_revision" and
                                       set(check) & {"reason", "proposal", "required_decision"}):
            fields.update({"reason", "proposal", "required_decision"})
            held_details(check)
        if set(check) != fields:
            raise RevisionError("unsupported or missing check fields")
    if seen != expected.keys():
        raise RevisionError("missing results for registered criteria: " + ", ".join(sorted(expected.keys() - seen)))


def validate_record(record, criteria, expression_outcomes=()):
    if not isinstance(record, dict) or set(record) != {"outcome", "checks"}:
        raise RevisionError("result input requires only outcome and checks; criteria belong in the plan")
    outcome = record["outcome"]
    if not isinstance(outcome, str) or outcome not in {"confirmed", "unchanged", "needs_revision", "held"}:
        raise RevisionError("invalid outcome")
    validate_checks(record["checks"], criteria)
    statuses = {check["status"] for check in record["checks"]} | set(expression_outcomes)
    if outcome in {"confirmed", "unchanged"} and statuses & {"needs_revision", "held"}:
        raise RevisionError("unresolved criteria cannot be confirmed or unchanged")
    if outcome == "needs_revision" and "needs_revision" not in statuses:
        raise RevisionError("needs_revision requires a criterion that still needs revision")
    if outcome == "held" and ("held" not in statuses or "needs_revision" in statuses):
        raise RevisionError("held requires documented held results for all unresolved criteria")


def record_review(state, uid, expected, record):
    unit = unit_by_id(state, uid)
    check_token(state, unit, expected)
    if has_active_hold(unit):
        raise RevisionError("held unit: invalidate with the new information/decision before recording")
    validate_plan(unit["plan"], criteria_for(state, "unit"))
    criteria = criteria_for(state, "unit", unit["plan"])
    expression_outcomes = expression_statuses(state, unit)
    validate_record(record, criteria, expression_outcomes)
    outcome = record["outcome"]
    if unit["attempts"] >= state["max_iterations"] and outcome != "held":
        raise RevisionError("iteration limit reached: only a documented hold may be recorded")
    if outcome == "unchanged" and (unit["text"] != unit["initial_text"] or unit["inherited_change"]):
        raise RevisionError("modified text cannot be recorded as unchanged")
    attempts = unit["attempts"] + 1
    if outcome == "needs_revision" and attempts >= state["max_iterations"]:
        outcome = "held"
    saved = copy.deepcopy(record)
    if outcome == "held":
        for check in saved["checks"]:
            if check["status"] == "needs_revision":
                held_details(check)
                check["status"] = "held"
    saved["outcome"] = outcome
    validate_record(saved, criteria, expression_outcomes)
    review = {"token": expected, "record": saved, "criteria": criteria,
              "expression_reviews": expression_fingerprints(state, unit),
              "iteration": attempts, "limit_reached": attempts >= state["max_iterations"]}
    unit["attempts"] = attempts
    unit["review"] = review
    unit["history"].append({"action": "review", **copy.deepcopy(review)})
    unit["awaiting_review"] = False
    unit["hold_active"] = "held" in expression_outcomes or any(check["status"] == "held" for check in saved["checks"])
    unit["hold_released_token"] = None
    unit["state"] = "recheck" if outcome == "needs_revision" else outcome
    state["final_review"] = None


def inline_signature(parts, environment):
    """Compare inline syntax without treating plain-text split boundaries as changes."""
    result = []
    for part in parts:
        for node in SPLIT_MARKDOWN.parse(markdown_source(part), copy.deepcopy(environment)):
            if node.type != "inline":
                continue
            for child in node.children or []:
                if child.type == "softbreak":
                    continue
                if child.type == "text":
                    content = "".join(child.content.split())
                    if not content:
                        continue
                    if result and result[-1][0] == "text":
                        result[-1] = ("text", result[-1][1] + content)
                    else:
                        result.append(("text", content))
                else:
                    result.append((child.type, child.tag, child.nesting,
                                   child.attrs, child.content, child.markup))
    return result


def safe_cuts(text, cuts, kind, environment):
    lines = source_lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    line_starts = {offset: index for index, offset in enumerate(offsets)}
    quoted = []
    for node in SPLIT_MARKDOWN.parse(markdown_source(text), copy.deepcopy(environment)):
        if node.map is None:
            continue
        first, last = node.map
        start, end = offsets[first], offsets[last]
        inside = [cut for cut in cuts if start < cut < end]
        if node.type in {"fence", "code_block", "heading_open", "html_block", "hr", "definition"} and inside:
            raise RevisionError("keep code blocks, headings, HTML blocks, thematic breaks, and link definitions together")
        if node.type == "inline" and inside:
            boundaries = [start, *inside, end]
            parts = [text[first:last] for first, last in zip(boundaries, boundaries[1:])]
            if inline_signature([text[start:end]], environment) != inline_signature(parts, environment):
                raise RevisionError("keep inline Markdown such as code spans and links together")
        if node.type == "list_item_open" and inside:
            raise RevisionError("split lists only between complete top-level items")
        if node.type == "table_open":
            if inside and kind not in {"table", "table_rows", "quote"}:
                raise RevisionError("separate the whole table before splitting its rows")
            header_end = offsets[min(first + 2, last)]
            if any(cut not in line_starts or cut < header_end for cut in inside):
                raise RevisionError("split tables between rows; keep column names and delimiter together")
        if node.type == "blockquote_open":
            quoted.append((start, end))
    if kind == "table_rows" and any(cut not in line_starts for cut in cuts):
        raise RevisionError("split tables only between complete rows")
    for cut in cuts:
        depth = sum(start < cut < end for start, end in quoted)
        if not depth:
            continue
        if cut not in line_starts or line_starts[cut] == len(lines):
            raise RevisionError("split quotes only before complete quoted lines")
        line, prefixes = lines[line_starts[cut]].expandtabs(4), 0
        match = re.match(r"^ {0,3}> ?", line)
        while match:
            prefixes += 1
            line = line[match.end():]
            match = re.match(r"^ {0,3}> ?", line)
        if prefixes < depth:
            raise RevisionError("keep lazy quote continuations with their paragraph")



def check_regroupable(state, units):
    for unit in units:
        if unit_has_hold(state, unit):
            raise RevisionError("held unit: invalidate with the new information/decision before regrouping")
        if unit["awaiting_review"]:
            raise RevisionError("record the check of the last edit before regrouping")


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
    for expression in state.get("expressions", {}).values():
        expression["owners"] = list(dict.fromkeys(
            uid for owner in expression["owners"]
            for uid in ([u["id"] for u in new] if owner in ids else [owner])))
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
    check_regroupable(state, [unit])
    if (not isinstance(parts, list) or len(parts) < 2 or
            any(not isinstance(p, str) or not p for p in parts) or "".join(parts) != unit["text"]):
        raise RevisionError("split parts must be nonempty and concatenate exactly to the current target")
    cuts, offset = [], 0
    for part in parts[:-1]:
        offset += len(part)
        cuts.append(offset)
    environment = {}
    document = render(state)
    MARKDOWN.parse(markdown_source(document), environment)
    position = state["units"].index(unit)
    start = sum(len(u["text"]) for u in state["units"][:position])
    safe_cuts(document, [start + cut for cut in cuts], unit["kind"], environment)
    safe_cuts(unit["text"], cuts, unit["kind"], environment)
    children = []
    neighbors = [u["id"] for u in state["units"][max(0, position - 1):position + 2]
                 if u is not unit]
    for part in parts:
        candidates = blocks(part)
        kind = candidates[0]["kind"] if len(candidates) == 1 else "group"
        child = make_unit(state, part, kind, [uid])
        child["notes"] = copy.deepcopy(unit["notes"])
        child["plan"] = copy.deepcopy(unit["plan"])
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
    elif unit["kind"] == "quote":
        # Keep a quoted table's column names (and any quoted introduction) in context.
        for child in children[1:]:
            child["context_ids"].append(children[0]["id"])
    replace_units(state, [unit], children, f"split {uid}")
    return [u["id"] for u in children]


def merge_units(state, ids):
    if len(ids) < 2 or len(ids) != len(set(ids)):
        raise RevisionError("merge requires at least two distinct units")
    old = [unit_by_id(state, uid) for uid in ids]
    positions = [state["units"].index(u) for u in old]
    if positions != list(range(positions[0], positions[0] + len(old))):
        raise RevisionError("merge requires contiguous units in document order")
    check_regroupable(state, old)
    text = "".join(u["text"] for u in old)
    candidates = blocks(text)
    kind = candidates[0]["kind"] if len(candidates) == 1 else "group"
    if all(u["kind"] == "table_rows" for u in old):
        kind = "table_rows"
    new = make_unit(state, text, kind, ids)
    new["notes"]["inherited"] = [copy.deepcopy(u["notes"]) for u in old]
    new["notes"]["inherited_plans"] = [copy.deepcopy(u["plan"]) for u in old]
    if all(u["plan"] for u in old):
        new["plan"] = {
            name: "\n".join(dict.fromkeys(u["plan"][name] for u in old))
            for name in ("problem", "evidence", "proposal")}
        new["plan"]["preservation"] = list(dict.fromkeys(
            item for u in old for item in u["plan"]["preservation"]))
        criterion_ids = {c["id"] for c in criteria_for(state, "unit")}
        if all(set(u["plan"]["criteria"]) == criterion_ids for u in old):
            new["plan"]["criteria"] = {
                key: "\n".join(dict.fromkeys(u["plan"]["criteria"][key] for u in old))
                for key in sorted(criterion_ids)}
        else:
            new["plan"] = None
    new["context_ids"] = list(dict.fromkeys(r for u in old for r in u["context_ids"] if r not in ids))
    new["attempts"] = max(u["attempts"] for u in old)
    new["inherited_change"] = any(u["inherited_change"] or u["text"] != u["initial_text"] for u in old)
    replace_units(state, old, [new], f"merge {','.join(ids)}")
    return new["id"]


def split_structure_shapes(units):
    """Keep shared table/list membership and item/cell structure across edits."""
    text = "".join(unit["text"] for unit in units)
    lines = source_lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    starts, ends, position = [], [], 0
    for unit in units:
        starts.append(position)
        position += len(unit["text"])
        ends.append(position)

    @lru_cache(maxsize=None)
    def owners(first, last):
        start, end = offsets[first], offsets[last]
        return tuple(units[i]["id"] for i in range(bisect_right(ends, start), bisect_left(starts, end))
                     if starts[i] < ends[i])

    parents = {"table_open", "bullet_list_open", "ordered_list_open"}
    tags = {"table", "thead", "tbody", "tr", "th", "td", "ul", "ol", "li"}
    result, maps, active, root_level = [], [], None, None
    for node in syntax(text):
        bounds = node.map if node.map is not None else (maps[-1] if maps else None)
        if active is None and node.type in parents and len(owners(*bounds)) > 1:
            active, root_level = [], node.level
        if active is not None and node.tag in tags and node.nesting:
            # The parser pads/truncates table cells, so also retain source separators.
            separators = len(re.findall(r"(?<!\\)\|", lines[bounds[0]])) if node.type == "tr_open" else None
            active.append((node.type, owners(*bounds), tuple(sorted(node.attrs.items())), separators))
        if node.nesting == 1:
            maps.append(bounds)
        elif node.nesting == -1:
            maps.pop()
            if active is not None and node.level == root_level:
                result.append(tuple(active))
                active = None
    return result


def table_and_list_spans(units):
    text = "".join(unit["text"] for unit in units)
    lines = source_lines(text)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    ranges, offset = [], 0
    for unit in units:
        ranges.append((unit["id"], offset, offset + len(unit["text"])))
        offset += len(unit["text"])
    result = []
    for node in syntax(text):
        if node.type not in {"table_open", "bullet_list_open", "ordered_list_open"}:
            continue
        start, end = (offsets[line] for line in node.map)
        result.append((node.type, tuple((uid, max(start - first, 0), min(end, last) - first)
                                       for uid, first, last in ranges if first < end and start < last)))
    return result


def reorder(state, ids):
    current = [u["id"] for u in state["units"]]
    if len(ids) != len(current) or set(ids) != set(current) or len(ids) != len(set(ids)):
        raise RevisionError("reorder must contain every active identifier exactly once")
    if ids == current:
        return
    positions = {uid: index for index, uid in enumerate(ids)}
    spans = table_and_list_spans(state["units"])
    for kind, entries in spans:
        members = [positions[entry[0]] for entry in entries]
        if members != list(range(members[0], members[0] + len(members))):
            structure = "table headers and rows" if kind == "table_open" else "list items"
            raise RevisionError(f"keep split {structure} together in order; merge the structure before reordering its parts")
    ordered = [unit_by_id(state, uid) for uid in ids]
    if Counter(spans) != Counter(table_and_list_spans(ordered)):
        raise RevisionError("reorder must preserve table and list boundaries")
    state["units"] = ordered
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
            validate_plan(unit["plan"], criteria_for(state, "unit"))
            criteria = criteria_for(state, "unit", unit["plan"])
            validate_record(review["record"], criteria, expression_statuses(state, unit))
            if review["criteria"] != criteria or review["record"]["outcome"] != unit["state"]:
                raise RevisionError("record does not match current criteria/state")
            if review.get("expression_reviews", {}) != expression_fingerprints(state, unit):
                raise RevisionError("expression results changed after the unit check")
        except RevisionError as error:
            issues.append(f"{uid}: {error}")
    return issues


def validate_final_record(state, record):
    if not isinstance(record, dict) or set(record) != {"original_comparison", "standalone_read", "checks"}:
        raise RevisionError("full-document result must be an object with original_comparison, standalone_read, checks")
    validate_plan(state["document_plan"], criteria_for(state, "document"))
    for name in ("original_comparison", "standalone_read"):
        require_text(record.get(name), name)
        if record[name].strip().casefold().strip(".。") in OPAQUE:
            raise RevisionError("describe the full-document check, not a bare OK")
    criteria = criteria_for(state, "document", state["document_plan"])
    validate_checks(record["checks"], criteria)
    if any(check["status"] == "needs_revision" for check in record["checks"]):
        raise RevisionError("return affected targets to recheck before final completion")
    return criteria


def current_document_hold(state):
    records = [*reversed(state["document_history"]), state["final_review"]]
    latest = next((record for record in records if record and "record" in record
                   and any(check["status"] == "held" for check in record["record"]["checks"])), None)
    if latest and not any(entry.get("action") == "release_hold" and entry["review_token"] == latest["token"]
                          and isinstance(entry.get("reason"), str) and entry["reason"].strip()
                          for entry in state["document_history"]):
        return latest
    return None


def final_review(state, expected, record):
    if expected != document_token(state):
        raise RevisionError("stale document token")
    if current_document_hold(state):
        raise RevisionError("held document: invalidate affected targets with new information/decision before recording")
    issues = blocking_issues(state)
    if issues:
        raise RevisionError("unfinished targets: " + "; ".join(issues))
    criteria = validate_final_record(state, record)
    state["final_review"] = {"record": copy.deepcopy(record), "token": expected, "criteria": criteria}
    state["document_history"].append({"action": "review", **copy.deepcopy(state["final_review"])})


def finish(state):
    issues = blocking_issues(state)
    if issues:
        raise RevisionError("unfinished targets: " + "; ".join(issues))
    if not state["final_review"] or state["final_review"]["token"] != document_token(state):
        raise RevisionError("current-version full-document review is required")
    full = state["final_review"]
    if current_document_hold(state) and not any(check["status"] == "held" for check in full["record"]["checks"]):
        raise RevisionError("held document: invalidate affected targets before resolving the full-document hold")
    if full["criteria"] != validate_final_record(state, full["record"]):
        raise RevisionError("full-document criteria changed")
    held = [{"id": u["id"], "plan": u["plan"], **u["review"]} for u in state["units"]
            if u["state"] == "held"]
    if any(check["status"] == "held" for check in full["record"]["checks"]):
        held.append({"id": "document", "plan": state["document_plan"], **full})
    expressions = [expression_view(state, e) for e in state.get("expressions", {}).values()]
    for expression in expressions:
        if not expression["review_current"]:
            raise RevisionError(f"{expression['id']}: current-version expression result is required")
        if expression["review"]["record"]["outcome"] == "held":
            held.append({"kind": "expression", **expression})
    return {"text": render(state), "document_token": document_token(state),
            "brief": state["brief"], "fully_resolved": not held, "held": held, "final_review": full,
            "expressions": expressions}


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--state", required=True, type=Path)
    sub = cli.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--original", required=True, type=Path)
    init.add_argument("--working", type=Path)
    init.add_argument("--brief", required=True, type=Path)
    init.add_argument("--max-iterations", type=int, default=3)
    migrate = sub.add_parser("upgrade")
    migrate.add_argument("--brief", required=True, type=Path)
    migrate.add_argument("--max-iterations", type=int)
    extend = sub.add_parser("extend-limit")
    extend.add_argument("--max-iterations", required=True, type=int)
    extend.add_argument("--reason", required=True)
    release = sub.add_parser("migrate-release")
    release.add_argument("--unit", required=True)
    release.add_argument("--token", required=True)
    release.add_argument("--review-token", required=True)
    release.add_argument("--evidence", required=True)
    sub.add_parser("next")
    sub.add_parser("status")
    show = sub.add_parser("show")
    pick = show.add_mutually_exclusive_group(required=True)
    pick.add_argument("--unit")
    pick.add_argument("--history")
    pick.add_argument("--document", choices=["original", "initial_working", "working"])
    plan = sub.add_parser("plan")
    plan_target = plan.add_mutually_exclusive_group(required=True)
    plan_target.add_argument("--unit")
    plan_target.add_argument("--document", action="store_true")
    plan.add_argument("--token", required=True)
    plan.add_argument("--file", required=True, type=Path)
    expressions = sub.add_parser("expressions")
    expressions.add_argument("--unit", required=True)
    expressions.add_argument("--token", required=True)
    expressions.add_argument("--file", required=True, type=Path)
    expression_check = sub.add_parser("expression-check")
    expression_check.add_argument("--id", required=True)
    expression_check.add_argument("--version", required=True)
    expression_check.add_argument("--file", required=True, type=Path)
    expression_goal = sub.add_parser("expression-goal")
    expression_goal.add_argument("--id", required=True)
    expression_goal.add_argument("--version", required=True)
    expression_goal.add_argument("--goal", required=True)
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
        elif args.command == "upgrade":
            if args.state.is_symlink():
                raise RevisionError("state must be a regular file, not a symbolic link")
            state = upgrade(read_json(args.state), read_json(args.brief), args.max_iterations)
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
            elif command == "plan":
                set_plan(state, args.unit, args.token, read_json(args.file))
                result = view(state, unit_by_id(state, args.unit)) if args.unit else status(state)
            elif command == "record":
                record_review(state, args.unit, args.token, read_json(args.file))
                result = status(state)
            elif command == "expressions":
                register_expressions(state, args.unit, args.token, read_json(args.file))
                result = view(state, unit_by_id(state, args.unit))
            elif command == "expression-check":
                record_expression(state, args.id, args.version, read_json(args.file))
                result = expression_view(state, expression_by_id(state, args.id))
            elif command == "expression-goal":
                change_expression_goal(state, args.id, args.version, args.goal)
                result = expression_view(state, expression_by_id(state, args.id))
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
                update_brief(state, read_json(args.file))
                result = status(state)
            elif command == "extend-limit":
                extend_limit(state, args.max_iterations, args.reason)
                result = status(state)
            elif command == "migrate-release":
                migrate_release(state, args.unit, args.token, args.review_token, args.evidence)
                result = view(state, unit_by_id(state, args.unit))
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
                write_many_atomic(outputs, new_mode=0o666)
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
