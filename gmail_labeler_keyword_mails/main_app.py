from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QVBoxLayout, QLabel, QMessageBox
from PySide6.QtCore import Qt
from auth_setup_gmail import ensure_auth  # Importování tvé autentifikační funkce
from gmail_client import GmailClient  # Pro připojení k Gmailu
import json

class AppGUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("LabelerApp")
        self.setGeometry(100, 100, 500, 350)
        self.setStyleSheet("background-color: #181818;")  # Dark background

        self.gmail_client = None  # Počáteční stav, Gmail klient není autentifikován
        self.labeler_app = None  # Bude vytvořeno po autentifikaci a nastavení profilu

        # Layout pro hlavní tlačítka
        main_layout = QVBoxLayout()

        # Titulek
        self.title_label = QLabel("Labeler Application", self)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #FFFFFF;")
        main_layout.addWidget(self.title_label)

        # Sekce pro tlačítka
        button_layout = QVBoxLayout()

        # Tlačítka pro hlavní akce
        self.start_button = QPushButton("Start Classifying 📝")
        self.start_button.setStyleSheet(self.button_style("#4CAF50"))  # Green
        self.start_button.clicked.connect(self.start_classifying)
        button_layout.addWidget(self.start_button)

        self.schedule_button = QPushButton("Schedule Classification ⏱️")
        self.schedule_button.setStyleSheet(self.button_style("#03A9F4"))  # Blue
        self.schedule_button.clicked.connect(self.schedule_classifying)
        button_layout.addWidget(self.schedule_button)

        self.create_profile_button = QPushButton("Create Profile 🆕")
        self.create_profile_button.setStyleSheet(self.button_style("#FF9800"))  # Orange
        self.create_profile_button.clicked.connect(self.create_profile)
        button_layout.addWidget(self.create_profile_button)

        self.google_auth_button = QPushButton("Authenticate with Google 🔑")
        self.google_auth_button.setStyleSheet(self.button_style("#8BC34A"))  # Light Green
        self.google_auth_button.clicked.connect(self.authenticate_google)
        button_layout.addWidget(self.google_auth_button)

        # Tlačítko pro ukončení aplikace
        self.quit_button = QPushButton("Quit ❌")
        self.quit_button.setStyleSheet(self.button_style("#B71C1C"))  # Red
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
                color: #FFFFFF;
                font-size: 16px;
                border-radius: 5px;
                padding: 12px;
                margin: 10px 0;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #555;
            }}
        """

    def authenticate_google(self):
        """Spuštění autentifikace přes Google."""
        self.status_label.setText("Status: Authenticating with Google 🔑...")
        try:
            provider = ensure_auth()  # Zavolání funkce pro autentifikaci
            self.update_google_auth_status(success=True)
        except Exception as e:
            self.update_google_auth_status(success=False)
        self.status_label.setText("Status: Ready")

    def update_google_auth_status(self, success: bool):
        """Aktualizace stavu autorizace Google účtu."""
        if success:
            QMessageBox.information(self, "Success", "Google authentication successful! 👍")
            self.google_auth_button.setText("Authenticated with Google ✅")
            self.google_auth_button.setStyleSheet(self.button_style("#4CAF50"))  # Green
        else:
            QMessageBox.critical(self, "Error", "Google authentication failed. ❌")
            self.google_auth_button.setText("Authenticate with Google 🔑")
            self.google_auth_button.setStyleSheet(self.button_style("#8BC34A"))  # Light Green

    def start_classifying(self):
        """Spuštění klasifikace e-mailů pomocí LLM."""
        if self.labeler_app:
            self.status_label.setText("Status: Classifying in progress...")
            self.labeler_app.run_once()  # Spustí klasifikaci dle profilu
            QMessageBox.information(self, "Started", "Classification process started.")
            self.status_label.setText("Status: Ready")
        else:
            QMessageBox.warning(self, "Error", "Google authentication required to start.")

    def schedule_classifying(self):
        """Naplánování klasifikace e-mailů každých 60 minut."""
        if self.labeler_app:
            self.status_label.setText("Status: Scheduling classification every 60 minutes...")
            self.labeler_app.schedule(60)  # Spustí plánování každých 60 minut
            QMessageBox.information(self, "Scheduled", "Classification scheduled every 60 minutes.")
            self.status_label.setText("Status: Ready")
        else:
            QMessageBox.warning(self, "Error", "Google authentication required to schedule.")

    def create_profile(self):
        """Vytvoření profilu pro LabelerApp."""
        self.status_label.setText("Status: Creating profile...")
        profile_data = self._load_profile_data()  # Načte data z JSON
        if profile_data:
            # Vytvoří profil a předá ho do LabelerApp
            app_config = AppConfig(**profile_data)
            self.labeler_app = LabelerApp(gmail=self.gmail_client, cfg=app_config)
            QMessageBox.information(self, "Profile Created", "Profile created successfully.")
        self.status_label.setText("Status: Ready")

    def _load_profile_data(self):
        """Načte profilová data z JSON souboru (např. bricks.json)."""
        profile_path = "profiles/bricks.json"  # Mělo by být dynamické podle vybraného profilu
        try:
            with open(profile_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            QMessageBox.critical(self, "Error", "Profile file not found.")
            return None

    def quit_app(self):
        """Ukončení aplikace."""
        self.close()

if __name__ == "__main__":
    app = QApplication([])

    # Vytvoření a zobrazení GUI
    window = AppGUI()
    window.show()

    app.exec()
