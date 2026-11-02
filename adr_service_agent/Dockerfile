FROM python:3.13.7-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py wattbox.py sonos.py ruckus.py unifi.py araknis.py crestron.py cp4.py control4.py service_report.py bluesound.py sense.py dashboard.py dashboard_visuals.py presentation.py ui_icons.py monitoring.py internet_quality.py internet_test.py ruckus_controller.py dashboard.css dashboard_live.js unifi_gateway.py network_history.py outlet_control.py network_diagnostics.py diagnostic_analysis.py diagnostic_hub.py voice_input.py voice_ui.js power_events.py native_events.py adr-logo.b64 ./
CMD ["python", "-u", "agent.py"]

