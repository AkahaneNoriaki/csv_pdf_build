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


def _children_by_key(relation):
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
        groups.setdefault(_key(c, child_fields), []).append(feature_record(c, names))
    return groups, [pairs[k] for k in child_fields]


def load_results(parent_layer, rel_detail, rel_forest, selected_only=False):
    """林地台帳の地物と、リレーション先レコードを読み込む (各レイヤ 1 回の走査)。"""
    pnames = field_names(parent_layer)
    rels = []
    for rel in (rel_detail, rel_forest):
        rels.append(_children_by_key(rel) if rel else ({}, []))
    if selected_only:
        feats = parent_layer.getSelectedFeatures(QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry))
    else:
        feats = parent_layer.getFeatures(QgsFeatureRequest().setFlags(QgsFeatureRequest.NoGeometry))
    results = []
    for f in feats:
        lists = []
        for groups, parent_fields in rels:
            lists.append(groups.get(_key(f, parent_fields), []) if parent_fields else [])
        results.append(Result(feature_record(f, pnames), lists[0], lists[1]))
    return results
