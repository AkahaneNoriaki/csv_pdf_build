"""QGIS レイヤ/リレーションから core 用のレコードを作る。"""
from collections import OrderedDict, defaultdict

from qgis.core import NULL, QgsFeatureRequest, QgsProject

from . import core
from .core import Result


def to_text(value):
    if value is None or value == NULL:
        return ""
    if hasattr(value, "toString"):
        return value.toString()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def field_names(layer):
    return [f.name() for f in layer.fields()] if layer else []


def relations_for(parent_layer):
    """parent_layer を参照元(親)とするプロジェクトのリレーション一覧。"""
    if parent_layer is None:
        return []
    mgr = QgsProject.instance().relationManager()
    return [r for r in mgr.relations().values()
            if r.isValid() and r.referencedLayerId() == parent_layer.id()]


def feature_record(feature, names):
    return OrderedDict((n, to_text(feature[n])) for n in names)


def _key(feature, names):
    return tuple(to_text(feature[n]).strip() for n in names)


def _request():
    """ジオメトリを読まない地物リクエスト (検索には不要で、読むと遅くなる)。"""
    flag = getattr(QgsFeatureRequest, "NoGeometry", None)
    if flag is None:
        flag = QgsFeatureRequest.Flag.NoGeometry
    return QgsFeatureRequest().setFlags(flag)


def parent_layers():
    """リレーションの親(参照先)になっているレイヤの一覧。"""
    mgr = QgsProject.instance().relationManager()
    seen, out = set(), []
    for r in mgr.relations().values():
        layer = r.referencedLayer()
        if r.isValid() and layer is not None and layer.id() not in seen:
            seen.add(layer.id())
            out.append(layer)
    return out


def relation_label(relation):
    return relation.referencingLayer().name()


class _Scan:
    """走査件数を数え、一定件数ごとに進捗を通知する。"""

    def __init__(self, progress):
        self.progress = progress
        self.n = 0
        self.label = ""

    def start(self, label):
        self.label = label
        if self.progress:
            self.progress(label, self.n)

    def tick(self):
        self.n += 1
        if self.progress and self.n % 2000 == 0:
            self.progress(self.label, self.n)


def new_date_diag():
    return {"parsed": 0, "failed": 0, "samples": []}


def _date_diag(rec, active, diag):
    """日付条件の対象項目が日付として読めたか数える (読めない値の例も控える)。"""
    for c in active:
        if c.op not in core.DATE_OPS:
            continue
        values = list(rec.values()) if c.field is None else [rec.get(c.field, "")]
        for v in values:
            if not v:
                continue
            if core.parse_date(v):
                diag["parsed"] += 1
            else:
                diag["failed"] += 1
                if v not in diag["samples"] and len(diag["samples"]) < 5:
                    diag["samples"].append(v)


def search(parent_layer, relations, source, conditions, selected_only=False, progress=None, diag=None):
    """リレーションレイヤを事前に読み込まず、検索のたびに必要な分だけ読んで Result を返す。

    1. 親レイヤを 1 回走査し、リレーションのキー -> 親の地物 ID の索引を作る
    2. 検索対象レイヤ(リレーション先 Excel など)を 1 回走査し、条件に一致した
       レコードが属する親の地物 ID を集める
    3. 一致した親だけを取り出し、紐づく全リレーション先のレコードを集める
       (一致したキーのレコードだけを保持する)
    戻り値は Result (fid つき) のリスト。条件との突き合わせ(Hit 化)は呼び出し側が
    core.search_records で行う。diag (new_date_diag()) を渡すと、日付条件の項目が
    日付として読めた件数と、読めなかった値の例が入る。
    """
    active = [c for c in conditions if c.text]
    scan = _Scan(progress)
    pnames = field_names(parent_layer)
    rel_by_id = OrderedDict((r.id(), r) for r in relations)
    cfields, pfields = {}, {}
    for rid, r in rel_by_id.items():
        pairs = r.fieldPairs()                    # {子の項目: 親の項目}
        cfields[rid] = list(pairs.keys())
        pfields[rid] = [pairs[k] for k in cfields[rid]]

    selected = set(parent_layer.selectedFeatureIds()) if selected_only else None
    need_parent_match = source in (core.SRC_PARENT, core.SRC_ALL)
    index = {rid: defaultdict(list) for rid in rel_by_id}
    all_fids = set()
    parent_ok = set()                             # 親検索: 全条件に一致した親
    parent_any = [set() for _ in active]          # 横断検索: 条件ごとに一致した親

    # --- 1. 親レイヤ ---
    scan.start("林地台帳を走査中: " + parent_layer.name())
    for f in parent_layer.getFeatures(_request()):
        scan.tick()
        fid = f.id()
        if selected is not None and fid not in selected:
            continue
        all_fids.add(fid)
        for rid in rel_by_id:
            key = _key(f, pfields[rid])
            if any(key):
                index[rid][key].append(fid)
        if need_parent_match:
            rec = feature_record(f, pnames)
            if diag is not None:
                _date_diag(rec, active, diag)
            if source == core.SRC_PARENT:
                if all(core.record_matches(rec, c.field, c.op, c.text) for c in active):
                    parent_ok.add(fid)
            else:
                for i, c in enumerate(active):
                    if core.record_matches(rec, c.field, c.op, c.text):
                        parent_any[i].add(fid)

    # --- 2. 検索対象のリレーション先 ---
    if source == core.SRC_PARENT:
        fids = parent_ok
    else:
        targets = [source] if source in rel_by_id else list(rel_by_id)
        child_ok = set()                          # リレーション先検索: 全条件に一致した親
        for rid in targets:
            layer = rel_by_id[rid].referencingLayer()
            names = field_names(layer)
            scan.start("検索中: " + layer.name())
            for c in layer.getFeatures(_request()):
                scan.tick()
                owners = index[rid].get(_key(c, cfields[rid]))
                if not owners:                    # 親に紐づかないレコードは対象外
                    continue
                rec = feature_record(c, names)
                if diag is not None:
                    _date_diag(rec, active, diag)
                if source == core.SRC_ALL:
                    for i, cond in enumerate(active):
                        if core.record_matches(rec, cond.field, cond.op, cond.text):
                            parent_any[i].update(owners)
                elif all(core.record_matches(rec, cond.field, cond.op, cond.text) for cond in active):
                    child_ok.update(owners)
        if source == core.SRC_ALL:
            fids = set.intersection(*parent_any) if active else set(all_fids)
        else:
            fids = child_ok

    # --- 3. 一致した親と、その全リレーション先を取り出す ---
    if not fids:
        return []
    scan.start("該当データを取得中")
    request = _request().setFilterFids(sorted(fids))
    parents = {f.id(): f for f in parent_layer.getFeatures(request)}
    wanted = {rid: set() for rid in rel_by_id}
    for f in parents.values():
        for rid in rel_by_id:
            key = _key(f, pfields[rid])
            if any(key):
                wanted[rid].add(key)
    groups = {}
    for rid, r in rel_by_id.items():
        groups[rid] = defaultdict(list)
        if not wanted[rid]:
            continue
        layer = r.referencingLayer()
        names = field_names(layer)
        scan.start("関連データを取得中: " + layer.name())
        for c in layer.getFeatures(_request()):
            scan.tick()
            key = _key(c, cfields[rid])
            if key in wanted[rid]:
                groups[rid][key].append(feature_record(c, names))

    results = []
    for fid in sorted(parents):
        f = parents[fid]
        children = {rid: groups[rid].get(_key(f, pfields[rid]), []) for rid in rel_by_id}
        res = Result(feature_record(f, pnames), children)
        res.fid = fid
        results.append(res)
    return results
