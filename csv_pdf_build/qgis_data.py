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


def load_results(parent_layer, rel_detail, rel_forest, selected_only=False):
    """林地台帳の全地物と、リレーション先レコードを読み込む。"""
    pnames = field_names(parent_layer)
    dnames = field_names(rel_detail.referencingLayer()) if rel_detail else []
    fnames = field_names(rel_forest.referencingLayer()) if rel_forest else []
    if selected_only:
        feats = parent_layer.getSelectedFeatures()
    else:
        feats = parent_layer.getFeatures(QgsFeatureRequest())
    results = []
    for f in feats:
        detail = [feature_record(c, dnames) for c in rel_detail.getRelatedFeatures(f)] if rel_detail else []
        forest = [feature_record(c, fnames) for c in rel_forest.getRelatedFeatures(f)] if rel_forest else []
        results.append(Result(feature_record(f, pnames), detail, forest))
    return results
