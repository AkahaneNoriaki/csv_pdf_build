from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QFontDatabase, QPageLayout, QPageSize, QTextDocument
from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFontComboBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
)

from . import core, qgis_data

ITEM_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable
CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked
SOURCES = (core.SRC_PARENT, core.SRC_DETAIL, core.SRC_FOREST)
NO_USE = "(使用しない)"
ALL_FIELDS = "(全項目)"


class ConditionRow(QHBoxLayout):
    """検索条件 1 行: [区分][項目][演算子][値][削除]"""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.source = QComboBox()
        for s in SOURCES:
            self.source.addItem(core.SOURCE_LABELS[s], s)
        self.field = QComboBox()
        self.op = QComboBox()
        for k, v in core.OPERATORS.items():
            self.op.addItem(v, k)
        self.text = QLineEdit()
        self.text.setPlaceholderText("検索値")
        self.text.returnPressed.connect(dialog.do_search)
        self.remove = QPushButton("×")
        self.remove.setMaximumWidth(28)
        self.remove.clicked.connect(lambda: dialog.remove_condition(self))
        for w in (self.source, self.field, self.op):
            self.addWidget(w)
        self.addWidget(self.text, 1)
        self.addWidget(self.remove)
        self.source.currentIndexChanged.connect(self.update_fields)
        self.update_fields()

    def widgets(self):
        return (self.source, self.field, self.op, self.text, self.remove)

    def update_fields(self):
        self.field.clear()
        self.field.addItem(ALL_FIELDS, None)
        for n in self.dialog.names.get(self.source.currentData(), []):
            self.field.addItem(n, n)

    def condition(self):
        return core.Condition(self.source.currentData(), self.field.currentData(),
                              self.op.currentData(), self.text.text())


class MainDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("林地台帳 CSV/PDF 出力")
        self.resize(900, 720)
        self.results = []      # 全件
        self.hits = []         # 検索結果
        self.names = {s: [] for s in SOURCES}
        self.rows = []
        self.relations = []

        root = QVBoxLayout(self)

        # --- レイヤ設定 ---
        box = QGroupBox("1. レイヤ・リレーション設定")
        form = QFormLayout(box)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.VectorLayer)
        self.detail_combo = QComboBox()
        self.forest_combo = QComboBox()
        form.addRow("林地台帳レイヤ", self.layer_combo)
        form.addRow("林地台帳の詳細情報(リレーション)", self.detail_combo)
        form.addRow("森林簿の詳細情報(リレーション)", self.forest_combo)
        self.selected_only = QComboBox()
        self.selected_only.addItem("全地物", False)
        self.selected_only.addItem("選択中の地物のみ", True)
        form.addRow("対象", self.selected_only)
        root.addWidget(box)

        # --- 出力項目 ---
        box = QGroupBox("2. 出力する項目")
        v = QVBoxLayout(box)
        self.tabs = QTabWidget()
        self.field_lists = {}
        for s in SOURCES:
            lw = QListWidget()
            lw.setMaximumHeight(130)
            self.field_lists[s] = lw
            self.tabs.addTab(lw, core.SOURCE_LABELS[s])
        v.addWidget(self.tabs)
        h = QHBoxLayout()
        b_all = QPushButton("全選択")
        b_none = QPushButton("全解除")
        b_all.clicked.connect(lambda: self.set_all_checked(True))
        b_none.clicked.connect(lambda: self.set_all_checked(False))
        h.addWidget(b_all)
        h.addWidget(b_none)
        h.addStretch(1)
        v.addLayout(h)
        root.addWidget(box)

        # --- 検索 ---
        box = QGroupBox("3. 検索条件 (すべてに一致)")
        self.cond_layout = QVBoxLayout(box)
        self.cond_rows = []
        h = QHBoxLayout()
        b_add = QPushButton("条件を追加")
        b_add.clicked.connect(self.add_condition)
        b_search = QPushButton("検索")
        b_search.clicked.connect(self.do_search)
        h.addWidget(b_add)
        h.addStretch(1)
        h.addWidget(b_search)
        self.cond_layout.addLayout(h)
        root.addWidget(box)

        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self.table, 1)
        self.status = QLabel("")
        root.addWidget(self.status)

        # --- 出力 ---
        box = QGroupBox("4. 出力 (表の行を選択した場合は選択行のみ、未選択なら検索結果すべて)")
        form = QFormLayout(box)
        self.enc_combo = QComboBox()
        for k, v_ in core.ENCODINGS.items():
            self.enc_combo.addItem(v_, k)
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.default_font()))
        self.title_edit = QLineEdit("林地台帳 出力")
        self.title_field = QComboBox()
        form.addRow("CSV 文字コード", self.enc_combo)
        form.addRow("PDF フォント", self.font_combo)
        form.addRow("PDF タイトル", self.title_edit)
        form.addRow("PDF 見出しに使う林地台帳項目", self.title_field)
        h = QHBoxLayout()
        b_csv = QPushButton("CSV 出力")
        b_pdf = QPushButton("PDF 出力")
        b_csv.clicked.connect(self.export_csv)
        b_pdf.clicked.connect(self.export_pdf)
        h.addStretch(1)
        h.addWidget(b_csv)
        h.addWidget(b_pdf)
        form.addRow(h)
        root.addWidget(box)

        self.layer_combo.layerChanged.connect(self.on_layer_changed)
        self.detail_combo.currentIndexChanged.connect(self.on_relation_changed)
        self.forest_combo.currentIndexChanged.connect(self.on_relation_changed)
        self.add_condition()

    # ---------- 設定 ----------
    @staticmethod
    def default_font():
        fams = set(QFontDatabase().families())
        for f in ("Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP",
                  "IPAexGothic", "IPAGothic", "Hiragino Sans"):
            if f in fams:
                return f
        return QFont().family()

    def refresh_layers(self):
        self.on_layer_changed(self.layer_combo.currentLayer())

    def on_layer_changed(self, layer):
        self.relations = qgis_data.relations_for(layer)
        for combo in (self.detail_combo, self.forest_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(NO_USE, None)
            for r in self.relations:
                combo.addItem("%s → %s" % (r.name(), r.referencingLayer().name()), r.id())
            combo.blockSignals(False)
        # 2 つのリレーションがあれば順に仮割り当て
        if len(self.relations) >= 1:
            self.detail_combo.setCurrentIndex(1)
        if len(self.relations) >= 2:
            self.forest_combo.setCurrentIndex(2)
        self.on_relation_changed()

    def relation(self, combo):
        rid = combo.currentData()
        return next((r for r in self.relations if r.id() == rid), None)

    def on_relation_changed(self, *_):
        layer = self.layer_combo.currentLayer()
        rels = {core.SRC_PARENT: layer,
                core.SRC_DETAIL: self.relation(self.detail_combo),
                core.SRC_FOREST: self.relation(self.forest_combo)}
        for s in SOURCES:
            src = rels[s]
            tl = src if s == core.SRC_PARENT else (src.referencingLayer() if src else None)
            self.names[s] = qgis_data.field_names(tl)
            lw = self.field_lists[s]
            lw.clear()
            for n in self.names[s]:
                it = QListWidgetItem(n)
                it.setFlags(it.flags() | ITEM_CHECKABLE)
                it.setCheckState(CHECKED)
                lw.addItem(it)
        self.title_field.clear()
        self.title_field.addItem("(なし)", None)
        for n in self.names[core.SRC_PARENT]:
            self.title_field.addItem(n, n)
        for row in self.cond_rows:
            row.update_fields()
        self.results, self.hits = [], []
        self.show_hits()

    def set_all_checked(self, on):
        lw = self.field_lists[SOURCES[self.tabs.currentIndex()]]
        for i in range(lw.count()):
            lw.item(i).setCheckState(CHECKED if on else UNCHECKED)

    def checked_fields(self):
        out = {}
        for s, lw in self.field_lists.items():
            out[s] = [lw.item(i).text() for i in range(lw.count())
                      if lw.item(i).checkState() == CHECKED]
        return out

    # ---------- 検索条件 ----------
    def add_condition(self):
        row = ConditionRow(self)
        self.cond_rows.append(row)
        self.cond_layout.insertLayout(len(self.cond_rows) - 1, row)

    def remove_condition(self, row):
        if len(self.cond_rows) <= 1:
            row.text.clear()
            return
        self.cond_rows.remove(row)
        for w in row.widgets():
            w.setParent(None)
            w.deleteLater()
        self.cond_layout.removeItem(row)

    # ---------- 検索 ----------
    def do_search(self):
        layer = self.layer_combo.currentLayer()
        if layer is None:
            QMessageBox.warning(self, "林地台帳 CSV/PDF 出力", "林地台帳レイヤを選択してください。")
            return
        self.results = qgis_data.load_results(
            layer, self.relation(self.detail_combo), self.relation(self.forest_combo),
            self.selected_only.currentData())
        self.hits = core.search(self.results, [r.condition() for r in self.cond_rows])
        self.show_hits()

    def show_hits(self):
        cols = self.checked_fields()[core.SRC_PARENT][:12]
        headers = cols + ["詳細件数", "森林簿件数"]
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.hits))
        for r, res in enumerate(self.hits):
            vals = [res.parent.get(c, "") for c in cols] + [str(len(res.detail)), str(len(res.forest))]
            for c, val in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(val))
        self.table.resizeColumnsToContents()
        self.status.setText("検索結果: %d 件 / 全 %d 件" % (len(self.hits), len(self.results)))

    def target_results(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        res = [self.hits[r] for r in rows] if rows else self.hits
        if not res:
            QMessageBox.information(self, "林地台帳 CSV/PDF 出力", "出力対象がありません。先に検索してください。")
        return res

    # ---------- 出力 ----------
    def export_csv(self):
        res = self.target_results()
        if not res:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSV 保存", "林地台帳.csv", "CSV (*.csv)")
        if not path:
            return
        header, rows = core.csv_table(res, self.checked_fields())
        try:
            core.write_csv(path, header, rows, self.enc_combo.currentData())
        except OSError as e:
            QMessageBox.critical(self, "林地台帳 CSV/PDF 出力", "保存に失敗しました:\n%s" % e)
            return
        self.iface.messageBar().pushSuccess("CSV 出力", "%d 行を保存しました: %s" % (len(rows), path))

    def export_pdf(self):
        res = self.target_results()
        if not res:
            return
        path, _ = QFileDialog.getSaveFileName(self, "PDF 保存", "林地台帳.pdf", "PDF (*.pdf)")
        if not path:
            return
        html = core.build_html(res, self.checked_fields(), self.title_edit.text(),
                               self.font_combo.currentFont().family(), self.title_field.currentData())
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        doc = QTextDocument()
        doc.setDefaultFont(self.font_combo.currentFont())
        doc.setHtml(html)
        doc.print_(printer) if hasattr(doc, "print_") else doc.print(printer)
        self.iface.messageBar().pushSuccess("PDF 出力", "%d 件を保存しました: %s" % (len(res), path))
