"""QGIS に依存しない検索・CSV・PDF(HTML) 生成ロジック。

レコードは ``OrderedDict[str, str]`` (項目名 -> 表示用文字列)。
検索対象(source)は SRC_PARENT / SRC_ALL / 各リレーションの ID。
"""
import csv
import datetime
import re
import unicodedata
from functools import lru_cache
from collections import OrderedDict
from html import escape

SRC_PARENT = "parent"   # 親レイヤ (林地台帳)
SRC_ALL = "all"         # 親と全リレーション先を横断

OPERATORS = OrderedDict([
    ("contains", "を含む"),
    ("equals", "と等しい"),
    ("startswith", "で始まる"),
    ("endswith", "で終わる"),
    ("not_contains", "を含まない"),
    ("date_from", "(日付) 以降"),
    ("date_to", "(日付) 以前"),
    ("date_equals", "(日付) と同じ"),
])

DATE_OPS = ("date_from", "date_to", "date_equals")

# 和暦の元年の西暦 - 1 (令和6年 -> 2018 + 6)
ERAS = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911, "明治": 1867,
        "R": 2018, "H": 1988, "S": 1925, "T": 1911, "M": 1867}
_ERA_RE = re.compile(r"(令和|平成|昭和|大正|明治|[RHSTM])\s*\.?\s*(元|\d+)\s*(?:年|[./-])\s*(\d{1,2})"
                     r"\s*(?:月|[./-])?\s*(?:(\d{1,2})\s*日?)?")
_WEST_RE = re.compile(r"(\d{4})\s*(?:年|[./-])\s*(\d{1,2})\s*(?:月|[./-])?\s*(?:(\d{1,2})\s*日?)?")


@lru_cache(maxsize=65536)
def parse_date(text):
    """和暦(令和6年5月1日, R6.5.1)・西暦(2024/5/1, 2024年5月1日)を date にする。

    日が無い場合は 1 日として扱う。読み取れなければ None。
    """
    t = unicodedata.normalize("NFKC", str(text)).upper().strip()
    m = _ERA_RE.search(t)
    if m:
        era, y, mo, d = m.group(1), m.group(2), m.group(3), m.group(4)
        year = ERAS[era] + (1 if y == "元" else int(y))
    else:
        m = _WEST_RE.search(t)
        if not m:
            return None
        year, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        return datetime.date(year, int(mo), int(d) if d else 1)
    except ValueError:
        return None


def condition_error(conditions):
    """検索前の入力チェック。問題があればメッセージを返す。"""
    for c in conditions:
        if c.text and c.op in DATE_OPS and parse_date(c.text) is None:
            return "日付を読み取れません: %s\n(例: 令和6年4月1日 / R6.4.1 / 2024-04-01)" % c.text
    return None

ENCODINGS = OrderedDict([
    ("utf-8-sig", "UTF-8 (BOM付き・Excel向け)"),
    ("utf-8", "UTF-8"),
    ("cp932", "Shift_JIS (CP932)"),
])


class Condition:
    """検索条件。field が None のときは全項目が対象。"""

    def __init__(self, field, op, text):
        self.field = field
        self.op = op
        self.text = text


class Result:
    """親レイヤ 1 件と、リレーション先ごとのレコード群 (children[relation_id])。"""

    def __init__(self, parent, children=None):
        self.parent = parent
        self.children = children or {}
        self.fid = None   # 親レイヤの地物 ID (地図で選択するため)

    def records(self, source):
        if source == SRC_PARENT:
            return [self.parent]
        if source == SRC_ALL:
            out = [self.parent]
            for recs in self.children.values():
                out += recs
            return out
        return self.children.get(source, [])


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


def date_matches(value, op, text):
    v, t = parse_date(value), parse_date(text)
    if v is None or t is None:
        return False
    return v >= t if op == "date_from" else v <= t if op == "date_to" else v == t


def record_matches(record, field, op, text):
    values = list(record.values()) if field is None else [record.get(field, "")]
    if op in DATE_OPS:
        return any(date_matches(v, op, text) for v in values)
    if op == "not_contains":
        return all(value_matches(v, op, text) for v in values)
    return any(value_matches(v, op, text) for v in values)


class Hit:
    """検索で一致した 1 レコード (result.records(source)[index])。"""

    def __init__(self, result, source, index):
        self.result = result
        self.source = source
        self.index = index

    @property
    def record(self):
        return self.result.records(self.source)[self.index]


def search_records(results, source, conditions):
    """source のレコード単位で全条件(AND)に一致するものを返す。

    source が SRC_ALL のときは、親と全リレーション先を横断し、各条件が
    どこかのレコードに一致する林地台帳を (親レコードの Hit として) 返す。
    """
    active = [c for c in conditions if c.text]
    hits = []
    for res in results:
        if source == SRC_ALL:
            recs = res.records(SRC_ALL)
            if all(any(record_matches(r, c.field, c.op, c.text) for r in recs) for c in active):
                hits.append(Hit(res, SRC_PARENT, 0))
            continue
        for i, rec in enumerate(res.records(source)):
            if all(record_matches(rec, c.field, c.op, c.text) for c in active):
                hits.append(Hit(res, source, i))
    return hits


def group_hits(hits, only_matched=True):
    """Hit を親ごとにまとめた Result のリストにする (出力用)。

    検索レイヤがリレーション先で only_matched=True なら、そのレイヤの
    レコードを一致したものだけに絞る。
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
        children = dict(res.children)
        children[source] = [res.records(source)[i] for i in idxs]
        out.append(Result(res.parent, children))
    return out


def csv_table(results, fields, labels):
    """CSV 用の (ヘッダ, 行リスト) を返す。

    labels は {source: 表示名} (親が先頭、以降がリレーション先の順)。
    1 つの親に対し、リレーション先の最大件数だけ行を出力する
    (親の項目は各行に繰り返し、足りない側は空欄)。
    """
    sources = [s for s in labels if fields.get(s)]
    header = ["%s:%s" % (labels[s], f) for s in sources for f in fields[s]]
    rows = []
    for res in results:
        n = max([1] + [len(res.records(s)) for s in sources if s != SRC_PARENT])
        for i in range(n):
            row = []
            for s in sources:
                if s == SRC_PARENT:
                    rec = res.parent
                else:
                    recs = res.records(s)
                    rec = recs[i] if i < len(recs) else {}
                row += [rec.get(f, "") for f in fields[s]]
            rows.append(row)
    return header, rows


def write_csv(path, header, rows, encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding, errors="replace") as fp:
        w = csv.writer(fp)
        w.writerow(header)
        w.writerows(rows)


def _kv_table(record, fields, cols=2):
    items = [(f, record.get(f, "")) for f in fields]
    out = ['<table border="1" cellspacing="0" cellpadding="3" width="100%">']
    for i in range(0, len(items), cols):
        out.append("<tr>")
        for k, v in items[i:i + cols]:
            out.append('<td bgcolor="#e8eef5" width="16%%"><b>%s</b></td>'
                       '<td width="34%%">%s</td>' % (escape(k), escape(v) or "&nbsp;"))
        for _ in range(cols - len(items[i:i + cols])):
            out.append('<td bgcolor="#e8eef5">&nbsp;</td><td>&nbsp;</td>')
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def build_html(results, fields, labels, title="林地台帳 出力", font_family="sans-serif", title_field=None):
    """1 親レコード = 1 ページの HTML を生成する (QTextDocument/QPrinter で PDF 化)。"""
    parts = ['<html><head><meta charset="utf-8"></head>'
             '<body style="font-family:\'%s\'; font-size:9pt;">' % escape(font_family, quote=True)]
    total = len(results)
    for i, res in enumerate(results):
        if i:
            parts.append('<p style="page-break-before:always"></p>')
        head = res.parent.get(title_field, "") if title_field else ""
        parts.append("<h2>%s %s</h2><p align='right'>%d / %d</p>"
                     % (escape(title), escape(head), i + 1, total))
        for src, label in labels.items():
            fs = fields.get(src, [])
            if not fs:
                continue
            parts.append("<h3>%s</h3>" % escape(label))
            recs = res.records(src)
            if not recs:
                parts.append("<p>(該当データなし)</p>")
            for n, rec in enumerate(recs, 1):
                if src != SRC_PARENT and len(recs) > 1:
                    parts.append("<p><b>%d 件目</b></p>" % n)
                parts.append(_kv_table(rec, fs))
                parts.append("<br>")
    parts.append("</body></html>")
    return "".join(parts)
