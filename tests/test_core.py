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

    def test_parse_date(self):
        import datetime as dt
        p = core.parse_date
        self.assertEqual(p("令和6年5月1日"), dt.date(2024, 5, 1))
        self.assertEqual(p("令和元年5月1日"), dt.date(2019, 5, 1))
        self.assertEqual(p("平成３０年４月３０日"), dt.date(2018, 4, 30))
        self.assertEqual(p("昭和63年12月31日"), dt.date(1988, 12, 31))
        self.assertEqual(p("R6.5.1"), dt.date(2024, 5, 1))
        self.assertEqual(p("2024/5/1"), dt.date(2024, 5, 1))
        self.assertEqual(p("2024-05-01T10:00:00"), dt.date(2024, 5, 1))
        self.assertEqual(p("令和6年5月"), dt.date(2024, 5, 1))
        self.assertIsNone(p("山林"))
        self.assertIsNone(p("令和6年13月1日"))

    def test_date_search(self):
        data = [core.Result(O(氏名="A", 更新日="令和6年4月1日")),
                core.Result(O(氏名="B", 更新日="令和6年6月30日")),
                core.Result(O(氏名="C", 更新日="平成30年1月1日")),
                core.Result(O(氏名="D", 更新日=""))]
        rng = [cond("更新日", "date_from", "2024-04-01"), cond("更新日", "date_to", "令和6年6月30日")]
        got = core.search_records(data, core.SRC_PARENT, rng)
        self.assertEqual([h.record["氏名"] for h in got], ["A", "B"])
        eq = core.search_records(data, core.SRC_PARENT, [cond("更新日", "date_equals", "H30.1.1")])
        self.assertEqual([h.record["氏名"] for h in eq], ["C"])
        self.assertIsNotNone(core.condition_error([cond("更新日", "date_from", "あ")]))
        self.assertIsNone(core.condition_error(rng))

    def test_group_by_owner_parent_and_relation(self):
        g = core.group_by_owner(sample(), core.SRC_PARENT, "所有者")
        self.assertEqual(list(g), ["山田 太郎", "鈴木 花子"])
        g = core.group_by_owner(sample(), D, "氏名")
        self.assertEqual(list(g), ["山田 太郎", "鈴木 花子", "佐藤 次郎"])
        # 鈴木の親は詳細が 2 件 (鈴木/佐藤) -> 所有者ごとに詳細 1 件へ絞られる
        self.assertEqual([len(r.children[D]) for r in g["佐藤 次郎"]], [1])
        self.assertEqual(g["佐藤 次郎"][0].children[D][0]["地目"], "山林")
        # 森林簿など他レイヤはそのまま
        self.assertEqual(len(g["山田 太郎"][0].children[F]), 2)

    def test_group_unknown_owner(self):
        data = [core.Result(O(所有者=""), {})]
        self.assertEqual(list(core.group_by_owner(data, core.SRC_PARENT, "所有者")), [core.UNKNOWN_OWNER])

    def test_safe_filename(self):
        used = set()
        self.assertEqual(core.safe_filename("山田/太郎:様", used), "山田_太郎_様")
        self.assertEqual(core.safe_filename("山田_太郎_様", used), "山田_太郎_様_2")
        self.assertEqual(core.safe_filename("  ", used), "名称なし")

    def test_html(self):
        f = {core.SRC_PARENT: ["地番"], F: ["樹種"]}
        html = core.build_html(sample(), f, LABELS, title_field="地番")
        self.assertEqual(html.count("page-break-before"), 1)
        self.assertIn("ヒノキ", html)
        self.assertIn("(該当データなし)", html)


if __name__ == "__main__":
    unittest.main()
