import socket
import threading
import sys
import netifaces
import queue
import time
from PyQt5.QtWidgets import (QWidget, QApplication, QVBoxLayout, QTextEdit, 
                            QHBoxLayout, QListWidget, QPushButton, QLineEdit,
                            QLabel, QComboBox, QCheckBox, QMessageBox)
from PyQt5.QtCore import pyqtSignal, QThread, Qt
import pyautogui

class MessageSender(QThread):
    message_sent = pyqtSignal(bool, str)  # Success flag, error message if any
    
    def __init__(self):
        super().__init__()
        self.message_queue = queue.Queue()
        self.running = True
        self.max_retries = 3
        self.retry_delay = 1.0  # seconds
        
    def add_message(self, window, message):
        self.message_queue.put((window, message))
        
    def run(self):
        while self.running:
            try:
                if not self.message_queue.empty():
                    window, message = self.message_queue.get()
                    success = False
                    error_msg = ""
                    
                    for attempt in range(self.max_retries):
                        try:
                            window.activate()
                            time.sleep(0.1)  # Give window time to activate
                            pyautogui.typewrite(message)
                            success = True
                            break
                        except Exception as e:
                            error_msg = str(e)
                            time.sleep(self.retry_delay)
                    
                    self.message_sent.emit(success, error_msg if not success else "")
                    self.message_queue.task_done()
                else:
                    time.sleep(0.1)
                    
            except Exception as e:
                self.message_sent.emit(False, str(e))
                time.sleep(0.1)
                
    def stop(self):
        self.running = False

class NetworkScanner(QThread):
    device_found = pyqtSignal(str)
    message_received = pyqtSignal(str, str)  # hostname, message
    host_status_changed = pyqtSignal(str, bool)  # hostname, is_host
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.broadcast_port = 12344
        self.message_port = 12345
        self.known_devices = set()
        self.known_hosts = set()
        self.message_socket = None
        self.hostname = socket.gethostname()
        
    def run(self):
        # Create multiple sockets for each network interface
        self.broadcast_sockets = []
        
        # Create message socket
        self.message_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.message_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.message_socket.bind(('0.0.0.0', self.message_port))
        
        for interface in netifaces.interfaces():
            try:
                # Get interface addresses
                addrs = netifaces.ifaddresses(interface)
                
                # Skip interfaces without IPv4 or loopback
                if netifaces.AF_INET not in addrs:
                    continue
                ip = addrs[netifaces.AF_INET][0]['addr']
                if ip.startswith('127.'):
                    continue
                    
                # Create socket for this interface
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                
                # Try to bind to the interface
                try:
                    sock.bind((ip, self.broadcast_port))
                    self.broadcast_sockets.append((sock, interface, ip))
                    print(f"Bound to interface {interface} with IP {ip}")
                except:
                    sock.close()
                    continue
                
            except Exception as e:
                print(f"Could not bind to interface {interface}: {e}")
                continue
        
        # Start listening and broadcasting threads
        for sock, interface, ip in self.broadcast_sockets:
            threading.Thread(target=self.listen_broadcasts, 
                           args=(sock,), 
                           daemon=True).start()
            
        threading.Thread(target=self.send_broadcasts, daemon=True).start()
        threading.Thread(target=self.listen_messages, daemon=True).start()

    def listen_broadcasts(self, sock):
        sock.settimeout(1.0)  # Add timeout to avoid blocking forever
        # Track last known host status for each device
        device_host_status = {}
        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                message = data.decode()
                if '|' in message:
                    hostname, is_host = message.split('|')
                    is_host = is_host == 'True'
                    device_id = f"{hostname}_{addr[0]}"
                    
                    # Only emit signal for new devices
                    if device_id not in self.known_devices:
                        self.known_devices.add(device_id)
                        self.device_found.emit(f"{hostname}|{addr[0]}")
                    
                    # Only emit host status if it changed
                    if hostname not in device_host_status or device_host_status[hostname] != is_host:
                        device_host_status[hostname] = is_host
                        if is_host:
                            self.known_hosts.add(hostname)
                        elif hostname in self.known_hosts:
                            self.known_hosts.remove(hostname)
                        self.host_status_changed.emit(hostname, is_host)
                        
            except socket.timeout:
                continue
            except Exception as e:
                if "forcibly closed" not in str(e):
                    print(f"Error listening for broadcasts: {e}")
                continue

    def send_broadcasts(self):
        broadcast_sent = set()  # Track which broadcasts were sent this round
        while self.running:
            try:
                broadcast_sent.clear()
                # Send on all network interfaces
                for sock, interface, ip in self.broadcast_sockets:
                    try:
                        # Calculate broadcast address
                        broadcast = '.'.join(ip.split('.')[:-1] + ['255'])
                        if broadcast not in broadcast_sent:
                            message = f"{self.hostname}|{self.is_host if hasattr(self, 'is_host') else False}"
                            sock.sendto(message.encode(), (broadcast, self.broadcast_port))
                            broadcast_sent.add(broadcast)
                    except Exception as e:
                        if "unreachable network" not in str(e):
                            print(f"Error broadcasting on interface {interface}: {e}")
                        
            except Exception as e:
                print(f"Error in send_broadcasts: {e}")
            
            self.msleep(2000)  # Send every 2 seconds

    def listen_messages(self):
        self.message_socket.settimeout(1.0)  # Add timeout
        while self.running:
            try:
                data, addr = self.message_socket.recvfrom(1024)
                message = data.decode()
                hostname, content = message.split('|', 1)  # Split on first | only
                self.message_received.emit(hostname, content)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Error receiving message: {e}")

    def broadcast_message(self, message):
        success = False
        full_message = f"{self.hostname}|{message}"
        
        # Send to all known devices
        sent_ips = set()  # Track which IPs we've sent to
        for device_id in self.known_devices:
            try:
                ip = device_id.split('_')[1]
                if ip not in sent_ips:
                    self.message_socket.sendto(full_message.encode(), (ip, self.message_port))
                    sent_ips.add(ip)
                    success = True
            except Exception as e:
                print(f"Error sending message to {ip}: {e}")
        return success

    def send_message(self, ip, message):
        try:
            full_message = f"{self.hostname}|{message}"
            self.message_socket.sendto(full_message.encode(), (ip, self.message_port))
            return True
        except Exception as e:
            print(f"Error sending message: {e}")
            return False

    def set_host_status(self, is_host):
        self.is_host = is_host

class SimpleGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.scanner = NetworkScanner()
        self.message_sender = MessageSender()
        self.scanner.device_found.connect(self.add_device)
        self.scanner.message_received.connect(self.show_message)
        self.scanner.host_status_changed.connect(self.handle_host_status)
        self.message_sender.message_sent.connect(self.handle_message_result)
        self.scanner.start()
        self.message_sender.start()
        self.is_host = False
        self.target_window = None
        self.target_control = None
        self.refresh_windows()

    def initUI(self):
        main_layout = QHBoxLayout()
        
        # Left side - device list and host controls
        left_layout = QVBoxLayout()
        
        # Host controls
        host_layout = QVBoxLayout()
        self.host_checkbox = QCheckBox("Act as Host")
        self.host_checkbox.stateChanged.connect(self.toggle_host)
        host_layout.addWidget(self.host_checkbox)
        
        # Window selection
        self.window_combo = QComboBox()
        self.window_combo.setEnabled(False)
        host_layout.addWidget(QLabel("Target Window:"))
        host_layout.addWidget(self.window_combo)
        
        # Control selection
        self.control_combo = QComboBox()
        self.control_combo.setEnabled(False)
        host_layout.addWidget(QLabel("Target Control:"))
        host_layout.addWidget(self.control_combo)
        
        refresh_button = QPushButton("Refresh Windows")
        refresh_button.clicked.connect(self.refresh_windows)
        host_layout.addWidget(refresh_button)
        
        left_layout.addLayout(host_layout)
        
        # Device list
        self.device_list = QListWidget()
        left_layout.addWidget(QLabel("Connected Devices:"))
        left_layout.addWidget(self.device_list)
        
        # Right side - chat area
        right_layout = QVBoxLayout()
        self.chat_area = QTextEdit()
        self.chat_area.setReadOnly(True)
        right_layout.addWidget(self.chat_area)
        
        # Message input area
        input_layout = QHBoxLayout()
        self.message_input = QTextEdit()
        self.message_input.setMaximumHeight(100)
        self.send_button = QPushButton('Send')
        self.send_button.clicked.connect(self.send_message)
        input_layout.addWidget(self.message_input)
        input_layout.addWidget(self.send_button)
        right_layout.addLayout(input_layout)
        
        main_layout.addLayout(left_layout)
        main_layout.addLayout(right_layout)
        
        self.setLayout(main_layout)
        self.setWindowTitle('Network Device Scanner & Chat')
        self.resize(800, 600)

    def toggle_host(self, state):
        self.is_host = bool(state)
        self.scanner.set_host_status(self.is_host)
        self.window_combo.setEnabled(self.is_host)
        self.control_combo.setEnabled(self.is_host)
        if self.is_host:
            self.refresh_windows()
            self.chat_area.append("* You are now acting as host\n")
        else:
            self.chat_area.append("* You are no longer acting as host\n")

    def handle_host_status(self, hostname, is_host):
        if hostname != socket.gethostname():
            if is_host:
                self.chat_area.append(f"* {hostname} is now acting as host\n")
            else:
                self.chat_area.append(f"* {hostname} is no longer acting as host\n")

    def refresh_windows(self):
        self.window_combo.clear()
        self.control_combo.clear()
        
        # Get all windows using pyautogui
        windows = []
        for window in pyautogui.getAllWindows():
            if window.title:
                windows.append(window)
                self.window_combo.addItem(window.title, window)
            
        self.window_combo.currentIndexChanged.connect(self.window_selected)

    def window_selected(self):
        self.control_combo.clear()
        window = self.window_combo.currentData()
        if window:
            # Since pyautogui doesn't provide direct access to controls,
            # we'll just create a single text input target
            self.control_combo.addItem("Text Input Target", window)

    def add_device(self, device_info):
        hostname, ip = device_info.split('|')
        item_text = f"{hostname} ({ip})"
        items = self.device_list.findItems(item_text, Qt.MatchExactly)
        if not items:
            self.device_list.addItem(item_text)
            self.chat_area.append(f"* {hostname} joined the network\n")

    def handle_message_result(self, success, error):
        if not success:
            self.chat_area.append(f"Error sending message to window: {error}\n")

    def show_message(self, hostname, message):
        self.chat_area.append(f"{hostname}: {message}\n")
        self.chat_area.verticalScrollBar().setValue(
            self.chat_area.verticalScrollBar().maximum()
        )
        
        # Only input message if we're the host AND the message is from someone else
        if self.is_host and hostname != socket.gethostname() and self.control_combo.currentData():
            window = self.control_combo.currentData()
            self.message_sender.add_message(window, message)

    def send_message(self):
        message = self.message_input.toPlainText().strip()
        if message:
            if self.scanner.broadcast_message(message):
                self.chat_area.append(f"You: {message}\n")
                self.chat_area.verticalScrollBar().setValue(
                    self.chat_area.verticalScrollBar().maximum()
                )
                self.message_input.clear()

    def closeEvent(self, event):
        self.scanner.running = False
        self.message_sender.stop()
        event.accept()

def main():
    app = QApplication(sys.argv)
    gui = SimpleGUI()
    gui.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()