def classFactory(iface):
    from .plugin import CsvPdfBuildPlugin
    return CsvPdfBuildPlugin(iface)
