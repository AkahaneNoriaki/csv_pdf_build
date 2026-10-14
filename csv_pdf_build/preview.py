"""印刷プレビュー: 印刷レイアウトのページを 1 枚ずつ画像にして表示する。

QPrinter / QPrintPreviewWidget は使わない (起動時にプリンタ情報を問い合わせ、ネットワーク
プリンタが繋がらない環境で QGIS が応答なしになることがあるため)。表示中のページだけ描く。
"""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
)

BASE_WIDTH = 900   # 倍率 100% のときのページ幅 (px)


class PreviewDialog(QDialog):
    def __init__(self, page_count, render_page, note, save_pdf, default_name="林地台帳.pdf", parent=None):
        """render_page(index, width_px) -> QImage。save_pdf(path) -> 失敗理由 or None (全ページを保存)。"""
        super().__init__(parent)
        self.pages = max(1, page_count)
        self.render_page = render_page
        self.save_func = save_pdf
        self.default_name = default_name
        self.saved_path = None
        self.page = 0
        self.zoom = 1.0
        self.cache = {}
        self.setWindowTitle("印刷プレビュー")
        self.resize(1000, 820)

        self.view = QLabel()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.view)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        bar = QHBoxLayout()
        for text, fn in (("◀ 前", lambda: self.go(-1)), ("次 ▶", lambda: self.go(1)),
                         ("拡大", lambda: self.set_zoom(self.zoom * 1.25)),
                         ("縮小", lambda: self.set_zoom(self.zoom / 1.25)),
                         ("幅に合わせる", self.fit_width)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: f())
            bar.addWidget(b)
        self.page_label = QLabel("")
        bar.addWidget(self.page_label)
        bar.addStretch(1)
        b_save = QPushButton("PDFとして保存")
        b_save.clicked.connect(self.save)
        b_close = QPushButton("閉じる")
        b_close.clicked.connect(self.reject)
        bar.addWidget(b_save)
        bar.addWidget(b_close)

        v = QVBoxLayout(self)
        v.addWidget(QLabel(note))
        v.addLayout(bar)
        v.addWidget(self.scroll, 1)
        self.render()

    def render(self):
        """表示中のページだけを描く (描いた画像は倍率ごとに使い回す)。"""
        key = (self.page, round(self.zoom, 3))
        if key not in self.cache:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                img = self.render_page(self.page, BASE_WIDTH * self.zoom)
                self.cache[key] = QPixmap.fromImage(img)
            finally:
                QApplication.restoreOverrideCursor()
        pix = self.cache[key]
        self.view.setPixmap(pix)
        self.view.resize(pix.size())
        self.page_label.setText("%d / %d ページ (%d%%)" % (self.page + 1, self.pages, round(self.zoom * 100)))

    def go(self, step):
        self.page = min(max(self.page + step, 0), self.pages - 1)
        self.render()

    def set_zoom(self, z):
        self.zoom = min(max(z, 0.3), 3.0)
        self.render()

    def fit_width(self):
        self.set_zoom((self.scroll.viewport().width() - 20) / BASE_WIDTH)

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, "PDF 保存", self.default_name, "PDF (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            err = self.save_func(path)
        finally:
            QApplication.restoreOverrideCursor()
        if err:
            from qgis.PyQt.QtWidgets import QMessageBox
            QMessageBox.critical(self, "印刷プレビュー", "PDF を保存できませんでした: " + err)
            return
        self.saved_path = path
        self.accept()
