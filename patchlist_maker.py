from PyQt5.QtCore import (
    QThread,
    pyqtSignal,
    QMutex,
    QSemaphore,
    Qt,
    QSize,
    QTimer,
    QEvent
)
from PyQt5.QtGui import QFont, QPixmap, QIcon
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QPushButton,
    QLabel,
    QFileDialog,
    QVBoxLayout,
    QWidget,
    QMessageBox,
    QHBoxLayout,
    QFrame,
    QCheckBox
)

import sys
import os
import hashlib
import json
import ftplib
import config

def get_file_hash(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(4096):
            sha256.update(chunk)
    return sha256.hexdigest()

def get_file_size(file_path):
    return os.path.getsize(file_path)

def load_version(version_file="version.json"):
    if not os.path.exists(version_file):
        return "1.0"
    with open(version_file, "r") as f:
        data = json.load(f)
    return data.get("version", "1.0")

def save_version(new_version, version_file="version.json"):
    with open(version_file, "w") as f:
        json.dump({"version": new_version}, f, indent=4)
    print(f"New version saved: {new_version}")

def increment_version(version):
    major, minor = map(int, version.split("."))
    minor += 1
    return f"{major}.{minor}"

def find_patcher_in_directory(directory):
    patcher_directory = os.path.join(directory, "patcher")
    if os.path.exists(patcher_directory) and os.path.isdir(patcher_directory):
        for file in os.listdir(patcher_directory):
            if file.endswith(".exe"):
                patcher_path = os.path.join(patcher_directory, file)
                file_hash = get_file_hash(patcher_path)
                file_size = get_file_size(patcher_path)
                return {
                    "name": file,
                    "hash": file_hash,
                    "size": file_size,
                    "path": patcher_path,
                }
    return None

def upload_to_ftp(ftp_server, ftp_user, ftp_password, local_path, remote_path):
    try:
        with ftplib.FTP(ftp_server) as ftp:
            ftp.login(user=ftp_user, passwd=ftp_password)
            ftp.set_pasv(False)

            remote_dir = os.path.dirname(remote_path)
            if remote_dir:
                parts = remote_dir.split("/")
                for part in parts:
                    if part not in ftp.nlst():
                        ftp.mkd(part)
                    ftp.cwd(part)

            with open(local_path, "rb") as file:
                ftp.storbinary(f"STOR {os.path.basename(remote_path)}", file)
            ftp.cwd("/")
            return True
    except ftplib.all_errors as e:
        print(f"FTP Error: {e}")
        return False

def download_from_ftp(ftp_server, ftp_user, ftp_password, remote_path, local_path):
    try:
        with ftplib.FTP(ftp_server) as ftp:
            ftp.login(user=ftp_user, passwd=ftp_password)
            ftp.set_pasv(False)
            with open(local_path, 'wb') as file:
                ftp.retrbinary(f'RETR {remote_path}', file.write)
            return True
    except ftplib.all_errors as e:
        print(f"FTP Download Error: {e}")
        return False

def create_patchlist(directory, version, status_label=None):
    if status_label:
        status_label.setText("Generating Patchlist...")
    
    patchlist_file = "patchlist.json"
    if os.path.exists(patchlist_file):
        os.remove(patchlist_file)
    
    patchlist = {}
    patchlist[f"patch_{version}"] = []
    patchlist["exe"] = {}
    patchlist["patcher"] = {}

    patcher_file_data = find_patcher_in_directory(directory)
    if patcher_file_data:
        patchlist["patcher"] = patcher_file_data

    files = {}
    exe_files = {}
    total_files = 0

    for root, dirs, files_in_dir in os.walk(directory):
        for file in files_in_dir:
            file_path = os.path.join(root, file)
            if file.endswith(".exe"):
                if patcher_file_data and file == patcher_file_data["name"]:
                    continue
                exe_files[file] = {
                    "path": os.path.relpath(file_path, directory),
                    "hash": get_file_hash(file_path),
                    "size": get_file_size(file_path),
                }
            else:
                files[file] = {
                    "path": os.path.relpath(file_path, directory),
                    "hash": get_file_hash(file_path),
                    "size": get_file_size(file_path),
                }
            total_files += 1

    patchlist[f"patch_{version}"].append(files)
    patchlist["exe"] = exe_files

    with open(patchlist_file, "w") as json_file:
        json.dump(patchlist, json_file, indent=4)
    
    return patchlist, total_files

class UploadFileThread(QThread):
    file_status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int, str)

    def __init__(self, file_path, remote_path, ftp_server, ftp_user, ftp_password, semaphore):
        super().__init__()
        self.file_path = file_path
        self.remote_path = remote_path
        self.ftp_server = ftp_server
        self.ftp_user = ftp_user
        self.ftp_password = ftp_password
        self.semaphore = semaphore

    def run(self):
        self.semaphore.acquire()
        file_name = os.path.basename(self.file_path)
        
        success = upload_to_ftp(
            self.ftp_server,
            self.ftp_user,
            self.ftp_password,
            self.file_path,
            self.remote_path,
        )

        self.semaphore.release()

        if success:
            self.file_status_update.emit(f"Uploaded: {file_name}")
        else:
            self.file_status_update.emit(f"Error uploading: {file_name}")

        self.progress_update.emit(1, file_name)

class UploadThread(QThread):
    status_update = pyqtSignal(str)
    progress_update = pyqtSignal(int)
    upload_complete = pyqtSignal(int, str)

    def __init__(self, directory, version, ftp_server, ftp_user, ftp_password, status_label, upload_ftp):
        super().__init__()
        self.directory = directory
        self.version = version
        self.ftp_server = ftp_server
        self.ftp_user = ftp_user
        self.ftp_password = ftp_password
        self.total_files = 0
        self.uploaded_files = 0
        self.uploaded_patches = 0
        self.mutex = QMutex()
        self.semaphore = QSemaphore(7)
        self.status_label = status_label
        self.upload_ftp = upload_ftp

    def run(self):
        temp_patchlist = "temp_patchlist.json"
        remote_patchlist_path = "./patcher/patchlist.json"
        old_patchlist = {}
        
        if download_from_ftp(
            self.ftp_server, self.ftp_user, self.ftp_password,
            remote_patchlist_path, temp_patchlist
        ):
            try:
                with open(temp_patchlist, "r") as f:
                    old_patchlist = json.load(f)
            except Exception as e:
                print(f"Error loading existing patchlist: {e}")

        new_patchlist, self.total_files = create_patchlist(
            self.directory, self.version, self.status_label
        )

        changed_files = self.find_changed_files(old_patchlist, new_patchlist)
        changed_files.append(os.path.abspath("patchlist.json"))
        
        self.upload_files(changed_files)
        self.upload_complete.emit(len(changed_files), f"patch_{self.version}")

    def find_changed_files(self, old_patchlist, new_patchlist):
        changed_files = []
        
        def get_latest_version():
            versions = []
            for key in old_patchlist:
                if key.startswith("patch_"):
                    try:
                        ver = tuple(map(int, key.split("_")[1].split(".")))
                        versions.append(ver)
                    except:
                        continue
            return max(versions) if versions else None

        latest_ver = get_latest_version()
        old_files = {}
        if latest_ver:
            old_key = f"patch_{latest_ver[0]}.{latest_ver[1]}"
            old_files = old_patchlist.get(old_key, [{}])[0]

        new_files = new_patchlist.get(f"patch_{self.version}", [{}])[0]
        for file, info in new_files.items():
            old_info = old_files.get(file)
            if not old_info or info["hash"] != old_info.get("hash"):
                changed_files.append(os.path.join(self.directory, info["path"]))

        old_exe = old_patchlist.get("exe", {})
        new_exe = new_patchlist.get("exe", {})
        for file, info in new_exe.items():
            old_info = old_exe.get(file)
            if not old_info or info["hash"] != old_info.get("hash"):
                changed_files.append(os.path.join(self.directory, info["path"]))

        old_patcher = old_patchlist.get("patcher", {})
        new_patcher = new_patchlist.get("patcher", {})
        if new_patcher and (not old_patcher or new_patcher["hash"] != old_patcher.get("hash")):
            changed_files.append(new_patcher["path"])

        return changed_files

    def upload_files(self, file_list):
        threads = []
        for file_path in file_list:
            file_name = os.path.basename(file_path)
            if file_name == "patchlist.json" or file_name == config.patcher_name:
                remote_path = f"./patcher/{file_name}"
            else:
                rel_path = os.path.relpath(file_path, self.directory)
                remote_path = f"{config.server_pack_folder}/{rel_path}"

            thread = UploadFileThread(
                file_path,
                remote_path.replace("\\", "/"),
                self.ftp_server,
                self.ftp_user,
                self.ftp_password,
                self.semaphore,
            )
            thread.file_status_update.connect(self.update_file_status)
            thread.progress_update.connect(self.update_progress)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.wait()

    def update_file_status(self, status):
        self.status_update.emit(status)

    def update_progress(self, increment, file_name):
        self.mutex.lock()
        self.uploaded_files += increment
        progress = (self.uploaded_files / self.total_files) * 100
        self.status_label.setText(f"{file_name} - {int(progress)}% Complete")
        self.mutex.unlock()
        self.uploaded_patches += 1

class PatchCreatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()

    def init_ui(self):
        self.setWindowTitle(config.app_title)
        self.setGeometry(300, 300, 600, 500)
        self.setStyleSheet("background-color: #2b2b2b; color: white;")
        self.setWindowIcon(QIcon(self.resource_path(config.icon_path)))

        layout = QVBoxLayout()
        layout.setSpacing(15)

        logo = QLabel()
        pixmap = QPixmap(self.resource_path(config.logo_path))
        logo.setPixmap(pixmap)
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)

        self.label = QLabel("Select Path")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(QFont("Arial", 12))
        layout.addWidget(self.label)

        button_layout = QHBoxLayout()

        self.select_btn = QPushButton()
        select_icon = QIcon(self.resource_path(config.select_btn))
        self.select_btn.setIcon(select_icon)
        self.select_btn.setIconSize(QSize(169, 127))
        self.select_btn.setStyleSheet("background: none; border: none;")
        self.select_btn.clicked.connect(self.select_directory)
        self.select_btn.installEventFilter(self)
        button_layout.addWidget(self.select_btn)

        self.create_btn = QPushButton()
        upload_icon = QIcon(self.resource_path(config.upload_btn))
        self.create_btn.setIcon(upload_icon)
        self.create_btn.setIconSize(QSize(169, 127))
        self.create_btn.setStyleSheet("background: none; border: none;")
        self.create_btn.clicked.connect(self.start_upload)
        self.create_btn.installEventFilter(self)
        button_layout.addWidget(self.create_btn)

        layout.addLayout(button_layout)

        self.upload_checkbox = QCheckBox("Upload via FTP")
        self.upload_checkbox.setChecked(True)

        checkbox_layout = QHBoxLayout()
        checkbox_layout.addStretch()
        checkbox_layout.addWidget(self.upload_checkbox)
        checkbox_layout.addStretch()
        checkbox_layout.setAlignment(Qt.AlignCenter)

        layout.addLayout(checkbox_layout)

        self.status_label = QLabel("Status: Waiting...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFont(QFont("Arial", 10))
        layout.addWidget(self.status_label)

        self.dots_count = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_dots)
        self.timer.start(500)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.selected_directory = None
        self.upload_thread = None

    def update_progress(self, progress):
        self.status_label.setText(f"Uploading... {progress}% Complete")

    def update_dots(self):
        self.dots_count += 1
        if self.dots_count > 3:
            self.dots_count = 0
        if self.status_label.text().startswith("Status: Waiting"):
            self.status_label.setText(f"Status: Waiting{'.' * self.dots_count}")

    def set_image(self, widget, image_path, size):
        full_path = self.resource_path(image_path)
        pixmap = QPixmap(full_path)
        if pixmap.isNull():
            print(f"Errore: impossibile caricare l'immagine '{full_path}'")
        else:
            if isinstance(widget, QLabel):
                widget.setPixmap(pixmap.scaled(size[0], size[1], Qt.KeepAspectRatio))
            elif isinstance(widget, QPushButton):
                widget.setIcon(QIcon(pixmap.scaled(size[0], size[1], Qt.KeepAspectRatio)))

    def resource_path(self, relative_path):
        try:
            if getattr(sys, "frozen", False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.abspath(".")
            return os.path.join(base_path, relative_path)
        except Exception as e:
            print(f"Error in resource_path: {e}")
            return relative_path

    def eventFilter(self, source, event):
        self.button_images = {
            self.select_btn: config.select_btn,
            self.create_btn: config.upload_btn,
        }
        if source in self.button_images:
            image_path = self.button_images[source]

            if event.type() == QEvent.Enter:
                hover_image_path = image_path.replace(".png", "_down.png")
                hover_image_path = self.resource_path(hover_image_path)
                self.set_image(source, hover_image_path, (1300, 300))

            elif event.type() == QEvent.Leave:
                regular_image_path = self.resource_path(image_path)
                self.set_image(source, regular_image_path, (1300, 300))

        return super().eventFilter(source, event)

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Update Directory")
        if directory:
            self.selected_directory = directory
            parts = directory.split("/")
            shortened_path = "/".join(parts[-2:]) if len(parts) > 1 else parts[0]
            self.label.setText(f"Directory: {shortened_path}")

    def start_upload(self):
        if not self.selected_directory:
            QMessageBox.warning(self, "Error", "Please select a directory first!")
            return

        current_version = load_version()
        new_version = increment_version(current_version)
        save_version(new_version)
        self.status_label.setText("Status: Making Patchlist...")

        upload_ftp = self.upload_checkbox.isChecked()

        if not upload_ftp:
            self.status_label.setText("Status: Generating patchlist.json locally...")
            patchlist, total_files = create_patchlist(
                self.selected_directory,
                new_version,
                self.status_label,
            )
            QMessageBox.information(
                self, "Patchlist Generated", "patchlist.json generated locally."
            )
            return

        self.upload_thread = UploadThread(
            self.selected_directory,
            new_version,
            config.ftp_server,
            config.ftp_user,
            config.ftp_password,
            self.status_label,
            upload_ftp,
        )
        self.upload_thread.status_update.connect(self.update_status)
        self.upload_thread.progress_update.connect(self.update_progress)
        self.upload_thread.upload_complete.connect(self.upload_complete)
        self.upload_thread.start()

    def update_status(self, status):
        if status != "Uploading...":
            self.status_label.setText(f"Status: {status}")

    def upload_complete(self, num_patches, version):
        self.status_label.setText(f"Status: {num_patches} patches uploaded ({version})")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PatchCreatorApp()
    window.show()
    sys.exit(app.exec_())