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
         "scope": "全文", "allowed_changes": "事実と条件を保った整理"}
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
    return revision.initialize(text, text, BRIEF, limit)


def record(outcome="unchanged", **extra):
    value = {"outcome": outcome, "problem": "この箇所に修正が必要な問題は見つからない",
             "evidence": "原文と照合し、対象と適用条件がこの段落内で読める",
             "preservation": ["適用する対象と条件を維持する"],
             "criteria": ["読者が対象と条件を判断できる"],
             "checks": [{"criterion": "適用条件の保持",
                         "observation": "準備ができた場合だけ進むという条件が残っている"}]}
    value.update(extra)
    return value


def hold():
    return record("held", reason="対象の上限値が原文にない",
                  proposal="上限を確認後、「一日あたり○件」と追記する",
                  required_decision="担当者に上限値の根拠と説明追加の採否を確認する")


def accept_all(value):
    for unit in value["units"]:
        revision.record_review(value, unit["id"], revision.token(value, unit), record())


def final(value):
    revision.final_review(value, revision.document_token(value), {
        "original_comparison": "全文を原文と照合し、数値・条件・リンク・断定を維持した",
        "standalone_read": "修正文だけを読み、対象の選択から条件と手順へ進めることを確認した"})


class SegmentationTests(unittest.TestCase):
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
        self.assertIn("最初の説明。", unit["history"][1]["text"])

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
        bad_records = [record(checks=[]), record(preservation=[]), record(criteria=[]),
                       record(problem=""), {"outcome": "unchanged"},
                       record(checks=[{"criterion": "条件の保持", "observation": "OK"}])]
        for bad in bad_records:
            value = state("説明。\n")
            with self.subTest(bad=bad), self.assertRaises(revision.RevisionError):
                revision.record_review(value, value["units"][0]["id"],
                                       revision.token(value, value["units"][0]), bad)

    def test_missing_current_record_cannot_bypass_finish(self):
        value = state("説明。\n")
        accept_all(value)
        final(value)
        value["units"][0]["review"]["checks"] = []
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
        revision.record_review(value, unit["id"], revision.token(value, unit), held)
        self.assertEqual(unit["state"], "held")
        self.assertTrue(unit["review"]["limit_reached"])
        self.assertEqual(revision.next_target(value)["target"]["id"], value["units"][1]["id"])
        for other in value["units"][1:]:
            revision.record_review(value, other["id"], revision.token(value, other), record())
        final(value)
        result = revision.finish(value)
        self.assertFalse(result["fully_resolved"])
        self.assertEqual(result["held"][0]["proposal"], held["proposal"])
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
            del bad[key]
            with self.assertRaises(revision.RevisionError):
                revision.record_review(value, unit["id"], revision.token(value, unit), bad)

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

    def test_annotation_and_review_history_survives_regrouping(self):
        value = state()
        unit = value["units"][0]
        revision.annotate(value, unit["id"], revision.token(value, unit),
                          {"findings": ["条件が不明"], "preservation": ["未決定を維持"]})
        revision.record_review(value, unit["id"], revision.token(value, unit), hold())
        old = copy.deepcopy(unit)
        revision.merge_units(value, [u["id"] for u in value["units"][:2]])
        self.assertIn(old, value["retired"])
        self.assertEqual(value["units"][0]["notes"]["inherited"][0]["findings"], ["条件が不明"])
        records = revision.history(value, value["units"][0]["id"])["records"]
        self.assertIn(old, records)
        self.assertEqual(revision.history(value, old["id"])["records"][0], old)


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

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--state", str(self.path),
                                 *map(str, args)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
        return result

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
