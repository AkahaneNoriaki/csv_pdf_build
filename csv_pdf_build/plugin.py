from qgis.PyQt.QtWidgets import QAction


class CsvPdfBuildPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        self.action = QAction("林地台帳 CSV/PDF 出力", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("&林地台帳 CSV/PDF 出力", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        self.iface.removePluginMenu("&林地台帳 CSV/PDF 出力", self.action)
        self.iface.removeToolBarIcon(self.action)
        self.action = None

    def run(self):
        from .dialog import MainDialog
        if self.dialog is None:
            self.dialog = MainDialog(self.iface, self.iface.mainWindow())
        self.dialog.refresh_layers()
        self.dialog.show()
        self.dialog.raise_()
