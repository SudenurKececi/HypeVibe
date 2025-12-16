import sys
import os
import json
import random
import requests
import qtawesome as qta

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QSlider, QFrame,
    QStackedWidget, QGraphicsDropShadowEffect, QMessageBox, QMenu,
    QAbstractItemView
)
from PyQt5.QtCore import Qt, QSize, QTimer, QThread, pyqtSignal, QPoint
from PyQt5.QtGui import QColor, QPixmap, QIcon

import vlc
import yt_dlp
import PyQt5


# --- 1. AYARLAR ---
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = os.path.join(
    os.path.dirname(PyQt5.__file__), 'Qt5', 'plugins'
)

vlc_path = r"C:\Program Files\VideoLAN\VLC"
if os.path.exists(vlc_path):
    try:
        os.add_dll_directory(vlc_path)
    except Exception:
        pass


# --- 2. ARKA PLAN İŞÇİLERİ ---
class ImageLoader(QThread):
    image_loaded = pyqtSignal(object, object)

    def __init__(self, url, list_item):
        super().__init__()
        self.url = url
        self.item = list_item

    def run(self):
        try:
            data = requests.get(self.url, timeout=7).content
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.image_loaded.emit(self.item, pixmap)
        except Exception:
            pass


class SearchThread(QThread):
    results_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, query):
        super().__init__()
        self.query = query

    def run(self):
        try:
            ydl_opts = {
                'quiet': True,
                'noplaylist': True,
                'default_search': 'ytsearch5',
                'extract_flat': False
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.query, download=False)

                if not info:
                    self.error_occurred.emit("Sonuç yok.")
                    return

                if 'entries' in info and info['entries']:
                    results = [e for e in info['entries'] if e]
                else:
                    results = [info]

                if not results:
                    self.error_occurred.emit("Sonuç yok.")
                else:
                    self.results_ready.emit(results)

        except Exception as e:
            self.error_occurred.emit(str(e))


class AudioThread(QThread):
    url_ready = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, video_url, title):
        super().__init__()
        self.url = video_url
        self.title = title

    def run(self):
        try:
            ydl_opts = {'format': 'best[height<=360]/best', 'quiet': True, 'noplaylist': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if not info or 'url' not in info:
                    self.error_occurred.emit("Stream URL alınamadı.")
                    return
                self.url_ready.emit(info['url'], self.title)
        except Exception as e:
            self.error_occurred.emit(str(e))


# --- 3. TASARIM ---
class NeonButton(QPushButton):
    def __init__(self, icon_name, size=24, color="#bd93f9", parent=None):
        super().__init__(parent)
        self.setIcon(qta.icon(icon_name, color=color))
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            "QPushButton:hover { background-color: rgba(189, 147, 249, 0.1); border-radius: 15px; }"
        )


class SidebarButton(QPushButton):
    def __init__(self, text, icon_name, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setIcon(qta.icon(icon_name, color="#e0e0e0"))
        self.setIconSize(QSize(20, 20))
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "QPushButton { background-color: transparent; color: #e0e0e0; text-align: left; "
            "padding: 15px 20px; font-size: 14px; font-family: 'Segoe UI', Arial; border: none; }"
            "QPushButton:hover { background-color: #2a2a3e; color: #bd93f9; border-left: 4px solid #bd93f9; }"
        )


# ✅ Queue drag-drop sonrası sıra değişince sinyal verir
class ReorderableListWidget(QListWidget):
    order_changed = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


# --- 4. ANA UYGULAMA ---
class HypeVibeNeon(QMainWindow):
    media_finished = pyqtSignal()  # VLC event -> Qt thread

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1150, 780)

        self.default_volume = 80

        # Queue
        self.queue = []

        # VLC
        self.instance = None
        self.player = None
        try:
            self.instance = vlc.Instance("--no-video --network-caching=5000 --quiet")
            self.player = self.instance.media_player_new()

            em = self.player.event_manager()
            em.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_vlc_end)

            self.media_finished.connect(self.on_media_finished)
        except Exception:
            pass

        # State
        self.favorites = self.load_favs()
        self.current_playlist = []
        self.current_index = -1
        self.image_threads = []
        self.old_pos = None
        self.current_data = None

        self.is_shuffle = False
        self.is_repeat = False

        self.init_ui()
        self.init_style()

        if self.player:
            try:
                self.player.audio_set_volume(self.default_volume)
            except Exception:
                pass

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_slider)
        self.timer.start(1000)

        self.refresh_queue_ui()

    # --- Güvenli ikon/pixmap setter'lar (CRASH FIX) ---
    def safe_set_item_icon(self, item, pixmap):
        try:
            if item is None:
                return
            item.setIcon(QIcon(pixmap))
        except RuntimeError:
            # "QListWidgetItem has been deleted" -> görmezden gel
            pass

    def safe_set_cover_pixmap(self, _item, pixmap):
        try:
            if not hasattr(self, "lbl_cover") or self.lbl_cover is None:
                return
            self.lbl_cover.setPixmap(
                pixmap.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        except RuntimeError:
            pass

    # --- VLC end event ---
    def _on_vlc_end(self, _event):
        try:
            self.media_finished.emit()
        except Exception:
            pass

    def on_media_finished(self):
        self.play_next(auto=True)

    # --- kapanış ---
    def closeEvent(self, event):
        self.save_favs_from_list()
        event.accept()

    # --- fav io ---
    def load_favs(self):
        if os.path.exists("favs.json"):
            try:
                with open("favs.json", "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def save_favs_from_list(self):
        new_favs = []
        for i in range(self.list_favs.count()):
            item = self.list_favs.item(i)
            new_favs.append(item.data(Qt.UserRole))

        self.favorites = new_favs
        with open("favs.json", "w", encoding="utf-8") as f:
            json.dump(self.favorites, f, ensure_ascii=False)

    # --- style ---
    def init_style(self):
        self.setStyleSheet("""
            QMainWindow { background-color: transparent; }
            QFrame#MainFrame { background-color: #1e1e2e; border-radius: 15px; border: 1px solid #44475a; }
            QLineEdit { background-color: #282a36; color: #f8f8f2; border-radius: 20px; padding: 10px 15px; border: 1px solid #44475a; }
            QListWidget { background-color: transparent; border: none; }
            QListWidget::item { color: #f8f8f2; padding: 6px; margin: 2px; border-radius: 6px; }
            QListWidget::item:hover { background-color: #44475a; }
            QListWidget::item:selected { background-color: rgba(189, 147, 249, 0.2); color: #bd93f9; }
            QLabel { color: #f8f8f2; font-family: 'Segoe UI', Arial; }
            QSlider::groove:horizontal { height: 6px; background: #44475a; border-radius: 3px; }
            QSlider::handle:horizontal { background: #bd93f9; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::sub-page:horizontal { background: #bd93f9; border-radius: 3px; }
        """)

    # --- UI ---
    def init_ui(self):
        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("MainFrame")
        self.main_frame.setGeometry(0, 0, 1150, 780)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.main_frame.setGraphicsEffect(shadow)

        root = QVBoxLayout(self.main_frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        title_bar = QFrame()
        title_bar.setFixedHeight(50)
        title_bar.setStyleSheet("background-color: #191a24; border-top-left-radius: 15px; border-top-right-radius: 15px;")
        tb = QHBoxLayout(title_bar)
        tb.addWidget(QLabel("  ⚡ HypeVibe"))
        tb.addStretch()

        btn_min = NeonButton('fa5s.minus', 16, "#f1fa8c")
        btn_min.clicked.connect(self.showMinimized)
        btn_close = NeonButton('fa5s.times', 16, "#ff5555")
        btn_close.clicked.connect(self.close)
        tb.addWidget(btn_min)
        tb.addWidget(btn_close)

        # Middle
        content = QHBoxLayout()

        sidebar = QFrame()
        sidebar.setFixedWidth(240)
        sidebar.setStyleSheet("background-color: #191a24; border-right: 1px solid #44475a;")
        sb = QVBoxLayout(sidebar)
        sb.addSpacing(20)

        self.pages = QStackedWidget()

        self.btn_home = SidebarButton("  Keşfet", 'fa5s.search')
        self.btn_home.clicked.connect(lambda: self.pages.setCurrentIndex(0))

        self.btn_lib = SidebarButton("  Kütüphanem", 'fa5s.heart')
        self.btn_lib.clicked.connect(lambda: self.pages.setCurrentIndex(1))

        self.btn_queue = SidebarButton("  Sıradakiler (0)", 'fa5s.list')
        self.btn_queue.clicked.connect(lambda: self.pages.setCurrentIndex(2))

        sb.addWidget(self.btn_home)
        sb.addWidget(self.btn_lib)
        sb.addWidget(self.btn_queue)
        sb.addStretch()

        # --- Page 0: Search ---
        p_search = QWidget()
        ls = QVBoxLayout(p_search)
        ls.setContentsMargins(30, 30, 30, 30)

        search_box = QHBoxLayout()
        self.inp_search = QLineEdit()
        self.inp_search.setPlaceholderText("Şarkı ara... (Enter)")
        self.inp_search.returnPressed.connect(self.do_search)

        btn_go = QPushButton()
        btn_go.setIcon(qta.icon('fa5s.search', color='#1e1e2e'))
        btn_go.setFixedSize(40, 40)
        btn_go.setStyleSheet("background-color: #bd93f9; border-radius: 20px;")
        btn_go.clicked.connect(self.do_search)

        search_box.addWidget(self.inp_search)
        search_box.addWidget(btn_go)

        self.list_results = QListWidget()
        self.list_results.setIconSize(QSize(120, 90))
        self.list_results.itemDoubleClicked.connect(lambda item: self.play_item(item, 'search'))

        # Arama sonuçları sağ tık menüsü
        self.list_results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_results.customContextMenuRequested.connect(self.show_search_context_menu)

        ls.addLayout(search_box)
        ls.addWidget(QLabel("Sonuçlar (5 Adet):"))
        ls.addWidget(self.list_results)

        # --- Page 1: Library (Favs) ---
        p_lib = QWidget()
        ll = QVBoxLayout(p_lib)
        ll.setContentsMargins(30, 30, 30, 30)

        self.list_favs = QListWidget()
        self.list_favs.setIconSize(QSize(80, 60))
        self.list_favs.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_favs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_favs.customContextMenuRequested.connect(self.show_favs_context_menu)
        self.list_favs.itemDoubleClicked.connect(lambda item: self.play_item(item, 'fav'))

        ll.addWidget(QLabel("💜 Favorilerim (Sürükle & Sırala)"))
        ll.addWidget(self.list_favs)
        self.load_favs_ui()

        # --- Page 2: Queue ---
        p_queue = QWidget()
        lq = QVBoxLayout(p_queue)
        lq.setContentsMargins(30, 30, 30, 30)

        top_row = QHBoxLayout()
        self.lbl_nowplaying = QLabel("🎧 Şimdi Çalıyor: -")
        self.lbl_nowplaying.setStyleSheet("font-weight: bold;")
        btn_clear_queue = QPushButton("Queue Temizle")
        btn_clear_queue.setCursor(Qt.PointingHandCursor)
        btn_clear_queue.setStyleSheet(
            "background-color:#282a36; color:#f8f8f2; padding:8px 12px; "
            "border-radius:10px; border:1px solid #44475a;"
        )
        btn_clear_queue.clicked.connect(self.clear_queue)

        top_row.addWidget(self.lbl_nowplaying)
        top_row.addStretch()
        top_row.addWidget(btn_clear_queue)

        # ✅ ReorderableListWidget + sıra değişince queue sync
        self.list_queue = ReorderableListWidget()
        self.list_queue.setIconSize(QSize(80, 60))
        self.list_queue.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_queue.customContextMenuRequested.connect(self.show_queue_context_menu)
        self.list_queue.itemDoubleClicked.connect(self.play_queue_item)
        self.list_queue.order_changed.connect(self.sync_queue_from_widget)

        lq.addLayout(top_row)
        lq.addWidget(QLabel("📌 Sıradakiler (Sürükle & Sırala):"))
        lq.addWidget(self.list_queue)

        # add pages
        self.pages.addWidget(p_search)
        self.pages.addWidget(p_lib)
        self.pages.addWidget(p_queue)

        content.addWidget(sidebar)
        content.addWidget(self.pages)

        # Bottom player bar
        player_bar = QFrame()
        player_bar.setFixedHeight(100)
        player_bar.setStyleSheet(
            "background-color: #15161e; border-top: 1px solid #bd93f9; "
            "border-bottom-left-radius: 15px; border-bottom-right-radius: 15px;"
        )
        pb = QHBoxLayout(player_bar)

        # Left info
        info_layout = QHBoxLayout()
        self.lbl_cover = QLabel()
        self.lbl_cover.setFixedSize(60, 60)
        self.lbl_cover.setStyleSheet("background-color: #333; border-radius: 5px;")

        text_layout = QVBoxLayout()
        self.lbl_title = QLabel("Müzik Seçilmedi")
        self.lbl_title.setStyleSheet("font-weight: bold;")
        self.lbl_artist = QLabel("HypeVibe")
        self.lbl_artist.setStyleSheet("color: #6272a4; font-size: 12px;")
        text_layout.addWidget(self.lbl_title)
        text_layout.addWidget(self.lbl_artist)

        info_layout.addWidget(self.lbl_cover)
        info_layout.addLayout(text_layout)

        # Center controls
        ctrl = QVBoxLayout()
        btns = QHBoxLayout()

        self.btn_shuffle = NeonButton('fa5s.random', 18, "#6272a4")
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)

        self.btn_prev = NeonButton('fa5s.step-backward', 20, "#f8f8f2")
        self.btn_prev.clicked.connect(self.play_prev)

        self.btn_play = NeonButton('fa5s.play-circle', 45, "#bd93f9")
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_next = NeonButton('fa5s.step-forward', 20, "#f8f8f2")
        self.btn_next.clicked.connect(self.play_next)

        self.btn_repeat = NeonButton('fa5s.redo', 18, "#6272a4")
        self.btn_repeat.clicked.connect(self.toggle_repeat)

        btns.addStretch()
        btns.addWidget(self.btn_shuffle)
        btns.addWidget(self.btn_prev)
        btns.addWidget(self.btn_play)
        btns.addWidget(self.btn_next)
        btns.addWidget(self.btn_repeat)
        btns.addStretch()

        seek = QHBoxLayout()
        self.lbl_curr = QLabel("00:00")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.sliderReleased.connect(self.seek_audio)
        self.lbl_total = QLabel("00:00")

        seek.addWidget(self.lbl_curr)
        seek.addWidget(self.slider)
        seek.addWidget(self.lbl_total)

        ctrl.addLayout(btns)
        ctrl.addLayout(seek)

        # Right: like + volume
        right = QVBoxLayout()
        right.setSpacing(6)

        self.btn_like = NeonButton('fa5s.heart', 20, "#6272a4")
        self.btn_like.clicked.connect(self.add_fav)

        vol_row = QHBoxLayout()
        self.lbl_vol_icon = QLabel()
        self._set_volume_icon(self.default_volume)

        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(self.default_volume)
        self.slider_vol.valueChanged.connect(self.set_volume)
        self.slider_vol.setFixedWidth(140)

        vol_row.addWidget(self.lbl_vol_icon)
        vol_row.addWidget(self.slider_vol)

        right.addWidget(self.btn_like, alignment=Qt.AlignRight)
        right.addLayout(vol_row)

        pb.addLayout(info_layout, 1)
        pb.addStretch()
        pb.addLayout(ctrl, 3)
        pb.addStretch()
        pb.addLayout(right, 1)

        root.addWidget(title_bar)
        root.addLayout(content)
        root.addWidget(player_bar)

        title_bar.mousePressEvent = self.mousePressEvent
        title_bar.mouseMoveEvent = self.mouseMoveEvent

    # --- window drag ---
    def mousePressEvent(self, e):
        self.old_pos = e.globalPos()

    def mouseMoveEvent(self, e):
        if self.old_pos:
            delta = QPoint(e.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = e.globalPos()

    # --- helpers ---
    def is_in_favs(self, url: str) -> bool:
        return any((f.get('url') == url) for f in self.favorites)

    # ✅ Search item'a fav marker (💜 + pembe)
    def apply_fav_marker_to_search_item(self, item: QListWidgetItem, is_fav: bool, base_title: str):
        if is_fav:
            if not item.text().startswith("💜 "):
                item.setText("💜 " + base_title)
            item.setForeground(QColor("#ff79c6"))
        else:
            item.setText(base_title)
            item.setForeground(QColor("#f8f8f2"))

    def update_search_marker_for_url(self, url: str):
        if not hasattr(self, "list_results"):
            return
        is_fav = self.is_in_favs(url)
        for i in range(self.list_results.count()):
            it = self.list_results.item(i)
            d = it.data(Qt.UserRole) or {}
            if d.get("url") == url:
                base = d.get("title", it.text().replace("💜 ", ""))
                self.apply_fav_marker_to_search_item(it, is_fav, base)

    def add_to_queue(self, data: dict):
        if not data or not data.get('url'):
            return
        self.queue.append(data)
        self.refresh_queue_ui()

    def sync_queue_from_widget(self):
        # Drag-drop sonrası widget'taki sırayı self.queue'ya yaz
        new_queue = []
        for i in range(self.list_queue.count()):
            item = self.list_queue.item(i)
            d = item.data(Qt.UserRole)
            if d:
                new_queue.append(d)
        self.queue = new_queue
        self.btn_queue.setText(f"  Sıradakiler ({len(self.queue)})")

    def toggle_favorite_data(self, data: dict):
        if not data or not data.get('url'):
            return

        self.save_favs_from_list()
        url = data['url']

        if self.is_in_favs(url):
            for i in range(self.list_favs.count()):
                if self.list_favs.item(i).data(Qt.UserRole).get('url') == url:
                    self.list_favs.takeItem(i)
                    break
        else:
            it = QListWidgetItem(data.get('title', ''))
            it.setIcon(qta.icon('fa5s.heart', color='#bd93f9'))
            it.setData(Qt.UserRole, data)
            self.list_favs.addItem(it)
            if data.get('thumbnail'):
                d = ImageLoader(data['thumbnail'], it)
                d.image_loaded.connect(self.safe_set_item_icon)  # ✅ crash fix
                self.image_threads.append(d)
                d.start()

        self.save_favs_from_list()

        if self.current_data and self.current_data.get('url') == url:
            is_fav = self.is_in_favs(url)
            self.btn_like.setIcon(qta.icon('fa5s.heart', color='#ff5555' if is_fav else '#6272a4'))

        # ✅ search listesi de güncellensin
        self.update_search_marker_for_url(url)

    def refresh_queue_ui(self):
        if hasattr(self, 'btn_queue'):
            self.btn_queue.setText(f"  Sıradakiler ({len(self.queue)})")

        if hasattr(self, 'lbl_nowplaying'):
            if self.current_data and self.current_data.get('title'):
                t = self.current_data['title']
                self.lbl_nowplaying.setText(f"🎧 Şimdi Çalıyor: {t[:60]}")
            else:
                self.lbl_nowplaying.setText("🎧 Şimdi Çalıyor: -")

        if not hasattr(self, 'list_queue'):
            return

        self.list_queue.clear()
        for s in self.queue:
            it = QListWidgetItem(s.get('title', ''))
            it.setIcon(qta.icon('fa5s.list', color='#bd93f9'))
            it.setData(Qt.UserRole, s)
            self.list_queue.addItem(it)

            if s.get('thumbnail'):
                d = ImageLoader(s['thumbnail'], it)
                d.image_loaded.connect(self.safe_set_item_icon)  # ✅ crash fix
                self.image_threads.append(d)
                d.start()

    # --- Search right-click menu ---
    def show_search_context_menu(self, pos):
        item = self.list_results.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.UserRole) or {}
        url = data.get('url', '')

        menu = QMenu()
        act_queue = menu.addAction("➕ Queue’ya Ekle")
        act_fav = menu.addAction("💜 Favoriye Ekle / Çıkar")
        act_copy = menu.addAction("🔗 Link Kopyala")

        action = menu.exec_(self.list_results.mapToGlobal(pos))
        if not action:
            return

        if action == act_queue:
            self.add_to_queue(data)
        elif action == act_fav:
            self.toggle_favorite_data(data)
        elif action == act_copy:
            QApplication.clipboard().setText(url or "")

    # --- Fav right-click menu ---
    def show_favs_context_menu(self, pos):
        item = self.list_favs.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.UserRole)

        menu = QMenu()
        act_queue = menu.addAction("➕ Queue’ya Ekle")
        menu.addSeparator()
        act_top = menu.addAction("⬆️ En Üste Taşı")
        act_bottom = menu.addAction("⬇️ En Alta Taşı")
        menu.addSeparator()
        act_del = menu.addAction("🗑️ Listeden Kaldır")

        action = menu.exec_(self.list_favs.mapToGlobal(pos))
        if not action:
            return

        if action == act_queue:
            self.add_to_queue(data)
        elif action == act_top:
            row = self.list_favs.row(item)
            if row > 0:
                it = self.list_favs.takeItem(row)
                self.list_favs.insertItem(0, it)
                self.list_favs.setCurrentRow(0)
                self.save_favs_from_list()
        elif action == act_bottom:
            row = self.list_favs.row(item)
            if row >= 0 and row < self.list_favs.count() - 1:
                it = self.list_favs.takeItem(row)
                self.list_favs.addItem(it)
                self.list_favs.setCurrentRow(self.list_favs.count() - 1)
                self.save_favs_from_list()
        elif action == act_del:
            row = self.list_favs.row(item)
            self.list_favs.takeItem(row)
            self.save_favs_from_list()
            self.btn_like.setIcon(qta.icon('fa5s.heart', color='#6272a4'))

    # --- Queue right-click menu ---
    def show_queue_context_menu(self, pos):
        item = self.list_queue.itemAt(pos)
        menu = QMenu()

        act_play = menu.addAction("▶️ Şimdi Çal")
        act_remove = menu.addAction("🗑️ Queue’dan Kaldır")
        menu.addSeparator()
        act_up = menu.addAction("⬆️ Yukarı Taşı")
        act_down = menu.addAction("⬇️ Aşağı Taşı")
        menu.addSeparator()
        act_clear = menu.addAction("🧹 Queue Temizle")

        action = menu.exec_(self.list_queue.mapToGlobal(pos))
        if not action:
            return

        if action == act_clear:
            self.clear_queue()
            return

        if not item:
            return

        row = self.list_queue.row(item)

        if action == act_play:
            self.play_queue_item(item)
        elif action == act_remove:
            if 0 <= row < len(self.queue):
                self.queue.pop(row)
                self.refresh_queue_ui()
        elif action == act_up:
            if row > 0 and row < len(self.queue):
                self.queue[row - 1], self.queue[row] = self.queue[row], self.queue[row - 1]
                self.refresh_queue_ui()
                self.list_queue.setCurrentRow(row - 1)
        elif action == act_down:
            if 0 <= row < len(self.queue) - 1:
                self.queue[row + 1], self.queue[row] = self.queue[row], self.queue[row + 1]
                self.refresh_queue_ui()
                self.list_queue.setCurrentRow(row + 1)

    def clear_queue(self):
        self.queue = []
        self.refresh_queue_ui()

    def play_queue_item(self, item):
        row = self.list_queue.row(item)
        if 0 <= row < len(self.queue):
            data = self.queue.pop(row)
            self.refresh_queue_ui()
            self.load_music(data)

    # --- Modlar ---
    def toggle_shuffle(self, _checked=False):
        self.is_shuffle = not self.is_shuffle
        color = "#bd93f9" if self.is_shuffle else "#6272a4"
        self.btn_shuffle.setIcon(qta.icon('fa5s.random', color=color))

    def toggle_repeat(self, _checked=False):
        self.is_repeat = not self.is_repeat
        color = "#bd93f9" if self.is_repeat else "#6272a4"
        self.btn_repeat.setIcon(qta.icon('fa5s.redo', color=color))

    # --- Volume ---
    def _set_volume_icon(self, v):
        if v <= 0:
            icon = qta.icon('fa5s.volume-mute', color='#f8f8f2')
        elif v <= 35:
            icon = qta.icon('fa5s.volume-down', color='#f8f8f2')
        else:
            icon = qta.icon('fa5s.volume-up', color='#f8f8f2')
        self.lbl_vol_icon.setPixmap(icon.pixmap(16, 16))

    def set_volume(self, v):
        self._set_volume_icon(v)
        if self.player:
            try:
                self.player.audio_set_volume(int(v))
            except Exception:
                pass

    # --- Search ---
    def do_search(self):
        q = self.inp_search.text().strip()
        if not q:
            return

        self.list_results.clear()
        self.lbl_title.setText("Aranıyor...")

        self.search_thread = SearchThread(q)
        self.search_thread.results_ready.connect(self.on_results)
        self.search_thread.error_occurred.connect(lambda e: self.lbl_title.setText(f"Hata: {e}"))
        self.search_thread.start()

    def on_results(self, res):
        self.lbl_title.setText(f"{len(res)} Sonuç")
        self.image_threads.clear()

        for r in res:
            title = r.get('title') or r.get('id') or 'Bilinmiyor'
            url = r.get('url') or r.get('webpage_url') or r.get('id')
            thumbnail = r.get('thumbnail', '')

            if not url:
                continue
            if len(url) == 11 and '.' not in url:
                url = f"https://www.youtube.com/watch?v={url}"

            data = {'title': title, 'url': url, 'thumbnail': thumbnail}

            it = QListWidgetItem(title)
            it.setIcon(qta.icon('fa5s.music', color='#bd93f9'))
            it.setData(Qt.UserRole, data)
            self.list_results.addItem(it)

            # ✅ favori işareti
            if self.is_in_favs(url):
                self.apply_fav_marker_to_search_item(it, True, title)

            if thumbnail:
                d = ImageLoader(thumbnail, it)
                d.image_loaded.connect(self.safe_set_item_icon)  # ✅ crash fix
                self.image_threads.append(d)
                d.start()

    # --- Play item ---
    def play_item(self, item, src):
        if src == 'search':
            self.current_playlist = [self.list_results.item(i).data(Qt.UserRole) for i in range(self.list_results.count())]
            self.current_index = self.list_results.row(item)
        else:
            self.current_playlist = [self.list_favs.item(i).data(Qt.UserRole) for i in range(self.list_favs.count())]
            self.current_index = self.list_favs.row(item)

        self.load_music(item.data(Qt.UserRole))

    def load_music(self, data):
        self.current_data = data
        self.lbl_title.setText("Yükleniyor...")
        self.lbl_artist.setText(data.get('title', ''))

        is_fav = self.is_in_favs(data.get('url', ''))
        self.btn_like.setIcon(qta.icon('fa5s.heart', color='#ff5555' if is_fav else '#6272a4'))

        self.refresh_queue_ui()

        if not self.instance or not self.player:
            QMessageBox.warning(self, "Hata", "VLC başlatılamadı. VLC kurulu mu?")
            return

        self.audio_thread = AudioThread(data['url'], data.get('title', ''))
        self.audio_thread.url_ready.connect(self.start_vlc)
        self.audio_thread.error_occurred.connect(lambda e: QMessageBox.warning(self, "Hata", f"Hata: {e}"))
        self.audio_thread.start()

    def start_vlc(self, url, title):
        if not self.instance or not self.player:
            return

        m = self.instance.media_new(url)
        self.player.set_media(m)
        self.player.play()

        try:
            self.player.audio_set_volume(int(self.slider_vol.value()))
        except Exception:
            pass

        self.lbl_title.setText(title[:25] + "..." if len(title) > 25 else title)
        self.btn_play.setIcon(qta.icon('fa5s.pause-circle', color='#bd93f9'))

        if self.current_data and self.current_data.get('thumbnail'):
            d = ImageLoader(self.current_data['thumbnail'], None)
            d.image_loaded.connect(self.safe_set_cover_pixmap)  # ✅ crash fix
            self.image_threads.append(d)
            d.start()

        self.refresh_queue_ui()

    def toggle_play(self, _checked=False):
        if not self.player:
            return
        try:
            if self.player.is_playing():
                self.player.pause()
                self.btn_play.setIcon(qta.icon('fa5s.play-circle', color='#bd93f9'))
            else:
                self.player.play()
                self.btn_play.setIcon(qta.icon('fa5s.pause-circle', color='#bd93f9'))
        except Exception:
            pass

    def stop_ui(self):
        self.btn_play.setIcon(qta.icon('fa5s.play-circle', color='#bd93f9'))

    def play_next(self, _checked=False, auto=False):
        if self.queue:
            nxt = self.queue.pop(0)
            self.refresh_queue_ui()
            self.load_music(nxt)
            return

        if not self.current_playlist:
            if auto:
                self.stop_ui()
            return

        if self.is_shuffle:
            self.current_index = random.randint(0, len(self.current_playlist) - 1)
            self.load_music(self.current_playlist[self.current_index])
            return

        if self.current_index < len(self.current_playlist) - 1:
            self.current_index += 1
            self.load_music(self.current_playlist[self.current_index])
        else:
            if self.is_repeat:
                self.current_index = 0
                self.load_music(self.current_playlist[self.current_index])
            else:
                if auto:
                    self.stop_ui()

    def play_prev(self, _checked=False):
        if not self.current_playlist:
            return

        if self.is_shuffle:
            self.current_index = random.randint(0, len(self.current_playlist) - 1)
            self.load_music(self.current_playlist[self.current_index])
            return

        if self.current_index > 0:
            self.current_index -= 1
            self.load_music(self.current_playlist[self.current_index])

    def seek_audio(self):
        if not self.player:
            return
        try:
            length = self.player.get_length()
            if length and length > 0:
                self.player.set_time(int(length * (self.slider.value() / 100)))
        except Exception:
            pass

    def update_slider(self):
        if not self.player:
            return
        try:
            l = self.player.get_length()
            c = self.player.get_time()
            if l and l > 0 and c >= 0:
                self.slider.setValue(int((c / l) * 100))
                self.lbl_curr.setText(f"{c // 60000:02}:{(c // 1000) % 60:02}")
                self.lbl_total.setText(f"{l // 60000:02}:{(l // 1000) % 60:02}")
        except Exception:
            pass

    # --- Like button (current) ---
    def add_fav(self, _checked=False):
        if not self.current_data:
            return
        self.toggle_favorite_data(self.current_data)

    # --- load fav ui ---
    def load_favs_ui(self):
        self.list_favs.clear()
        for s in self.favorites:
            it = QListWidgetItem(s.get('title', ''))
            it.setIcon(qta.icon('fa5s.heart', color='#bd93f9'))
            it.setData(Qt.UserRole, s)
            self.list_favs.addItem(it)

            if s.get('thumbnail'):
                d = ImageLoader(s['thumbnail'], it)
                d.image_loaded.connect(self.safe_set_item_icon)  # ✅ crash fix
                self.image_threads.append(d)
                d.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = HypeVibeNeon()
    window.show()
    sys.exit(app.exec_())
