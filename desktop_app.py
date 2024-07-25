from PyQt5.QtCore import *
from PyQt5.QtWidgets import *
from PyQt5.QtWebEngineWidgets import *
import sys


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("http://127.0.0.1:5000"))

        self.setCentralWidget(self.browser)
        self.showMaximized()

    def navigate_home(self):
        self.browser.setUrl(QUrl("http://127.0.0.1:5000/products.html"))

    def navigate_logs(self):
        self.browser.setUrl(QUrl("http://127.0.0.1:5000/logs.html"))

    def navigate_current_track(self):
        self.browser.setUrl(QUrl("http://127.0.0.1:5000/product_details.html"))

    def navigate_logout(self):
        self.browser.setUrl(QUrl("http://127.0.0.1:5000/logout"))


app = QApplication(sys.argv)
QApplication.setApplicationName("Music Player Admin")
window = MainWindow()
app.exec_()
