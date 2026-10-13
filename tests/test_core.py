import csv
import os
import sys
import tempfile
import unittest
from collections import OrderedDict as O

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "csv_pdf_build"))
import core  # noqa: E402


def sample():
    return [
        core.Result(O(地番="１２３番", 所有者="山田 太郎"),
                    [O(地目="山林", 面積="10")],
                    [O(樹種="スギ", 林齢="40"), O(樹種="ヒノキ", 林齢="30")]),
        core.Result(O(地番="456番", 所有者="鈴木 花子"),
                    [O(地目="原野", 面積="5"), O(地目="山林", 面積="2")], []),
    ]


class CoreTest(unittest.TestCase):
    def test_search_parent_width_insensitive(self):
        hits = core.search(sample(), [core.Condition(core.SRC_PARENT, "地番", "contains", "123")])
        self.assertEqual([h.parent["地番"] for h in hits], ["１２３番"])

    def test_search_child_any(self):
        hits = core.search(sample(), [core.Condition(core.SRC_FOREST, "樹種", "equals", "ヒノキ")])
        self.assertEqual(len(hits), 1)

    def test_and_and_all_fields(self):
        c = [core.Condition(core.SRC_DETAIL, None, "contains", "山林"),
             core.Condition(core.SRC_PARENT, "所有者", "startswith", "鈴木")]
        self.assertEqual(len(core.search(sample(), c)), 1)

    def test_csv(self):
        f = {core.SRC_PARENT: ["地番"], core.SRC_DETAIL: ["地目"], core.SRC_FOREST: ["樹種"]}
        h, rows = core.csv_table(sample(), f)
        self.assertEqual(h, ["林地台帳:地番", "林地台帳詳細:地目", "森林簿詳細:樹種"])
        self.assertEqual(rows, [["１２３番", "山林", "スギ"], ["１２３番", "", "ヒノキ"],
                                ["456番", "原野", ""], ["456番", "山林", ""]])
        p = os.path.join(tempfile.mkdtemp(), "o.csv")
        core.write_csv(p, h, rows, "cp932")
        with open(p, encoding="cp932", newline="") as fp:
            self.assertEqual(len(list(csv.reader(fp))), 5)

    def test_search_records_and_group(self):
        data = sample()
        hits = core.search_records(data, core.SRC_DETAIL,
                                   [core.Condition(core.SRC_DETAIL, "地目", "equals", "山林")])
        self.assertEqual([(h.result.parent["地番"], h.index) for h in hits],
                         [("１２３番", 0), ("456番", 1)])
        out = core.group_hits(hits)
        self.assertEqual([len(r.detail) for r in out], [1, 1])
        self.assertEqual(out[1].detail[0]["面積"], "2")
        self.assertEqual(len(core.group_hits(hits, only_matched=False)[1].detail), 2)

    def test_html(self):
        f = {core.SRC_PARENT: ["地番"], core.SRC_FOREST: ["樹種"]}
        html = core.build_html(sample(), f, title_field="地番")
        self.assertEqual(html.count("page-break-before"), 1)
        self.assertIn("ヒノキ", html)
        self.assertIn("(該当データなし)", html)


if __name__ == "__main__":
    unittest.main()
