# 📦 Produktions-Forecast & Planung

Diese Streamlit-Applikation dient der datengestützten Produktions- und Materialbedarfsplanung (MRP). Sie verbindet sich direkt mit Google BigQuery, um aktuelle Bestände, historische Verkaufszahlen sowie Stücklisten (COGS) abzurufen, und unterstützt bei der Berechnung von Produktionsmengen und rechtzeitigen Materialbestellungen.

## ✨ Hauptfunktionen

*   **Google BigQuery Integration:** Automatischer Abruf von Live-Beständen, Verkaufshistorien und Stücklisten (BOM).
*   **Interaktiver Sales Forecast:** Systemseitige Berechnung von Abverkaufsprognosen auf Basis historischer Daten (3-Monats-Schnitt & Vorjahreswerte), mit der Möglichkeit für manuelle Anpassungen im UI.
*   **Produktionsplanung:** Automatische Berechnung von Produktionsbedarfen unter Berücksichtigung von Mindestbestellmengen (MOQ), Sicherheitsbeständen und Vorlaufzeiten.
*   **Material Requirements Planning (MRP):** Detaillierte Stücklistenauflösung und Berechnung des Netto-Materialbedarfs (abzüglich aktueller Lagerbestände), gruppiert nach Lieferanten und spätesten Bestelldaten.

## 📂 Projektstruktur

```text
.
├── .env                        # Umgebungsvariablen (nicht in Git versioniert)
├── .gitignore                  # Ignorierte Dateien für Git
├── app.py                      # Hauptanwendung (Streamlit UI)
├── requirements.txt            # Python Abhängigkeiten
├── service-account-key.json    # Google Cloud Zugangsdaten (nicht in Git versioniert)
└── src/                        # Quellcode-Module
    ├── data_integration.py     # BigQuery Queries & Datenabruf
    ├── forecasting.py          # Logik zur Forecast-Berechnung
    ├── inventory_math.py       # Berechnung der Produktionsmengen
    ├── mrp_math.py             # Stücklistenauflösung & Bestellplanung
    └── utils.py                # Hilfsfunktionen (z.B. Datumsberechnungen)
```

## 🚀 Installation & Setup

Befolge diese Schritte, um das Projekt lokal auf deinem Rechner zum Laufen zu bringen.

### 1. Python Virtual Environment (venv) erstellen

Es wird empfohlen, eine isolierte Python-Umgebung zu nutzen. Öffne dein Terminal im Projektordner und führe folgenden Befehl aus:

```bash
python -m venv venv
```

### 2. Virtuelle Umgebung aktivieren

Je nach Betriebssystem musst du die Umgebung unterschiedlich aktivieren:

*   **Windows:**
    ```cmd
    venv\Scripts\activate
    ```
*   **Mac / Linux:**
    ```bash
    source venv/bin/activate
    ```
*(Du erkennst eine aktive Umgebung an dem `(venv)` Präfix in deinem Terminal.)*

### 3. Abhängigkeiten installieren

Installiere nun alle benötigten Python-Pakete aus der `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 4. Konfiguration & Google Cloud Authentifizierung

Damit die Applikation auf BigQuery zugreifen kann, benötigt sie gültige Google Cloud Credentials.

1.  Lege deine Service-Account-Schlüsseldatei in das Hauptverzeichnis und nenne sie `service-account-key.json`.
2.  Erstelle eine Datei mit dem Namen `.env` im Hauptverzeichnis.
3.  Füge den folgenden Inhalt in die `.env` Datei ein, um den Pfad zum Schlüssel zu deklarieren:

```env
GOOGLE_APPLICATION_CREDENTIALS="service-account-key.json"
```

*Hinweis: Stelle sicher, dass sowohl die `.env` als auch die `service-account-key.json` in der `.gitignore` eingetragen sind, damit sensible Daten nicht versehentlich in dein Repository hochgeladen werden!*

## 🖥️ App starten

Sobald das Setup abgeschlossen ist, kannst du die Anwendung mit Streamlit starten:

```bash
streamlit run app.py
```

Die App öffnet sich daraufhin automatisch in deinem Standard-Webbrowser unter `http://localhost:8501`.