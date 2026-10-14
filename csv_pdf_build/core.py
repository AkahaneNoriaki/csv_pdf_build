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
        "令": 2018, "平": 1988, "昭": 1925, "大": 1911, "明": 1867,
        "R": 2018, "H": 1988, "S": 1925, "T": 1911, "M": 1867}
_ERA = r"(令和|平成|昭和|大正|明治|令|平|昭|大|明|[RHSTM])"
_SEP = r"[./\-・,]"
_ERA_RE = re.compile(_ERA + r"\s*\.?\s*(元|\d+)\s*(?:年|" + _SEP + r")\s*(\d{1,2})"
                     r"\s*(?:月|" + _SEP + r")?\s*(?:(\d{1,2})\s*日?)?")
_ERA_COMPACT_RE = re.compile(_ERA + r"\s*\.?\s*(\d{2})(\d{2})(\d{2})(?!\d)")
_WEST_RE = re.compile(r"(\d{4})\s*(?:年|" + _SEP + r")\s*(\d{1,2})\s*(?:月|" + _SEP + r")?\s*(?:(\d{1,2})\s*日?)?")
_WEST_COMPACT_RE = re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)")
_SERIAL_RE = re.compile(r"^(\d{5})(?:\.\d+)?$")   # Excel の日付シリアル値 (46160 = 2026-05-18)
_EXCEL_EPOCH = datetime.date(1899, 12, 30)
_KANJI_RE = re.compile(r"[〇一二三四五六七八九十]+")
_KANJI_DIGITS = {c: str(i) for i, c in enumerate("〇一二三四五六七八九")}


def _kanji_to_arabic(m):
    """二〇二四 -> 2024, 十二 -> 12, 二十 -> 20"""
    s = m.group(0)
    if "十" in s:
        left, _, right = s.partition("十")
        tens = int("".join(_KANJI_DIGITS[c] for c in left)) if left else 1
        ones = int("".join(_KANJI_DIGITS[c] for c in right)) if right else 0
        return str(tens * 10 + ones)
    return "".join(_KANJI_DIGITS[c] for c in s)


@lru_cache(maxsize=65536)
def parse_date(text):
    """和暦・西暦の日付文字列を date にする。読み取れなければ None。

    対応例: 令和6年5月1日 / 令和元年5月1日 / 令6.4.1 / R06.04.01 / 令和060401 /
    令和六年四月一日 / 2024/5/1 / 2024年5月1日 / 20240501 / 2024-05-01T10:00:00 /
    46160 (Excel のシリアル値、1954〜2119 年の範囲のみ)
    日・月が無いか 00 の場合は 1 日・1 月として扱う (明治33年01月00日 など)。
    """
    t = unicodedata.normalize("NFKC", str(text)).upper().strip()
    m = _SERIAL_RE.match(t)
    if m and 20000 <= int(m.group(1)) <= 80000:
        return _EXCEL_EPOCH + datetime.timedelta(days=int(m.group(1)))
    t = _KANJI_RE.sub(_kanji_to_arabic, t)
    m = _ERA_RE.search(t)
    if m:
        era, y, mo, d = m.group(1), m.group(2), m.group(3), m.group(4)
        year = ERAS[era] + (1 if y == "元" else int(y))
    else:
        m = _ERA_COMPACT_RE.search(t)
        if m:
            era, y, mo, d = m.groups()
            year = ERAS[era] + int(y)
        else:
            m = _WEST_RE.search(t) or _WEST_COMPACT_RE.search(t)
            if not m:
                return None
            year, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        return datetime.date(year, int(mo) or 1, int(d or 0) or 1)
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
    """検索条件。field が None のときは全項目が対象。

    source を指定すると、検索するレイヤとは別のレイヤ (期間指定の林地台帳マスターなど) の
    項目で絞り込む。None のときは検索するレイヤが対象。
    """

    def __init__(self, field, op, text, source=None):
        self.field = field
        self.op = op
        self.text = text
        self.source = source


def split_conditions(conditions, source):
    """入力のある条件を (検索するレイヤの条件, {別レイヤの source: [条件]}) に分ける。"""
    own, other = [], OrderedDict()
    for c in conditions:
        if not c.text:
            continue
        if c.source is None or c.source == source:
            own.append(c)
        else:
            other.setdefault(c.source, []).append(c)
    return own, other


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
    if field is not None and field not in record:
        return False    # その項目を持たないレイヤのレコードは対象外 (横断検索用)
    values = list(record.values()) if field is None else [record[field]]
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
    別レイヤの条件 (source 指定) は qgis_data.search で絞り込み済みなので、ここでは見ない。
    """
    active = split_conditions(conditions, source)[0]
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
        narrowed = Result(res.parent, children)
        narrowed.fid = res.fid      # 地図に表示する地物 ID を引き継ぐ
        out.append(narrowed)
    return out


def result_values(results, source, field):
    """Result 群の source レイヤのレコードにある field の値 (空でないもの) の集合。"""
    out = set()
    for res in results:
        for rec in res.records(source):
            v = rec.get(field, "").strip()
            if v:
                out.add(v)
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


UNKNOWN_OWNER = "(所有者不明)"


def group_by_owner(results, source, field):
    """所有者(source のレイヤの field の値)ごとに Result をまとめる。

    source が親なら親レコードの値で分ける。リレーション先なら、その値を持つ
    子レコードだけに絞った Result を所有者ごとに作る (1 つの親が複数の所有者に
    属することがある)。戻り値は OrderedDict[所有者名 -> [Result]]。
    """
    groups = OrderedDict()
    for res in results:
        if source == SRC_PARENT:
            owner = res.parent.get(field, "").strip() or UNKNOWN_OWNER
            groups.setdefault(owner, []).append(res)
            continue
        recs = res.records(source)
        owners = OrderedDict()
        for rec in recs:
            owners.setdefault(rec.get(field, "").strip() or UNKNOWN_OWNER, []).append(rec)
        if not owners:
            groups.setdefault(UNKNOWN_OWNER, []).append(res)
        for owner, owner_recs in owners.items():
            children = dict(res.children)
            children[source] = owner_recs
            narrowed = Result(res.parent, children)
            narrowed.fid = res.fid
            groups.setdefault(owner, []).append(narrowed)
    return groups


def safe_filename(name, used=None, limit=80):
    """ファイル名に使えない文字を置き換える。used を渡すと重複に連番を付ける。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .") or "名称なし"
    s = s[:limit]
    if used is not None:
        base, n = s, 2
        while s.casefold() in used:
            s = "%s_%d" % (base, n)
            n += 1
        used.add(s.casefold())
    return s
