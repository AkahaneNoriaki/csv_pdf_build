import csv
import os
import sys
import tempfile
import unittest
from collections import OrderedDict as O

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "csv_pdf_build"))
import core  # noqa: E402

D, F = "rel_detail", "rel_forest"
LABELS = O([(core.SRC_PARENT, "林地台帳"), (D, "林地台帳詳細"), (F, "森林簿詳細")])


def sample():
    return [
        core.Result(O(地番="１２３番", 所有者="山田 太郎"),
                    {D: [O(地目="山林", 氏名="山田 太郎")],
                     F: [O(樹種="スギ", 林齢="40"), O(樹種="ヒノキ", 林齢="30")]}),
        core.Result(O(地番="456番", 所有者="鈴木 花子"),
                    {D: [O(地目="原野", 氏名="鈴木 花子"), O(地目="山林", 氏名="佐藤 次郎")], F: []}),
    ]


def cond(field, op, text):
    return core.Condition(field, op, text)


class CoreTest(unittest.TestCase):
    def test_parent_width_insensitive(self):
        hits = core.search_records(sample(), core.SRC_PARENT, [cond("地番", "contains", "123")])
        self.assertEqual([h.record["地番"] for h in hits], ["１２３番"])

    def test_related_layer_by_name(self):
        hits = core.search_records(sample(), D, [cond("氏名", "contains", "佐藤")])
        self.assertEqual([(h.result.parent["地番"], h.index) for h in hits], [("456番", 1)])

    def test_all_layers(self):
        hits = core.search_records(sample(), core.SRC_ALL, [cond(None, "contains", "ヒノキ")])
        self.assertEqual([h.record["地番"] for h in hits], ["１２３番"])
        both = [cond(None, "contains", "佐藤"), cond(None, "contains", "原野")]
        self.assertEqual(len(core.search_records(sample(), core.SRC_ALL, both)), 1)
        self.assertEqual(len(core.search_records(sample(), core.SRC_ALL, [cond(None, "contains", "なし")])), 0)

    def test_group_hits(self):
        hits = core.search_records(sample(), D, [cond("地目", "equals", "山林")])
        out = core.group_hits(hits)
        self.assertEqual([len(r.children[D]) for r in out], [1, 1])
        self.assertEqual(out[1].children[D][0]["氏名"], "佐藤 次郎")
        self.assertEqual(len(core.group_hits(hits, only_matched=False)[1].children[D]), 2)

    def test_csv(self):
        f = {core.SRC_PARENT: ["地番"], D: ["地目"], F: ["樹種"]}
        h, rows = core.csv_table(sample(), f, LABELS)
        self.assertEqual(h, ["林地台帳:地番", "林地台帳詳細:地目", "森林簿詳細:樹種"])
        self.assertEqual(rows, [["１２３番", "山林", "スギ"], ["１２３番", "", "ヒノキ"],
                                ["456番", "原野", ""], ["456番", "山林", ""]])
        p = os.path.join(tempfile.mkdtemp(), "o.csv")
        core.write_csv(p, h, rows, "cp932")
        with open(p, encoding="cp932", newline="") as fp:
            self.assertEqual(len(list(csv.reader(fp))), 5)

    def test_html(self):
        f = {core.SRC_PARENT: ["地番"], F: ["樹種"]}
        html = core.build_html(sample(), f, LABELS, title_field="地番")
        self.assertEqual(html.count("page-break-before"), 1)
        self.assertIn("ヒノキ", html)
        self.assertIn("(該当データなし)", html)


if __name__ == "__main__":
    unittest.main()
