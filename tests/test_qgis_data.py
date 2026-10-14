"""qgis.core を疑似オブジェクトに差し替えて、qgis_data.search の流れを確認する。"""
import os
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)


class _Req:
    NoGeometry = 1

    def __init__(self):
        self.fids = None

    def setFlags(self, _):
        return self

    def setSubsetOfAttributes(self, _):
        return self

    def setFilterFids(self, fids):
        self.fids = set(fids)
        return self


_qc = types.ModuleType("qgis.core")
_qc.NULL = object()
_qc.QgsFeatureRequest = _Req
_qc.QgsProject = object
_qt = types.ModuleType("qgis.PyQt.QtCore")
_qt.QDate = _qt.QDateTime = type("QDateStub", (), {})
sys.modules.setdefault("qgis", types.ModuleType("qgis"))
sys.modules["qgis.core"] = _qc
sys.modules.setdefault("qgis.PyQt", types.ModuleType("qgis.PyQt"))
sys.modules["qgis.PyQt.QtCore"] = _qt

from csv_pdf_build import core, qgis_data  # noqa: E402


class F:
    def __init__(self, fid, **attrs):
        self._id, self.attrs = fid, attrs

    def id(self):
        return self._id

    def __getitem__(self, k):
        return self.attrs[k]


class Field:
    def __init__(self, n):
        self.n = n

    def name(self):
        return self.n


class Fields(list):
    def indexOf(self, name):
        return next((i for i, f in enumerate(self) if f.name() == name), -1)


class Layer:
    def __init__(self, name, fields, feats):
        self._name, self._fields, self.feats = name, fields, feats
        self.scans = 0

    def name(self):
        return self._name

    def fields(self):
        return Fields(Field(n) for n in self._fields)

    def selectedFeatureIds(self):
        return []

    def getFeatures(self, req=None):
        self.scans += 1
        return [f for f in self.feats if req is None or req.fids is None or f.id() in req.fids]


class Rel:
    def __init__(self, rid, child, pairs):
        self._id, self.child, self.pairs = rid, child, pairs

    def id(self):
        return self._id

    def fieldPairs(self):
        return self.pairs

    def referencingLayer(self):
        return self.child


def setup():
    parent = Layer("林地台帳", ["地番", "所有者"], [
        F(1, 地番="1-1", 所有者="山田"), F(2, 地番="2-2", 所有者="鈴木"), F(3, 地番="3-3", 所有者="佐藤")])
    detail = Layer("詳細", ["地番", "氏名", "更新日"], [
        F(10, 地番="1-1", 氏名="山田 太郎", 更新日="令和5年4月1日"),
        F(11, 地番="2-2", 氏名="鈴木 花子", 更新日="令和6年5月1日"),
        F(12, 地番="2-2", 氏名="佐藤 次郎", 更新日="令和6年7月1日"),
        F(13, 地番="9-9", 氏名="佐藤 孤児", 更新日="令和6年5月1日")])      # 親なし
    forest = Layer("森林簿", ["地番", "樹種"], [
        F(20, 地番="1-1", 樹種="スギ"), F(21, 地番="2-2", 樹種="ヒノキ"), F(22, 地番="3-3", 樹種="カラマツ")])
    rels = [Rel("d", detail, {"地番": "地番"}), Rel("f", forest, {"地番": "地番"})]
    return parent, rels, detail, forest


def c(field, op, text):
    return core.Condition(field, op, text)


class SearchTest(unittest.TestCase):
    def test_search_related_returns_parent_with_forest(self):
        parent, rels, detail, forest = setup()
        res = qgis_data.search(parent, rels, "d", [c("氏名", "contains", "佐藤")])
        self.assertEqual([r.parent["地番"] for r in res], ["2-2"])        # 孤児の佐藤は除外
        self.assertEqual(res[0].fid, 2)
        self.assertEqual([x["樹種"] for x in res[0].children["f"]], ["ヒノキ"])   # 森林簿も付随
        self.assertEqual(len(res[0].children["d"]), 2)
        hits = core.search_records(res, "d", [c("氏名", "contains", "佐藤")])
        self.assertEqual([h.index for h in hits], [1])

    def test_date_range_on_related(self):
        parent, rels, *_ = setup()
        cond = [c("更新日", "date_from", "令和6年4月1日"), c("更新日", "date_to", "R6.6.30")]
        res = qgis_data.search(parent, rels, "d", cond)
        self.assertEqual([r.parent["地番"] for r in res], ["2-2"])

    def test_parent_search(self):
        parent, rels, *_ = setup()
        res = qgis_data.search(parent, rels, core.SRC_PARENT, [c("所有者", "equals", "山田")])
        self.assertEqual([r.fid for r in res], [1])
        self.assertEqual(res[0].children["f"][0]["樹種"], "スギ")

    def test_all_layers_and(self):
        parent, rels, *_ = setup()
        cond = [c(None, "contains", "山田"), c(None, "contains", "スギ")]
        self.assertEqual([r.fid for r in qgis_data.search(parent, rels, core.SRC_ALL, cond)], [1])
        cond = [c(None, "contains", "山田"), c(None, "contains", "ヒノキ")]
        self.assertEqual(qgis_data.search(parent, rels, core.SRC_ALL, cond), [])

    def test_period_on_other_layer(self):
        """期間指定は検索するレイヤと別のレイヤ (林地台帳マスターなど) の項目でも絞り込める。"""
        parent, rels, *_ = setup()
        period = [core.Condition("更新日", "date_from", "令和6年4月1日", "d"),
                  core.Condition("更新日", "date_to", "R6.6.30", "d")]
        res = qgis_data.search(parent, rels, "f", period)                 # 森林簿を検索
        self.assertEqual([r.fid for r in res], [2])
        self.assertEqual(len(core.search_records(res, "f", period)), 1)   # 森林簿の 1 件
        cond = [c("樹種", "contains", "スギ")] + period                    # 条件と期間の AND
        self.assertEqual(qgis_data.search(parent, rels, "f", cond), [])
        res = qgis_data.search(parent, rels, core.SRC_PARENT, period)
        self.assertEqual([r.fid for r in res], [2])

    def test_overlay_keys(self):
        """重ねる図形: 検索結果のキー値から、別レイヤの一致する地物 ID を引ける。"""
        parent, rels, detail, forest = setup()
        res = qgis_data.search(parent, rels, "d", [c("氏名", "contains", "佐藤")])
        keys = core.result_values(res, "f", "樹種")
        self.assertEqual(keys, {"ヒノキ"})
        self.assertEqual(dict(qgis_data.key_index(forest, "樹種", keys)), {"ヒノキ": [21]})
        self.assertEqual(core.result_values(res, core.SRC_PARENT, "地番"), {"2-2"})

    def test_no_match_and_scan_counts(self):
        parent, rels, detail, forest = setup()
        self.assertEqual(qgis_data.search(parent, rels, "d", [c("氏名", "contains", "存在しない")]), [])
        self.assertEqual(forest.scans, 0)       # 一致が無ければ森林簿は読まない
        qgis_data.search(parent, rels, "d", [c("氏名", "contains", "山田")])
        self.assertEqual(forest.scans, 1)


if __name__ == "__main__":
    unittest.main()
