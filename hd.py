# WhatsApp Video Converter GUI (PyQt6 - Final Version)
#
# This script creates a modern, scalable GUI using the PyQt6 framework
# to intelligently convert videos for WhatsApp. This version uses a robust
# two-column view to correctly manage file paths and statuses, and all
# command-line arguments have been meticulously verified.
#
# Prerequisites:
# 1. Python 3
# 2. FFmpeg & FFprobe: Must be installed and in the system's PATH.
# 3. Required Python libraries:
#    pip install PyQt6

import os
import sys
import json
import subprocess
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLabel, QComboBox, QFrame, QMessageBox,
    QProgressBar
)
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal

class ConversionWorker(QThread):
    """
    Worker thread for running the ffmpeg conversion process.
    Communicates with the main GUI thread via signals.
    """
    update_status = pyqtSignal(str, str)
    update_progress = pyqtSignal(int)
    show_error = pyqtSignal(str, str)
    finished = pyqtSignal()

    def __init__(self, files_to_process, selected_ratio_str):
        super().__init__()
        self.files_to_process = files_to_process
        self.selected_ratio_str = selected_ratio_str
        self.is_running = True

    def get_video_metadata(self, file_path):
        """Analyzes the video file with ffprobe to get its dimensions and rotation."""
        command = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", file_path
        ]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True, encoding='utf-8')
            metadata = json.loads(result.stdout)
            video_stream = next((s for s in metadata['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream: return None, "No video stream found."
            width, height = int(video_stream['width']), int(video_stream['height'])
            rotation = int(video_stream.get('tags', {}).get('rotate', 0))
            return {"width": width, "height": height, "rotation": rotation}, None
        except Exception as e:
            error_detail = f"Failed to get video metadata for {os.path.basename(file_path)}.\n\nError: {e}"
            if isinstance(e, subprocess.CalledProcessError):
                error_detail += f"\n\nFFprobe Output:\n{e.stderr}"
            return None, error_detail

    def run(self):
        """The main conversion logic that runs in the worker thread."""
        ratio_map = {
            "Crop to 16:9 (Widescreen)": 16/9, "Crop to 9:16 (Tall/Story)": 9/16,
            "Crop to 1:1 (Square)": 1.0, "Crop to 19.5:9 (Modern Phone)": 19.5/9
        }
        total_files = len(self.files_to_process)
        for i, file_path in enumerate(self.files_to_process):
            if not self.is_running: break
            base, _ = os.path.splitext(file_path)
            output_file = base + "_whatsapp.mp4"
            self.update_status.emit(file_path, "⚙️ Analyzing...")
            metadata, error = self.get_video_metadata(file_path)
            if error:
                self.update_status.emit(file_path, "❌ Analyze Failed")
                self.show_error.emit("Analysis Error", error)
                continue

            width, height = (metadata['height'], metadata['width']) if metadata['rotation'] in [90, 270] else (metadata['width'], metadata['height'])
            if self.selected_ratio_str == "Keep Original (Pad)":
                target_w, target_h = (1280, 720) if width >= height else (720, 1280)
                scale_filter = f"scale=w={target_w}:h={target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
            else:
                target_ar = ratio_map[self.selected_ratio_str]
                input_ar = width / height
                target_w, target_h = (1280, int(1280 / target_ar)) if target_ar >= 1 else (int(1280 * target_ar), 1280)
                crop_filter = f"crop=ih*{target_ar}:ih" if input_ar > target_ar else f"crop=iw:iw/{target_ar}"
                scale_filter = f"{crop_filter},scale={target_w}:{target_h}"

            # --- QUALITY IMPROVEMENT ---
            # Changed preset from 'slower' to 'veryslow' for better compression
            # efficiency and quality at the cost of longer encoding time.
            common_args = [
                "-vf", scale_filter, "-c:v", "libx264", "-preset", "veryslow", "-tune", "film",
                "-profile:v", "high", "-level", "3.0", "-b:v", "3100k", "-maxrate", "3200k",
                "-bufsize", "6400k", "-pix_fmt", "yuv420p", "-r", "60"
            ]
            first_pass_cmd = ["ffmpeg", "-y", "-i", file_path] + common_args + ["-an", "-pass", "1", "-f", "mp4", os.devnull]
            second_pass_cmd = ["ffmpeg", "-y", "-i", file_path] + common_args + ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-pass", "2", output_file]

            try:
                self.update_status.emit(file_path, "🏃 Pass 1...")
                subprocess.run(first_pass_cmd, check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.update_status.emit(file_path, "🏁 Pass 2...")
                subprocess.run(second_pass_cmd, check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                self.update_status.emit(file_path, "✅ Done")
            except subprocess.CalledProcessError as e:
                self.update_status.emit(file_path, "❌ Error")
                self.show_error.emit("Conversion Error", f"FFmpeg Error for {os.path.basename(file_path)}:\n{e.stderr}")
            finally:
                progress = int(((i + 1) / total_files) * 100)
                self.update_progress.emit(progress)
                for f in ["ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"]:
                    if os.path.exists(f):
                        try: os.remove(f)
                        except OSError: pass
        self.finished.emit()

class DropZoneTree(QTreeWidget):
    """ Custom QTreeWidget that handles drag and drop and displays columns. """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setHeaderLabels(["File Name", "Status"])
        self.setAlternatingRowColors(True)
        self.header().setStretchLastSection(False)
        self.header().resizeSection(0, 350)
        self.header().resizeSection(1, 150)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: event.ignore()

    # --- FIX ---
    # Re-added the dragMoveEvent handler. This event is necessary to continuously
    # accept the drag operation as the cursor moves over the widget, which
    # ensures the drop event will be triggered correctly.
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if os.path.isfile(file_path):
                exists = any(self.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == file_path for i in range(self.topLevelItemCount()))
                if not exists:
                    item = QTreeWidgetItem([os.path.basename(file_path), "Queued"])
                    item.setData(0, Qt.ItemDataRole.UserRole, file_path)
                    self.addTopLevelItem(item)

class VideoConverterApp(QMainWindow):
    """ Main application window. """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WhatsApp Video Converter")
        self.setGeometry(100, 100, 700, 650)
        self.setMinimumSize(600, 500)
        self.setup_ui()
        self.apply_stylesheet()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        drop_label = QLabel("Drag and Drop Video Files Below")
        drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(drop_label)

        self.file_list_tree = DropZoneTree()
        layout.addWidget(self.file_list_tree, 1)

        options_frame = QFrame()
        options_layout = QHBoxLayout(options_frame)
        options_layout.setContentsMargins(0, 0, 0, 0)
        ratio_label = QLabel("Aspect Ratio:")
        self.aspect_ratio_choice = QComboBox()
        self.aspect_ratio_choice.addItems([
            "Keep Original (Pad)", "Crop to 16:9 (Widescreen)",
            "Crop to 9:16 (Tall/Story)", "Crop to 1:1 (Square)",
            "Crop to 19.5:9 (Modern Phone)"
        ])
        options_layout.addWidget(ratio_label)
        options_layout.addWidget(self.aspect_ratio_choice, 1)
        layout.addWidget(options_frame)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.progress_bar)

        self.convert_button = QPushButton("▶️ Start Conversion")
        self.clear_button = QPushButton("🗑️ Clear List")
        button_layout = QHBoxLayout()
        button_layout.addWidget(self.convert_button)
        button_layout.addWidget(self.clear_button)
        layout.addLayout(button_layout)

        self.convert_button.clicked.connect(self.start_conversion)
        self.clear_button.clicked.connect(self.clear_list)
        self.statusBar().showMessage("Ready. Drag files to begin.")

    def apply_stylesheet(self):
        self.setStyleSheet("""
            QWidget {
                background-color: #2B2B2B;
                color: #BBBBBB;
                font-size: 14px;
            }
            QMainWindow { background-color: #3C3F41; }
            QLabel { font-weight: bold; }
            QPushButton {
                font-weight: bold; background-color: #0078D7; color: white;
                border: none; padding: 10px; border-radius: 5px;
            }
            QPushButton:hover { background-color: #005A9E; }
            QPushButton:disabled { background-color: #555555; color: #999999; }
            QComboBox {
                padding: 5px; border: 1px solid #555; border-radius: 5px;
                background-color: #3C3F41;
            }
            QComboBox::drop-down { border: none; }
            QTreeWidget {
                border: 1px solid #555; border-radius: 5px;
                background-color: #3C3F41;
                alternate-background-color: #45494A;
            }
            QHeaderView::section {
                background-color: #555555; padding: 5px;
                border: 1px solid #666666; font-weight: bold;
            }
            QStatusBar { font-size: 12px; }
            QProgressBar {
                border: 1px solid #555; border-radius: 5px;
                text-align: center; font-weight: bold; color: white;
            }
            QProgressBar::chunk {
                background-color: #0078D7; border-radius: 4px;
            }
        """)

    def start_conversion(self):
        files_to_process = [self.file_list_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                            for i in range(self.file_list_tree.topLevelItemCount())
                            if self.file_list_tree.topLevelItem(i).text(1) == "Queued"]
        if not files_to_process:
            QMessageBox.information(self, "No Files", "Please add new video files to the list first.")
            return

        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.convert_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        selected_ratio = self.aspect_ratio_choice.currentText()
        self.worker = ConversionWorker(files_to_process, selected_ratio)
        self.worker.update_status.connect(self.on_update_status)
        self.worker.update_progress.connect(self.progress_bar.setValue)
        self.worker.show_error.connect(self.on_show_error)
        self.worker.finished.connect(self.on_conversion_finished)
        self.worker.start()

    def on_update_status(self, file_path, status):
        for i in range(self.file_list_tree.topLevelItemCount()):
            item = self.file_list_tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == file_path:
                item.setText(1, status)
                if "✅" in status: item.setForeground(1, QColor("#4CAF50")) # Green
                elif "❌" in status: item.setForeground(1, QColor("#F44336")) # Red
                else: item.setForeground(1, QColor("#FFFFFF")) # White
                break

    def on_show_error(self, title, message):
        QMessageBox.critical(self, title, message)

    def on_conversion_finished(self):
        self.convert_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        self.statusBar().showMessage("🎉 All conversions complete!")

    def clear_list(self):
        self.file_list_tree.clear()
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage("List cleared. Ready for new files.")

def check_ffmpeg_ffprobe():
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, creationflags=creation_flags)
        subprocess.run(["ffprobe", "-version"], check=True, capture_output=True, creationflags=creation_flags)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

if __name__ == "__main__":
    def main():
        app = QApplication(sys.argv)
        if not check_ffmpeg_ffprobe():
            QMessageBox.critical(None, "Dependency Not Found", "FFmpeg and FFprobe are not in your system's PATH. Please install FFmpeg.")
        else:
            window = VideoConverterApp()
            window.show()
            sys.exit(app.exec())
    main()
