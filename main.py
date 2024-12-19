import sys
import json
import socket
import logging
import sqlite3
import uuid
import threading
import time
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QPushButton, QTextEdit, QLabel, 
                            QComboBox, QMessageBox, QFileDialog,
                            QListWidget, QLineEdit)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QMetaObject, pyqtSlot, Q_ARG
from PyQt5.QtGui import QKeyEvent
import pyautogui
import keyboard
import os

# Set up logging
logging.basicConfig(
    filename=f'transfer_app_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def get_persistent_id():
    id_file = "machine_id.txt"
    if os.path.exists(id_file):
        with open(id_file, "r") as f:
            return f.read().strip()
    else:
        machine_id = str(uuid.uuid4())[:8]
        with open(id_file, "w") as f:
            f.write(machine_id)
        return machine_id

class NetworkScanner(QThread):
    devices_found = pyqtSignal(list)
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.instance_id = get_persistent_id()
        
    def run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('0.0.0.0', 12345))  # Explicitly bind to all interfaces
            
            # Start broadcast thread
            broadcast_thread = threading.Thread(target=self.broadcast_presence)
            broadcast_thread.daemon = True
            broadcast_thread.start()
            
            devices = {}
            while self.running:
                try:
                    sock.settimeout(1.0)  # Don't block forever
                    data, addr = sock.recvfrom(1024)
                    if data.startswith(b'DISCOVER:'):
                        remote_id = data.decode().split(':')[1]
                        if remote_id != self.instance_id:  # Don't add self to devices
                            devices[remote_id] = addr[0]
                            self.devices_found.emit([(id, ip) for id, ip in devices.items()])
                except socket.timeout:
                    continue
                    
        except Exception as e:
            logging.error(f"Network scanning error: {str(e)}")
            
    def broadcast_presence(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        while self.running:
            if hasattr(self, 'is_connected') and self.is_connected:
                time.sleep(30)  # Check connection status every 30 seconds
                continue
                
            try:
                # Get all network interfaces
                hostname = socket.gethostname()
                addresses = socket.getaddrinfo(hostname, None)
                broadcast_addresses = set()
                
                # Generate broadcast addresses for each network interface
                for addr in addresses:
                    ip = addr[4][0]
                    if ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.'):
                        # Convert IP to broadcast address by setting last octet to 255
                        broadcast_ip = '.'.join(ip.split('.')[:-1] + ['255'])
                        broadcast_addresses.add(broadcast_ip)
                
                # Add global broadcast as fallback
                broadcast_addresses.add('255.255.255.255')
                
                # Send to all discovered broadcast addresses
                for addr in broadcast_addresses:
                    try:
                        sock.sendto(f'DISCOVER:{self.instance_id}'.encode(), (addr, 12345))
                    except:
                        continue
                        
                time.sleep(30)  # Broadcast every 30 seconds
            except Exception as e:
                logging.error(f"Broadcast error: {str(e)}")
                time.sleep(30)
                continue
                
    def stop(self):
        self.running = False

class WindowSelector(QThread):
    window_found = pyqtSignal(dict)
    
    def run(self):
        try:
            keyboard.wait('ctrl+shift')
            
            # Get all windows
            windows = pyautogui.getAllWindows()
            
            # Get active window
            active = pyautogui.getActiveWindow()
            
            if active:
                window_info = {
                    'title': active.title,
                    'coordinates': pyautogui.position(),
                    'app_name': active.app,
                    'all_windows': [(w.title, w.app) for w in windows],
                    'controls': self.get_window_controls(active)
                }
                
                self.window_found.emit(window_info)
            
        except Exception as e:
            logging.error(f"Window selection error: {str(e)}")
            
    def get_window_controls(self, window):
        # This is a placeholder - would need OS-specific implementation
        # to get actual controls like text boxes, buttons etc
        return ["Text Input 1", "Text Input 2", "Text Area"]

class DataTransferApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Data Transfer Application")
        self.setGeometry(100, 100, 800, 600)
        
        # Get persistent instance ID
        self.instance_id = get_persistent_id()
        self.setWindowTitle(f"Data Transfer Application - {self.instance_id}")
        
        # Initialize database
        self.init_database()
        
        # Create main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        # Create menu bar
        self.create_menu_bar()
        
        # Initial mode selection
        self.mode_selection = QComboBox()
        self.mode_selection.addItems(["Select Mode", "Sender", "Receiver"])
        self.mode_selection.currentTextChanged.connect(self.mode_changed)
        layout.addWidget(self.mode_selection)
        
        # Create sender and receiver widgets
        self.sender_widget = QWidget()
        self.receiver_widget = QWidget()
        self.sender_layout = QVBoxLayout(self.sender_widget)
        self.receiver_layout = QVBoxLayout(self.receiver_widget)
        
        # Initialize sender UI
        self.init_sender_ui()
        
        # Initialize receiver UI
        self.init_receiver_ui()
        
        # Hide both initially
        self.sender_widget.hide()
        self.receiver_widget.hide()
        layout.addWidget(self.sender_widget)
        layout.addWidget(self.receiver_widget)
        
        # Status bar
        self.statusBar().showMessage(f"Instance ID: {self.instance_id}")
        
        # Network scanner
        self.scanner = NetworkScanner()
        self.scanner.devices_found.connect(self.update_device_list)
        
        # Connection variables
        self.connected_socket = None
        self.is_connected = False
        
        # Window selection variables
        self.available_windows = []
        self.available_controls = []
        
    def init_database(self):
        try:
            self.conn = sqlite3.connect('transfer_settings.db')
            self.cursor = self.conn.cursor()
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS saved_locations
                (name TEXT PRIMARY KEY, window_title TEXT, coordinates TEXT, 
                app_name TEXT, control_name TEXT)
            ''')
            self.conn.commit()
        except Exception as e:
            logging.error(f"Database initialization error: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to initialize database")
            
    def create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu('File')
        
        save_action = file_menu.addAction('Save Settings')
        save_action.triggered.connect(self.save_settings)
        
        load_action = file_menu.addAction('Load Settings')
        load_action.triggered.connect(self.load_settings)
    
    def init_sender_ui(self):
        # Device list with labels
        devices_layout = QHBoxLayout()
        self.device_list = QComboBox()
        devices_layout.addWidget(QLabel("Available Receivers:"))
        devices_layout.addWidget(self.device_list)
        
        # Refresh button
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        devices_layout.addWidget(self.refresh_btn)
        
        # Manual ID input
        id_layout = QHBoxLayout()
        self.id_input = QLineEdit()
        self.id_input.setPlaceholderText("Enter receiver ID...")
        id_layout.addWidget(QLabel("Receiver ID:"))
        id_layout.addWidget(self.id_input)
        
        self.sender_layout.addLayout(devices_layout)
        self.sender_layout.addLayout(id_layout)
        
        # Connect button
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_to_receiver)
        self.sender_layout.addWidget(self.connect_btn)
        
        # Text input area
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("Enter text to send...")
        self.text_input.setAcceptRichText(False)
        self.sender_layout.addWidget(self.text_input)
        
        # Send button
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send_data)
        self.sender_layout.addWidget(self.send_btn)
        
        # Start auto-refresh timer
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_devices)
        self.refresh_timer.start(5000)  # Refresh every 5 seconds
        
        # Initialize scanner
        self.scanner = NetworkScanner()
        self.scanner.devices_found.connect(self.update_device_list)
        
        # Initial device scan
        self.refresh_devices()
    
    def init_receiver_ui(self):
        # Window selection
        self.window_list = QListWidget()
        self.receiver_layout.addWidget(QLabel("Available Windows:"))
        self.receiver_layout.addWidget(self.window_list)
        
        # Control selection
        self.control_list = QListWidget()
        self.receiver_layout.addWidget(QLabel("Available Controls:"))
        self.receiver_layout.addWidget(self.control_list)
        
        # Refresh windows button
        refresh_btn = QPushButton("Refresh Windows")
        refresh_btn.clicked.connect(self.refresh_windows)
        self.receiver_layout.addWidget(refresh_btn)
        
        # Status label
        self.status_label = QLabel("Waiting for connection...")
        self.receiver_layout.addWidget(self.status_label)
        
        # Connect signals
        self.window_list.itemClicked.connect(self.window_selected)
    
    def refresh_windows(self):
        windows = pyautogui.getAllWindows()
        self.available_windows = [(w.title, w._hWnd, w.box) for w in windows]
        self.window_list.clear()
        for title, hwnd, _ in self.available_windows:
            self.window_list.addItem(f"{title} ({hwnd})")
    
    def window_selected(self, item):
        # When a window is selected, populate controls
        self.control_list.clear()
        # This would need OS-specific implementation
        self.available_controls = ["Text Input 1", "Text Input 2", "Text Area"]
        for control in self.available_controls:
            self.control_list.addItem(control)
    
    def mode_changed(self, mode):
        if mode == "Sender":
            self.sender_widget.show()
            self.receiver_widget.hide()
            self.scanner.start()  # Start scanning for receivers
            self.refresh_timer.start()  # Start auto-refresh timer
        elif mode == "Receiver":
            self.sender_widget.hide()
            self.receiver_widget.show()
            self.refresh_timer.stop()  # Stop auto-refresh timer
            self.start_receiver_server()
            self.refresh_windows()
    
    def refresh_devices(self):
        self.scanner.running = False
        self.scanner.wait()
        self.scanner = NetworkScanner()
        self.scanner.devices_found.connect(self.update_device_list)
        self.scanner.start()
    
    def update_device_list(self, devices):
        # Get list of current devices
        current_devices = set()
        for i in range(self.device_list.count()):
            current_devices.add(self.device_list.itemText(i))
        
        # Add only new devices
        for device_id, ip in devices:
            if device_id != self.instance_id:  # Don't show self
                device_text = f"{device_id} ({ip})"
                if device_text not in current_devices:
                    self.device_list.addItem(device_text)
    
    def connect_to_receiver(self):
        try:
            # Check if manual ID is provided
            manual_id = self.id_input.text().strip()
            if manual_id:
                # Here you would need to implement logic to find IP by ID
                # For now, we'll show an error
                raise ValueError("Manual ID connection not implemented yet")
                
            selected_device = self.device_list.currentText()
            if not selected_device:
                raise ValueError("No device selected")
            
            # Extract IP from device string
            ip = selected_device.split('(')[1].rstrip(')')
                
            self.connected_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.connected_socket.connect((ip, 12346))
            self.is_connected = True
            
            # Update UI
            self.statusBar().showMessage(f"Connected to {selected_device}")
            QMessageBox.information(self, "Connected", f"Connected to {selected_device}")
            
            logging.info(f"Connected to receiver at {selected_device}")
        except Exception as e:
            logging.error(f"Connection error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to connect: {str(e)}")
    
    def send_data(self):
        if not self.is_connected:
            QMessageBox.warning(self, "Error", "Not connected to a receiver")
            return
            
        try:
            data = self.text_input.toPlainText()
            self.connected_socket.send(data.encode())
            self.text_input.clear()
            logging.info(f"Data sent successfully: {data[:100]}...")
        except Exception as e:
            logging.error(f"Send error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to send data: {str(e)}")
    
    def start_receiver_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.bind(('', 12346))
            self.server_socket.listen(1)
            
            # Start listening thread
            self.receiver_thread = threading.Thread(target=self.receive_data)
            self.receiver_thread.daemon = True
            self.receiver_thread.start()
            
            logging.info("Receiver server started")
        except Exception as e:
            logging.error(f"Server start error: {str(e)}")
            QMessageBox.critical(self, "Error", f"Failed to start receiver: {str(e)}")
    
    def receive_data(self):
        while True:
            try:
                client_socket, address = self.server_socket.accept()
                
                # Use signals to update UI from thread
                QMetaObject.invokeMethod(self, "handle_connection_request",
                                       Qt.QueuedConnection,
                                       Q_ARG(str, address[0]),
                                       Q_ARG(object, client_socket))
                
            except Exception as e:
                logging.error(f"Receive error: {str(e)}")
                continue
                
    @pyqtSlot(str, object)
    def handle_connection_request(self, address, client_socket):
        reply = QMessageBox.question(self, 'Connection Request',
            f'Accept connection from {address}?',
            QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            self.status_label.setText(f"Connected to {address}")
            
            # Start a new thread for receiving data
            receive_thread = threading.Thread(target=self.handle_client_data,
                                           args=(client_socket,))
            receive_thread.daemon = True
            receive_thread.start()
        else:
            client_socket.close()
            
    def handle_client_data(self, client_socket):
        while True:
            try:
                data = client_socket.recv(1024).decode()
                if not data:
                    break
                    
                # Use signals to update UI from thread
                QMetaObject.invokeMethod(self, "input_data_at_location",
                                       Qt.QueuedConnection,
                                       Q_ARG(str, data))
                
                logging.info(f"Data received and input: {data[:100]}...")
            except Exception as e:
                logging.error(f"Client data error: {str(e)}")
                break
        client_socket.close()
    
    def save_location(self, name, window_info):
        try:
            control = self.control_list.currentItem().text() if self.control_list.currentItem() else ""
            self.cursor.execute('''
                INSERT OR REPLACE INTO saved_locations 
                (name, window_title, coordinates, app_name, control_name)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, window_info['title'], str(window_info['coordinates']), 
                 window_info['app_name'], control))
            self.conn.commit()
            logging.info(f"Saved location: {name} at {window_info['coordinates']}")
        except Exception as e:
            logging.error(f"Save location error: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to save location")
    
    @pyqtSlot(str)
    def input_data_at_location(self, data):
        try:
            selected_window = self.window_list.currentItem()
            selected_control = self.control_list.currentItem()
            
            if not selected_window or not selected_control:
                raise ValueError("No window or control selected")
                
            window_title = selected_window.text().split(" (")[0]
            control_name = selected_control.text()
            
            # Activate window
            windows = pyautogui.getWindowsWithTitle(window_title)
            if windows:
                windows[0].activate()
                
                # Input data
                pyautogui.write(data)
                
                logging.info(f"Data input at window: {window_title}, control: {control_name}")
        except Exception as e:
            logging.error(f"Input error: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to input data at location")
    
    def save_settings(self):
        try:
            filename, _ = QFileDialog.getSaveFileName(self, "Save Settings", 
                                                    "", "JSON files (*.json)")
            if filename:
                settings = {
                    'saved_locations': {}
                }
                
                self.cursor.execute('SELECT * FROM saved_locations')
                for row in self.cursor.fetchall():
                    settings['saved_locations'][row[0]] = {
                        'window_title': row[1],
                        'coordinates': row[2],
                        'app_name': row[3],
                        'control_name': row[4]
                    }
                
                with open(filename, 'w') as f:
                    json.dump(settings, f)
                
                logging.info(f"Settings saved to {filename}")
        except Exception as e:
            logging.error(f"Settings save error: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to save settings")
    
    def load_settings(self):
        try:
            filename, _ = QFileDialog.getOpenFileName(self, "Load Settings", 
                                                    "", "JSON files (*.json)")
            if filename:
                with open(filename, 'r') as f:
                    settings = json.load(f)
                
                for name, data in settings['saved_locations'].items():
                    window_info = {
                        'title': data['window_title'],
                        'coordinates': eval(data['coordinates']),
                        'app_name': data['app_name']
                    }
                    self.save_location(name, window_info)
                
                logging.info(f"Settings loaded from {filename}")
        except Exception as e:
            logging.error(f"Settings load error: {str(e)}")
            QMessageBox.critical(self, "Error", "Failed to load settings")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            self.send_data()
        else:
            super().keyPressEvent(event)

if __name__ == '__main__':
    try:
        app = QApplication(sys.argv)
        window = DataTransferApp()
        window.show()
        sys.exit(app.exec_())
    except Exception as e:
        logging.critical(f"Application crash: {str(e)}")
        raise
