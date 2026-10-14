"""本物の QGIS (python3-qgis) があれば、検索 -> 地図の印刷レイアウト -> PDF を画面なしで通しで動かす。

実行: QT_QPA_PLATFORM=offscreen /usr/bin/python3.12 -m unittest tests.test_qgis_integration
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsProject,
                           QgsRelation, QgsVectorLayer)
    from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
    HAVE_QGIS = True
except ImportError:
    HAVE_QGIS = False


class FakeBar:
    def pushSuccess(self, *a):
        pass


class FakeCanvas:
    def __init__(self, layers):
        self._layers = layers

    def layers(self):
        return self._layers


class FakeIface:
    def __init__(self, layers):
        self.canvas = FakeCanvas(layers)

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return FakeBar()


def table(name, fields, rows, geom=None):
    uri = ("%s?crs=EPSG:6677&" % geom if geom else "NoGeometry?") + "&".join("field=%s:string" % f for f in fields)
    layer = QgsVectorLayer(uri, name, "memory")
    feats = []
    for attrs, wkt in rows:
        f = QgsFeature(layer.fields())
        f.setAttributes(attrs)
        if wkt:
            f.setGeometry(QgsGeometry.fromWkt(wkt))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return layer


def square(x, y):
    return "POLYGON((%d %d,%d %d,%d %d,%d %d,%d %d))" % (x, y, x + 50, y, x + 50, y + 40, x, y + 40, x, y)


@unittest.skipUnless(HAVE_QGIS, "QGIS (python3-qgis) が無い")
class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication([], False)
        cls.app.initQgis()
        cls.project = QgsProject.instance()
        cls.project.setCrs(QgsCoordinateReferenceSystem("EPSG:6677"))   # 実運用と同じく平面直角座標系
        cls.parent = table("林地台帳", ["地番", "所有者"], [
            (["1-1", "山田"], square(0, 0)), (["2-2", "鈴木"], square(100, 0)), (["3-3", "山田"], square(0, 100))],
            "MultiPolygon")
        cls.detail = table("林地台帳詳細", ["地番", "氏名", "届け出年月日"], [
            (["1-1", "山田 太郎", "令和5年4月1日"], None), (["2-2", "鈴木 花子", "令和6年5月1日"], None),
            (["3-3", "山田 太郎", "令和六年七月一日"], None)])
        cls.forest = table("森林簿詳細", ["地番", "樹種"], [(["1-1", "スギ"], None), (["2-2", "ヒノキ"], None),
                                                          (["3-3", "スギ"], None)])
        for layer in (cls.parent, cls.detail, cls.forest):
            cls.project.addMapLayer(layer)
        for rid, child in (("rel_d", cls.detail), ("rel_f", cls.forest)):
            rel = QgsRelation()
            rel.setId(rid)
            rel.setName(rid)
            rel.setReferencingLayer(child.id())
            rel.setReferencedLayer(cls.parent.id())
            rel.addFieldPair("地番", "地番")
            assert rel.isValid()
            cls.project.relationManager().addRelation(rel)

    @classmethod
    def tearDownClass(cls):
        cls.app.exitQgis()

    def make_dialog(self):
        from csv_pdf_build.dialog import MainDialog
        dlg = MainDialog(FakeIface([self.parent]))
        dlg.refresh_layers()
        return dlg

    def test_search_related_then_map_pdf(self):
        dlg = self.make_dialog()
        # 検索レイヤは氏名を持つリレーション先が初期値。期間 + 氏名で検索する
        self.assertIn("林地台帳詳細", dlg.source_combo.currentText())
        dlg.cond_rows[0].text.setText("山田")
        dlg.period_from.setText("令和6年4月1日")
        dlg.period_to.setText("R6.12.31")
        dlg.do_search()
        self.assertEqual([r[0].result.parent["地番"] for r in dlg.rows], ["3-3"])   # 令和六年七月一日の山田だけ

        out = os.path.join(tempfile.mkdtemp(), "map.pdf")
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        dlg.export_pdf()
        with open(out, "rb") as fp:
            data = fp.read()
        self.assertEqual(data[:5], b"%PDF-")
        self.assertGreater(len(data), 2000)
        # 一時の強調レイヤは後始末されている
        self.assertEqual([l.name() for l in self.project.mapLayers().values() if l.name().startswith("強調表示")], [])

    def test_owner_pdfs_and_preview(self):
        dlg = self.make_dialog()
        dlg.source_combo.setCurrentIndex(dlg.source_combo.findData(dlg.parent_layer and "all"))
        dlg.cond_rows[0].field.setCurrentIndex(0)
        dlg.do_search()
        self.assertEqual(len(dlg.rows), 3)
        built = dlg.build_print([r[0].result for r in dlg.rows])
        try:
            self.assertEqual(built.page_count(), 3)
            img = built.render_page(0, 400)
            self.assertFalse(img.isNull())
            from csv_pdf_build.preview import PreviewDialog
            dlg_prev = PreviewDialog(built.page_count(), built.render_page, "テスト", lambda path: None)
            dlg_prev.go(1)
            dlg_prev.set_zoom(1.5)
            self.assertEqual((dlg_prev.page, dlg_prev.pages), (1, 3))
            self.assertFalse(dlg_prev.view.pixmap().isNull())
        finally:
            built.cleanup()

        folder = tempfile.mkdtemp()
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: folder)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        dlg.owner_combo.setCurrentIndex(next(i for i in range(dlg.owner_combo.count())
                                             if dlg.owner_combo.itemText(i).endswith("氏名")))
        dlg.export_owner_pdfs()
        self.assertEqual(sorted(os.listdir(folder)), ["山田 太郎.pdf", "鈴木 花子.pdf"])


if __name__ == "__main__":
    unittest.main()
