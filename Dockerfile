# Offizielles, schlankes Python-Image
FROM python:3.14-slim

# Arbeitsverzeichnis im Container
WORKDIR /app

# Requirements kopieren und installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Den restlichen Code kopieren
COPY . .

# Streamlit-Port freigeben (Cloud Run nutzt meist 8080)
EXPOSE 8080

# Befehl zum Starten der App
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]