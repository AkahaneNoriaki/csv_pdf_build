"""印刷プレビューと PDF 出力。

QPrinter / QPrintPreviewWidget は使わない。これらは起動時にプリンタ情報を問い合わせるため、
ネットワークプリンタが繋がらない環境で QGIS が応答なしになることがある。
代わりに、プレビューは QTextDocument を 1 ページずつ画像に描き、PDF は QPdfWriter で直接書く。
"""
from qgis.PyQt.QtCore import QMarginsF, QRectF, QSizeF, Qt
from qgis.PyQt.QtGui import QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
)

# 画面表示用の A4 (96dpi 相当のピクセル) と余白 (約 15mm)
PAGE_W, PAGE_H, MARGIN = 794, 1123, 57


def write_pdf(doc, path):
    """QTextDocument を A4 縦の PDF として path に書く (プリンタは使わない)。"""
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    try:
        writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
    except TypeError:       # 古い Qt はミリ単位の引数だけ
        writer.setPageMargins(QMarginsF(15, 15, 15, 15))
    doc.print_(writer) if hasattr(doc, "print_") else doc.print(writer)


class PreviewDialog(QDialog):
    def __init__(self, doc, shown, total, make_full_doc=None, default_name="林地台帳.pdf", parent=None):
        """doc: プレビュー用 (先頭の一部)。make_full_doc: 保存時に全件の文書を作る関数。"""
        super().__init__(parent)
        self.make_full_doc = make_full_doc
        self.default_name = default_name
        self.saved_path = None
        self.setWindowTitle("印刷プレビュー")
        self.resize(860, 920)

        # レイアウト計算用に複製する (元の文書のページ設定を変えない)
        self.doc = doc.clone()
        self.content_w = PAGE_W - 2 * MARGIN
        self.content_h = PAGE_H - 2 * MARGIN
        self.doc.setPageSize(QSizeF(self.content_w, self.content_h))
        self.pages = max(1, self.doc.pageCount())
        self.page = 0
        self.zoom = 1.0

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        b_save.clicked.connect(self.save_pdf)
        b_close = QPushButton("閉じる")
        b_close.clicked.connect(self.reject)
        bar.addWidget(b_save)
        bar.addWidget(b_close)

        v = QVBoxLayout(self)
        note = "%d 件の林地台帳を出力します。" % total
        if shown < total:
            note += "プレビューは先頭 %d 件のみ表示します (PDF には全 %d 件が入ります)。" % (shown, total)
        v.addWidget(QLabel(note))
        v.addLayout(bar)
        v.addWidget(self.scroll, 1)
        self.render()

    def render(self):
        """現在のページだけを画像に描く (全ページは描かないので軽い)。"""
        z = self.zoom
        img = QImage(int(PAGE_W * z), int(PAGE_H * z), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.white)
        p = QPainter(img)
        p.scale(z, z)
        top = self.page * self.content_h
        p.translate(MARGIN, MARGIN - top)
        rect = QRectF(0, top, self.content_w, self.content_h)
        p.setClipRect(rect)
        self.doc.drawContents(p, rect)
        p.end()
        self.view.setPixmap(QPixmap.fromImage(img))
        self.view.resize(img.width(), img.height())
        self.page_label.setText("%d / %d ページ (%d%%)" % (self.page + 1, self.pages, round(z * 100)))

    def go(self, step):
        self.page = min(max(self.page + step, 0), self.pages - 1)
        self.render()

    def set_zoom(self, z):
        self.zoom = min(max(z, 0.3), 3.0)
        self.render()

    def fit_width(self):
        self.set_zoom((self.scroll.viewport().width() - 20) / PAGE_W)

    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "PDF 保存", self.default_name, "PDF (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            write_pdf(self.make_full_doc() if self.make_full_doc else self.doc, path)
        finally:
            QApplication.restoreOverrideCursor()
        self.saved_path = path
        self.accept()
