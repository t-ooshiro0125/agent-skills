import copy
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "revision.py"
SPEC = importlib.util.spec_from_file_location("revision", SCRIPT)
revision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(revision)

BRIEF = {"purpose": "手順を正しく選ぶ", "reader": "新任の担当者",
         "reader_difficulties": "対象と適用条件の意味を読み取りにくい",
         "desired_understanding": "対象と条件を理解し、実行してよい手順を選べる",
         "scope": "全文", "allowed_changes": "事実と条件を保った整理",
         "criteria": [
             {"id": "meaning", "stage": "unit", "description": "意味と条件を保持する"},
             {"id": "clarity", "stage": "unit", "description": "読者が対象と条件を判断できる"},
             {"id": "coherence", "stage": "document", "description": "全文の説明がつながる"}]}
TICKS = chr(96) * 3
DOCUMENT = (
    "\ufeff# 作業の選択\r\n\r\n"
    "利用条件は [規約](https://example.test/rules?a=1&b=2) を参照する。\r\n\r\n"
    "| 対象 | 条件 |\r\n| --- | --- |\r\n| 写真 | 許可済み |\r\n| 履歴 | 要確認 |\r\n\r\n"
    "準備できた場合だけ次を実行する。\r\n\r\n"
    "1. 保存する。\r\n   - 元データを残す。\r\n\r\n"
    "2. 確認する。\r\n\r\n"
    + TICKS + "sh\r\n# 見出しではない\r\n\r\nprintf 'a|b'\r\n" + TICKS + "\r\n\r\n"
    "補足\r\n----\r\n\r\n末尾に改行なし"
)


def state(text="最初の説明。\n\n次の説明。\n\n最後の説明。\n", limit=3):
    value = revision.initialize(text, text, BRIEF, limit)
    for unit in value["units"]:
        ensure_plan(value, unit)
    return value


def fixture_plan(value, stage="unit"):
    return {"problem": "依頼の観点で対象を点検する",
            "evidence": "原文に対象と条件が記載されている",
            "proposal": "意味を保ち、必要な対象・条件を明示する",
            "preservation": ["対象と適用条件を維持する"],
            "criteria": {c["id"]: c["description"] for c in revision.criteria_for(value, stage)}}


def ensure_plan(value, unit=None):
    if unit is not None and unit["plan"] is None:
        revision.set_plan(value, unit["id"], revision.token(value, unit), fixture_plan(value))
    elif unit is None and value["document_plan"] is None:
        revision.set_plan(value, None, revision.document_token(value), fixture_plan(value, "document"))


def result_for(value, unit=None, outcome="unchanged"):
    stage = "unit" if unit is not None else "document"
    plan = unit["plan"] if unit is not None else value["document_plan"]
    checks = [{"criterion_id": c["id"], "criterion_version": c["version"],
               "status": "passed", "observation": "本文に対象と適用条件が残っている"}
              for c in revision.criteria_for(value, stage, plan)]
    if outcome in {"held", "needs_revision"}:
        checks[0]["status"] = outcome
    if outcome == "held":
        checks[0].update({"reason": "対象の上限値が原文にない",
                         "proposal": "上限を確認後、「一日あたり○件」と追記する",
                         "required_decision": "担当者に上限値の根拠と説明追加の採否を確認する"})
    return {"outcome": outcome, "checks": checks}


def record(outcome="unchanged", **extra):
    dummy = {"brief": BRIEF, "document_plan": None}
    unit = {"plan": fixture_plan(dummy)}
    value = result_for(dummy, unit, outcome)
    value.update(extra)
    return value


def hold():
    return record("held")


def accept_all(value):
    for unit in value["units"]:
        ensure_plan(value, unit)
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))


def final(value):
    ensure_plan(value)
    revision.final_review(value, revision.document_token(value), {
        "original_comparison": "全文を原文と照合し、数値・条件・リンク・断定を維持した",
        "standalone_read": "修正文だけを読み、対象の選択から条件と手順へ進めることを確認した",
        "checks": result_for(value)["checks"]})


class SegmentationTests(unittest.TestCase):
    def test_inline_markdown_cannot_be_split_inside_its_syntax(self):
        for markup in ("`--option`", "``a`b``", "`a\nb`", "[案内](https://example.test/path)",
                       "![図](https://example.test/image)", "<https://example.test/path>",
                       "**強調**", "*強調*", "\\*", "&amp;", "[案内][ref]"):
            text = "[ref]: https://example.test/reference\n\n前" + markup + "後。\n"
            value = state(text)
            unit = next(u for u in value["units"] if u["text"].startswith("前"))
            start = unit["text"].index(markup)
            for index in range(1, len(markup)):
                with self.subTest(markup=markup, index=index):
                    cut = start + index
                    before = copy.deepcopy(value)
                    with self.assertRaises(revision.RevisionError):
                        revision.split_unit(value, unit["id"], revision.token(value, unit),
                                            [unit["text"][:cut], unit["text"][cut:]])
                    self.assertEqual(value, before)

    def test_plain_text_and_whole_inline_constructs_can_be_separate_targets(self):
        for text, parts in (
                ("前の説明。[案内](https://example.test/path)を参照する。\n",
                 ["前の", "説明。", "[案内](https://example.test/path)", "を参照する。\n"]),
                ("> 前の説明。\n> 次の説明。\n", ["> 前の説明。\n", "> 次の説明。\n"]),
                ("前の説明。\r\n次の説明。\r\n", ["前の説明。\r\n", "次の説明。\r\n"])):
            with self.subTest(text=text):
                value = state(text)
                unit = value["units"][0]
                revision.split_unit(value, unit["id"], revision.token(value, unit), parts)
                self.assertEqual(revision.render(value), text)
        value = state("> 前 `a\n> b` 後。\n")
        unit = value["units"][0]
        with self.assertRaisesRegex(revision.RevisionError, "inline Markdown"):
            revision.split_unit(value, unit["id"], revision.token(value, unit), ["> 前 `a\n", "> b` 後。\n"])
        value = state("[ref]: https://example.test/path\n\n参照。\n")
        unit = value["units"][0]
        cut = unit["text"].index("path")
        with self.assertRaisesRegex(revision.RevisionError, "link definitions"):
            revision.split_unit(value, unit["id"], revision.token(value, unit),
                                [unit["text"][:cut], unit["text"][cut:]])

    def test_lossless_reassembly_including_crlf_links_table_code_and_order(self):
        value = state(DOCUMENT)
        accept_all(value)
        final(value)
        self.assertEqual(revision.finish(value)["text"].encode(), DOCUMENT.encode())
        kinds = [u["kind"] for u in value["units"]]
        self.assertIn("table", kinds)
        self.assertIn("list", kinds)
        code = [u["text"] for u in value["units"] if u["kind"] == "code"]
        self.assertEqual(len(code), 1)
        self.assertIn("# 見出しではない\r\n\r\nprintf", code[0])

    def test_nested_list_fence_and_unclosed_fence_stay_whole(self):
        text = "1. 実行\n   " + TICKS + "\n   a\n\n   b\n   " + TICKS + "\n\n次へ\n\n" + TICKS + "\n末尾\n"
        value = state(text)
        self.assertIn("   a\n\n   b", value["units"][0]["text"])
        self.assertEqual(revision.render(value), text)
        self.assertEqual(value["units"][-1]["kind"], "code")

    def test_split_merge_preserves_exact_text_and_retires_history(self):
        value = state("前の説明。\n\n後の説明。\n")
        old = [u["id"] for u in value["units"]]
        joined = revision.merge_units(value, old)
        unit = revision.unit_by_id(value, joined)
        children = revision.split_unit(value, joined, revision.token(value, unit),
                                       ["前の説明。\n\n", "後の説明。\n"])
        self.assertEqual(revision.render(value), value["original"])
        self.assertTrue(all(revision.unit_by_id(value, x)["parents"] == [joined] for x in children))
        self.assertEqual({u["id"] for u in value["retired"]}, set(old + [joined]))
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, children[0], revision.token(value, value["units"][0]), ["前", "欠落"])

    def test_table_row_has_header_and_common_condition_context(self):
        value = state("申請済みのときだけ使う。\n\n| 対象 | 条件 |\n| --- | --- |\n| A | 有効 |\n| B | 保留 |\n")
        table = value["units"][1]
        children = revision.split_unit(value, table["id"], revision.token(value, table),
                                       ["| 対象 | 条件 |\n| --- | --- |\n",
                                        "| A | 有効 |\n", "| B | 保留 |\n"])
        row = revision.view(value, revision.unit_by_id(value, children[-1]))
        refs = row["reference_only"]["related"]
        self.assertTrue(any("| 対象 | 条件 |" in ref["text"] for ref in refs))
        self.assertTrue(any("申請済み" in ref["text"] for ref in refs))
        self.assertEqual(row["target"]["text"], "| B | 保留 |\n")

    def test_split_rejects_partial_headers_rows_and_code_even_after_merge(self):
        cases = [
            ("| A | B |\n| --- | --- |\n| x | y |\n", ["| A | B |\n", "| --- | --- |\n| x | y |\n"]),
            ("| A | B |\n| --- | --- |\n| x | y |\n", ["| A | B |\n| --- | --- |\n| x", " | y |\n"]),
            (TICKS + "\na\nb\n" + TICKS + "\n", [TICKS + "\na\n", "b\n" + TICKS + "\n"]),
            ("    a\n    b\n", ["    a\n", "    b\n"]),
            ("見出し\n====\n", ["見出し\n", "====\n"]),
        ]
        for text, parts in cases:
            with self.subTest(text=text):
                value = state(text)
                unit = value["units"][0]
                with self.assertRaises(revision.RevisionError):
                    revision.split_unit(value, unit["id"], revision.token(value, unit), parts)
        value = state("条件。\n\n" + cases[0][0])
        merged = revision.merge_units(value, [u["id"] for u in value["units"]])
        unit = revision.unit_by_id(value, merged)
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, merged, revision.token(value, unit),
                                ["条件。\n\n| A | B |\n", "| --- | --- |\n| x | y |\n"])

    def test_repeated_table_split_retains_header_context_and_row_boundaries(self):
        value = state("| A | B |\n| --- | --- |\n| x | 1 |\n| y | 2 |\n")
        unit = value["units"][0]
        ids = revision.split_unit(value, unit["id"], revision.token(value, unit),
                                  ["| A | B |\n| --- | --- |\n", "| x | 1 |\n| y | 2 |\n"])
        rows = revision.unit_by_id(value, ids[1])
        children = revision.split_unit(value, rows["id"], revision.token(value, rows),
                                       ["| x | 1 |\n", "| y | 2 |\n"])
        last = revision.unit_by_id(value, children[-1])
        self.assertEqual(last["kind"], "table_rows")
        self.assertIn(ids[0], last["context_ids"])
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, last["id"], revision.token(value, last), ["| y", " | 2 |\n"])

    def test_quoted_fences_cannot_be_split_even_inside_merged_targets(self):
        for prefix, marker in (("> ", TICKS), ("> > ", "~~~"), (">\t", TICKS)):
            with self.subTest(prefix=prefix):
                code = prefix + marker + "sh\n" + prefix + "a\n" + prefix + "b\n" + prefix + marker + "\n\n"
                value = state(code + "後続の段落。\n")
                uid = revision.merge_units(value, [u["id"] for u in value["units"]])
                unit = revision.unit_by_id(value, uid)
                cut = code.index(prefix + "b")
                with self.assertRaises(revision.RevisionError):
                    revision.split_unit(value, uid, revision.token(value, unit),
                                        [unit["text"][:cut], unit["text"][cut:]])
                revision.split_unit(value, uid, revision.token(value, unit),
                                    [code, "後続の段落。\n"])
                self.assertEqual(revision.render(value), value["original"])

    def test_tab_indented_fences_allow_item_boundaries_and_reject_code_splits(self):
        first = "- item\n\t" + TICKS + "sh\n\techo hi\n\t" + TICKS + "\n"
        value = state(first + "- next\n")
        unit = value["units"][0]
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, unit["id"], revision.token(value, unit),
                                ["- item\n\t" + TICKS + "sh\n",
                                 "\techo hi\n\t" + TICKS + "\n- next\n"])
        revision.split_unit(value, unit["id"], revision.token(value, unit), [first, "- next\n"])
        self.assertEqual(revision.render(value), value["original"])

    def test_lists_split_only_between_whole_items_including_in_merged_targets(self):
        first = "1. first\n   continued\n   - nested\n\n"
        second = "2. second\n"
        for merged in (False, True):
            with self.subTest(merged=merged):
                value = state(("前の段落。\n\n" if merged else "") + first + second)
                uid = (revision.merge_units(value, [u["id"] for u in value["units"]])
                       if merged else value["units"][0]["id"])
                unit = revision.unit_by_id(value, uid)
                text = unit["text"]
                for cut in (text.index("continued"), text.index("   - nested"),
                            text.index("second") + 3):
                    with self.assertRaises(revision.RevisionError):
                        revision.split_unit(value, uid, revision.token(value, unit),
                                            [text[:cut], text[cut:]])
                cut = text.index("2. second")
                revision.split_unit(value, uid, revision.token(value, unit),
                                    [text[:cut], text[cut:]])
                self.assertEqual(revision.render(value), value["original"])

    def test_quoted_lists_headings_and_tables_keep_inner_structure_and_context(self):
        cases = [
            ("> - first item\n> - second item\n", "> - second", ["> - first item\n", "> - second item\n"]),
            ("> > 1. first item\n> > 2. second item\n", "> > 2.",
             ["> > 1. first item\n", "> > 2. second item\n"]),
            ("> Title\n> ====\n> paragraph\n", "> paragraph",
             ["> Title\n> ====\n", "> paragraph\n"]),
            ("> | A | B |\n> | --- | --- |\n> | x | y |\n", "> | x",
             ["> | A | B |\n> | --- | --- |\n", "> | x | y |\n"]),
        ]
        for text, _, parts in cases:
            with self.subTest(text=text):
                value = state(text)
                unit = value["units"][0]
                unsafe = text.index("item") + 2 if "item" in text else text.index("\n") + 1
                with self.assertRaises(revision.RevisionError):
                    revision.split_unit(value, unit["id"], revision.token(value, unit),
                                        [text[:unsafe], text[unsafe:]])
                self.assertEqual("".join(parts), text)
                ids = revision.split_unit(value, unit["id"], revision.token(value, unit), parts)
                self.assertEqual(revision.render(value), value["original"])
                if "| A | B |" in text:
                    row = revision.view(value, revision.unit_by_id(value, ids[-1]))
                    self.assertTrue(any("| A | B |" in ref["text"]
                                        for ref in row["reference_only"]["related"]))

    def test_quote_markers_inside_quoted_code_are_literal_content(self):
        text = "> " + TICKS + "md\n> > " + TICKS + "\n> code continues\n> " + TICKS + "\n"
        value = state(text)
        unit = value["units"][0]
        cut = text.index("> code continues")
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, unit["id"], revision.token(value, unit),
                                [text[:cut], text[cut:]])

    def test_lazy_list_and_quote_continuations_remain_with_their_item(self):
        cases = [
            ("- first line\nlazy continuation\n", "- second\n", "list"),
            ("1. first line\nlazy continuation\n", "2. second\n", "list"),
            ("> - first line\nlazy continuation\n", "> - second\n", "quote"),
        ]
        for first, second, kind in cases:
            for merged in (False, True):
                with self.subTest(kind=kind, merged=merged):
                    value = state(first + second)
                    self.assertEqual(len(value["units"]), 1)
                    self.assertEqual(value["units"][0]["kind"], kind)
                    self.assertIn("lazy continuation", value["units"][0]["text"])
                    if merged:
                        value = state("前の段落。\n\n" + first + second)
                        uid = revision.merge_units(value, [u["id"] for u in value["units"]])
                    else:
                        uid = value["units"][0]["id"]
                    unit = revision.unit_by_id(value, uid)
                    text = unit["text"]
                    cut = text.index("lazy continuation")
                    with self.assertRaises(revision.RevisionError):
                        revision.split_unit(value, uid, revision.token(value, unit),
                                            [text[:cut], text[cut:]])
                    cut = text.index(second)
                    revision.split_unit(value, uid, revision.token(value, unit),
                                        [text[:cut], text[cut:]])
                    self.assertEqual(revision.render(value), value["original"])

    def test_lists_end_at_blank_lines_headings_and_after_fenced_code(self):
        first = "- " + TICKS + "sh\n  echo hi\n  " + TICKS + "\n"
        cases = [("- first\n\n", "outside paragraph\n"),
                 ("- first\n", "# Heading\n"),
                 (first, "outside paragraph\n"),
                 ("> first\n\n", "outside paragraph\n")]
        for before, after in cases:
            with self.subTest(text=before + after):
                value = state(before + after)
                self.assertEqual(len(value["units"]), 2)
                self.assertEqual(value["units"][0]["text"], before)
                uid = revision.merge_units(value, [u["id"] for u in value["units"]])
                unit = revision.unit_by_id(value, uid)
                revision.split_unit(value, uid, revision.token(value, unit), [before, after])
                self.assertEqual(revision.render(value), before + after)

    def test_single_column_tables_and_multiline_headings_preserve_boundaries(self):
        table = "| Column |\n| - |\n| Value |\n"
        value = state(table)
        self.assertEqual(value["units"][0]["kind"], "table")
        unit = value["units"][0]
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, unit["id"], revision.token(value, unit),
                                ["| Column |\n", "| - |\n| Value |\n"])
        parts = ["| Column |\n| - |\n", "| Value |\n"]
        revision.split_unit(value, unit["id"], revision.token(value, unit), parts)
        heading = "First line\nSecond line\n===\n"
        value = state(heading + "\nFollowing paragraph\n")
        self.assertEqual(value["units"][0]["kind"], "heading")
        self.assertEqual(revision.context(value, value["units"][1])["headings"][0]["level"], 1)
        unit = value["units"][0]
        with self.assertRaises(revision.RevisionError):
            revision.split_unit(value, unit["id"], revision.token(value, unit),
                                ["First line\n", "Second line\n===\n\n"])

    def test_unrendered_reference_definitions_and_unicode_line_characters_are_lossless(self):
        text = "[ref]: https://example.test/a\r\n\r\nTitle\u2028still the same line\r\n====\r\n"
        value = state(text)
        self.assertEqual(revision.render(value).encode(), text.encode())
        self.assertEqual(value["units"][-1]["kind"], "heading")
        self.assertIn("\u2028", value["units"][-1]["text"])

    def test_thematic_breaks_remain_whole_in_root_quoted_and_merged_targets(self):
        for marker in ("---", "***", "___", "- - -"):
            for prefix in ("", "> "):
                with self.subTest(marker=marker, prefix=prefix):
                    separator = prefix + marker + "\n\n"
                    value = state(separator + "後の段落。\n")
                    if not prefix:
                        self.assertEqual(value["units"][0]["kind"], "separator")
                    uid = revision.merge_units(value, [u["id"] for u in value["units"]])
                    unit = revision.unit_by_id(value, uid)
                    text = unit["text"]
                    cut = len(prefix) + 1
                    with self.assertRaises(revision.RevisionError):
                        revision.split_unit(value, uid, revision.token(value, unit),
                                            [text[:cut], text[cut:]])
                    revision.split_unit(value, uid, revision.token(value, unit),
                                        [separator, "後の段落。\n"])
                    self.assertEqual(revision.render(value), text)

    def test_reorder_requires_complete_permutation_and_invalidates_all(self):
        value = state()
        accept_all(value)
        final(value)
        ids = [u["id"] for u in value["units"]]
        with self.assertRaises(revision.RevisionError):
            revision.reorder(value, ids[:-1])
        revision.reorder(value, ids[::-1])
        self.assertEqual([u["id"] for u in value["units"]], ids[::-1])
        self.assertTrue(all(u["state"] == "recheck" for u in value["units"]))
        self.assertIsNone(value["final_review"])

    def test_reorder_keeps_split_table_headers_and_rows_together(self):
        for prefix in ("", "> "):
            with self.subTest(prefix=prefix):
                header = prefix + "| A | B |\n" + prefix + "| --- | --- |\n"
                row_a, row_b = prefix + "| x | 1 |\n", prefix + "| y | 2 |\n"
                value = state("前の段落。\n\n" + header + row_a + row_b + "\n後の段落。\n")
                table = value["units"][1]
                ids = revision.split_unit(value, table["id"], revision.token(value, table),
                                          [header, row_a, row_b + "\n"])
                before_id, after_id = value["units"][0]["id"], value["units"][-1]["id"]
                for order in ([before_id, ids[0], after_id, ids[1], ids[2]],
                              [before_id, ids[1], ids[0], ids[2], after_id],
                              [before_id, ids[0], ids[2], ids[1], after_id]):
                    before = copy.deepcopy(value)
                    with self.assertRaisesRegex(revision.RevisionError, "table headers and rows"):
                        revision.reorder(value, order)
                    self.assertEqual(value, before)
                revision.reorder(value, [*ids, before_id, after_id])
                self.assertEqual(sum(n.type == "table_open" for n in revision.syntax(revision.render(value))), 1)
                self.assertEqual([u["id"] for u in value["units"]][:3], ids)

    def test_reorder_cannot_attach_rows_to_another_table_header(self):
        value = state("| A |\n| --- |\n| x |\n\n別の表。\n\n| B |\n| --- |\n| y |\n")
        first, between, second = value["units"]
        ids = revision.split_unit(value, first["id"], revision.token(value, first),
                                  ["| A |\n| --- |\n", "| x |\n\n"])
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(revision.RevisionError, "table headers and rows"):
            revision.reorder(value, [ids[0], between["id"], second["id"], ids[1]])
        self.assertEqual(value, before)

    def test_reorder_keeps_split_list_items_together(self):
        for first, second in (("1. first\n   - nested\n", "2. second\n\n"),
                              ("- first\n", "- second\n\n"),
                              ("> 1. first\n", "> 2. second\n\n")):
            with self.subTest(first=first):
                value = state("前の段落。\n\n" + first + second + "後の段落。\n")
                unit = value["units"][1]
                ids = revision.split_unit(value, unit["id"], revision.token(value, unit), [first, second])
                before_id, after_id = value["units"][0]["id"], value["units"][-1]["id"]
                for order in ([before_id, ids[0], after_id, ids[1]],
                              [before_id, ids[1], ids[0], after_id]):
                    before = copy.deepcopy(value)
                    with self.assertRaisesRegex(revision.RevisionError, "list items"):
                        revision.reorder(value, order)
                    self.assertEqual(value, before)
                revision.reorder(value, [*ids, before_id, after_id])
                self.assertEqual([u["id"] for u in value["units"]][:2], ids)

    def test_reorder_preserves_list_and_table_boundaries_after_moving_whole_targets(self):
        for text, order in (
                ("移動する段落。\n\n1. first\n2. second\n", [1, 0]),
                ("| A |\n| --- |\n| x |\n\n中間の段落。\n\n| B |\n| --- |\n| y |\n", [2, 0, 1])):
            with self.subTest(text=text):
                value = state(text)
                before = copy.deepcopy(value)
                with self.assertRaisesRegex(revision.RevisionError, "boundaries"):
                    revision.reorder(value, [value["units"][i]["id"] for i in order])
                self.assertEqual(value, before)


class ReviewTests(unittest.TestCase):
    def test_pending_and_recheck_cannot_finish(self):
        value = state()
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)
        accept_all(value)
        final(value)
        revision.invalidate(value, [value["units"][1]["id"]], "共通の条件を再確認する")
        with self.assertRaises(revision.RevisionError):
            revision.final_review(value, revision.document_token(value), {})
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)

    def test_edit_invalidates_old_check_neighbor_and_full_review(self):
        value = state()
        accept_all(value)
        final(value)
        unit = value["units"][0]
        old_token = revision.token(value, unit)
        revision.edit(value, unit["id"], old_token, "最初の説明を明確にした。\n\n")
        self.assertEqual(unit["state"], "recheck")
        self.assertEqual(value["units"][1]["state"], "recheck")
        self.assertEqual(value["units"][2]["state"], "unchanged")
        self.assertIsNone(value["final_review"])
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, unit["id"], old_token, record("confirmed"))
        self.assertTrue(any(e["action"] == "edit" and "最初の説明。" in e["text"]
                            for e in unit["history"]))

    def test_unchanged_is_not_available_after_edit_and_edit_requires_check(self):
        value = state("元の説明。\n")
        unit = value["units"][0]
        revision.edit(value, unit["id"], revision.token(value, unit), "明確にした説明。\n")
        with self.assertRaises(revision.RevisionError):
            revision.edit(value, unit["id"], revision.token(value, unit), "再度変更。\n")
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, unit["id"], revision.token(value, unit), record())
        revision.record_review(value, unit["id"], revision.token(value, unit), record("confirmed"))
        self.assertEqual(unit["state"], "confirmed")

    def test_restoring_text_does_not_restore_an_old_unit_token(self):
        value = state("元の説明。\n", limit=5)
        unit = value["units"][0]
        old = revision.view(value, unit)["target"]
        prepared = result_for(value, unit)
        revision.edit(value, unit["id"], old["token"], "変更した説明。\n")
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit, "confirmed"))
        revision.edit(value, unit["id"], revision.token(value, unit), old["text"])
        restored = revision.view(value, unit)["target"]
        self.assertNotEqual(restored["token"], old["token"])
        self.assertNotEqual(restored["content_token"], old["content_token"])
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(revision.RevisionError, "stale unit token"):
            revision.record_review(value, unit["id"], old["token"], prepared)
        self.assertEqual(value, before)
        revision.record_review(value, unit["id"], restored["token"], result_for(value, unit, "confirmed"))
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_related_context_and_shared_premise_invalidate_affected_units(self):
        value = state()
        a, b, c = value["units"]
        revision.annotate(value, c["id"], revision.token(value, c), {"context_ids": [a["id"]]})
        accept_all(value)
        revision.edit(value, a["id"], revision.token(value, a), "条件を説明した。\n\n")
        self.assertEqual(c["state"], "recheck")
        value = state()
        accept_all(value)
        revision.edit(value, value["units"][0]["id"], revision.token(value, value["units"][0]),
                      "共通条件。\n\n", all_affected=True)
        self.assertTrue(all(u["state"] == "recheck" for u in value["units"]))

    def test_bare_ok_or_missing_required_records_are_rejected(self):
        bare = record()
        bare["checks"][0]["observation"] = "OK"
        bad_records = [record(checks=[]), record(criteria=["meaning"]), {"outcome": "unchanged"}, bare]
        for bad in bad_records:
            value = state("説明。\n")
            with self.subTest(bad=bad), self.assertRaises(revision.RevisionError):
                revision.record_review(value, value["units"][0]["id"],
                                       revision.token(value, value["units"][0]), bad)

    def test_missing_current_record_cannot_bypass_finish(self):
        value = state("説明。\n")
        accept_all(value)
        final(value)
        value["units"][0]["review"]["record"]["checks"] = []
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)

    def test_limit_holds_issue_and_next_target_continues(self):
        value = state(limit=1)
        unit = value["units"][0]
        bad = record("needs_revision")
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, unit["id"], revision.token(value, unit), bad)
        held = hold()
        held["outcome"] = "needs_revision"
        held["checks"][0]["status"] = "needs_revision"
        revision.record_review(value, unit["id"], revision.token(value, unit), held)
        self.assertEqual(unit["state"], "held")
        self.assertTrue(unit["review"]["limit_reached"])
        self.assertEqual(revision.next_target(value)["target"]["id"], value["units"][1]["id"])
        for other in value["units"][1:]:
            revision.record_review(value, other["id"], revision.token(value, other), record())
        final(value)
        result = revision.finish(value)
        self.assertFalse(result["fully_resolved"])
        self.assertEqual(result["held"][0]["record"]["checks"][0]["proposal"],
                         held["checks"][0]["proposal"])
        with self.assertRaises(revision.RevisionError):
            revision.edit(value, unit["id"], revision.token(value, unit), "推測の値。\n\n")

    def test_iteration_limit_cannot_be_reset_by_split_or_merge(self):
        value = state(limit=1)
        accept_all(value)
        joined = revision.merge_units(value, [u["id"] for u in value["units"]])
        unit = revision.unit_by_id(value, joined)
        self.assertEqual(unit["attempts"], 1)
        with self.assertRaises(revision.RevisionError):
            revision.edit(value, joined, revision.token(value, unit), "上限後の変更。\n")
        children = revision.split_unit(value, joined, revision.token(value, unit),
                                       ["最初の説明。\n\n", "次の説明。\n\n最後の説明。\n"])
        self.assertTrue(all(revision.unit_by_id(value, uid)["attempts"] == 1 for uid in children))

    def test_held_requires_concrete_reason_proposal_and_decision(self):
        value = state("説明。\n")
        unit = value["units"][0]
        for key in ("reason", "proposal", "required_decision"):
            bad = hold()
            del bad["checks"][0][key]
            with self.assertRaises(revision.RevisionError):
                revision.record_review(value, unit["id"], revision.token(value, unit), bad)

    def test_hold_cannot_be_overwritten_until_explicit_invalidation(self):
        value = state("対象。\n")
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        final(value)
        previous = copy.deepcopy(value)
        for outcome in ("confirmed", "unchanged", "needs_revision", "held"):
            with self.subTest(outcome=outcome), self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
                revision.record_review(value, unit["id"], revision.token(value, unit),
                                       result_for(value, unit, outcome))
            self.assertEqual(value, previous)
            self.assertFalse(revision.finish(value)["fully_resolved"])
        revision.invalidate(value, [unit["id"]], "不足していた情報と判断を得た")
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
        self.assertIn(previous["units"][0]["history"][-1], unit["history"])
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_context_changes_do_not_clear_the_hold_barrier(self):
        value = state("前の説明。\n\n後の説明。\n\n隣の説明。\n")
        uid = revision.merge_units(value, [u["id"] for u in value["units"][:2]])
        unit, neighbor = value["units"]
        revision.record_review(value, uid, revision.token(value, unit), result_for(value, unit, "held"))
        revision.edit(value, neighbor["id"], revision.token(value, neighbor), "隣の説明を明確にした。\n")
        self.assertEqual(unit["state"], "recheck")
        self.assertTrue(revision.view(value, unit)["target"]["hold_active"])
        before = copy.deepcopy(value)
        operations = [
            lambda: revision.record_review(value, uid, revision.token(value, unit), result_for(value, unit)),
            lambda: revision.edit(value, uid, revision.token(value, unit), "さらに変更。\n"),
            lambda: revision.split_unit(value, uid, revision.token(value, unit),
                                        ["前の説明。\n\n", "後の説明。\n\n"]),
            lambda: revision.merge_units(value, [u["id"] for u in value["units"]])]
        for operation in operations:
            with self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
                operation()
            self.assertEqual(value, before)
        revision.invalidate(value, [uid], "文脈の変更を踏まえ保留事項を再確認する")
        self.assertFalse(revision.view(value, unit)["target"]["hold_active"])
        revision.record_review(value, uid, revision.token(value, unit), result_for(value, unit))
        revision.record_review(value, neighbor["id"], revision.token(value, neighbor),
                               result_for(value, neighbor, "confirmed"))
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_legacy_states_retain_holds_until_explicit_invalidation(self):
        for legacy_format in (1, 2):
            with self.subTest(legacy_format=legacy_format):
                value = state("対象。\n")
                unit = value["units"][0]
                revision.record_review(value, unit["id"], revision.token(value, unit), hold())
                del unit["hold_active"]
                if legacy_format == 1:
                    value["format"] = 1
                    value = revision.upgrade(value, BRIEF)
                    unit = value["units"][0]
                    ensure_plan(value, unit)
                self.assertTrue(revision.has_active_hold(unit))
                with self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
                    revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
                revision.invalidate(value, [unit["id"]], "保留事項の判断が得られた")
                revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
                self.assertFalse(revision.has_active_hold(unit))

    def test_reader_or_criteria_changes_do_not_reopen_a_hold(self):
        for change in ("reader", "reader_difficulties", "criteria"):
            with self.subTest(change=change):
                value = state("対象。\n")
                unit = value["units"][0]
                revision.record_review(value, unit["id"], revision.token(value, unit), hold())
                brief = copy.deepcopy(value["brief"])
                if change == "criteria":
                    brief["criteria"][0]["description"] = "元の条件と留保を保持する"
                else:
                    brief[change] += "。用語の意味も確認する"
                revision.update_brief(value, brief)
                self.assertEqual(unit["state"], "recheck")
                self.assertTrue(revision.has_active_hold(unit))
                before = copy.deepcopy(value)
                with self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
                    revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
                self.assertEqual(value, before)
                revision.invalidate(value, [unit["id"]], "保留の原因となった情報と判断が得られた")
                revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
                self.assertFalse(revision.has_active_hold(unit))

    def test_mixed_held_and_revision_checks_require_explicit_invalidation(self):
        for legacy_marker in ("current", "absent", "false"):
            with self.subTest(legacy_marker=legacy_marker):
                value = state("対象。\n")
                unit = value["units"][0]
                mixed = result_for(value, unit, "held")
                mixed["outcome"] = "needs_revision"
                mixed["checks"][1].update(status="needs_revision", observation="対象と条件の関係を明示する必要がある")
                revision.record_review(value, unit["id"], revision.token(value, unit), mixed)
                self.assertEqual(unit["state"], "recheck")
                self.assertTrue(unit["hold_active"])
                if legacy_marker == "absent":
                    del unit["hold_active"]
                elif legacy_marker == "false":
                    unit["hold_active"] = False
                    unit.pop("hold_released_token", None)
                self.assertTrue(revision.has_active_hold(unit))
                before = copy.deepcopy(value)
                for operation in (
                    lambda: revision.edit(value, unit["id"], revision.token(value, unit), "明示した条件。\n"),
                    lambda: revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))):
                    with self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
                        operation()
                    self.assertEqual(value, before)
                with self.assertRaises(revision.RevisionError):
                    revision.finish(value)
                revision.invalidate(value, [unit["id"]], "保留していた判断を得たので残る修正を行う")
                self.assertEqual(unit["hold_released_token"], before["units"][0]["review"]["token"])
                revision.edit(value, unit["id"], revision.token(value, unit), "明示した条件。\n")
                revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit, "confirmed"))
                self.assertFalse(revision.has_active_hold(unit))
                self.assertIsNone(unit["hold_released_token"])
                self.assertIn(before["units"][0]["history"][-1], unit["history"])
                final(value)
                self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_release_is_bound_to_the_held_review_and_survives_context_changes(self):
        value = state("対象。\n")
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        first_token = unit["review"]["token"]
        revision.invalidate(value, [unit["id"]], "必要な判断を得て再開する")
        self.assertEqual(unit["hold_released_token"], first_token)
        brief = copy.deepcopy(value["brief"])
        brief["reader"] += "。確認対象の用語は別途説明する"
        revision.update_brief(value, brief)
        self.assertFalse(revision.has_active_hold(unit))
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit, "held"))
        self.assertNotEqual(unit["review"]["token"], first_token)
        brief = copy.deepcopy(value["brief"])
        brief["purpose"] += "。条件を再点検する"
        revision.update_brief(value, brief)
        self.assertEqual(unit["state"], "recheck")
        unit["hold_active"] = False
        unit["hold_released_token"] = first_token
        self.assertTrue(revision.has_active_hold(unit))
        with self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
            revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))

    def test_next_skips_active_holds_after_context_or_brief_changes(self):
        for change in ("neighbor", "brief"):
            for outcome in ("held", "needs_revision"):
                with self.subTest(change=change, outcome=outcome):
                    value = state()
                    held, neighbor, last = value["units"]
                    payload = result_for(value, held, "held")
                    if outcome == "needs_revision":
                        payload["outcome"] = outcome
                        payload["checks"][1].update(status="needs_revision", observation="対象の条件を明示する必要がある")
                    revision.record_review(value, held["id"], revision.token(value, held), payload)
                    if change == "neighbor":
                        revision.edit(value, neighbor["id"], revision.token(value, neighbor), "隣の説明を明確にした。\n\n")
                    else:
                        brief = copy.deepcopy(value["brief"])
                        brief["reader"] += "。必要な用語は本文で説明する"
                        revision.update_brief(value, brief)
                    self.assertEqual(held["state"], "recheck")
                    self.assertTrue(revision.has_active_hold(held))
                    self.assertEqual(revision.next_target(value)["target"]["id"], neighbor["id"])
                    revision.record_review(value, neighbor["id"], revision.token(value, neighbor),
                                           result_for(value, neighbor, "confirmed"))
                    self.assertEqual(revision.next_target(value)["target"]["id"], last["id"])
                    revision.record_review(value, last["id"], revision.token(value, last), result_for(value, last))
                    self.assertIsNone(revision.next_target(value)["target"])
                    with self.assertRaises(revision.RevisionError):
                        revision.finish(value)
                    revision.invalidate(value, [held["id"]], "保留事項の判断が得られた")
                    self.assertEqual(revision.next_target(value)["target"]["id"], held["id"])

    def test_migration_records_verified_old_releases_without_reinvalidating(self):
        for outcome in ("held", "needs_revision"):
            with self.subTest(outcome=outcome):
                value = state("対象。\n")
                unit = value["units"][0]
                payload = result_for(value, unit, "held")
                if outcome == "needs_revision":
                    payload["outcome"] = outcome
                    payload["checks"][1].update(status="needs_revision", observation="対象の条件を明示する必要がある")
                revision.record_review(value, unit["id"], revision.token(value, unit), payload)
                old_token = unit["review"]["token"]
                revision.invalidate(value, [unit["id"]], "必要な情報と判断を得て再開する")
                del unit["hold_released_token"]
                before = copy.deepcopy(value)
                revision.migrate_release(value, unit["id"], revision.token(value, unit), old_token,
                                         "旧実行ログに、この対象の明示invalidateと判断内容が記録されている")
                for key in ("id", "text", "state", "generation", "attempts", "review"):
                    self.assertEqual(unit[key], before["units"][0][key])
                self.assertEqual(unit["history"][:-1], before["units"][0]["history"])
                self.assertFalse(revision.has_active_hold(unit))
                revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
                final(value)
                self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_regrouping_does_not_turn_edited_text_into_unchanged(self):
        value = state()
        unit = value["units"][0]
        revision.edit(value, unit["id"], revision.token(value, unit), "変更した説明。\n\n")
        revision.record_review(value, unit["id"], revision.token(value, unit), record("confirmed"))
        uid = revision.merge_units(value, [u["id"] for u in value["units"][:2]])
        merged = revision.unit_by_id(value, uid)
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, uid, revision.token(value, merged), record())

    def test_final_read_required_and_stale_token_rejected(self):
        value = state("説明。\n")
        accept_all(value)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)
        with self.assertRaises(revision.RevisionError):
            revision.final_review(value, "古い版", {})
        with self.assertRaises(revision.RevisionError):
            revision.final_review(value, revision.document_token(value),
                                  {"original_comparison": "OK", "standalone_read": "OK"})
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_original_is_immutable_and_different_working_copy_is_not_called_original(self):
        value = revision.initialize("変更前の原文。\n", "整理済みの初期稿。\n", BRIEF)
        unit = value["units"][0]
        shown = revision.view(value, unit)["target"]
        self.assertEqual(shown["original_text"], "変更前の原文。\n")
        self.assertEqual(shown["original_scope"], "whole_document")
        self.assertEqual(shown["initial_working_text"], "整理済みの初期稿。\n")
        value["original"] = "書き換えた原文。\n"
        with self.assertRaises(revision.RevisionError):
            revision.validate_state(value)

    def test_repeated_original_excerpt_keeps_whole_context_until_lines_are_specified(self):
        original = "# 許可後\n\n保管する。\n\n# 処理後\n\n保管する。\n\n"
        value = revision.initialize(original, "保管する。\n\n", BRIEF)
        unit = value["units"][0]
        shown = revision.view(value, unit)["target"]
        self.assertEqual(shown["original_scope"], "whole_document")
        self.assertEqual(shown["original_text"], original)
        revision.annotate(value, unit["id"], revision.token(value, unit), {"original_lines": [5, 8]})
        shown = revision.view(value, unit)["target"]
        self.assertEqual(shown["original_scope"], "specified_lines")
        self.assertEqual(shown["original_text"], "# 処理後\n\n保管する。\n\n")

    def test_unique_excerpt_is_shortened_but_overlapping_matches_keep_original_context(self):
        for original, working, expected_scope in (
                ("# 条件\n\n保管する。\n\n", "保管する。\n\n", "matching_excerpt"),
                ("aaaa", "aaa", "whole_document")):
            with self.subTest(expected_scope=expected_scope):
                value = revision.initialize(original, working, BRIEF)
                shown = revision.view(value, value["units"][0])["target"]
                self.assertEqual(shown["original_scope"], expected_scope)
                self.assertEqual(shown["original_text"], working if expected_scope == "matching_excerpt" else original)

    def test_annotation_and_review_history_survives_regrouping(self):
        value = state()
        unit = value["units"][0]
        revision.annotate(value, unit["id"], revision.token(value, unit),
                          {"findings": ["条件が不明"]})
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        revision.invalidate(value, [unit["id"]], "保留していた条件の根拠が得られた")
        old = copy.deepcopy(unit)
        revision.merge_units(value, [u["id"] for u in value["units"][:2]])
        self.assertIn(old, value["retired"])
        self.assertEqual(value["units"][0]["notes"]["inherited"][0]["findings"], ["条件が不明"])
        records = revision.history(value, value["units"][0]["id"])["records"]
        self.assertIn(old, records)
        self.assertEqual(revision.history(value, old["id"])["records"][0], old)

    def test_regrouping_rejects_held_targets_without_losing_the_hold(self):
        for operation, held_index in (("split", 0), ("merge", 0), ("merge", 1)):
            with self.subTest(operation=operation, held_index=held_index):
                value = state("前の説明。\n\n後の説明。\n")
                if operation == "split":
                    revision.merge_units(value, [u["id"] for u in value["units"]])
                accept_all(value)
                unit = value["units"][held_index]
                revision.record_review(value, unit["id"], revision.token(value, unit),
                                       result_for(value, unit, "held"))
                final(value)
                before = copy.deepcopy(value)
                def regroup():
                    if operation == "split":
                        return revision.split_unit(value, unit["id"], revision.token(value, unit),
                                                   ["前の説明。\n\n", "後の説明。\n"])
                    return revision.merge_units(value, [u["id"] for u in value["units"]])
                with self.assertRaisesRegex(revision.RevisionError, "held.*invalidate"):
                    regroup()
                self.assertEqual(value, before)
                result = revision.finish(value)
                self.assertFalse(result["fully_resolved"])
                self.assertEqual(result["held"][0]["record"], unit["review"]["record"])
                revision.invalidate(value, [unit["id"]], "不足していた条件を確認した")
                regroup()
                retired = next(u for u in value["retired"] if u["id"] == unit["id"])
                self.assertEqual(retired["review"], unit["review"])
                self.assertTrue(all(u["attempts"] == 2 for u in value["units"]))
                with self.assertRaises(revision.RevisionError):
                    revision.finish(value)

    def test_regrouping_requires_the_last_edit_to_be_checked(self):
        for operation, edited_index in (("split", 0), ("merge", 0), ("merge", 1)):
            with self.subTest(operation=operation, edited_index=edited_index):
                value = state("前の説明。\n\n後の説明。\n")
                if operation == "split":
                    revision.merge_units(value, [u["id"] for u in value["units"]])
                unit = value["units"][edited_index]
                edited = unit["text"].replace("説明", "明確な説明", 1)
                revision.edit(value, unit["id"], revision.token(value, unit), edited)
                def regroup():
                    if operation == "split":
                        cut = edited.index("\n\n") + 2
                        return revision.split_unit(value, unit["id"], revision.token(value, unit),
                                                   [edited[:cut], edited[cut:]])
                    return revision.merge_units(value, [u["id"] for u in value["units"]])
                for invalidate_first in (False, True):
                    if invalidate_first:
                        revision.invalidate(value, [unit["id"]], "追加の文脈を再確認する")
                    before = copy.deepcopy(value)
                    with self.assertRaisesRegex(revision.RevisionError, "last edit.*before regrouping"):
                        regroup()
                    self.assertEqual(value, before)
                    self.assertTrue(unit["awaiting_review"])
                    with self.assertRaisesRegex(revision.RevisionError, "last edit before editing"):
                        revision.edit(value, unit["id"], revision.token(value, unit), "次の修正。\n")
                revision.record_review(value, unit["id"], revision.token(value, unit),
                                       result_for(value, unit, "confirmed"))
                regroup()
                self.assertEqual(revision.render(value), edited if operation == "split" else
                                 "".join(u["text"] for u in value["retired"][-2:]))


class CriterionWorkflowTests(unittest.TestCase):
    def test_reader_difficulties_goals_and_registered_criteria_are_presented(self):
        value = state("確認する対象。\n")
        shown = revision.next_target(value)
        brief = shown["reference_only"]["brief"]
        self.assertEqual(brief["reader_difficulties"], BRIEF["reader_difficulties"])
        self.assertEqual(brief["desired_understanding"], BRIEF["desired_understanding"])
        self.assertEqual({c["id"] for c in shown["target"]["required_criteria"]}, {"meaning", "clarity"})
        self.assertEqual([c["id"] for c in revision.status(value)["document_criteria"]], ["coherence"])
        for name in ("reader_difficulties", "desired_understanding"):
            invalid = copy.deepcopy(BRIEF)
            del invalid[name]
            with self.assertRaises(revision.RevisionError):
                revision.initialize("原文", "原文", invalid)

    def test_two_registered_criteria_with_one_result_cannot_complete(self):
        value = state("確認する対象。\n")
        unit = value["units"][0]
        incomplete = record()
        incomplete["checks"] = incomplete["checks"][:1]
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(revision.RevisionError, "missing results.*clarity"):
            revision.record_review(value, unit["id"], revision.token(value, unit), incomplete)
        self.assertEqual(value, before)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)
        incomplete["criteria"] = ["meaning"]
        with self.assertRaisesRegex(revision.RevisionError, "criteria belong in the plan"):
            revision.record_review(value, unit["id"], revision.token(value, unit), incomplete)
        self.assertEqual(len(unit["plan"]["criteria"]), 2)

    def test_changed_registered_definition_rejects_old_results_even_with_new_target_token(self):
        value = state("対象。\n")
        unit = value["units"][0]
        previous = record()
        revision.record_review(value, unit["id"], revision.token(value, unit), previous)
        final(value)
        old_review = copy.deepcopy(unit["review"])
        brief = copy.deepcopy(value["brief"])
        brief["criteria"][1]["description"] = "読者が対象と確認事項を本文から読み取れる"
        revision.update_brief(value, brief)
        self.assertEqual(unit["state"], "recheck")
        self.assertIsNone(value["final_review"])
        with self.assertRaisesRegex(revision.RevisionError, "stale criterion version"):
            revision.record_review(value, unit["id"], revision.token(value, unit), previous)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)
        self.assertIn({"action": "review", **old_review}, unit["history"])

    def test_changed_completion_condition_invalidates_the_target_result(self):
        value = state("対象。\n")
        unit = value["units"][0]
        previous = record()
        revision.record_review(value, unit["id"], revision.token(value, unit), previous)
        plan = copy.deepcopy(unit["plan"])
        plan["criteria"]["clarity"] = "何を対象に何を確認するかを一文で読み取れる"
        revision.set_plan(value, unit["id"], revision.token(value, unit), plan)
        self.assertEqual(unit["state"], "recheck")
        self.assertEqual(unit["text"], "対象。\n")
        with self.assertRaisesRegex(revision.RevisionError, "stale criterion version"):
            revision.record_review(value, unit["id"], revision.token(value, unit), previous)

    def test_plans_cannot_contain_results_and_do_not_confirm_a_target(self):
        value = revision.initialize("対象。\n", "対象。\n", BRIEF)
        unit = value["units"][0]
        plan = fixture_plan(value)
        for field, data in (("checks", record()["checks"]), ("outcome", "confirmed"),
                            ("status", "passed")):
            invalid = copy.deepcopy(plan)
            invalid[field] = data
            with self.assertRaisesRegex(revision.RevisionError, "no results"):
                revision.set_plan(value, unit["id"], revision.token(value, unit), invalid)
        nested = copy.deepcopy(plan)
        nested["criteria"]["clarity"] = {"status": "passed", "observation": "事前の確認結果"}
        with self.assertRaises(revision.RevisionError):
            revision.set_plan(value, unit["id"], revision.token(value, unit), nested)
        revision.set_plan(value, unit["id"], revision.token(value, unit), plan)
        self.assertEqual(unit["state"], "pending")
        self.assertIsNone(unit["review"])
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)

    def test_confirmation_requires_a_plan_with_all_registered_ids(self):
        value = revision.initialize("対象。\n", "対象。\n", BRIEF)
        unit = value["units"][0]
        with self.assertRaisesRegex(revision.RevisionError, "plan requires"):
            revision.record_review(value, unit["id"], revision.token(value, unit), record())
        plan = fixture_plan(value)
        del plan["criteria"]["clarity"]
        with self.assertRaisesRegex(revision.RevisionError, "every registered criterion"):
            revision.set_plan(value, unit["id"], revision.token(value, unit), plan)
        with self.assertRaises(revision.RevisionError):
            revision.edit(value, unit["id"], revision.token(value, unit), "修正。\n")

    def test_unapplied_criteria_need_a_reason_and_duplicate_or_unknown_ids_are_rejected(self):
        value = state("# 見出し\n")
        unit = value["units"][0]
        checks = record()
        checks["checks"][1]["status"] = "not_applicable"
        with self.assertRaisesRegex(revision.RevisionError, "not_applicable reason"):
            revision.record_review(value, unit["id"], revision.token(value, unit), checks)
        checks["checks"][1]["reason"] = "この対象は見出しのみで、別途説明する用語を含まない"
        revision.record_review(value, unit["id"], revision.token(value, unit), checks)
        self.assertEqual(unit["review"]["record"]["checks"][1]["status"], "not_applicable")
        for uid in ("meaning", "unregistered"):
            invalid = record()
            invalid["checks"][1]["criterion_id"] = uid
            with self.assertRaisesRegex(revision.RevisionError, "unknown or duplicate"):
                revision.record_review(value, unit["id"], revision.token(value, unit), invalid)

    def test_required_criteria_cannot_be_silently_dropped_or_moved(self):
        value = state("対象。\n")
        for operation in ("drop", "move"):
            brief = copy.deepcopy(value["brief"])
            if operation == "drop":
                brief["criteria"].pop(1)
            else:
                brief["criteria"][1]["stage"] = "document"
            before = copy.deepcopy(value)
            with self.assertRaisesRegex(revision.RevisionError, "do not silently drop or move"):
                revision.update_brief(value, brief)
            self.assertEqual(value, before)

    def test_reader_goal_changes_invalidate_earlier_checks(self):
        value = state("対象。\n")
        accept_all(value)
        final(value)
        unit = value["units"][0]
        old_token = revision.token(value, unit)
        brief = copy.deepcopy(value["brief"])
        brief["reader_difficulties"] = "条件は分かるが、専門用語の役割が分からない"
        revision.update_brief(value, brief)
        self.assertEqual(unit["state"], "recheck")
        self.assertIsNone(value["final_review"])
        with self.assertRaisesRegex(revision.RevisionError, "stale unit token"):
            revision.record_review(value, unit["id"], old_token, record())

    def test_added_criterion_requires_a_new_plan_and_result(self):
        value = state("対象。\n")
        accept_all(value)
        final(value)
        unit = value["units"][0]
        old_result = result_for(value, unit)
        brief = copy.deepcopy(value["brief"])
        brief["criteria"].append({"id": "terms", "stage": "unit",
                                  "description": "必要な用語の意味と役割を理解できる"})
        revision.update_brief(value, brief)
        with self.assertRaisesRegex(revision.RevisionError, "every registered criterion"):
            revision.record_review(value, unit["id"], revision.token(value, unit), old_result)
        plan = copy.deepcopy(unit["plan"])
        plan["criteria"]["terms"] = "本文にある用語の対象と意味が分かる"
        revision.set_plan(value, unit["id"], revision.token(value, unit), plan)
        with self.assertRaisesRegex(revision.RevisionError, "missing results.*terms"):
            revision.record_review(value, unit["id"], revision.token(value, unit), old_result)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_document_criteria_are_covered_and_versions_are_checked(self):
        value = state("対象。\n")
        accept_all(value)
        ensure_plan(value)
        old_check = result_for(value)["checks"]
        plan = copy.deepcopy(value["document_plan"])
        plan["criteria"]["coherence"] = "原文なしでも選択から手順まで説明を追える"
        revision.set_plan(value, None, revision.document_token(value), plan)
        payload = {"original_comparison": "原文の対象と条件を全文照合した",
                   "standalone_read": "修正文だけを読んで説明の順序を確認した",
                   "checks": old_check}
        with self.assertRaisesRegex(revision.RevisionError, "stale criterion version"):
            revision.final_review(value, revision.document_token(value), payload)
        payload["checks"] = []
        with self.assertRaisesRegex(revision.RevisionError, "missing results"):
            revision.final_review(value, revision.document_token(value), payload)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)

    def test_document_hold_is_not_resolved_and_needs_revision_cannot_finish(self):
        value = state("対象。\n")
        accept_all(value)
        ensure_plan(value)
        payload = {"original_comparison": "原文の対象と条件を全文照合した",
                   "standalone_read": "修正文の説明順序には判断待ちが残る",
                   "checks": result_for(value, outcome="needs_revision")["checks"]}
        with self.assertRaisesRegex(revision.RevisionError, "return affected targets"):
            revision.final_review(value, revision.document_token(value), payload)
        check = payload["checks"][0]
        check.update({"status": "held", "reason": "説明順序に方針判断が必要",
                      "proposal": "利用者の選択を冒頭に置く案を採用する",
                      "required_decision": "文書の所有者が先に説明する内容を決める"})
        revision.final_review(value, revision.document_token(value), payload)
        result = revision.finish(value)
        self.assertFalse(result["fully_resolved"])
        self.assertEqual(result["held"][0]["id"], "document")

    def test_document_hold_requires_invalidation_even_after_rechecking_a_unit(self):
        value = state("対象。\n", limit=5)
        accept_all(value)
        ensure_plan(value)
        held = {"original_comparison": "原文との照合には方針判断が残る",
                "standalone_read": "説明の順序は読者が追える",
                "checks": result_for(value, outcome="held")["checks"]}
        revision.final_review(value, revision.document_token(value), held)
        passed = {**held, "checks": result_for(value)["checks"]}
        for payload in (held, passed):
            before = copy.deepcopy(value)
            with self.assertRaisesRegex(revision.RevisionError, "held document"):
                revision.final_review(value, revision.document_token(value), payload)
            self.assertEqual(value, before)
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
        self.assertIsNone(value["final_review"])
        with self.assertRaisesRegex(revision.RevisionError, "held document"):
            revision.final_review(value, revision.document_token(value), passed)
        # A legacy state that already overwrote a hold must not report it resolved.
        value["final_review"] = {"record": passed, "token": revision.document_token(value),
                                 "criteria": revision.validate_final_record(value, passed)}
        with self.assertRaisesRegex(revision.RevisionError, "held document"):
            revision.finish(value)
        revision.invalidate(value, [unit["id"]], "保留していた方針の判断が得られた")
        with self.assertRaisesRegex(revision.RevisionError, "unfinished targets"):
            revision.final_review(value, revision.document_token(value), passed)
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
        revision.final_review(value, revision.document_token(value), passed)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_results_are_not_derived_from_changed_text_or_plan(self):
        value = state("対象。\n")
        unit = value["units"][0]
        revision.edit(value, unit["id"], revision.token(value, unit), "明示した対象。\n")
        self.assertIsNone(unit["review"])
        self.assertEqual(unit["state"], "recheck")
        wrong = record("confirmed")
        wrong["checks"][1]["status"] = "needs_revision"
        with self.assertRaisesRegex(revision.RevisionError, "unresolved criteria"):
            revision.record_review(value, unit["id"], revision.token(value, unit), wrong)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)

    def test_same_observation_can_be_recorded_after_a_new_context_is_checked(self):
        value = state("対象。\n\n隣の説明。\n")
        first, second = value["units"]
        payload = record()
        revision.record_review(value, first["id"], revision.token(value, first), payload)
        old = revision.token(value, first)
        revision.edit(value, second["id"], revision.token(value, second), "隣の説明を明確にした。\n")
        self.assertNotEqual(old, revision.token(value, first))
        revision.record_review(value, first["id"], revision.token(value, first), payload)
        self.assertEqual(first["state"], "unchanged")
        self.assertEqual(first["review"]["record"], payload)

    def test_upgrade_retains_original_ids_history_and_attempts_but_requires_new_checks(self):
        value = state("対象。\n")
        unit = value["units"][0]
        legacy = copy.deepcopy(value)
        legacy["format"] = 1
        legacy["brief"] = {key: BRIEF[key] for key in ("purpose", "reader", "scope", "allowed_changes")}
        previous = {"token": "legacy", "outcome": "unchanged", "checks": [{"observation": "過去の確認"}]}
        legacy["units"][0].update({"review": previous, "attempts": 2, "state": "unchanged"})
        del legacy["units"][0]["plan"]
        legacy["final_review"] = {"token": "legacy-document", "original_comparison": "過去の照合"}
        upgraded = revision.upgrade(legacy, BRIEF)
        current = upgraded["units"][0]
        self.assertEqual(upgraded["original"], legacy["original"])
        self.assertEqual(revision.render(upgraded), revision.render(legacy))
        self.assertEqual(current["id"], unit["id"])
        self.assertEqual(current["attempts"], 2)
        self.assertEqual(current["state"], "recheck")
        self.assertIsNone(current["plan"])
        self.assertIsNone(current["review"])
        self.assertIn({"action": "legacy_review", "review": previous}, current["history"])
        self.assertIsNone(upgraded["final_review"])
        with self.assertRaises(revision.RevisionError):
            revision.finish(upgraded)
        self.assertEqual(legacy["format"], 1)

    def test_extend_limit_retains_hold_attempts_history_and_requires_explicit_recheck(self):
        value = state("条件が未定の説明。\n", limit=1)
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        final(value)
        old = copy.deepcopy(value)
        revision.extend_limit(value, 2, "上限値の根拠と説明追加の判断が得られた")
        self.assertEqual(unit["state"], "held")
        self.assertEqual(unit["attempts"], 1)
        self.assertEqual(unit["history"], old["units"][0]["history"])
        self.assertEqual(unit["review"], old["units"][0]["review"])
        self.assertEqual(revision.render(value), revision.render(old))
        self.assertIsNone(value["final_review"])
        self.assertEqual(value["events"][-1]["previous_max_iterations"], 1)
        self.assertEqual(value["events"][-1]["max_iterations"], 2)
        with self.assertRaisesRegex(revision.RevisionError, "held unit"):
            revision.edit(value, unit["id"], revision.token(value, unit), "根拠のある説明。\n")
        revision.invalidate(value, [unit["id"]], "上限値を確認したので説明へ反映する")
        revision.edit(value, unit["id"], revision.token(value, unit), "根拠のある説明。\n")
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit, "confirmed"))
        self.assertEqual(unit["attempts"], 2)
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_extend_limit_rejects_reset_or_unexplained_changes_without_mutation(self):
        value = state("対象。\n", limit=3)
        for maximum, reason in [(2, "下げる"), (3, "同じ"), (True, "不正な型"),
                                (4.5, "不正な型"), (4, " ")]:
            before = copy.deepcopy(value)
            with self.subTest(maximum=maximum, reason=reason), self.assertRaises(revision.RevisionError):
                revision.extend_limit(value, maximum, reason)
            self.assertEqual(value, before)


class ExpressionTrackingTests(unittest.TestCase):
    def register(self, value, unit=None, entries=None):
        unit = unit or value["units"][0]
        entries = entries or [{"id": "term_a", "quote": "用語A", "origin": "inspection",
                               "goal": "用語Aの意味と、この判断で使う役割が分かる"}]
        revision.register_expressions(value, unit["id"], revision.token(value, unit), entries)
        return revision.expression_by_id(value, entries[0]["id"])

    def explanation(self, value, unit=None, **fields):
        unit = unit or value["units"][0]
        return {"outcome": "explained", "observation": "引用した本文で対象と判断に必要な意味を確認した",
                "evidence": [{"unit_id": unit["id"], "token": revision.token(value, unit),
                              "quote": unit["text"].strip()}], **fields}

    def check(self, value, expression, result=None):
        revision.record_expression(value, expression["id"], revision.expression_version(value, expression),
                                   result or self.explanation(value))

    def test_two_expressions_require_both_results_before_unit_or_document_completion(self):
        value = state("用語Aと用語Bを判断に使う。\n")
        unit = value["units"][0]
        a = self.register(value, entries=[
            {"id": "term_a", "quote": "用語A", "origin": "user", "goal": "Aの意味が分かる"},
            {"id": "term_b", "quote": "用語B", "origin": "inspection", "goal": "Bの意味が分かる"}])
        self.check(value, a)
        with self.assertRaisesRegex(revision.RevisionError, "term_b"):
            revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
        with self.assertRaises(revision.RevisionError):
            final(value)
        self.check(value, value["expressions"]["term_b"])
        accept_all(value)
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_registration_is_atomic_and_rejects_duplicate_ids_or_result_fields(self):
        value = state("用語A。\n")
        unit = value["units"][0]
        valid = {"id": "term_a", "quote": "用語A", "origin": "user", "goal": "Aの意味が分かる"}
        for invalid in ({**valid, "id": "absent", "quote": "ない用語"},
                        {**valid, "outcome": "explained"}, valid):
            before = copy.deepcopy(value)
            with self.assertRaises(revision.RevisionError):
                self.register(value, entries=[valid, invalid])
            self.assertEqual(value, before)
        self.register(value, entries=[valid])
        with self.assertRaises(revision.RevisionError):
            self.register(value, entries=[valid])
        self.assertEqual(value["expressions"]["term_a"]["review"], None)
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))

    def test_content_token_accepts_expression_results_prepared_together(self):
        for already_saved in (False, True):
            with self.subTest(already_saved=already_saved):
                value = state("用語Aと用語B。\n", limit=5)
                unit = value["units"][0]
                self.register(value, entries=[
                    {"id": "a", "quote": "用語A", "origin": "inspection", "goal": "Aの意味が分かる"},
                    {"id": "b", "quote": "用語B", "origin": "inspection", "goal": "Bの意味が分かる"}])
                if already_saved:
                    self.check(value, value["expressions"]["a"])
                shown = revision.view(value, unit)
                results = []
                for expression in shown["required_expressions"]:
                    result = self.explanation(value)
                    result["evidence"][0]["token"] = shown["target"]["content_token"]
                    results.append((expression["id"], expression["version"], result))
                for eid, version, result in results:
                    revision.record_expression(value, eid, version, result)
                self.assertEqual(revision.view(value, unit)["target"]["content_token"],
                                 shown["target"]["content_token"])
                self.assertNotEqual(revision.token(value, unit), shown["target"]["token"])
                self.assertTrue(all(revision.expression_review_current(value, e)
                                    for e in value["expressions"].values()))
                with self.assertRaisesRegex(revision.RevisionError, "stale unit token"):
                    revision.record_review(value, unit["id"], shown["target"]["token"], result_for(value, unit))
                accept_all(value)
                final(value)
                self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_restoring_text_rejects_old_expression_versions_and_evidence(self):
        value = state("用語Aの元の説明。\n", limit=5)
        unit = value["units"][0]
        expression = self.register(value)
        old_version = revision.expression_version(value, expression)
        prepared = self.explanation(value)
        prepared["evidence"][0]["token"] = revision.view(value, unit)["target"]["content_token"]
        original = unit["text"]
        revision.edit(value, unit["id"], revision.token(value, unit), "用語Aの新しい説明。\n")
        self.check(value, expression)
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit, "confirmed"))
        revision.edit(value, unit["id"], revision.token(value, unit), original)
        before = copy.deepcopy(value)
        with self.assertRaisesRegex(revision.RevisionError, "stale expression version"):
            revision.record_expression(value, expression["id"], old_version, prepared)
        with self.assertRaisesRegex(revision.RevisionError, "stale expression evidence"):
            self.check(value, expression, prepared)
        self.assertEqual(value, before)
        self.check(value, expression)
        revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit, "confirmed"))
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_repeated_quote_requires_an_exact_character_position(self):
        value = state("用語Aと用語A。\n")
        with self.assertRaisesRegex(revision.RevisionError, "uniquely"):
            self.register(value)
        expression = self.register(value, entries=[
            {"id": "term_a", "quote": "用語A", "start": 4, "origin": "inspection", "goal": "意味が分かる"}])
        self.assertEqual(expression["source"]["start"], 4)
        result = self.explanation(value)
        result["evidence"][0].update(quote="用語A", start=3)
        with self.assertRaisesRegex(revision.RevisionError, "position"):
            self.check(value, expression, result)

    def test_evidence_can_reference_another_paragraph_without_deleting_the_source_word(self):
        value = state("用語Aを使う。\n\n用語Aは利用できる件数の上限を示す。\n")
        expression = self.register(value)
        self.check(value, expression, self.explanation(value, value["units"][1], outcome="unchanged",
                                                     reason="後段に何の上限かを説明している"))
        accept_all(value)
        final(value)
        result = revision.finish(value)
        self.assertEqual(result["text"], value["original"])
        self.assertTrue(result["fully_resolved"])
        self.assertEqual(expression["source"]["quote"], "用語A")

    def test_absent_explanation_is_rejected_even_with_a_fresh_unit_token(self):
        value = state("用語A。\n")
        expression = self.register(value)
        result = self.explanation(value)
        result["evidence"][0]["quote"] = "本文には存在しない説明"
        with self.assertRaisesRegex(revision.RevisionError, "quote"):
            self.check(value, expression, result)
        self.assertIsNone(expression["review"])

    def test_changed_explanation_invalidates_a_different_owner_and_old_evidence_cannot_be_relabelled(self):
        value = state("用語A。\n\nAは上限を指す。\n", limit=5)
        owner, explanation = value["units"]
        expression = self.register(value)
        result = self.explanation(value, explanation)
        self.check(value, expression, result)
        accept_all(value)
        final(value)
        revision.edit(value, explanation["id"], revision.token(value, explanation), "別の説明。\n")
        self.assertFalse(revision.expression_review_current(value, expression))
        self.assertEqual(owner["state"], "recheck")
        with self.assertRaises(revision.RevisionError):
            self.check(value, expression, result)
        result["evidence"][0]["token"] = revision.token(value, explanation)
        with self.assertRaisesRegex(revision.RevisionError, "quote"):
            self.check(value, expression, result)
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)

    def test_body_reader_context_and_goal_changes_require_new_expression_results(self):
        for change in ("body", "reader", "goal", "neighbor"):
            with self.subTest(change=change):
                value = state("用語A。\n\n後段。\n")
                owner = value["units"][0]
                expression = self.register(value)
                version = revision.expression_version(value, expression)
                result = self.explanation(value)
                self.check(value, expression, result)
                accept_all(value)
                if change == "body":
                    revision.edit(value, owner["id"], revision.token(value, owner), "用語Aの説明。\n")
                elif change == "reader":
                    brief = copy.deepcopy(value["brief"])
                    brief["reader_difficulties"] = "用語を既知としない"
                    revision.update_brief(value, brief)
                elif change == "goal":
                    revision.change_expression_goal(value, expression["id"], version, "Aを何の判断に使えるか分かる")
                else:
                    neighbor = value["units"][1]
                    revision.edit(value, neighbor["id"], revision.token(value, neighbor), "後段の条件。\n")
                self.assertFalse(revision.expression_review_current(value, expression))
                with self.assertRaisesRegex(revision.RevisionError, "stale expression version"):
                    revision.record_expression(value, expression["id"], version, result)

    def test_changing_only_the_expression_result_invalidates_the_unit_result(self):
        value = state("用語A。\n", limit=5)
        unit = value["units"][0]
        expression = self.register(value)
        self.check(value, expression)
        accept_all(value)
        final(value)
        old_token = revision.token(value, unit)
        self.check(value, expression, self.explanation(value, outcome="needs_revision",
                                                     observation="Aを何に使うかは本文から分からない"))
        self.assertNotEqual(revision.token(value, unit), old_token)
        self.assertEqual(unit["state"], "recheck")
        with self.assertRaises(revision.RevisionError):
            revision.finish(value)
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, unit["id"], old_token, result_for(value, unit))
        self.assertIsNone(value["final_review"])

    def test_successful_expression_results_reject_prepared_unit_checks(self):
        for outcome in ("explained", "unchanged"):
            for existing_result in (False, True):
                with self.subTest(outcome=outcome, existing_result=existing_result):
                    value = state("用語A。\n", limit=5)
                    unit = value["units"][0]
                    expression = self.register(value)
                    if existing_result:
                        self.check(value, expression)
                        accept_all(value)
                        final(value)
                    old_token = revision.token(value, unit)
                    prepared = result_for(value, unit)
                    result = self.explanation(value, outcome=outcome)
                    if outcome == "unchanged":
                        result["reason"] = "必要な意味が本文で既に説明されている"
                    self.check(value, expression, result)
                    with self.assertRaisesRegex(revision.RevisionError, "stale unit token"):
                        revision.record_review(value, unit["id"], old_token, prepared)
                    shown = revision.view(value, unit)
                    self.assertTrue(shown["required_expressions"][0]["review_current"])
                    revision.record_review(value, unit["id"], shown["target"]["token"], result_for(value, unit))
                    final(value)
                    self.assertTrue(revision.finish(value)["fully_resolved"])

    def test_unchanged_requires_a_reason_and_held_requires_actionable_details(self):
        value = state("用語A。\n")
        expression = self.register(value)
        for outcome in ("unchanged", "held"):
            with self.subTest(outcome=outcome), self.assertRaises(revision.RevisionError):
                self.check(value, expression, self.explanation(value, outcome=outcome))
        self.assertIsNone(expression["review"])

    def test_iteration_limit_requires_a_documented_hold_for_each_unresolved_expression(self):
        value = state("用語A。\n", limit=1)
        unit = value["units"][0]
        expression = self.register(value)
        self.check(value, expression, self.explanation(value, outcome="needs_revision"))
        result = result_for(value, unit)
        result["outcome"] = "needs_revision"
        before = copy.deepcopy(value)
        with self.assertRaises(revision.RevisionError):
            revision.record_review(value, unit["id"], revision.token(value, unit), result)
        self.assertEqual(value, before)
        self.check(value, expression, self.explanation(value, outcome="held", reason="Aの意味を確定できない",
            proposal="意味の根拠を確認して説明を補う", required_decision="根拠資料と説明追加の可否を確認する"))
        result["outcome"] = "held"
        revision.record_review(value, unit["id"], revision.token(value, unit), result)
        self.assertEqual(unit["attempts"], 1)
        self.assertTrue(unit["review"]["limit_reached"])
        final(value)
        self.assertFalse(revision.finish(value)["fully_resolved"])

    def test_held_expression_blocks_mutation_but_can_be_reported_and_explicitly_reopened(self):
        value = state("用語A。\n\n独立した説明。\n", limit=5)
        unit = value["units"][0]
        expression = self.register(value)
        self.check(value, expression, self.explanation(value, outcome="held", reason="Aの定義の根拠が不足",
                 proposal="定義を確認して判断に必要な意味を添える", required_decision="定義の一次資料を確認する"))
        self.assertTrue(revision.view(value, unit)["target"]["hold_active"])
        self.assertEqual(revision.next_target(value)["target"]["id"], value["units"][1]["id"])
        for operation in (
                lambda: self.check(value, expression),
                lambda: revision.edit(value, unit["id"], revision.token(value, unit), "用語Aの説明。\n"),
                lambda: revision.merge_units(value, [u["id"] for u in value["units"]])):
            with self.assertRaises(revision.RevisionError):
                operation()
        result = result_for(value, unit)
        result["outcome"] = "held"
        revision.record_review(value, unit["id"], revision.token(value, unit), result)
        other = value["units"][1]
        revision.record_review(value, other["id"], revision.token(value, other), result_for(value, other))
        final(value)
        report = revision.finish(value)
        self.assertFalse(report["fully_resolved"])
        self.assertTrue(any(h.get("kind") == "expression" for h in report["held"]))
        brief = copy.deepcopy(value["brief"])
        brief["reader"] = "別の担当者"
        revision.update_brief(value, brief)
        self.assertTrue(revision.expression_has_hold(expression))
        revision.invalidate(value, iter([unit["id"]]), "不足する定義の根拠を得た")
        self.assertFalse(revision.unit_has_hold(value, unit))
        self.check(value, expression)
        accept_all(value)
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])
        self.assertEqual(len(expression["history"]), 2)

    def test_split_merge_and_reorder_preserve_ids_sources_history_and_require_current_checks(self):
        value = state("用語A。\n\n用語B。\n\n後段。\n", limit=8)
        first, second, last = value["units"]
        a = self.register(value)
        b = self.register(value, second, [{"id": "term_b", "quote": "用語B", "origin": "user", "goal": "Bの意味が分かる"}])
        self.check(value, a)
        self.check(value, b, self.explanation(value, second))
        accept_all(value)
        original_source = copy.deepcopy(a["source"])
        merged_id = revision.merge_units(value, [first["id"], second["id"]])
        merged = revision.unit_by_id(value, merged_id)
        self.assertEqual(a["owners"], [merged_id])
        ids = revision.split_unit(value, merged_id, revision.token(value, merged), [first["text"], second["text"]])
        self.assertEqual(a["owners"], ids)
        self.assertEqual(b["owners"], ids)
        revision.reorder(value, [last["id"], *ids])
        revision.validate_state(value)
        self.assertEqual(set(value["expressions"]), {"term_a", "term_b"})
        self.assertEqual(a["source"], original_source)
        self.assertEqual(len(a["history"]), 1)
        self.assertFalse(revision.expression_review_current(value, a))
        for unit in value["units"]:
            if unit["id"] in ids:
                with self.assertRaisesRegex(revision.RevisionError, "term_a"):
                    revision.record_review(value, unit["id"], revision.token(value, unit), result_for(value, unit))
        self.check(value, a, self.explanation(value, revision.unit_by_id(value, ids[0])))
        self.check(value, b, self.explanation(value, revision.unit_by_id(value, ids[1])))
        accept_all(value)
        final(value)
        self.assertTrue(revision.finish(value)["fully_resolved"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.json"
        self.original = self.root / "input.md"
        self.original.write_bytes(DOCUMENT.encode())
        self.brief = self.root / "brief.json"
        self.brief.write_text(json.dumps(BRIEF), encoding="utf-8")

    def cli(self, *args, success=True, umask=-1):
        result = subprocess.run([sys.executable, str(SCRIPT), "--state", str(self.path),
                                 *map(str, args)], capture_output=True, text=True, umask=umask)
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        return result

    def test_cli_rejects_held_document_overwrite_inline_split_and_list_reorder_without_saving(self):
        value = state("対象。\n")
        accept_all(value)
        ensure_plan(value)
        payload = {"original_comparison": "原文との照合で方針判断が残る",
                   "standalone_read": "本文の対象と条件は読める",
                   "checks": result_for(value, outcome="held")["checks"]}
        revision.final_review(value, revision.document_token(value), payload)
        revision.save(self.path, value)
        result_path = self.root / "result.json"
        result_path.write_text(json.dumps({**payload, "checks": result_for(value)["checks"]}))
        before = self.path.read_bytes()
        self.cli("final-review", "--token", revision.document_token(value), "--file", result_path, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        value = state("前 `--option` 後。\n")
        revision.save(self.path, value)
        unit = value["units"][0]
        result_path.write_text(json.dumps(["前 `--", "option` 後。\n"]))
        before = self.path.read_bytes()
        self.cli("split", "--unit", unit["id"], "--token", revision.token(value, unit),
                 "--file", result_path, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        value = state("1. first\n2. second\n\n後の段落。\n")
        unit = value["units"][0]
        ids = revision.split_unit(value, unit["id"], revision.token(value, unit),
                                  ["1. first\n", "2. second\n\n"])
        revision.save(self.path, value)
        before = self.path.read_bytes()
        self.cli("reorder", "--units", ids[0], value["units"][-1]["id"], ids[1], success=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_batch_expression_checks_use_content_tokens_and_keep_aggregate_checks_fresh(self):
        value = state("用語Aと用語B。\n", limit=5)
        unit = value["units"][0]
        revision.register_expressions(value, unit["id"], revision.token(value, unit), [
            {"id": "a", "quote": "用語A", "origin": "inspection", "goal": "Aの意味が分かる"},
            {"id": "b", "quote": "用語B", "origin": "inspection", "goal": "Bの意味が分かる"}])
        a = value["expressions"]["a"]
        revision.record_expression(value, "a", revision.expression_version(value, a), {
            "outcome": "explained", "observation": "Aの意味を本文で確認した",
            "evidence": [{"unit_id": unit["id"], "token": revision.token(value, unit), "quote": "用語A"}]})
        revision.save(self.path, value)
        shown = json.loads(self.cli("show", "--unit", unit["id"]).stdout)
        for expression in shown["required_expressions"]:
            result_path = self.root / (expression["id"] + ".json")
            result_path.write_text(json.dumps({
                "outcome": "explained", "observation": "用語の意味を同じ本文で確認した",
                "evidence": [{"unit_id": unit["id"], "token": shown["target"]["content_token"],
                              "quote": expression["source"]["quote"]}]}))
            self.cli("expression-check", "--id", expression["id"], "--version", expression["version"],
                     "--file", result_path)
        current = revision.load(self.path)
        self.assertTrue(all(revision.expression_review_current(current, e)
                            for e in current["expressions"].values()))
        aggregate = self.root / "aggregate.json"
        aggregate.write_text(json.dumps(result_for(current, current["units"][0])))
        before = self.path.read_bytes()
        self.cli("record", "--unit", unit["id"], "--token", shown["target"]["token"],
                 "--file", aggregate, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        fresh = json.loads(self.cli("show", "--unit", unit["id"]).stdout)
        self.cli("record", "--unit", unit["id"], "--token", fresh["target"]["token"], "--file", aggregate)
        self.assertEqual(revision.load(self.path)["units"][0]["state"], "unchanged")

    def test_cli_expression_coverage_and_evidence_errors_preserve_state(self):
        value = state("用語Aと用語B。\n")
        unit = value["units"][0]
        revision.save(self.path, value)
        registrations = self.root / "expressions.json"
        registrations.write_text(json.dumps([
            {"id": "a", "quote": "用語A", "origin": "user", "goal": "Aの意味が分かる"},
            {"id": "b", "quote": "用語B", "origin": "inspection", "goal": "Bの意味が分かる"}]))
        shown = json.loads(self.cli("expressions", "--unit", unit["id"], "--token",
                                    revision.token(value, unit), "--file", registrations).stdout)
        expression_result = self.root / "expression-result.json"
        expression_result.write_text(json.dumps({"outcome": "explained", "observation": "Aの意味を本文で確認した",
            "evidence": [{"unit_id": unit["id"], "token": shown["target"]["token"], "quote": "存在しない引用"}]}))
        before = self.path.read_bytes()
        self.cli("expression-check", "--id", "a", "--version", shown["required_expressions"][0]["version"],
                 "--file", expression_result, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        result = json.loads(expression_result.read_text())
        result["evidence"][0]["quote"] = "用語A"
        expression_result.write_text(json.dumps(result))
        self.cli("expression-check", "--id", "a", "--version", shown["required_expressions"][0]["version"],
                 "--file", expression_result)
        value = revision.load(self.path)
        unit = value["units"][0]
        aggregate = self.root / "aggregate.json"
        aggregate.write_text(json.dumps(result_for(value, unit)))
        before = self.path.read_bytes()
        self.cli("record", "--unit", unit["id"], "--token", revision.token(value, unit), "--file", aggregate,
                 success=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(revision.load(self.path)["original"], "用語Aと用語B。\n")

    def test_cli_hold_requires_invalidation_before_a_new_result(self):
        value = state("対象。\n")
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        final(value)
        revision.save(self.path, value)
        before = self.path.read_bytes()
        result_file = self.root / "result.json"
        result_file.write_text(json.dumps(result_for(value, unit)))
        self.cli("record", "--unit", unit["id"], "--token", revision.token(value, unit),
                 "--file", result_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        output, report = self.root / "output.md", self.root / "report.json"
        self.cli("finish", "--output", output, "--report", report)
        self.assertFalse(json.loads(report.read_text())["fully_resolved"])
        self.cli("invalidate", "--units", unit["id"], "--reason", "根拠となる情報と判断を得た")
        shown = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        self.assertFalse(shown["hold_active"])
        self.cli("record", "--unit", unit["id"], "--token", shown["token"], "--file", result_file)
        value = revision.load(self.path)
        final(value)
        revision.save(self.path, value)
        self.cli("finish", "--output", output, "--report", report)
        self.assertTrue(json.loads(report.read_text())["fully_resolved"])

    def test_cli_mixed_held_check_cannot_be_overwritten_or_edited(self):
        value = state("対象。\n")
        unit = value["units"][0]
        revision.save(self.path, value)
        result_file = self.root / "mixed.json"
        mixed = result_for(value, unit, "held")
        mixed["outcome"] = "needs_revision"
        mixed["checks"][1].update(status="needs_revision", observation="対象と条件の関係を明示する必要がある")
        result_file.write_text(json.dumps(mixed))
        self.cli("record", "--unit", unit["id"], "--token", revision.token(value, unit), "--file", result_file)
        shown = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        self.assertTrue(shown["hold_active"])
        self.assertEqual(shown["state"], "recheck")
        before = self.path.read_bytes()
        result_file.write_text(json.dumps(result_for(value, unit)))
        self.cli("record", "--unit", unit["id"], "--token", shown["token"], "--file", result_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        edit_file = self.root / "edit.md"
        edit_file.write_text("条件を明示した説明。\n")
        self.cli("edit", "--unit", unit["id"], "--token", shown["token"], "--file", edit_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.cli("invalidate", "--units", unit["id"], "--reason", "保留事項の判断を確認した")
        shown = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        self.assertFalse(shown["hold_active"])
        self.cli("record", "--unit", unit["id"], "--token", shown["token"], "--file", result_file)

    def test_cli_old_false_marker_does_not_override_a_persisted_held_check(self):
        value = state("対象。\n")
        unit = value["units"][0]
        mixed = result_for(value, unit, "held")
        mixed["outcome"] = "needs_revision"
        mixed["checks"][1].update(status="needs_revision", observation="対象と条件を明示する必要がある")
        revision.record_review(value, unit["id"], revision.token(value, unit), mixed)
        unit["hold_active"] = False
        unit.pop("hold_released_token", None)
        self.path.write_text(json.dumps(value))
        shown = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        self.assertTrue(shown["hold_active"])
        before = self.path.read_bytes()
        result_file, edit_file = self.root / "new-result.json", self.root / "edit.md"
        result_file.write_text(json.dumps(result_for(value, unit)))
        edit_file.write_text("条件を明示した説明。\n")
        self.cli("record", "--unit", unit["id"], "--token", shown["token"], "--file", result_file, success=False)
        self.cli("edit", "--unit", unit["id"], "--token", shown["token"], "--file", edit_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        old_token = unit["review"]["token"]
        self.cli("invalidate", "--units", unit["id"], "--reason", "不足していた情報と判断を確認できた")
        current = revision.load(self.path)
        self.assertEqual(current["units"][0]["hold_released_token"], old_token)
        shown = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        self.assertFalse(shown["hold_active"])
        self.cli("record", "--unit", unit["id"], "--token", shown["token"], "--file", result_file)
        current = revision.load(self.path)
        final(current)
        revision.save(self.path, current)
        report = self.root / "report.json"
        self.cli("finish", "--output", self.root / "revised.md", "--report", report)
        self.assertTrue(json.loads(report.read_text())["fully_resolved"])

    def test_cli_migrates_a_verified_old_release_and_resumes_editing(self):
        value = state("対象。\n")
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        old_token = unit["review"]["token"]
        revision.invalidate(value, [unit["id"]], "必要な情報と判断を得た")
        del unit["hold_released_token"]
        self.path.write_text(json.dumps(value))
        migrated = json.loads(self.cli("migrate-release", "--unit", unit["id"],
                                      "--token", revision.token(value, unit), "--review-token", old_token,
                                      "--evidence", "旧ログのinvalidate対象と判断を確認した").stdout)["target"]
        self.assertFalse(migrated["hold_active"])
        current = revision.load(self.path)
        self.assertEqual(current["units"][0]["generation"], unit["generation"])
        self.assertEqual(current["units"][0]["attempts"], unit["attempts"])
        edit_file = self.root / "edit.md"
        edit_file.write_text("確認した条件を明示した説明。\n")
        edited = json.loads(self.cli("edit", "--unit", unit["id"], "--token", migrated["token"],
                                    "--file", edit_file).stdout)["target"]
        current = revision.load(self.path)
        result_file = self.root / "new-result.json"
        result_file.write_text(json.dumps(result_for(current, current["units"][0], "confirmed")))
        self.cli("record", "--unit", unit["id"], "--token", edited["token"], "--file", result_file)
        current = revision.load(self.path)
        final(current)
        revision.save(self.path, current)
        report = self.root / "report.json"
        self.cli("finish", "--output", self.root / "revised.md", "--report", report)
        self.assertTrue(json.loads(report.read_text())["fully_resolved"])

    def test_cli_release_migration_rejects_current_holds_or_missing_evidence(self):
        value = state("対象。\n")
        unit = value["units"][0]
        mixed = result_for(value, unit, "held")
        mixed["outcome"] = "needs_revision"
        mixed["checks"][1].update(status="needs_revision", observation="対象の条件を明示する必要がある")
        revision.record_review(value, unit["id"], revision.token(value, unit), mixed)
        unit["hold_active"] = False
        del unit["hold_released_token"]
        self.path.write_text(json.dumps(value))
        before = self.path.read_bytes()
        self.cli("migrate-release", "--unit", unit["id"], "--token", revision.token(value, unit),
                 "--review-token", unit["review"]["token"], "--evidence", "旧ログを確認した", success=False)
        self.assertEqual(self.path.read_bytes(), before)
        revision.invalidate(value, [unit["id"]], "旧版で必要な判断を得た")
        del unit["hold_released_token"]
        self.path.write_text(json.dumps(value))
        before = self.path.read_bytes()
        for token_value, evidence in (("wrong-review-token", "旧ログを確認した"),
                                      (unit["review"]["token"], " ")):
            self.cli("migrate-release", "--unit", unit["id"], "--token", revision.token(value, unit),
                     "--review-token", token_value, "--evidence", evidence, success=False)
            self.assertEqual(self.path.read_bytes(), before)

    def test_regrouping_blocks_held_and_unchecked_edits_without_rewriting_state(self):
        for operation in ("split", "merge"):
            for barrier in ("held", "awaiting_review"):
                with self.subTest(operation=operation, barrier=barrier):
                    value = state("前の説明。\n\n後の説明。\n")
                    if operation == "split":
                        revision.merge_units(value, [u["id"] for u in value["units"]])
                    unit = value["units"][-1]
                    if barrier == "held":
                        revision.record_review(value, unit["id"], revision.token(value, unit),
                                               result_for(value, unit, "held"))
                    else:
                        revision.edit(value, unit["id"], revision.token(value, unit),
                                      unit["text"].replace("説明", "明確な説明", 1))
                    revision.save(self.path, value)
                    parts_file = self.root / "parts.json"
                    if operation == "split":
                        cut = unit["text"].index("\n\n") + 2
                        parts_file.write_text(json.dumps([unit["text"][:cut], unit["text"][cut:]]))
                    def regroup(success):
                        if operation == "split":
                            current = revision.load(self.path)
                            self.cli("split", "--unit", unit["id"], "--token",
                                     revision.token(current, current["units"][0]),
                                     "--file", parts_file, success=success)
                        else:
                            self.cli("merge", "--units", *[u["id"] for u in value["units"]],
                                     success=success)
                    before = self.path.read_bytes()
                    regroup(False)
                    self.assertEqual(self.path.read_bytes(), before)
                    if barrier == "held":
                        self.cli("invalidate", "--units", unit["id"], "--reason", "不足条件を確認した")
                    else:
                        result_file = self.root / "result.json"
                        result_file.write_text(json.dumps(result_for(value, unit, "confirmed")))
                        self.cli("record", "--unit", unit["id"], "--token", revision.token(value, unit),
                                 "--file", result_file)
                    regroup(True)
                    current = revision.load(self.path)
                    self.assertEqual(revision.render(current), revision.render(value))
                    self.assertTrue(any(u["id"] == unit["id"] for u in current["retired"]))
                    output = self.root / "output.md"
                    self.cli("finish", "--output", output, success=False)
                    self.assertFalse(output.exists())

    def test_missing_dependency_reports_required_setup_without_writing_state(self):
        result = subprocess.run([sys.executable, "-S", str(SCRIPT), "--state", str(self.path),
                                 "status"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirements.txt", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.path.exists())

    def test_non_object_state_is_rejected_without_rewriting_input(self):
        for invalid in ([], "text", None, 42, True):
            with self.subTest(value=invalid):
                self.path.write_text(json.dumps(invalid))
                before = self.path.read_bytes()
                result = self.cli("status", success=False)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("state must be an object", result.stderr)
                self.assertEqual(self.path.read_bytes(), before)

    def test_cli_roundtrip_and_failed_finish_leaves_output_untouched(self):
        self.cli("init", "--original", self.original, "--brief", self.brief)
        output = self.root / "output.md"
        output.write_text("既存の出力", encoding="utf-8")
        before = self.path.read_bytes()
        self.cli("finish", "--output", output, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(output.read_text(), "既存の出力")
        value = revision.load(self.path)
        accept_all(value)
        final(value)
        revision.save(self.path, value)
        report = self.root / "report.json"
        self.cli("finish", "--output", output, "--report", report)
        self.assertEqual(output.read_bytes(), self.original.read_bytes())
        self.assertTrue(json.loads(report.read_text())["fully_resolved"])

    def test_cli_separate_plan_and_results_enforce_coverage_before_export(self):
        self.original.write_text("対象と条件の説明。\n")
        self.cli("init", "--original", self.original, "--brief", self.brief)
        target = json.loads(self.cli("next").stdout)["target"]
        plan_file = self.root / "plan.json"
        plan = fixture_plan(revision.load(self.path))
        plan_file.write_text(json.dumps({**plan, "outcome": "confirmed"}))
        before = self.path.read_bytes()
        self.cli("plan", "--unit", target["id"], "--token", target["token"],
                 "--file", plan_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        plan_file.write_text(json.dumps(plan))
        target = json.loads(self.cli("plan", "--unit", target["id"], "--token", target["token"],
                                     "--file", plan_file).stdout)["target"]
        self.assertEqual(target["state"], "pending")
        self.assertIsNone(revision.load(self.path)["units"][0]["review"])
        output = self.root / "output.md"
        self.cli("finish", "--output", output, success=False)
        result_file = self.root / "result.json"
        payload = record()
        result_file.write_text(json.dumps({**payload, "checks": payload["checks"][:1]}))
        before = self.path.read_bytes()
        self.cli("record", "--unit", target["id"], "--token", target["token"],
                 "--file", result_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.cli("finish", "--output", output, success=False)
        self.assertFalse(output.exists())
        result_file.write_text(json.dumps(payload))
        current = json.loads(self.cli("record", "--unit", target["id"], "--token", target["token"],
                                      "--file", result_file).stdout)
        plan_file.write_text(json.dumps(fixture_plan(revision.load(self.path), "document")))
        current = json.loads(self.cli("plan", "--document", "--token", current["document_token"],
                                      "--file", plan_file).stdout)
        value = revision.load(self.path)
        result_file.write_text(json.dumps({
            "original_comparison": "全文の対象・条件と元の説明を照合した",
            "standalone_read": "本文から対象と条件の関係を理解できる",
            "checks": result_for(value)["checks"]}))
        self.cli("final-review", "--token", current["document_token"], "--file", result_file)
        report = self.root / "report.json"
        self.cli("finish", "--output", output, "--report", report)
        self.assertEqual(output.read_bytes(), self.original.read_bytes())
        self.assertTrue(json.loads(report.read_text())["fully_resolved"])

    def test_cli_upgrade_preserves_legacy_history_and_rejects_old_completion(self):
        legacy = state("対象。\n")
        accept_all(legacy)
        final(legacy)
        legacy["format"] = 1
        legacy["brief"] = {key: BRIEF[key] for key in ("purpose", "reader", "scope", "allowed_changes")}
        del legacy["document_plan"], legacy["document_history"]
        for unit in legacy["units"]:
            del unit["plan"]
        self.path.write_text(json.dumps(legacy))
        before = self.path.read_bytes()
        self.cli("status", success=False)
        self.cli("upgrade", "--brief", self.brief, "--max-iterations", 1, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        current = json.loads(self.cli("upgrade", "--brief", self.brief,
                                      "--max-iterations", 6).stdout)
        upgraded = revision.load(self.path)
        self.assertEqual(current["counts"], {"recheck": 1})
        self.assertEqual(upgraded["max_iterations"], 6)
        unit = upgraded["units"][0]
        self.assertEqual(unit["id"], legacy["units"][0]["id"])
        self.assertEqual(unit["attempts"], legacy["units"][0]["attempts"])
        self.assertEqual(unit["history"][:-1], legacy["units"][0]["history"])
        self.assertEqual(upgraded["document_history"][0]["review"], legacy["final_review"])
        self.assertIsNone(unit["review"])
        self.assertIsNone(upgraded["final_review"])
        output = self.root / "output.md"
        self.cli("finish", "--output", output, success=False)
        self.assertFalse(output.exists())

    def test_cli_resumes_current_state_at_the_limit_after_new_information(self):
        value = state("条件が未定の説明。\n", limit=1)
        unit = value["units"][0]
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        revision.save(self.path, value)
        self.cli("invalidate", "--units", unit["id"], "--reason", "必要な上限値を確認できた")
        target = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        edit_file = self.root / "edit.md"
        edit_file.write_text("根拠のある説明。\n")
        before = self.path.read_bytes()
        self.cli("edit", "--unit", unit["id"], "--token", target["token"],
                 "--file", edit_file, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        extended = self.cli("extend-limit", "--max-iterations", 2,
                            "--reason", "得られた根拠を反映し再確認する")
        self.assertEqual(json.loads(extended.stdout)["max_iterations"], 2)
        current = revision.load(self.path)
        self.assertEqual(current["format"], 2)
        self.assertEqual(current["max_iterations"], 2)
        self.assertEqual(current["units"][0]["attempts"], 1)
        self.assertEqual(current["units"][0]["history"], value["units"][0]["history"])
        self.cli("edit", "--unit", unit["id"], "--token", target["token"], "--file", edit_file)
        current = revision.load(self.path)
        target = json.loads(self.cli("show", "--unit", unit["id"]).stdout)["target"]
        result_file = self.root / "result.json"
        result_file.write_text(json.dumps(result_for(current, current["units"][0], "confirmed")))
        self.cli("record", "--unit", unit["id"], "--token", target["token"], "--file", result_file)
        self.assertEqual(revision.load(self.path)["units"][0]["attempts"], 2)

    def test_final_review_rejects_non_object_json_without_changing_state(self):
        self.cli("init", "--original", self.original, "--brief", self.brief)
        value = revision.load(self.path)
        accept_all(value)
        revision.save(self.path, value)
        token = revision.document_token(value)
        before = self.path.read_bytes()
        record_file = self.root / "final-review.json"
        for invalid in ([], "text", None, 42, True):
            with self.subTest(value=invalid):
                record_file.write_text(json.dumps(invalid))
                result = self.cli("final-review", "--token", token, "--file", record_file,
                                  success=False)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("must be an object", result.stderr)
                self.assertEqual(self.path.read_bytes(), before)

    def test_cli_rejects_state_overwrite_bad_split_and_stale_edit(self):
        self.cli("init", "--original", self.original, "--brief", self.brief)
        before = self.path.read_bytes()
        self.cli("init", "--original", self.original, "--brief", self.brief, success=False)
        self.assertEqual(self.path.read_bytes(), before)
        target = json.loads(self.cli("next").stdout)["target"]
        edit_file = self.root / "edit.txt"
        edit_file.write_text("変更。\n")
        self.cli("edit", "--unit", target["id"], "--token", "stale", "--file", edit_file,
                 success=False)
        self.assertEqual(self.path.read_bytes(), before)
        parts = self.root / "parts.json"
        parts.write_text(json.dumps(["欠落", "した本文"]))
        self.cli("split", "--unit", target["id"], "--token", target["token"], "--file", parts,
                 success=False)
        self.assertEqual(self.path.read_bytes(), before)

    def test_finish_preserves_existing_file_mode(self):
        self.cli("init", "--original", self.original, "--brief", self.brief)
        value = revision.load(self.path)
        accept_all(value)
        final(value)
        revision.save(self.path, value)
        output = self.root / "output.md"
        output.write_text("旧文書")
        output.chmod(0o644)
        self.cli("finish", "--output", output)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
        self.assertEqual(output.read_bytes(), self.original.read_bytes())

    def test_new_output_and_report_modes_follow_umask_and_state_stays_private(self):
        self.cli("init", "--original", self.original, "--brief", self.brief, umask=0o022)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        value = revision.load(self.path)
        accept_all(value)
        final(value)
        revision.save(self.path, value)
        for mask in (0o022, 0o027, 0o077):
            output, report = self.root / f"output-{mask}.md", self.root / f"report-{mask}.json"
            with self.subTest(umask=mask):
                self.cli("finish", "--output", output, "--report", report, umask=mask)
                for path in (output, report):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o666 & ~mask)
                self.assertEqual(output.read_bytes(), self.original.read_bytes())
        output, report = self.root / "existing.md", self.root / "existing.json"
        output.write_text("既存の本文")
        report.write_text("既存のレポート")
        output.chmod(0o644)
        report.chmod(0o640)
        self.cli("finish", "--output", output, "--report", report, umask=0o077)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(report.stat().st_mode), 0o640)

    def test_temporary_name_collision_does_not_remove_the_existing_file(self):
        existing = self.root / ".revision-collision.tmp"
        existing.write_text("別の処理のファイル")
        with patch.object(revision.secrets, "token_hex", return_value="collision"):
            with self.assertRaises(FileExistsError):
                revision.stage_file(self.root / "output.md", b"new output")
        self.assertEqual(existing.read_text(), "別の処理のファイル")
        self.assertFalse((self.root / "output.md").exists())

    def test_finish_rejects_symlinks_without_replacing_link_or_other_outputs(self):
        self.cli("init", "--original", self.original, "--brief", self.brief)
        value = revision.load(self.path)
        accept_all(value)
        final(value)
        revision.save(self.path, value)
        target = self.root / "target.md"
        target.write_text("リンク先の文書")
        output = self.root / "linked.md"
        output.symlink_to(target)
        self.cli("finish", "--output", output, success=False)
        self.assertTrue(output.is_symlink())
        self.assertEqual(target.read_text(), "リンク先の文書")
        normal = self.root / "normal.md"
        normal.write_text("既存文書")
        self.cli("finish", "--output", normal, "--report", output, success=False)
        self.assertEqual(normal.read_text(), "既存文書")

        self.assertTrue(output.is_symlink())
        with self.assertRaises(revision.RevisionError):
            revision.write_atomic(output, "置換しない")
        backing = self.root / "backing-state.json"
        self.path.rename(backing)
        self.path.symlink_to(backing)
        before = backing.read_bytes()
        self.cli("finish", "--output", normal, success=False)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(backing.read_bytes(), before)
        self.assertEqual(normal.read_text(), "既存文書")

    def test_invalid_report_destination_leaves_document_and_state_unchanged(self):
        self.cli("init", "--original", self.original, "--brief", self.brief)
        value = revision.load(self.path)
        accept_all(value)
        final(value)
        revision.save(self.path, value)
        output = self.root / "output.md"
        output.write_text("既存の本文")
        before = self.path.read_bytes()
        directory = self.root / "report-dir"
        directory.mkdir()
        file_parent = self.root / "file-parent"
        file_parent.write_text("通常ファイル")
        for report in (directory, file_parent / "report.json"):
            with self.subTest(report=report):
                self.cli("finish", "--output", output, "--report", report, success=False)
                self.assertEqual(output.read_text(), "既存の本文")
                self.assertEqual(self.path.read_bytes(), before)
        # A valid finish only reads its already-confirmed state.
        self.cli("finish", "--output", output, "--report", self.root / "report.json")
        self.assertEqual(self.path.read_bytes(), before)

    def test_report_commit_error_restores_existing_document_and_report(self):
        output, report = self.root / "output.md", self.root / "report.json"
        output.write_text("既存の本文")
        output.chmod(0o644)
        report.write_text("既存のレポート")
        original_replace = revision.os.replace
        def replace(source, destination):
            if Path(destination) == report:
                raise PermissionError("simulated report replacement failure")
            return original_replace(source, destination)
        with patch.object(revision.os, "replace", side_effect=replace):
            with self.assertRaises(PermissionError):
                revision.write_many_atomic([(output, "新しい本文"), (report, "新しいレポート")])
        self.assertEqual(output.read_text(), "既存の本文")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
        self.assertEqual(report.read_text(), "既存のレポート")
        self.assertEqual(set(self.root.iterdir()), {self.original, self.brief, output, report})
        output.unlink()
        with patch.object(revision.os, "replace", side_effect=replace):
            with self.assertRaises(PermissionError):
                revision.write_many_atomic([(output, "新しい本文"), (report, "新しいレポート")])
        self.assertFalse(output.exists())
        self.assertEqual(report.read_text(), "既存のレポート")

    def test_failed_restoration_keeps_recovery_copy_and_reports_its_path(self):
        output, report = self.root / "output.md", self.root / "report.json"
        output.write_text("復元すべき本文")
        report.write_text("既存レポート")
        original_replace = revision.os.replace
        document_replacements = 0
        def replace(source, destination):
            nonlocal document_replacements
            if Path(destination) == output:
                document_replacements += 1
                if document_replacements > 1:
                    raise PermissionError("simulated restoration failure")
            if Path(destination) == report:
                raise PermissionError("simulated report failure")
            return original_replace(source, destination)
        with patch.object(revision.os, "replace", side_effect=replace):
            with self.assertRaises(revision.RevisionError) as error:
                revision.write_many_atomic([(output, "新しい本文"), (report, "新しいレポート")])
        copies = set(self.root.iterdir()) - {self.original, self.brief, output, report}
        self.assertEqual(len(copies), 1)
        recovery = copies.pop()
        self.assertEqual(recovery.read_text(), "復元すべき本文")
        self.assertIn(str(recovery), str(error.exception))
        self.assertEqual(report.read_text(), "既存レポート")


if __name__ == "__main__":
    unittest.main()
