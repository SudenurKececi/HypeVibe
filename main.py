import sys
import os
import json
import random  # Karışık çalmak için
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
            # Not: video stream seçimi pratikte daha stabil olabiliyor.
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


# --- 4. ANA UYGULAMA ---

class HypeVibeNeon(QMainWindow):
    media_finished = pyqtSignal()  # VLC event -> Qt thread'e güvenli aktarım

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(1150, 780)

        # ✅ ÖNCE default_volume tanımla (init_ui içinde kullanılıyor)
        self.default_volume = 80

        # Queue (Sıraya eklenenler)
        self.queue = []

        self.instance = None
        self.player = None
        try:
            self.instance = vlc.Instance("--no-video --network-caching=5000 --quiet")
            self.player = self.instance.media_player_new()

            # VLC "bitti" event'i
            em = self.player.event_manager()
            em.event_attach(vlc.EventType.MediaPlayerEndReached, self._on_vlc_end)

            # VLC threadinden gelen event -> Qt slot
            self.media_finished.connect(self.on_media_finished)

        except Exception:
            pass

        self.favorites = self.load_favs()
        self.current_playlist = []
        self.current_index = -1
        self.image_threads = []
        self.old_pos = None

        # Oynatma Modları
        self.is_shuffle = False
        self.is_repeat = False

        self.init_ui()
        self.init_style()

        # VLC ses ayarı
        if self.player:
            try:
                self.player.audio_set_volume(self.default_volume)
            except Exception:
                pass

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_slider)
        self.timer.start(1000)

    # Program Kapanırken Kaydet
    def closeEvent(self, event):
        self.save_favs_from_list()
        event.accept()

    def _on_vlc_end(self, _event):
        # VLC callback başka thread'de çalışabilir -> signal ile GUI thread'e geç
        try:
            self.media_finished.emit()
        except Exception:
            pass

    def on_media_finished(self):
        # Şarkı bitti -> otomatik next
        self.play_next(auto=True)

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

    def init_style(self):
        self.setStyleSheet("""
            QMainWindow { background-color: transparent; }
            QFrame#MainFrame { background-color: #1e1e2e; border-radius: 15px; border: 1px solid #44475a; }
            QLineEdit { background-color: #282a36; color: #f8f8f2; border-radius: 20px; padding: 10px 15px; border: 1px solid #44475a; }
            QListWidget { background-color: transparent; border: none; }
            QListWidget::item { color: #f8f8f2; padding: 5px; margin: 2px; border-radius: 5px; }
            QListWidget::item:hover { background-color: #44475a; }
            QListWidget::item:selected { background-color: rgba(189, 147, 249, 0.2); color: #bd93f9; }
            QLabel { color: #f8f8f2; font-family: 'Segoe UI', Arial; }
            QSlider::groove:horizontal { height: 6px; background: #44475a; border-radius: 3px; }
            QSlider::handle:horizontal { background: #bd93f9; width: 14px; margin: -4px 0; border-radius: 7px; }
            QSlider::sub-page:horizontal { background: #bd93f9; border-radius: 3px; }
        """)

    def init_ui(self):
        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("MainFrame")
        self.main_frame.setGeometry(0, 0, 1150, 780)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 150))
        self.main_frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.main_frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ÜST BAR
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

        # ORTA
        content = QHBoxLayout()

        sidebar = QFrame()
        sidebar.setFixedWidth(240)
        sidebar.setStyleSheet("background-color: #191a24; border-right: 1px solid #44475a;")
        sb = QVBoxLayout(sidebar)
        sb.addSpacing(20)

        btn_home = SidebarButton("  Keşfet", 'fa5s.search')
        btn_home.clicked.connect(lambda: self.pages.setCurrentIndex(0))
        btn_lib = SidebarButton("  Kütüphanem", 'fa5s.heart')
        btn_lib.clicked.connect(lambda: self.pages.setCurrentIndex(1))

        sb.addWidget(btn_home)
        sb.addWidget(btn_lib)
        sb.addStretch()

        self.pages = QStackedWidget()

        # Arama
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

        ls.addLayout(search_box)
        ls.addWidget(QLabel("Sonuçlar (5 Adet):"))
        ls.addWidget(self.list_results)

        # Kütüphane (Favoriler)
        p_lib = QWidget()
        ll = QVBoxLayout(p_lib)
        ll.setContentsMargins(30, 30, 30, 30)

        self.list_favs = QListWidget()
        self.list_favs.setIconSize(QSize(80, 60))

        # Sürükle bırak
        self.list_favs.setDragDropMode(QAbstractItemView.InternalMove)

        # Sağ tık menü
        self.list_favs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_favs.customContextMenuRequested.connect(self.show_context_menu)

        self.list_favs.itemDoubleClicked.connect(lambda item: self.play_item(item, 'fav'))

        ll.addWidget(QLabel("💜 Favorilerim (Sürükle & Sırala)"))
        ll.addWidget(self.list_favs)

        self.load_favs_ui()

        self.pages.addWidget(p_search)
        self.pages.addWidget(p_lib)

        content.addWidget(sidebar)
        content.addWidget(self.pages)

        # ALT BAR
        player_bar = QFrame()
        player_bar.setFixedHeight(100)
        player_bar.setStyleSheet(
            "background-color: #15161e; border-top: 1px solid #bd93f9; "
            "border-bottom-left-radius: 15px; border-bottom-right-radius: 15px;"
        )
        pb = QHBoxLayout(player_bar)

        # Sol bilgi
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

        # Orta kontrol
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

        # Sağ taraf: like + volume
        right = QVBoxLayout()
        right.setSpacing(6)

        self.btn_like = NeonButton('fa5s.heart', 20, "#6272a4")
        self.btn_like.clicked.connect(self.add_fav)

        vol_row = QHBoxLayout()
        self.lbl_vol_icon = QLabel()

        # ✅ default_volume ile başlat
        self._set_volume_icon(self.default_volume)

        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(self.default_volume)  # ✅
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

        layout.addWidget(title_bar)
        layout.addLayout(content)
        layout.addWidget(player_bar)

        title_bar.mousePressEvent = self.mousePressEvent
        title_bar.mouseMoveEvent = self.mouseMoveEvent

    # --- Pencere taşıma ---
    def mousePressEvent(self, e):
        self.old_pos = e.globalPos()

    def mouseMoveEvent(self, e):
        if self.old_pos:
            delta = QPoint(e.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = e.globalPos()

    # --- Sağ tık menüsü (Favoriler) ---
    def show_context_menu(self, pos):
        item = self.list_favs.itemAt(pos)
        if not item:
            return

        menu = QMenu()

        act_queue = menu.addAction("➕ Sıraya Ekle (Queue)")
        menu.addSeparator()
        act_top = menu.addAction("⬆️ En Üste Taşı")
        act_bottom = menu.addAction("⬇️ En Alta Taşı")
        menu.addSeparator()
        act_del = menu.addAction("🗑️ Listeden Kaldır")

        action = menu.exec_(self.list_favs.mapToGlobal(pos))
        if not action:
            return

        if action == act_queue:
            self.queue.append(item.data(Qt.UserRole))

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

    # --- Modlar ---
    def toggle_shuffle(self, _checked=False):
        self.is_shuffle = not self.is_shuffle
        color = "#bd93f9" if self.is_shuffle else "#6272a4"
        self.btn_shuffle.setIcon(qta.icon('fa5s.random', color=color))

    def toggle_repeat(self, _checked=False):
        self.is_repeat = not self.is_repeat
        color = "#bd93f9" if self.is_repeat else "#6272a4"
        self.btn_repeat.setIcon(qta.icon('fa5s.redo', color=color))

    # --- Ses ---
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

    # --- Arama ---
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

            it = QListWidgetItem(title)
            it.setIcon(qta.icon('fa5s.music', color='#bd93f9'))
            it.setData(Qt.UserRole, {'title': title, 'url': url, 'thumbnail': thumbnail})
            self.list_results.addItem(it)

            if thumbnail:
                d = ImageLoader(thumbnail, it)
                d.image_loaded.connect(lambda i, p: i.setIcon(QIcon(p)))
                self.image_threads.append(d)
                d.start()

    # --- Çalma ---
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

        is_fav = any(f.get('url') == data.get('url') for f in self.favorites)
        self.btn_like.setIcon(qta.icon('fa5s.heart', color='#ff5555' if is_fav else '#6272a4'))

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

        # ses slider'ı her play'de tekrar uygula
        try:
            self.player.audio_set_volume(int(self.slider_vol.value()))
        except Exception:
            pass

        self.lbl_title.setText(title[:25] + "..." if len(title) > 25 else title)
        self.btn_play.setIcon(qta.icon('fa5s.pause-circle', color='#bd93f9'))

        if self.current_data and self.current_data.get('thumbnail'):
            d = ImageLoader(self.current_data['thumbnail'], None)
            d.image_loaded.connect(
                lambda _, p: self.lbl_cover.setPixmap(
                    p.scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            )
            self.image_threads.append(d)
            d.start()

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

    def play_next(self, auto=False):
        # 1) Queue varsa önce onu çal
        if self.queue:
            nxt = self.queue.pop(0)
            self.load_music(nxt)
            return

        if not self.current_playlist:
            if auto:
                self.stop_ui()
            return

        # Karışık
        if self.is_shuffle:
            self.current_index = random.randint(0, len(self.current_playlist) - 1)
            self.load_music(self.current_playlist[self.current_index])
            return

        # Normal
        if self.current_index < len(self.current_playlist) - 1:
            self.current_index += 1
            self.load_music(self.current_playlist[self.current_index])
        else:
            # Liste bitti
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

    # --- Favori toggle ---
    def add_fav(self, _checked=False):
        if not hasattr(self, 'current_data'):
            return

        self.save_favs_from_list()

        already_in = False
        for i in range(self.list_favs.count()):
            if self.list_favs.item(i).data(Qt.UserRole).get('url') == self.current_data.get('url'):
                already_in = True
                break

        if already_in:
            for i in range(self.list_favs.count()):
                if self.list_favs.item(i).data(Qt.UserRole).get('url') == self.current_data.get('url'):
                    self.list_favs.takeItem(i)
                    break
            self.btn_like.setIcon(qta.icon('fa5s.heart', color='#6272a4'))
        else:
            it = QListWidgetItem(self.current_data.get('title', ''))
            it.setIcon(qta.icon('fa5s.heart', color='#bd93f9'))
            it.setData(Qt.UserRole, self.current_data)
            self.list_favs.addItem(it)
            self.btn_like.setIcon(qta.icon('fa5s.heart', color='#ff5555'))

            if self.current_data.get('thumbnail'):
                d = ImageLoader(self.current_data['thumbnail'], it)
                d.image_loaded.connect(lambda i, p: i.setIcon(QIcon(p)))
                self.image_threads.append(d)
                d.start()

        self.save_favs_from_list()

    def load_favs_ui(self):
        self.list_favs.clear()
        for s in self.favorites:
            it = QListWidgetItem(s.get('title', ''))
            it.setIcon(qta.icon('fa5s.heart', color='#bd93f9'))
            it.setData(Qt.UserRole, s)
            self.list_favs.addItem(it)

            if s.get('thumbnail'):
                d = ImageLoader(s['thumbnail'], it)
                d.image_loaded.connect(lambda i, p: i.setIcon(QIcon(p)))
                self.image_threads.append(d)
                d.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = HypeVibeNeon()
    window.show()
    sys.exit(app.exec_())
