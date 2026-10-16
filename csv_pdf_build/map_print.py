"""検索結果の林地台帳を、QGIS の印刷レイアウトで地図として出力する。

1 ページ = 地図 1 枚。対象の地物を赤で強調した一時レイヤを地図の最上位に重ね、地物の範囲に
ズームする。下部に、林地台帳と紐づくリレーション先(森林簿など)の情報欄を置ける。
重ねる図形 (Overlay: 施業班など) を指定すると、キーが一致する地物を青で強調して一緒に描く。
"""
import unicodedata
from collections import OrderedDict
from html import escape

from qgis.core import (
    QgsCoordinateTransform, QgsFeature, QgsFeatureRequest, QgsFillSymbol, QgsLayoutExporter,
    QgsLayoutItemLabel, QgsLayoutItemMap, QgsLayoutItemPage, QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutPoint,
    QgsLayoutSize, QgsLineSymbol, QgsMarkerSymbol, QgsPalLayerSettings, QgsPrintLayout, QgsProject,
    QgsRectangle, QgsSingleSymbolRenderer, QgsTextBufferSettings, QgsTextFormat, QgsUnitTypes,
    QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QColor, QFont

from . import core, qgis_data

# QGIS 3.x (QgsWkbTypes) / QGIS 3.30+・4 (Qgis.GeometryType) 両対応
try:
    from qgis.core import Qgis
    _GEOM_POLYGON = Qgis.GeometryType.Polygon
    _GEOM_LINE = Qgis.GeometryType.Line
except (ImportError, AttributeError):
    _GEOM_POLYGON = QgsWkbTypes.PolygonGeometry
    _GEOM_LINE = QgsWkbTypes.LineGeometry

MARGIN = 10          # 用紙の余白 (mm)
TITLE_H = 9          # タイトル行の高さ
INFO_H = 42          # 情報欄の高さ
NORTH_SIZE = 14      # 方位記号の大きさ (mm)
EXTENT_MARGIN = 2.0  # 対象範囲の周囲に取る余白の倍率 (周りの筆も見えるようにする)


def _mm():
    unit = getattr(QgsUnitTypes, "LayoutMillimeters", None)
    if unit is None:
        from qgis.core import Qgis
        unit = Qgis.LayoutUnit.Millimeters
    return unit


class Overlay:
    """一緒に描く別の図形レイヤ (施業班など)。

    検索結果の source レイヤの field の値と、layer の key_field の値が一致する地物を描く。
    """

    def __init__(self, layer, key_field, source, field):
        self.layer = layer
        self.key_field = key_field
        self.source = source
        self.field = field

    def keys(self, results):
        return core.result_values(results, self.source, self.field)


class Options:
    def __init__(self, landscape=True, scale=0, show_info=True, font="sans-serif", label_field=None,
                 overlay=None, list_field=None, heads=None, key_fields=None):
        self.landscape = landscape
        self.scale = scale              # 0 = 対象に合わせて自動
        self.show_info = show_info
        self.font = font
        self.label_field = label_field  # 地図上のラベルに使う親レイヤの項目
        self.overlay = overlay          # Overlay または None
        self.list_field = list_field    # まとめページの情報欄に並べる親レイヤの項目 (所在KEY など)
        self.heads = heads or {}        # 情報欄の見出し {source: 変更林地 / 林地台帳 / 森林簿}
        self.key_fields = key_fields or {}   # 情報欄にその項目の値だけを並べるレイヤ {source: INAKEY}


class PageSpec:
    """1 ページ分: 表示する親の地物 ID と、情報欄に載せる Result。"""

    def __init__(self, title, results):
        self.title = title
        self.results = results
        self.fids = [r.fid for r in results if r.fid is not None]


def make_specs(results, each, title, title_of):
    """each=True: 1 件 1 ページ。False: 全件をまとめて 1 ページ。"""
    if not each:
        return [PageSpec(title, list(results))] if results else []
    return [PageSpec(("%s %s" % (title, title_of(r))).strip(), [r]) for r in results]


class Built:
    """組み立て済みのレイアウトと、後始末が必要な一時レイヤ。"""

    def __init__(self, layout, project, temp_layers, page_size):
        self.layout = layout
        self.project = project
        self.temp_layers = temp_layers
        self.page_size = page_size      # (幅mm, 高さmm)

    def page_count(self):
        return self.layout.pageCollection().pageCount()

    def render_page(self, index, width_px):
        w, h = self.page_size
        exporter = QgsLayoutExporter(self.layout)
        return exporter.renderPageToImage(index, QSize(int(width_px), int(width_px * h / w)))

    def export_pdf(self, path):
        """PDF に書き出す。失敗したときは理由の文字列、成功なら None を返す。"""
        exporter = QgsLayoutExporter(self.layout)
        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = 200
        result = exporter.exportToPdf(path, settings)
        if result == QgsLayoutExporter.ExportResult.Success:
            return None
        return {QgsLayoutExporter.ExportResult.FileError: "ファイルに書き込めません (他で開いていないか確認してください)",
                QgsLayoutExporter.ExportResult.MemoryError: "メモリ不足です (用紙数を減らしてください)",
                QgsLayoutExporter.ExportResult.PrintError: "出力に失敗しました",
                QgsLayoutExporter.ExportResult.SvgLayerError: "出力に失敗しました"}.get(result, "出力に失敗しました (コード %s)" % result)

    def cleanup(self):
        for layer in self.temp_layers:
            self.project.removeMapLayer(layer.id())
        self.temp_layers = []


# ---------- 強調表示レイヤ ----------
PARCEL_COLOR = (220, 0, 0)     # 林地台帳 (赤)
OVERLAY_COLOR = (0, 80, 220)   # 重ねる図形 (青)
LEGEND_PARCEL = "林地"         # 凡例の表示名
LEGEND_OVERLAY = "森林"
SUMMARY_HEAD = "変更林地一覧"  # まとめページの情報欄の見出し


def _symbol(gtype, overlay):
    r, g, b = OVERLAY_COLOR if overlay else PARCEL_COLOR
    if gtype == _GEOM_POLYGON:
        fill = "%d,%d,%d,40" % (r, g, b) if overlay else "255,235,0,90"
        return QgsFillSymbol.createSimple({"color": fill, "outline_color": "%d,%d,%d,255" % (r, g, b),
                                           "outline_width": "0.6" if overlay else "0.8"})
    if gtype == _GEOM_LINE:
        return QgsLineSymbol.createSimple({"color": "%d,%d,%d,255" % (r, g, b), "width": "1.2"})
    return QgsMarkerSymbol.createSimple({"name": "circle", "color": "%d,%d,%d,255" % (r, g, b), "size": "3"})


def _highlight_layer(source_layer, fids, label_field, name, overlay=False):
    """対象の地物だけを複製した一時レイヤ (林地台帳は赤、重ねる図形は青で強調、ラベルつき)。"""
    wkb = QgsWkbTypes.multiType(QgsWkbTypes.flatType(source_layer.wkbType()))
    layer = QgsVectorLayer("%s?field=label:string" % QgsWkbTypes.displayString(wkb), name, "memory")
    layer.setCrs(source_layer.crs())
    feats, rect = [], QgsRectangle()
    rect.setNull()
    request = QgsFeatureRequest().setFilterFids(list(fids))
    for f in source_layer.getFeatures(request):
        geom = f.geometry()
        if geom is None or geom.isNull():
            continue
        geom.convertToMultiType()
        nf = QgsFeature(layer.fields())
        nf.setGeometry(geom)
        nf.setAttribute("label", qgis_data.to_text(f[label_field]) if label_field else "")
        feats.append(nf)
        rect.combineExtentWith(geom.boundingBox())
    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()

    layer.setRenderer(QgsSingleSymbolRenderer(_symbol(layer.geometryType(), overlay)))

    if label_field:
        fmt = QgsTextFormat()
        fmt.setSize(9)
        fmt.setColor(QColor(120, 0, 0))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setColor(QColor(255, 255, 255))
        buf.setSize(1)
        fmt.setBuffer(buf)
        settings = QgsPalLayerSettings()
        settings.fieldName = "label"
        settings.setFormat(fmt)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
    return layer, (None if rect.isNull() or feats == [] else rect)


# ---------- 情報欄の文章 ----------
def _short(rec, fields):
    rec = core.format_record(rec, wareki=True)      # 日付は 令和8年5月27日 の形で載せる
    return " / ".join(rec.get(f, "") for f in fields if rec.get(f, ""))


def _width(text):
    """全角を 1、半角を 0.55 とした表示幅。"""
    return sum(1 if unicodedata.east_asian_width(ch) in "WF" else 0.55 for ch in text)


def _listing(values, line_width=80, max_lines=8):
    """値を「、」区切りで並べ、line_width (全角文字数) ごとに改行した文章。

    印刷レイアウトのラベルは自動で折り返さないため、ここで改行する。max_lines 行に
    入りきらない分は「…他 N 件」にする。
    """
    lines = [[]]                            # 行ごとの値のリスト
    for v in values:
        if lines[-1] and _width("、".join(lines[-1] + [v]) + "、") > line_width:
            lines.append([])
        lines[-1].append(v)
    if len(lines) > max_lines:              # 入りきらない分は最後の行を削って「…他 N 件」にする
        lines = lines[:max_lines]
        shown = sum(len(l) for l in lines)
        while lines[-1] and _width("、".join(lines[-1]) + "、…他 %d 件" % (len(values) - shown)) > line_width:
            lines[-1].pop()
            shown -= 1
        lines[-1].append("…他 %d 件" % (len(values) - shown))
    return "、\n".join("、".join(l) for l in lines)


def info_columns(results, fields, labels, list_field=None, line_width=80, max_lines=8,
                 heads=None, key_fields=None):
    """情報欄の列 [(見出し, 本文), ...]。

    1 件なら親 + 各リレーション先の列。見出しは heads {source: 名前} (無ければレイヤ名)。
    key_fields {source: 項目} のレイヤは、その項目の値 (森林簿の INAKEY など) を重複なしで並べる。
    複数件なら親レイヤの list_field (所在KEY など) を「、」区切りで並べる。
    いずれも枠に入りきらない分は「…他 N 件」とする。
    """
    heads, key_fields = heads or {}, key_fields or {}
    if len(results) != 1:
        field = list_field or (fields.get(core.SRC_PARENT) or [None])[0]
        values = [r.parent.get(field, "") for r in results] if field else []
        head = "%s (%d 件)" % (SUMMARY_HEAD, len(results))
        return [(head, _listing([v for v in values if v], line_width, max_lines))]
    res = results[0]
    shown = [s for s in labels if fields.get(s)]
    col_width = int(line_width / max(1, len(shown))) - 1
    cols = []
    for src, label in labels.items():
        fs = fields.get(src, [])
        if not fs:
            continue
        label = heads.get(src, label)
        if src in key_fields:
            values = list(OrderedDict.fromkeys(
                r.get(key_fields[src], "").strip() for r in res.records(src) if r.get(key_fields[src], "").strip()))
            body = _listing(values, col_width, max_lines) if values else "(該当なし)"
            label = "%s %s" % (label, key_fields[src])
        elif src == core.SRC_PARENT:
            parent = core.format_record(res.parent, wareki=True)
            body = "\n".join("%s: %s" % (f, parent.get(f, "")) for f in fs)
        else:
            recs = res.records(src)
            body = "\n".join(_short(r, fs) for r in recs[:12]) or "(該当なし)"
            if len(recs) > 12:
                body += "\n…他 %d 件" % (len(recs) - 12)
        cols.append((label, body))
    return cols


# ---------- レイアウト ----------
def _text_format(font, size):
    fmt = QgsTextFormat()
    fmt.setFont(QFont(font))
    fmt.setSize(size)
    return fmt


def _label(layout, text, font, size, page, x, y, w, h, frame=False, valign=Qt.AlignmentFlag.AlignVCenter):
    label = QgsLayoutItemLabel(layout)
    label.setText(text)
    if hasattr(label, "setTextFormat"):
        label.setTextFormat(_text_format(font, size))
    else:
        label.setFont(QFont(font, int(size)))
    label.setVAlign(valign)
    if hasattr(label, "setMarginX"):
        label.setMarginX(1.5)
        label.setMarginY(1.2)
    label.setFrameEnabled(frame)
    layout.addLayoutItem(label)
    _place(label, page, x, y, w, h)
    return label


def _legend(layout, parent_name, overlay_name, font, page, x, y, w, h):
    """地図の右下に置く凡例 (色の四角 + レイヤ名)。"""
    label = QgsLayoutItemLabel(layout)
    label.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
    item = '<span style="color:rgb(%d,%d,%d)">■</span> %s'
    label.setText('<div style="font-family:\'%s\'; font-size:9pt;">%s&nbsp;&nbsp;%s</div>'
                  % (escape(font, quote=True), item % (PARCEL_COLOR + (escape(parent_name),)),
                     item % (OVERLAY_COLOR + (escape(overlay_name),))))
    label.setBackgroundEnabled(False)
    label.setFrameEnabled(True)
    if hasattr(label, "setMarginX"):
        label.setMarginX(1.5)
        label.setMarginY(1.0)
    layout.addLayoutItem(label)
    _place(label, page, x, y, w, h)
    return label


def _place(item, page, x, y, w, h):
    mm = _mm()
    item.attemptResize(QgsLayoutSize(w, h, mm))
    item.attemptMove(QgsLayoutPoint(x, y, mm), True, False, page)


def build(project, parent_layer, specs, options, base_layers, fields, labels):
    """specs の各ページに地図・タイトル・縮尺・情報欄を置いたレイアウトを作る。"""
    mm = _mm()
    W, H = (297, 210) if options.landscape else (210, 297)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("林地台帳出力")
    pages = layout.pageCollection()
    pages.page(0).setPageSize(QgsLayoutSize(W, H, mm))
    for _ in range(1, len(specs)):
        page = QgsLayoutItemPage(layout)
        page.setPageSize(QgsLayoutSize(W, H, mm))
        pages.addPage(page)

    to_project = QgsCoordinateTransform(parent_layer.crs(), project.crs(), project)
    ov = options.overlay
    if ov is not None:
        ov_index = qgis_data.key_index(ov.layer, ov.key_field, ov.keys([r for s in specs for r in s.results]))
        ov_to_project = QgsCoordinateTransform(ov.layer.crs(), project.crs(), project)
    info_h = INFO_H if options.show_info else 0
    map_y = MARGIN + TITLE_H + 2
    map_h = H - map_y - MARGIN - (info_h + 2 if info_h else 0)
    temp_layers = []

    for i, spec in enumerate(specs):
        hl, rect = _highlight_layer(parent_layer, spec.fids, options.label_field, "強調表示_%d" % (i + 1))
        project.addMapLayer(hl, False)       # 凡例には出さずプロジェクトに登録する
        temp_layers.append(hl)
        if rect is not None:
            rect = to_project.transformBoundingBox(rect)
        shown = [hl]
        if ov is not None:
            ov_fids = {fid for k in ov.keys(spec.results) for fid in ov_index.get(k, [])}
            ol, ov_rect = _highlight_layer(ov.layer, ov_fids, None, "重ねる図形_%d" % (i + 1), overlay=True)
            project.addMapLayer(ol, False)
            temp_layers.append(ol)
            shown.append(ol)                 # 林地台帳の下に描く
            if ov_rect is not None:          # 施業班も入るように範囲を広げる
                ov_rect = ov_to_project.transformBoundingBox(ov_rect)
                if rect is None:
                    rect = ov_rect
                else:
                    rect.combineExtentWith(ov_rect)

        _label(layout, spec.title, options.font, 14, i, MARGIN, MARGIN, W - 2 * MARGIN - 55, TITLE_H)

        map_item = QgsLayoutItemMap(layout)
        map_item.setId("map_%d" % i)
        layout.addLayoutItem(map_item)
        _place(map_item, i, MARGIN, map_y, W - 2 * MARGIN, map_h)
        map_item.setCrs(project.crs())
        map_item.setLayers(shown + [l for l in base_layers if ov is None or l.id() != ov.layer.id()])
        map_item.setKeepLayerSet(True)
        map_item.setFrameEnabled(True)
        if rect is not None:
            rect.scale(EXTENT_MARGIN)
            if rect.width() == 0 or rect.height() == 0:
                rect.grow(10)
            map_item.zoomToExtent(rect)
            # 指定が無ければ、対象が入る縮尺を切りのよい値 (1:1,000 など) に切り上げる
            map_item.setScale(options.scale if options.scale > 0 else core.nice_scale(map_item.scale()))

        scale = map_item.scale()
        scale_text = "縮尺 1:{:,}".format(int(round(scale))) if scale == scale and scale > 0 else "縮尺 -"
        _label(layout, scale_text,
               options.font, 10, i, W - MARGIN - 52, MARGIN, 52, TITLE_H)

        north = QgsLayoutItemPicture(layout)
        north.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        north.setLinkedMap(map_item)         # 地図の回転に追従する
        north.setBackgroundEnabled(False)
        layout.addLayoutItem(north)
        _place(north, i, W - MARGIN - 3 - NORTH_SIZE, map_y + 3, NORTH_SIZE, NORTH_SIZE)

        bar = QgsLayoutItemScaleBar(layout)
        bar.setLinkedMap(map_item)
        bar.setUnits(project.crs().mapUnits())
        bar.setNumberOfSegments(2)
        bar.setNumberOfSegmentsLeft(0)
        bar.setStyle("Single Box")
        bar.setBackgroundEnabled(False)
        bar.applyDefaultSize(project.crs().mapUnits())
        layout.addLayoutItem(bar)
        bar.attemptMove(QgsLayoutPoint(MARGIN + 3, map_y + map_h - 14, mm), True, False, i)
        if ov is not None:
            legend_w = 10 + 3.0 * len(LEGEND_PARCEL + LEGEND_OVERLAY)
            _legend(layout, LEGEND_PARCEL, LEGEND_OVERLAY, options.font, i,
                    W - MARGIN - 3 - legend_w, map_y + map_h - 11, legend_w, 8)

        if options.show_info:
            # 9pt の全角 1 文字 ≒ 3.3mm、1 行 ≒ 5mm (見出し行の分を除く)
            cols = info_columns(spec.results, fields, labels, options.list_field,
                                int((W - 2 * MARGIN - 6) / 3.3), int((info_h - 7) / 5.0),
                                options.heads, options.key_fields)
            if cols:
                gap = 2
                col_w = (W - 2 * MARGIN - gap * (len(cols) - 1)) / len(cols)
                y = H - MARGIN - info_h
                for k, (head, body) in enumerate(cols):
                    _label(layout, "【%s】\n%s" % (head, body), options.font, 9, i,
                           MARGIN + k * (col_w + gap), y, col_w, info_h, frame=True, valign=Qt.AlignmentFlag.AlignTop)
    return Built(layout, project, temp_layers, (W, H))
