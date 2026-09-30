# 📦 Produktions-Forecast & Planung

Diese Streamlit-Applikation dient der datengestützten Produktions- und Materialbedarfsplanung (MRP). Sie verbindet sich direkt mit Google BigQuery, um aktuelle Bestände, historische Verkaufszahlen sowie Stücklisten (COGS) abzurufen, und unterstützt bei der Berechnung von Produktionsmengen und rechtzeitigen Materialbestellungen.

## ✨ Hauptfunktionen

*   **Google BigQuery Integration:** Automatischer Abruf von Live-Beständen, Verkaufshistorien, Stücklisten (BOM) sowie offenen Bestell- und Produktionsaufträgen.
*   **Interaktiver Sales Forecast:** Systemseitige Absatzprognose (3-Monats-Schnitt & Vorjahressaisonalität) mit interaktiver manueller Anpassung.
*   **Produktionsplanung & Machbarkeit:** Nettobedarfsrechnung mit MOQ, Sicherheitsbestand, tagesgenauen Vorlaufzeiten und automatischer Fristprüfung gegen Materialengpässe (Lost Sales).
*   **Material Requirements Planning (MRP):** Stücklistenauflösung und FIFO-Netting mit Lagerbeständen und offenen Bestellungen, gruppiert nach Lieferant.
*   **Liquiditätsübersicht:** Monatlich aggregierter Cashbedarf auf Basis spätester Bestellfristen.

## 📂 Projektstruktur

```text
.
├── .env                        # Umgebungsvariablen (nicht in Git versioniert)
├── .gitignore                  # Ignorierte Dateien für Git
├── app.py                      # Hauptanwendung (Streamlit UI)
├── requirements.txt            # Python-Abhängigkeiten
├── service-account-key.json    # Google Cloud Zugangsdaten (nicht in Git versioniert)
└── src/                        # Quellcode-Module
    ├── bom.py                  # Stücklistenauflösung & COGS-Indizierung
    ├── data_integration.py     # BigQuery-Queries & Snapshot-Persistenz
    ├── forecasting.py          # Statistische Absatzprognose
    ├── inventory_math.py       # Nettoproduktionsbedarf & Feasibility-Prüfung
    ├── mrp_math.py             # MRP-Materialbedarfe & Cashbedarfsberechnung
    ├── planning_service.py     # Workflow-Orchestrierung & UI-Aufbereitung
    ├── supply_tracker.py       # MaterialSupplyTracker & FIFO-Allokation
    └── utils.py                # Datums-, Währungs- und Mengenformatierung
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