"""QGIS に依存しない検索・CSV・PDF(HTML) 生成ロジック。

レコードは ``OrderedDict[str, str]`` (項目名 -> 表示用文字列) として扱う。
"""
import csv
import unicodedata
from collections import OrderedDict
from html import escape

# 検索対象の区分
SRC_PARENT = "parent"   # 林地台帳レイヤ
SRC_DETAIL = "detail"   # 林地台帳の詳細情報 (Excel)
SRC_FOREST = "forest"   # 森林簿の詳細情報 (Excel)

SOURCE_LABELS = {
    SRC_PARENT: "林地台帳",
    SRC_DETAIL: "林地台帳詳細",
    SRC_FOREST: "森林簿詳細",
}

OPERATORS = OrderedDict([
    ("contains", "を含む"),
    ("equals", "と等しい"),
    ("startswith", "で始まる"),
    ("endswith", "で終わる"),
    ("not_contains", "を含まない"),
])

ENCODINGS = OrderedDict([
    ("utf-8-sig", "UTF-8 (BOM付き・Excel向け)"),
    ("utf-8", "UTF-8"),
    ("cp932", "Shift_JIS (CP932)"),
])


class Condition:
    """検索条件。field が None のときはその区分の全項目が対象。"""

    def __init__(self, source, field, op, text):
        self.source = source
        self.field = field
        self.op = op
        self.text = text


class Result:
    """林地台帳 1 件と、それにぶら下がる詳細レコード群。"""

    def __init__(self, parent, detail=None, forest=None):
        self.parent = parent
        self.detail = detail or []
        self.forest = forest or []

    def records(self, source):
        if source == SRC_PARENT:
            return [self.parent]
        return self.detail if source == SRC_DETAIL else self.forest


def normalize(text):
    """全角/半角・大文字小文字の違いを吸収する。"""
    return unicodedata.normalize("NFKC", str(text)).casefold().strip()


def value_matches(value, op, text):
    v, t = normalize(value), normalize(text)
    if op == "equals":
        return v == t
    if op == "startswith":
        return v.startswith(t)
    if op == "endswith":
        return v.endswith(t)
    if op == "not_contains":
        return t not in v
    return t in v


def record_matches(record, field, op, text):
    if field is None:
        values = list(record.values())
    else:
        values = [record.get(field, "")]
    if op == "not_contains":
        return all(value_matches(v, op, text) for v in values)
    return any(value_matches(v, op, text) for v in values)


def result_matches(result, conditions):
    """全条件を満たす (AND)。詳細側は 1 件でも一致すれば成立。"""
    for c in conditions:
        if not c.text:
            continue
        recs = result.records(c.source)
        if not any(record_matches(r, c.field, c.op, c.text) for r in recs):
            return False
    return True


def search(results, conditions):
    return [r for r in results if result_matches(r, conditions)]


class Hit:
    """検索レイヤ上で条件に一致した 1 レコード (result.records(source)[index])。"""

    def __init__(self, result, source, index):
        self.result = result
        self.source = source
        self.index = index

    @property
    def record(self):
        return self.result.records(self.source)[self.index]


def search_records(results, source, conditions):
    """検索レイヤ(source)のレコード単位で、全条件(AND)に一致するものを返す。"""
    active = [c for c in conditions if c.text]
    hits = []
    for res in results:
        for i, rec in enumerate(res.records(source)):
            if all(record_matches(rec, c.field, c.op, c.text) for c in active):
                hits.append(Hit(res, source, i))
    return hits


def group_hits(hits, only_matched=True):
    """Hit を林地台帳ごとにまとめた Result のリストにする (出力用)。

    検索レイヤが詳細/森林簿で only_matched=True なら、そのレイヤの
    子レコードを一致したものだけに絞る。
    """
    order, picked = [], {}
    for h in hits:
        key = id(h.result)
        if key not in picked:
            picked[key] = (h.result, h.source, [])
            order.append(key)
        picked[key][2].append(h.index)
    out = []
    for key in order:
        res, source, idxs = picked[key]
        if source == SRC_PARENT or not only_matched:
            out.append(res)
            continue
        kept = [res.records(source)[i] for i in idxs]
        out.append(Result(res.parent,
                          kept if source == SRC_DETAIL else res.detail,
                          kept if source == SRC_FOREST else res.forest))
    return out


def _pick(record, fields):
    return OrderedDict((f, record.get(f, "")) for f in fields)


def csv_table(results, fields):
    """CSV 用の (ヘッダ, 行リスト) を返す。

    1 つの林地台帳に対し、詳細/森林簿の件数が多い方の数だけ行を出力する
    (林地台帳の項目は各行に繰り返す。足りない側は空欄)。
    fields は {SRC_*: [項目名,...]}。
    """
    header = []
    for src in (SRC_PARENT, SRC_DETAIL, SRC_FOREST):
        header += ["%s:%s" % (SOURCE_LABELS[src], f) for f in fields.get(src, [])]
    rows = []
    for res in results:
        n = max(1, len(res.detail), len(res.forest))
        for i in range(n):
            row = []
            for src in (SRC_PARENT, SRC_DETAIL, SRC_FOREST):
                fs = fields.get(src, [])
                if src == SRC_PARENT:
                    rec = res.parent
                else:
                    recs = res.records(src)
                    rec = recs[i] if i < len(recs) else {}
                row += [rec.get(f, "") for f in fs]
            rows.append(row)
    return header, rows


def write_csv(path, header, rows, encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding, errors="replace") as fp:
        w = csv.writer(fp)
        w.writerow(header)
        w.writerows(rows)


def _kv_table(record, cols=2):
    items = list(record.items())
    out = ['<table border="1" cellspacing="0" cellpadding="3" width="100%">']
    for i in range(0, len(items), cols):
        out.append("<tr>")
        for k, v in items[i:i + cols]:
            out.append('<td bgcolor="#e8eef5" width="16%%"><b>%s</b></td>'
                       '<td width="%d%%">%s</td>' % (escape(k), 34 if cols == 2 else 17, escape(v) or "&nbsp;"))
        for _ in range(cols - len(items[i:i + cols])):
            out.append('<td bgcolor="#e8eef5">&nbsp;</td><td>&nbsp;</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def build_html(results, fields, title="林地台帳 出力", font_family="sans-serif", title_field=None):
    """1 林地台帳 = 1 ページの HTML を生成する (QTextDocument/QPrinter で PDF 化)。"""
    parts = ['<html><head><meta charset="utf-8"></head>'
             '<body style="font-family:\'%s\'; font-size:9pt;">' % escape(font_family, quote=True)]
    total = len(results)
    for i, res in enumerate(results):
        if i:
            parts.append('<p style="page-break-before:always"></p>')
        head = res.parent.get(title_field, "") if title_field else ""
        parts.append("<h2>%s %s</h2><p align='right'>%d / %d</p>"
                     % (escape(title), escape(head), i + 1, total))
        for src in (SRC_PARENT, SRC_DETAIL, SRC_FOREST):
            fs = fields.get(src, [])
            if not fs:
                continue
            parts.append("<h3>%s</h3>" % escape(SOURCE_LABELS[src]))
            recs = res.records(src)
            if not recs:
                parts.append("<p>(該当データなし)</p>")
            for n, rec in enumerate(recs, 1):
                if src != SRC_PARENT and len(recs) > 1:
                    parts.append("<p><b>%d 件目</b></p>" % n)
                parts.append(_kv_table(_pick(rec, fs)))
                parts.append("<br>")
    parts.append("</body></html>")
    return "".join(parts)
