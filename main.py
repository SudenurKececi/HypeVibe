import sys
import os
import requests
import time

# --- YAMALAR ---
import PyQt5
qt5_dirname = os.path.dirname(PyQt5.__file__)
plugin_path = os.path.join(qt5_dirname, 'Qt5', 'plugins')
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path

vlc_path = r"C:\Program Files\VideoLAN\VLC"
if os.path.exists(vlc_path):
    os.add_dll_directory(vlc_path)

import vlc
import yt_dlp
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QLineEdit, QListWidget, 
                             QListWidgetItem, QSlider, QMessageBox)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QPixmap, QIcon

class HypeVibePlayer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HypeVibe Player - Pro")
        self.setGeometry(100, 100, 950, 650)
        
        # VLC ve Zamanlayıcı
        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()
        self.timer = QTimer(self)
        self.timer.setInterval(1000) # Her 1 saniyede bir güncelle
        self.timer.timeout.connect(self.update_ui)
        
        self.init_ui()
        self.set_style()
        self.search_results = []
        self.is_dragging = False # Kullanıcı çubuğu tutuyor mu?

    def set_style(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121212; }
            QLabel { color: white; font-family: Arial; }
            QPushButton { 
                background-color: #1DB954; color: white; border-radius: 15px; 
                padding: 8px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background-color: #1ed760; }
            QLineEdit { 
                padding: 10px; border-radius: 20px; border: 1px solid #333;
                background-color: #282828; color: white; font-size: 14px;
            }
            QListWidget { 
                background-color: #121212; color: white; border: none; font-size: 14px;
            }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #282828; }
            QListWidget::item:selected { background-color: #282828; color: #1DB954; }
            QSlider::groove:horizontal {
                height: 8px; background: #535353; border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: white; width: 14px; margin: -3px 0; border-radius: 7px;
            }
            QSlider::sub-page:horizontal { background: #1DB954; border-radius: 4px; }
        """)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # --- 1. ARAMA ---
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Şarkı ara...")
        self.search_input.returnPressed.connect(self.search_music)
        
        search_btn = QPushButton("Ara")
        search_btn.setFixedWidth(80)
        search_btn.clicked.connect(self.search_music)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_btn)
        
        # --- 2. LİSTE ---
        self.result_list = QListWidget()
        self.result_list.setIconSize(QSize(100, 75))
        self.result_list.itemDoubleClicked.connect(self.play_selected)
        
        # --- 3. PLAYER KONTROL PANELI ---
        player_layout = QVBoxLayout()
        
        # Bilgi ve Kapak
        info_row = QHBoxLayout()
        self.current_image = QLabel()
        self.current_image.setFixedSize(60, 60)
        self.current_image.setStyleSheet("background-color: #333;")
        
        self.song_title = QLabel("Müzik seçilmedi")
        self.song_title.setStyleSheet("font-size: 14px; font-weight: bold; margin-left: 10px;")
        
        info_row.addWidget(self.current_image)
        info_row.addWidget(self.song_title)
        info_row.addStretch()
        
        # İlerleme Çubuğu (Seek Bar)
        seek_layout = QHBoxLayout()
        self.lbl_current_time = QLabel("00:00")
        self.lbl_total_time = QLabel("00:00")
        
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 1000)
        self.seek_slider.sliderPressed.connect(self.slider_pressed)
        self.seek_slider.sliderReleased.connect(self.slider_released)
        self.seek_slider.valueChanged.connect(self.set_position)
        
        seek_layout.addWidget(self.lbl_current_time)
        seek_layout.addWidget(self.seek_slider)
        seek_layout.addWidget(self.lbl_total_time)
        
        # Butonlar ve Ses
        controls_row = QHBoxLayout()
        
        self.btn_play = QPushButton("Oynat")
        self.btn_play.setFixedSize(80, 40)
        self.btn_play.clicked.connect(self.toggle_play)
        
        # Ses Ayarı
        vol_label = QLabel("Ses:")
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(70) # Varsayılan ses
        self.vol_slider.setFixedWidth(100)
        self.vol_slider.valueChanged.connect(self.set_volume)
        
        controls_row.addStretch()
        controls_row.addWidget(self.btn_play)
        controls_row.addStretch()
        controls_row.addWidget(vol_label)
        controls_row.addWidget(self.vol_slider)
        
        # Paneli Birleştir
        player_layout.addLayout(info_row)
        player_layout.addLayout(seek_layout)
        player_layout.addLayout(controls_row)
        
        # Ana Düzen
        main_layout.addLayout(search_layout)
        main_layout.addWidget(self.result_list)
        main_layout.addSpacing(10)
        main_layout.addLayout(player_layout)

    # --- FONKSİYONLAR ---
    def search_music(self):
        query = self.search_input.text()
        if not query: return
        self.song_title.setText("Aranıyor...")
        self.result_list.clear()
        QApplication.processEvents()
        
        try:
            ydl_opts = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True, 'default_search': 'ytsearch5'}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                results = info.get('entries', [info])
                for vid in results: self.add_item(vid)
            self.song_title.setText("Bir şarkı seçin")
        except: self.song_title.setText("Hata oluştu")

    def add_item(self, data):
        item = QListWidgetItem(f"{data['title']}\n{data.get('duration_string', '')}")
        if data.get('thumbnail'):
            try:
                pix = QPixmap()
                pix.loadFromData(requests.get(data['thumbnail']).content)
                item.setIcon(QIcon(pix))
            except: pass
        item.setData(Qt.UserRole, data)
        self.result_list.addItem(item)

    def play_selected(self, item):
        data = item.data(Qt.UserRole)
        self.player.set_media(self.instance.media_new(data['url']))
        self.player.play()
        
        self.song_title.setText(data['title'][:40] + "..." if len(data['title'])>40 else data['title'])
        self.btn_play.setText("Durdur")
        self.timer.start()
        
        if data.get('thumbnail'):
             pix = QPixmap()
             pix.loadFromData(requests.get(data['thumbnail']).content)
             self.current_image.setPixmap(pix.scaled(60, 60, Qt.KeepAspectRatio))

    def toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.btn_play.setText("Devam")
        else:
            self.player.play()
            self.btn_play.setText("Durdur")

    def update_ui(self):
        # Eğer kullanıcı slider ile oynuyorsa güncelleme yapma (çatışma olmasın)
        if self.player.is_playing() and not self.is_dragging:
            # Toplam süre ve şu anki zaman
            length = self.player.get_length()
            current = self.player.get_time()
            
            if length > 0:
                # Yüzdelik hesapla ve slider'ı güncelle (Binde bir hassasiyet)
                perc = (current / length) * 1000
                self.seek_slider.blockSignals(True) # Döngüye girmesin diye sinyali kes
                self.seek_slider.setValue(int(perc))
                self.seek_slider.blockSignals(False)
                
                # Süreleri yaz (Milisaniyeyi dakikaya çevir)
                self.lbl_current_time.setText(self.format_time(current))
                self.lbl_total_time.setText(self.format_time(length))

    def set_position(self, value):
        # Kullanıcı slider'ı kaydırınca şarkıyı o konuma at
        # Sadece sürükleme bittiğinde veya tıklandığında çalışmalı
        if self.is_dragging: return # Sürüklerken atlama yapma, bırakınca yap
        
        length = self.player.get_length()
        if length > 0:
            target_time = length * (value / 1000)
            self.player.set_time(int(target_time))

    def slider_pressed(self):
        self.is_dragging = True # Kullanıcı tutuyor

    def slider_released(self):
        # Kullanıcı bıraktığında konumu ayarla
        self.is_dragging = False
        self.set_position(self.seek_slider.value())

    def set_volume(self, value):
        self.player.audio_set_volume(value)

    def format_time(self, ms):
        seconds = int(ms / 1000)
        minutes, seconds = divmod(seconds, 60)
        return f"{minutes:02}:{seconds:02}"

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = HypeVibePlayer()
    window.show()
    sys.exit(app.exec_())