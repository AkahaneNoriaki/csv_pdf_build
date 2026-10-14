"""PyQt5 があれば、印刷プレビューと PDF 書き出しを画面なし(offscreen)で実際に動かす。"""
import os
import sys
import tempfile
import types
import unittest
from collections import OrderedDict as O

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5 import QtCore, QtGui, QtWidgets
except ImportError:
    QtWidgets = None

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@unittest.skipIf(QtWidgets is None, "PyQt5 が無い")
class PreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qgis, pyqt = types.ModuleType("qgis"), types.ModuleType("qgis.PyQt")
        for n, m in (("QtCore", QtCore), ("QtGui", QtGui), ("QtWidgets", QtWidgets)):
            setattr(pyqt, n, m)
            sys.modules["qgis.PyQt." + n] = m
        qgis.PyQt = pyqt
        sys.modules["qgis"], sys.modules["qgis.PyQt"] = qgis, pyqt
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from csv_pdf_build import core, preview
        cls.core, cls.preview = core, preview

    def make_doc(self, n):
        core = self.core
        labels = O([(core.SRC_PARENT, "林地台帳"), ("d", "詳細")])
        fields = {core.SRC_PARENT: ["地番"], "d": ["氏名"]}
        res = [core.Result(O(地番="%d番" % i), {"d": [O(氏名="山田")]}) for i in range(n)]
        doc = QtGui.QTextDocument()
        doc.setHtml(core.build_html(res, fields, labels, "テスト"))
        return doc

    def test_preview_pages_and_pdf(self):
        dlg = self.preview.PreviewDialog(self.make_doc(5), 5, 5)
        self.assertEqual(dlg.pages, 5)
        dlg.go(1)
        dlg.set_zoom(1.5)
        self.assertEqual(dlg.page, 1)
        self.assertFalse(dlg.view.pixmap().isNull())
        path = os.path.join(tempfile.mkdtemp(), "o.pdf")
        self.preview.write_pdf(self.make_doc(5), path)
        with open(path, "rb") as fp:
            self.assertEqual(fp.read(5), b"%PDF-")


if __name__ == "__main__":
    unittest.main()
