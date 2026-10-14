"""QGIS レイヤ/リレーションから core 用のレコードを作る。"""
from collections import OrderedDict

from qgis.core import NULL, QgsFeatureRequest, QgsProject

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


def _children_by_key(relation, tick):
    """子レイヤを 1 回だけ走査して、親キーごとにレコードをまとめる。

    relation.getRelatedFeatures() を親ごとに呼ぶと、親の件数 x 子の件数の
    検索になり (特に Excel は索引が無い)、極端に遅くなるため使わない。
    """
    layer = relation.referencingLayer()
    pairs = relation.fieldPairs()          # {子の項目: 親の項目}
    child_fields = list(pairs.keys())
    names = field_names(layer)
    groups = {}
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
    for c in layer.getFeatures(request):
        tick()
        groups.setdefault(_key(c, child_fields), []).append(feature_record(c, names))
    return groups, [pairs[k] for k in child_fields]


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


def load_results(parent_layer, relations, selected_only=False, progress=None):
    """親レイヤの地物と、全リレーション先のレコードを読み込む (各レイヤ 1 回の走査)。

    子レコードは Result.children[relation.id()] に入る。
    progress(done, total) が与えられると、一定件数ごとに呼び出す。
    """
    total = parent_layer.featureCount() + sum(r.referencingLayer().featureCount() for r in relations)
    state = {"done": 0}

    def tick():
        state["done"] += 1
        if progress and state["done"] % 500 == 0:
            progress(state["done"], total)

    pnames = field_names(parent_layer)
    rels = [(r.id(),) + _children_by_key(r, tick) for r in relations]
    request = QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry)
    feats = parent_layer.getSelectedFeatures(request) if selected_only else parent_layer.getFeatures(request)
    results = []
    for f in feats:
        tick()
        children = {rid: groups.get(_key(f, pf), []) for rid, groups, pf in rels}
        res = Result(feature_record(f, pnames), children)
        res.fid = f.id()
        results.append(res)
    if progress:
        progress(total, total)
    return results
