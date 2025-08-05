from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
import random

class AppGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("LabelerApp")
        self.setGeometry(100, 100, 500, 400)

        # Layout pro hlavní tlačítka
        main_layout = QVBoxLayout()

        # Titulek
        self.title_label = QLabel("Labeler Application", self)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        main_layout.addWidget(self.title_label)

        # Sekce pro tlačítka
        button_layout = QVBoxLayout()

        # Tlačítka pro hlavní akce
        self.start_button = QPushButton("Start Classifying 📝")
        self.start_button.setStyleSheet(self.button_style("lightgreen"))
        self.start_button.clicked.connect(self.start_classifying)
        button_layout.addWidget(self.start_button)

        self.schedule_button = QPushButton("Schedule Classification ⏱️")
        self.schedule_button.setStyleSheet(self.button_style("lightblue"))
        self.schedule_button.clicked.connect(self.schedule_classifying)
        button_layout.addWidget(self.schedule_button)

        self.create_profile_button = QPushButton("Create Profile 🆕")
        self.create_profile_button.setStyleSheet(self.button_style("lightgray"))
        self.create_profile_button.clicked.connect(self.create_profile)
        button_layout.addWidget(self.create_profile_button)

        self.google_auth_button = QPushButton("Authenticate with Google 🔑")
        self.google_auth_button.setStyleSheet(self.button_style("lightyellow"))
        self.google_auth_button.clicked.connect(self.authenticate_google)
        button_layout.addWidget(self.google_auth_button)

        # Tlačítko pro ukončení aplikace
        self.quit_button = QPushButton("Quit ❌")
        self.quit_button.setStyleSheet(self.button_style("lightcoral"))
        self.quit_button.clicked.connect(self.quit_app)
        button_layout.addWidget(self.quit_button)

        # Přidání tlačítek do hlavního layoutu
        main_layout.addLayout(button_layout)

        # Status Message pro zpětnou vazbu
        self.status_label = QLabel("Status: Ready", self)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 14px; color: gray;")
        main_layout.addWidget(self.status_label)

        # Nastavení hlavního layoutu
        self.setLayout(main_layout)

    def button_style(self, color):
        """Nastavení stylu tlačítka s jemným barevným efektem."""
        return f"""
            QPushButton {{
                background-color: {color};
                color: #333;
                font-size: 16px;
                border-radius: 5px;
                padding: 12px;
                margin: 5px;
            }}
            QPushButton:hover {{
                background-color: #ddd;
            }}
        """

    def start_classifying(self):
        """Simulace spuštění klasifikace."""
        self.status_label.setText("Status: Classifying in progress...")
        # Zde by byla volání skutečné funkce pro klasifikaci
        QMessageBox.information(self, "Started", "Classification process started.")
        self.status_label.setText("Status: Ready")

    def schedule_classifying(self):
        """Simulace naplánování klasifikace."""
        self.status_label.setText("Status: Scheduling classification every 60 minutes...")
        # Zde by byla volání skutečné funkce pro plánování
        QMessageBox.information(self, "Scheduled", "Classification scheduled every 60 minutes.")
        self.status_label.setText("Status: Ready")

    def create_profile(self):
        """Simulace vytvoření profilu."""
        self.status_label.setText("Status: Creating profile...")
        # Zde by byla volání skutečné funkce pro vytvoření profilu
        QMessageBox.information(self, "Profile Created", "Profile created successfully.")
        self.status_label.setText("Status: Ready")

    def authenticate_google(self):
        """Simulace autentifikace přes Google."""
        self.status_label.setText("Status: Authenticating with Google 🔑...")
        # Zde by byla volání skutečné funkce pro autentifikaci
        self.update_google_auth_status(success=True)
        self.status_label.setText("Status: Ready")

    def update_google_auth_status(self, success: bool):
        """Aktualizace stavu autorizace Google účtu."""
        if success:
            QMessageBox.information(self, "Success", "Google authentication successful! 👍")
            self.google_auth_button.setText("Authenticated with Google ✅")
            self.google_auth_button.setStyleSheet(self.button_style("lightgreen"))
        else:
            QMessageBox.critical(self, "Error", "Google authentication failed. ❌")
            self.google_auth_button.setText("Authenticate with Google 🔑")
            self.google_auth_button.setStyleSheet(self.button_style("lightyellow"))

    def quit_app(self):
        """Ukončení aplikace."""
        self.close()

if __name__ == "__main__":
    app = QApplication([])

    # Vytvoření a zobrazení GUI
    window = AppGUI()
    window.show()

    app.exec()
