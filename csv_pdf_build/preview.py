"""印刷プレビュー画面と PDF 出力 (QTextDocument を A4 縦で印刷する)。"""
from qgis.PyQt.QtGui import QPageLayout, QPageSize
from qgis.PyQt.QtPrintSupport import QPrinter, QPrintPreviewWidget
from qgis.PyQt.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


def make_printer(path=None):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if path:
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    return printer


def print_doc(doc, printer):
    doc.print_(printer) if hasattr(doc, "print_") else doc.print(printer)


class PreviewDialog(QDialog):
    def __init__(self, doc, count, default_name="林地台帳.pdf", parent=None):
        super().__init__(parent)
        self.doc = doc
        self.default_name = default_name
        self.saved_path = None
        self.setWindowTitle("印刷プレビュー")
        self.resize(820, 900)

        self.preview = QPrintPreviewWidget(make_printer(), self)
        self.preview.paintRequested.connect(lambda printer: print_doc(self.doc, printer))

        bar = QHBoxLayout()
        for text, fn in (("幅に合わせる", self.preview.fitToWidth), ("全体表示", self.preview.fitInView),
                         ("拡大", self.preview.zoomIn), ("縮小", self.preview.zoomOut),
                         ("◀ 前", self.prev_page), ("次 ▶", self.next_page)):
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
        v.addWidget(QLabel("%d 件の林地台帳を出力します (1 件 = 1 ページ以上)" % count))
        v.addLayout(bar)
        v.addWidget(self.preview, 1)
        self.preview.previewChanged.connect(self.update_page_label)
        self.preview.fitToWidth()

    def update_page_label(self):
        self.page_label.setText("%d / %d ページ" % (self.preview.currentPage(), self.preview.pageCount()))

    def prev_page(self):
        self.preview.setCurrentPage(max(1, self.preview.currentPage() - 1))
        self.update_page_label()

    def next_page(self):
        self.preview.setCurrentPage(min(self.preview.pageCount(), self.preview.currentPage() + 1))
        self.update_page_label()

    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "PDF 保存", self.default_name, "PDF (*.pdf)")
        if not path:
            return
        print_doc(self.doc, make_printer(path))
        self.saved_path = path
        self.accept()
