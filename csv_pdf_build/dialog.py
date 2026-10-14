import os
import traceback
from collections import OrderedDict

from qgis.core import QgsCoordinateTransform, QgsProject, QgsRectangle, QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QFontDatabase
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QSpinBox, QComboBox, QDialog, QFileDialog,
    QFontComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout,
)

from . import core, map_print, preview, qgis_data

ITEM_CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable
CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked
ALL_FIELDS = "(全項目)"
PREVIEW_LIMIT = 10   # 印刷プレビューに描く最大ページ数 (地図の描画は 1 ページごとに時間がかかる)
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
        first = not self.dialog.cond_rows or self.dialog.cond_rows[0] is self
        if first and self.field.findData("氏名") >= 0:
            self.field.setCurrentIndex(self.field.findData("氏名"))   # 先頭の条件は氏名を既定にする

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
        h.addWidget(QLabel("期間指定"))
        self.period_field = QComboBox()
        self.period_from = QLineEdit()
        self.period_from.setPlaceholderText("開始日 (例: 令和6年4月1日)")
        self.period_to = QLineEdit()
        self.period_to.setPlaceholderText("終了日 (例: 令和6年6月30日)")
        for w in (self.period_from, self.period_to):
            w.returnPressed.connect(self.do_search)
        h.addWidget(self.period_field, 1)
        h.addWidget(self.period_from, 2)
        h.addWidget(QLabel("〜"))
        h.addWidget(self.period_to, 2)
        v.addLayout(h)
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
        b_sel = QPushButton("地図で選択")
        b_zoom = QPushButton("選択して拡大")
        b_sel.clicked.connect(lambda: self.select_on_map(False))
        b_zoom.clicked.connect(lambda: self.select_on_map(True))
        h.addWidget(self.status, 1)
        h.addWidget(self.progress)
        h.addWidget(b_sel)
        h.addWidget(b_zoom)
        root.addLayout(h)

        # --- 2. 検索レイヤ選択 ---
        box = QGroupBox("2. 検索レイヤ選択 (リレーション先は自動で検出し、検索時に必要な分だけ読みます)")
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
        box = QGroupBox("4. 地図PDF出力 (表の行を選択した場合は選択行のみ、未選択なら検索結果すべて)")
        form = QFormLayout(box)
        self.only_matched = QCheckBox("リレーション先を検索した場合は、一致したレコードのみ出力する")
        self.only_matched.setChecked(True)
        self.owner_combo = QComboBox()
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.default_font()))
        self.title_edit = QLineEdit("林地台帳")
        self.title_field = QComboBox()
        self.range_combo = QComboBox()
        self.range_combo.addItem("1筆(1件)ごとに1ページ", True)
        self.range_combo.addItem("検索結果をまとめて1ページ", False)
        self.orient_combo = QComboBox()
        self.orient_combo.addItem("A4 横", True)
        self.orient_combo.addItem("A4 縦", False)
        self.scale_spin = QSpinBox()
        self.scale_spin.setRange(0, 1000000)
        self.scale_spin.setSingleStep(500)
        self.scale_spin.setSpecialValueText("自動 (対象に合わせる)")
        self.scale_spin.setPrefix("1 : ")
        self.info_check = QCheckBox("ページ下に林地台帳・森林簿などの情報を表示する")
        self.info_check.setChecked(True)
        self.overlay_layer = QComboBox()        # 一緒に描く図形レイヤ (施業班など)
        self.overlay_key = QComboBox()          # そのレイヤのキー項目 (KEY_02 など)
        self.overlay_match = QComboBox()        # 検索結果側の項目 (林地台帳の施業KEY など)
        self.overlay_layer.currentIndexChanged.connect(lambda *_: self.update_overlay_keys())
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(self.overlay_layer, 2)
        overlay_row.addWidget(QLabel("の"))
        overlay_row.addWidget(self.overlay_key, 1)
        overlay_row.addWidget(QLabel("＝"))
        overlay_row.addWidget(self.overlay_match, 2)
        form.addRow(self.only_matched)
        form.addRow("地図の範囲", self.range_combo)
        form.addRow("用紙", self.orient_combo)
        form.addRow("縮尺", self.scale_spin)
        form.addRow("重ねる図形 (青で表示)", overlay_row)
        form.addRow(self.info_check)
        form.addRow("フォント", self.font_combo)
        form.addRow("タイトル", self.title_edit)
        form.addRow("地図ラベル・結果表に使う親レイヤの項目", self.title_field)
        form.addRow("所有者別PDFの所有者項目", self.owner_combo)
        h = QHBoxLayout()
        b_prev = QPushButton("印刷プレビュー → PDF")
        b_pdf = QPushButton("PDF 出力")
        b_owner = QPushButton("所有者別PDFを作成")
        b_prev.clicked.connect(self.preview_pdf)
        b_pdf.clicked.connect(self.export_pdf)
        b_owner.clicked.connect(self.export_owner_pdfs)
        h.addStretch(1)
        h.addWidget(b_prev)
        h.addWidget(b_pdf)
        h.addWidget(b_owner)
        form.addRow(h)
        root.addWidget(box)

        self.layer_combo.currentIndexChanged.connect(lambda *_: self.on_layer_changed())
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)
        self.add_condition()

    @staticmethod
    def default_font():
        fams = set(QFontDatabase().families())
        for f in ("Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP",
                  "IPAexGothic", "IPAGothic", "Hiragino Sans"):
            if f in fams:
                return f
        return QFont().family()

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
        self.on_layer_changed()
        if not self.layer_combo.count():
            self.status.setText("リレーションが設定されたレイヤがありません。"
                                "プロジェクトのプロパティ > リレーション で設定してください。")

    def on_layer_changed(self):
        layer = QgsProject.instance().mapLayer(self.layer_combo.currentData() or "")
        self.parent_layer = layer
        self.relations = qgis_data.relations_for(layer)
        self.labels = OrderedDict([(core.SRC_PARENT, layer.name() if layer else "")])
        self.names = {core.SRC_PARENT: qgis_data.field_names(layer)}
        for r in self.relations:
            self.labels[r.id()] = qgis_data.relation_label(r)
            self.names[r.id()] = qgis_data.field_names(r.referencingLayer())
        union = OrderedDict()
        for src in self.labels:
            for n in self.names[src]:
                union[n] = True
        self.names[core.SRC_ALL] = list(union)   # 横断検索: どのレイヤの項目名でも指定できる
        self.related_label.setText("、".join(self.labels[r.id()] for r in self.relations) or "(なし)")

        # 検索するレイヤの選択肢
        keep = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for src, label in self.labels.items():
            if src != core.SRC_PARENT:
                self.source_combo.addItem("リレーション先: " + label, src)
        self.source_combo.addItem("親: " + self.labels[core.SRC_PARENT], core.SRC_PARENT)
        self.source_combo.addItem("すべてのレイヤを横断", core.SRC_ALL)
        i = self.source_combo.findData(keep)
        if i < 0:   # 初回は、氏名・所有者の項目を持つリレーション先を検索レイヤの初期値にする
            i = next((k for k in range(self.source_combo.count())
                      if self.source_combo.itemData(k) in self.relation_ids()
                      and any(("氏名" in n or "所有者" in n) for n in self.names[self.source_combo.itemData(k)])), 0)
        self.source_combo.setCurrentIndex(i)
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
        self.update_period_fields()
        self.update_owner_combo()
        self.update_overlay_layers()
        self.results, self.hits = [], []
        self.show_hits()

    def relation_ids(self):
        return [r.id() for r in self.relations]

    def update_period_fields(self):
        """期間指定の日付項目。検索するレイヤに関係なく、全レイヤの項目から選べる。

        林地台帳マスターの現_届出 (届出日) を既定にする。
        """
        self.period_field.clear()
        self.period_field.addItem("(検索するレイヤの全項目)", None)
        for src, label in self.labels.items():
            for n in self.names[src]:
                self.period_field.addItem("%s: %s" % (label, n), (src, n))
        for key in ("現_届出", "届け出年月日", "届出年月日", "入力年月日", "年月日", "更新日"):
            hit = next((i for i in range(1, self.period_field.count())
                        if key in self.period_field.itemData(i)[1]), -1)
            if hit >= 0:
                self.period_field.setCurrentIndex(hit)
                break

    def current_conditions(self):
        """条件行 + 期間指定 (開始日以降・終了日以前) をまとめた検索条件。"""
        conds = [r.condition() for r in self.cond_rows]
        src, field = self.period_field.currentData() or (None, None)
        if self.period_from.text().strip():
            conds.append(core.Condition(field, "date_from", self.period_from.text().strip(), src))
        if self.period_to.text().strip():
            conds.append(core.Condition(field, "date_to", self.period_to.text().strip(), src))
        return conds

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

    # ---------- 検索 ----------
    def do_search(self):
        """リレーション先(Excel)などを検索し、一致した親レイヤと紐づく情報を取り出す。"""
        if self.parent_layer is None:
            QMessageBox.warning(self, TITLE, "リレーション済みのレイヤを選択してください。")
            return
        conds = self.current_conditions()
        err = core.condition_error(conds)
        if err:
            QMessageBox.warning(self, TITLE, err)
            return
        self.setEnabled(False)   # 検索中は操作を受け付けない
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)   # 件数不明の動作表示
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        def progress(label, count):
            self.status.setText("%s (%d 件処理)" % (label, count))
            QApplication.processEvents()

        try:
            diag = qgis_data.new_date_diag()
            self.results = qgis_data.search(
                self.parent_layer, self.relations, self.search_source(), conds,
                self.selected_only.currentData(), progress, diag)
            self.hits = core.search_records(self.results, self.search_source(), conds)
        except Exception:  # 失敗を黙殺せず画面に出す
            self.results, self.hits = [], []
            QMessageBox.critical(self, TITLE, "検索中にエラーが発生しました:\n\n" + traceback.format_exc())
        finally:
            QApplication.restoreOverrideCursor()
            self.progress.setVisible(False)
            self.setEnabled(True)
        self.show_hits()
        if not self.hits:
            self.status.setText("該当するデータはありませんでした (%s)" % self.source_combo.currentText())
            self.explain_date_problem(conds, diag)

    def explain_date_problem(self, conds, diag):
        """日付条件で 0 件のとき、日付が読めなかったのが原因かを知らせる。"""
        if not any(c.op in core.DATE_OPS and c.text for c in conds):
            return
        field = next(c.field for c in conds if c.op in core.DATE_OPS and c.text) or "(全項目)"
        if diag["parsed"] == 0 and diag["failed"]:
            QMessageBox.warning(
                self, TITLE, "日付項目「%s」の値を日付として読み取れませんでした (%d 件)。\n\n読み取れなかった値の例:\n%s\n\n"
                "この書式を開発者に伝えてください。" % (field, diag["failed"], "\n".join(diag["samples"])))
        elif diag["parsed"] == 0:
            QMessageBox.warning(
                self, TITLE, "日付項目「%s」に値がありません。\n検索するレイヤに、この項目が含まれているか確認してください。\n"
                "(別のExcelにある場合は、期間指定の項目で「レイヤ名: 項目名」を選んでください)" % field)

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
        if self.hits:
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
        selected = [self.parent_layer]
        msg = "%s: %d 件" % (self.parent_layer.name(), len(fids))
        ov = self.overlay()
        if ov is not None:      # 重ねる図形 (施業班) も選択する
            index = qgis_data.key_index(ov.layer, ov.key_field, ov.keys(results))
            ov_fids = sorted({fid for v in index.values() for fid in v})
            ov.layer.selectByIds(ov_fids)
            selected.append(ov.layer)
            msg += "、%s: %d 件" % (ov.layer.name(), len(ov_fids))
        if zoom:
            self.zoom_to_selected(selected)
        self.iface.messageBar().pushSuccess("地図で選択", msg + " を選択しました")

    def zoom_to_selected(self, layers):
        """複数レイヤの選択地物がすべて入る範囲に地図を合わせる。"""
        canvas = self.iface.mapCanvas()
        crs = canvas.mapSettings().destinationCrs()
        rect = QgsRectangle()
        rect.setNull()
        for layer in layers:
            if layer.selectedFeatureCount():
                tr = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance())
                rect.combineExtentWith(tr.transformBoundingBox(layer.boundingBoxOfSelected()))
        if rect.isNull():
            return
        rect.scale(1.2)
        canvas.setExtent(rect)
        canvas.refresh()

    # ---------- 出力 ----------
    def update_owner_combo(self):
        """所有者別PDFで分ける項目 (親・リレーション先の全項目から選ぶ)。"""
        self.owner_combo.clear()
        for src, label in self.labels.items():
            for n in self.names[src]:
                self.owner_combo.addItem("%s: %s" % (label, n), (src, n))
        # 氏名・所有者はリレーション先の項目を優先する (親より先に探す)
        order = [s for s in self.labels if s != core.SRC_PARENT] + [core.SRC_PARENT]
        for key in ("所有者氏名", "氏名", "所有者"):
            for src in order:
                hit = next((i for i in range(self.owner_combo.count())
                            if self.owner_combo.itemData(i)[0] == src and key in self.owner_combo.itemData(i)[1]), -1)
                if hit >= 0:
                    self.owner_combo.setCurrentIndex(hit)
                    return

    def update_overlay_layers(self):
        """重ねる図形の候補 = 図形を持つベクタレイヤ (親レイヤ以外)。施業班を既定にする。"""
        keep = self.overlay_layer.currentData()
        self.overlay_layer.blockSignals(True)
        self.overlay_layer.clear()
        self.overlay_layer.addItem("(なし)", None)
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.isSpatial() and layer is not self.parent_layer:
                self.overlay_layer.addItem(layer.name(), layer.id())
        i = self.overlay_layer.findData(keep) if keep else -1
        if i < 0:
            i = next((k for k in range(1, self.overlay_layer.count())
                      if "施業班" in self.overlay_layer.itemText(k)), 0)
        self.overlay_layer.setCurrentIndex(i)
        self.overlay_layer.blockSignals(False)

        self.overlay_match.clear()
        for src, label in self.labels.items():
            for n in self.names[src]:
                self.overlay_match.addItem("%s: %s" % (label, n), (src, n))
        hit = next((k for k in range(self.overlay_match.count())
                    if "施業" in self.overlay_match.itemData(k)[1]
                    and any(w in self.overlay_match.itemData(k)[1].upper() for w in ("KEY", "キー"))), 0)
        self.overlay_match.setCurrentIndex(hit)     # 親(林地台帳)の施業KEY が先に見つかる
        self.update_overlay_keys()

    def update_overlay_keys(self):
        layer = self.overlay_layer_obj()
        self.overlay_key.clear()
        for n in qgis_data.field_names(layer):
            self.overlay_key.addItem(n, n)
        for key in ("KEY_02", "施業KEY", "施業キー", "KEY"):
            i = next((k for k in range(self.overlay_key.count()) if key in self.overlay_key.itemText(k).upper()), -1)
            if i >= 0:
                self.overlay_key.setCurrentIndex(i)
                break

    def overlay_layer_obj(self):
        return QgsProject.instance().mapLayer(self.overlay_layer.currentData() or "")

    def overlay(self):
        """選択中の「重ねる図形」の設定 (無ければ None)。"""
        layer, match = self.overlay_layer_obj(), self.overlay_match.currentData()
        if layer is None or not self.overlay_key.currentData() or not match:
            return None
        return map_print.Overlay(layer, self.overlay_key.currentData(), match[0], match[1])

    def build_print(self, results, title=None):
        """検索結果を地図の印刷レイアウトにする。使い終わったら cleanup() を呼ぶこと。"""
        if self.parent_layer is None or not self.parent_layer.isSpatial():
            raise RuntimeError("検索レイヤに図形がないため、地図を出力できません。")
        label_field = self.title_field.currentData()
        specs = map_print.make_specs(results, self.range_combo.currentData(),
                                     title if title is not None else self.title_edit.text(), self.parent_label)
        options = map_print.Options(
            landscape=self.orient_combo.currentData(), scale=self.scale_spin.value(),
            show_info=self.info_check.isChecked(), font=self.font_combo.currentFont().family(),
            label_field=label_field, overlay=self.overlay())
        return map_print.build(QgsProject.instance(), self.parent_layer, specs, options,
                               self.iface.mapCanvas().layers(), self.checked_fields(), self.labels)

    def preview_pdf(self):
        """印刷プレビューを開き、そこから PDF 保存する (プレビューは先頭 PREVIEW_LIMIT ページのみ描く)。"""
        res = self.target_results()
        if not res:
            return
        each = self.range_combo.currentData()
        shown = res[:PREVIEW_LIMIT] if each else res
        built = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            built = self.build_print(shown)
        except Exception as e:
            QMessageBox.critical(self, TITLE, "プレビューを作れませんでした:\n%s" % e)
            return
        finally:
            QApplication.restoreOverrideCursor()
        note = "%d 件を出力します。" % len(res)
        if each and len(shown) < len(res):
            note += "プレビューは先頭 %d ページのみ表示します (PDF には全 %d ページが入ります)。" % (len(shown), len(res))

        def save_all(path):
            full = self.build_print(res)
            try:
                return full.export_pdf(path)
            finally:
                full.cleanup()

        try:
            dlg = preview.PreviewDialog(built.page_count(), built.render_page, note, save_all, parent=self)
            if dlg.exec() and dlg.saved_path:
                self.iface.messageBar().pushSuccess("PDF 出力", "%d 件を保存しました: %s" % (len(res), dlg.saved_path))
        finally:
            built.cleanup()

    def export_pdf(self):
        res = self.target_results()
        if not res:
            return
        path, _ = QFileDialog.getSaveFileName(self, "PDF 保存", "林地台帳.pdf", "PDF (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        built = None
        try:
            built = self.build_print(res)
            err = built.export_pdf(path)
        except Exception:
            err = traceback.format_exc()
        finally:
            QApplication.restoreOverrideCursor()
            if built:
                built.cleanup()
        if err:
            QMessageBox.critical(self, TITLE, "PDF を保存できませんでした:\n" + err)
            return
        self.iface.messageBar().pushSuccess("PDF 出力", "%d 件を保存しました: %s" % (len(res), path))

    def export_owner_pdfs(self):
        """検索結果を所有者ごとに分け、所有者ごとに 1 つの PDF を作る。"""
        data = self.owner_combo.currentData()
        res = self.target_results() if data else []
        if not data:
            QMessageBox.information(self, TITLE, "所有者の項目を選択してください。")
        if not res:
            return
        groups = core.group_by_owner(res, data[0], data[1])
        folder = QFileDialog.getExistingDirectory(self, "所有者別PDFの保存先フォルダ")
        if not folder:
            return
        ok = QMessageBox.question(
            self, TITLE, "%d 人分の PDF を作成します。\n保存先: %s\n\nよろしいですか?" % (len(groups), folder))
        if ok != QMessageBox.StandardButton.Yes:
            return
        used, saved, errors = set(), 0, []
        self.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for i, (owner, items) in enumerate(groups.items(), 1):
                self.status.setText("PDF 作成中 %d / %d: %s" % (i, len(groups), owner))
                QApplication.processEvents()
                path = os.path.join(folder, core.safe_filename(owner, used) + ".pdf")
                try:
                    built = self.build_print(items, "%s %s 様" % (self.title_edit.text(), owner))
                    try:
                        err = built.export_pdf(path)
                    finally:
                        built.cleanup()
                    if err:
                        raise RuntimeError(err)
                    saved += 1
                except Exception as e:  # 1 人分の失敗で全体を止めない
                    errors.append("%s: %s" % (owner, e))
        finally:
            QApplication.restoreOverrideCursor()
            self.setEnabled(True)
        msg = "%d / %d 人分の PDF を保存しました: %s" % (saved, len(groups), folder)
        self.status.setText(msg)
        if errors:
            QMessageBox.warning(self, TITLE, msg + "\n\n失敗:\n" + "\n".join(errors[:20]))
        else:
            self.iface.messageBar().pushSuccess("所有者別PDF", msg)
