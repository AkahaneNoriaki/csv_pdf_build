from collections import OrderedDict

from qgis.core import QgsProject
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QFont, QFontDatabase, QPageLayout, QPageSize, QTextDocument
from qgis.PyQt.QtPrintSupport import QPrinter
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout,
)

from . import core, qgis_data

ITEM_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable
CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked
ALL_FIELDS = "(全項目)"
TITLE = "林地台帳 CSV/PDF 出力"


class ConditionRow(QHBoxLayout):
    """検索条件 1 行: [項目][演算子][値][削除]  (項目は検索するレイヤのもの)"""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
        self.field = QComboBox()
        self.op = QComboBox()
        for k, v in core.OPERATORS.items():
            self.op.addItem(v, k)
        self.text = QLineEdit()
        self.text.setPlaceholderText("検索値 (例: 氏名 / 令和6年4月1日)")
        self.text.returnPressed.connect(dialog.do_search)
        self.remove = QPushButton("×")
        self.remove.setMaximumWidth(28)
        self.remove.clicked.connect(lambda: dialog.remove_condition(self))
        self.addWidget(self.field, 1)
        self.addWidget(self.op)
        self.addWidget(self.text, 2)
        self.addWidget(self.remove)
        self.update_fields()

    def widgets(self):
        return (self.field, self.op, self.text, self.remove)

    def update_fields(self):
        self.field.clear()
        self.field.addItem(ALL_FIELDS, None)
        for n in self.dialog.names.get(self.dialog.search_source(), []):
            self.field.addItem(n, n)

    def condition(self):
        return core.Condition(self.field.currentData(), self.op.currentData(), self.text.text())


class MainDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(TITLE)
        self.resize(900, 720)
        self.parent_layer = None
        self.relations = []
        self.labels = OrderedDict()                 # source -> 表示名 (親が先頭)
        self.names = {core.SRC_PARENT: []}          # source -> 項目名リスト
        self.results = []                           # 読み込み済み全件
        self.hits = []                              # 検索結果 (Hit)
        self.rows = []                              # 表の各行 = Hit のリスト

        root = QVBoxLayout(self)

        # --- 1. 検索 ---
        box = QGroupBox("1. 検索 (検索するレイヤを選んで条件を入力)")
        v = QVBoxLayout(box)
        h = QHBoxLayout()
        h.addWidget(QLabel("検索するレイヤ"))
        self.source_combo = QComboBox()
        h.addWidget(self.source_combo, 1)
        v.addLayout(h)
        self.cond_layout = QVBoxLayout()
        self.cond_rows = []
        v.addLayout(self.cond_layout)
        h = QHBoxLayout()
        b_add = QPushButton("条件を追加")
        b_add.clicked.connect(self.add_condition)
        b_search = QPushButton("検索")
        b_search.clicked.connect(self.do_search)
        self.parent_view = QCheckBox("結果を親レイヤの行で表示")
        self.parent_view.setChecked(True)
        self.parent_view.toggled.connect(self.on_view_changed)
        h.addWidget(b_add)
        h.addWidget(self.parent_view)
        h.addStretch(1)
        h.addWidget(b_search)
        v.addLayout(h)
        v.addWidget(QLabel("日付は「(日付) 以降/以前/と同じ」で指定。令和6年4月1日・R6.4.1・2024-04-01 の形式で入力できます"))
        root.addWidget(box)

        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self.table, 1)
        h = QHBoxLayout()
        self.status = QLabel("")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        b_reload = QPushButton("データ再読込")
        b_reload.clicked.connect(self.load_data)
        b_sel = QPushButton("地図で選択")
        b_zoom = QPushButton("選択して拡大")
        b_sel.clicked.connect(lambda: self.select_on_map(False))
        b_zoom.clicked.connect(lambda: self.select_on_map(True))
        h.addWidget(self.status, 1)
        h.addWidget(self.progress)
        h.addWidget(b_sel)
        h.addWidget(b_zoom)
        h.addWidget(b_reload)
        root.addLayout(h)

        # --- 2. 検索レイヤ選択 ---
        box = QGroupBox("2. 検索レイヤ選択 (選ぶとリレーション先も自動で読み込みます)")
        form = QFormLayout(box)
        self.layer_combo = QComboBox()
        self.related_label = QLabel("")
        self.related_label.setWordWrap(True)
        self.selected_only = QComboBox()
        self.selected_only.addItem("全地物", False)
        self.selected_only.addItem("選択中の地物のみ", True)
        form.addRow("リレーション済みレイヤ", self.layer_combo)
        form.addRow("リレーション先", self.related_label)
        form.addRow("対象", self.selected_only)
        root.addWidget(box)

        # --- 3. 出力項目 ---
        box = QGroupBox("3. 出力する項目")
        v = QVBoxLayout(box)
        self.tabs = QTabWidget()
        self.field_lists = {}
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

        # --- 4. 出力 ---
        box = QGroupBox("4. 出力 (表の行を選択した場合は選択行のみ、未選択なら検索結果すべて)")
        form = QFormLayout(box)
        self.only_matched = QCheckBox("リレーション先を検索した場合は、一致したレコードのみ出力する")
        self.only_matched.setChecked(True)
        self.enc_combo = QComboBox()
        for k, v_ in core.ENCODINGS.items():
            self.enc_combo.addItem(v_, k)
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.default_font()))
        self.title_edit = QLineEdit("林地台帳 出力")
        self.title_field = QComboBox()
        form.addRow(self.only_matched)
        form.addRow("CSV 文字コード", self.enc_combo)
        form.addRow("PDF フォント", self.font_combo)
        form.addRow("PDF タイトル", self.title_edit)
        form.addRow("PDF 見出し・結果表に使う親レイヤの項目", self.title_field)
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

        self.layer_combo.currentIndexChanged.connect(lambda *_: self.on_layer_changed(load=True))
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        self.selected_only.currentIndexChanged.connect(lambda *_: self.load_data())
        self.add_condition()

    @staticmethod
    def default_font():
        fams = set(QFontDatabase().families())
        for f in ("Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP",
                  "IPAexGothic", "IPAGothic", "Hiragino Sans"):
            if f in fams:
                return f
        return QFont().family()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.load_data)   # 開いたときに最新データを読み込む

    # ---------- レイヤ選択 ----------
    def refresh_layers(self):
        """リレーションの親になっているレイヤを列挙する (プラグインを開くたびに呼ぶ)。"""
        keep = self.layer_combo.currentData()
        self.layer_combo.blockSignals(True)
        self.layer_combo.clear()
        for layer in qgis_data.parent_layers():
            self.layer_combo.addItem(layer.name(), layer.id())
        self.layer_combo.setCurrentIndex(max(self.layer_combo.findData(keep), 0))
        self.layer_combo.blockSignals(False)
        self.on_layer_changed(load=self.isVisible())
        if not self.layer_combo.count():
            self.status.setText("リレーションが設定されたレイヤがありません。"
                                "プロジェクトのプロパティ > リレーション で設定してください。")

    def on_layer_changed(self, load=False):
        layer = QgsProject.instance().mapLayer(self.layer_combo.currentData() or "")
        self.parent_layer = layer
        self.relations = qgis_data.relations_for(layer)
        self.labels = OrderedDict([(core.SRC_PARENT, layer.name() if layer else "")])
        self.names = {core.SRC_PARENT: qgis_data.field_names(layer)}
        for r in self.relations:
            self.labels[r.id()] = qgis_data.relation_label(r)
            self.names[r.id()] = qgis_data.field_names(r.referencingLayer())
        self.names[core.SRC_ALL] = []
        self.related_label.setText("、".join(self.labels[r.id()] for r in self.relations) or "(なし)")

        # 検索するレイヤの選択肢
        keep = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("すべてのレイヤを横断 (氏名など)", core.SRC_ALL)
        for src, label in self.labels.items():
            prefix = "親: " if src == core.SRC_PARENT else "リレーション先: "
            self.source_combo.addItem(prefix + label, src)
        i = self.source_combo.findData(keep)
        self.source_combo.setCurrentIndex(max(i, 0))
        self.source_combo.blockSignals(False)

        # 出力項目タブ
        self.tabs.clear()
        self.field_lists = {}
        for src, label in self.labels.items():
            lw = QListWidget()
            lw.setMaximumHeight(110)
            for n in self.names[src]:
                it = QListWidgetItem(n)
                it.setFlags(it.flags() | ITEM_CHECKABLE)
                it.setCheckState(CHECKED)
                lw.addItem(it)
            self.field_lists[src] = lw
            self.tabs.addTab(lw, label)
        self.title_field.clear()
        self.title_field.addItem("(なし)", None)
        for n in self.names[core.SRC_PARENT]:
            self.title_field.addItem(n, n)
        for row in self.cond_rows:
            row.update_fields()
        self.results, self.hits = [], []
        self.show_hits()
        if load:
            QTimer.singleShot(0, self.load_data)

    def search_source(self):
        return self.source_combo.currentData() or core.SRC_ALL

    def on_source_changed(self, *_):
        for row in self.cond_rows:
            row.update_fields()
        self.hits = []
        self.show_hits()

    def set_all_checked(self, on):
        lw = self.tabs.currentWidget()
        for i in range(lw.count() if lw else 0):
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

    # ---------- 読み込み・検索 ----------
    def load_data(self):
        """親レイヤとリレーション先を先にまとめて読み込む (検索はこのデータに対して行う)。"""
        self.results, self.hits = [], []
        layer = self.parent_layer
        if layer is None:
            self.show_hits()
            return
        self.setEnabled(False)   # 読み込み中は操作を受け付けない
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        def progress(done, total):
            self.progress.setValue(int(done * 100 / max(total, 1)))
            self.status.setText("データを読み込み中... %d / %d" % (done, total))
            QApplication.processEvents()

        try:
            self.results = qgis_data.load_results(
                layer, self.relations, self.selected_only.currentData(), progress)
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.setVisible(False)
            self.setEnabled(True)
        self.show_hits()
        self.status.setText("読み込み完了: %s %d 件 (リレーション先 %d レイヤ)。条件を入れて検索してください。"
                            % (layer.name(), len(self.results), len(self.relations)))

    def do_search(self):
        if self.parent_layer is None:
            QMessageBox.warning(self, TITLE, "リレーション済みのレイヤを選択してください。")
            return
        conds = [r.condition() for r in self.cond_rows]
        err = core.condition_error(conds)
        if err:
            QMessageBox.warning(self, TITLE, err)
            return
        if not self.results:
            self.load_data()
        self.hits = core.search_records(self.results, self.search_source(), conds)
        self.show_hits()

    def parent_label(self, res):
        names = self.names[core.SRC_PARENT]
        key = self.title_field.currentData() or (names[0] if names else None)
        return res.parent.get(key, "") if key else ""

    def on_view_changed(self, *_):
        self.show_hits()

    def build_rows(self):
        """表の行を作る。リレーション先を検索して親の行で表示するときは、親ごとにまとめる。"""
        src = self.search_source()
        if self.parent_view.isChecked() and src not in (core.SRC_PARENT, core.SRC_ALL):
            groups = OrderedDict()
            for h in self.hits:
                groups.setdefault(id(h.result), []).append(h)
            self.rows = list(groups.values())
        else:
            self.rows = [[h] for h in self.hits]

    def show_hits(self):
        self.build_rows()
        src = self.search_source()
        by_parent = src in (core.SRC_PARENT, core.SRC_ALL) or self.parent_view.isChecked()
        shown = core.SRC_PARENT if by_parent else src
        counted = by_parent and src not in (core.SRC_PARENT, core.SRC_ALL)
        cols = (self.checked_fields().get(shown) or self.names.get(shown, []))[:12]
        headers = (["親レイヤ"] if not by_parent else []) + cols + (["一致件数"] if counted else [])
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            first = row[0]
            rec = first.result.parent if shown == core.SRC_PARENT else first.record
            vals = ([self.parent_label(first.result)] if not by_parent else []) \
                + [rec.get(c, "") for c in cols] + ([str(len(row))] if counted else [])
            for c, val in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(val))
        self.table.resizeColumnsToContents()
        if self.hits or self.results:
            self.status.setText("検索結果: %d 件 (%s)" % (len(self.rows), self.source_combo.currentText()))

    def target_hits(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        chosen = [self.rows[r] for r in rows] if rows else self.rows
        return [h for row in chosen for h in row]

    def target_results(self):
        res = core.group_hits(self.target_hits(), self.only_matched.isChecked())
        if not res:
            QMessageBox.information(self, TITLE, "出力対象がありません。先に検索してください。")
        return res

    def select_on_map(self, zoom):
        """検索結果の親レイヤの地物を QGIS 上で選択する (表の行を選んでいればその行だけ)。"""
        results = core.group_hits(self.target_hits(), False)
        fids = [r.fid for r in results if r.fid is not None]
        if not fids or self.parent_layer is None:
            QMessageBox.information(self, TITLE, "選択対象がありません。先に検索してください。")
            return
        self.parent_layer.selectByIds(fids)
        if zoom:
            self.iface.mapCanvas().zoomToSelected(self.parent_layer)
        self.iface.messageBar().pushSuccess("地図で選択", "%s: %d 件を選択しました" % (self.parent_layer.name(), len(fids)))

    # ---------- 出力 ----------
    def export_csv(self):
        res = self.target_results()
        if not res:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSV 保存", "林地台帳.csv", "CSV (*.csv)")
        if not path:
            return
        header, rows = core.csv_table(res, self.checked_fields(), self.labels)
        try:
            core.write_csv(path, header, rows, self.enc_combo.currentData())
        except OSError as e:
            QMessageBox.critical(self, TITLE, "保存に失敗しました:\n%s" % e)
            return
        self.iface.messageBar().pushSuccess("CSV 出力", "%d 行を保存しました: %s" % (len(rows), path))

    def export_pdf(self):
        res = self.target_results()
        if not res:
            return
        path, _ = QFileDialog.getSaveFileName(self, "PDF 保存", "林地台帳.pdf", "PDF (*.pdf)")
        if not path:
            return
        html = core.build_html(res, self.checked_fields(), self.labels, self.title_edit.text(),
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
