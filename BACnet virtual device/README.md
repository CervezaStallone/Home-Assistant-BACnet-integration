# Virtueel BACnet Apparaat

Een volledig functioneel virtueel BACnet/IP apparaat dat een thermostaat/HVAC-controller
simuleert met dynamisch veranderende waarden. Ideaal voor het testen van BACnet-integraties
zoals VOLTTRON BACnet auto-configuratie.

## Kenmerken

- **16 BACnet objecten** — analog inputs/outputs/values + binary inputs/outputs/values
- **Dynamische simulatie** — waarden veranderen realistisch over tijd
- **Beschrijfbare outputs** — setpoints en schakelaars zijn via BACnet beschrijfbaar
- **VOLTTRON-compatibel** — inclusief registry CSV voor de VOLTTRON BACnet driver
- **Configureerbaar** — via omgevingsvariabelen of config.env

## Gesimuleerde Objecten

| Type | Inst | Naam | Eenheid | Beschrijfbaar |
|------|------|------|---------|---------------|
| AI | 0 | ZoneTemperatuur | °C | Nee |
| AI | 1 | Luchtvochtigheid | %RH | Nee |
| AI | 2 | CO2Concentratie | ppm | Nee |
| AI | 3 | BuitenTemperatuur | °C | Nee |
| AI | 4 | Luchtdruk | hPa | Nee |
| AO | 0 | TemperatuurSetpoint | °C | **Ja** |
| AO | 1 | VentilatorSnelheid | % | **Ja** |
| AV | 0 | EnergieTotaal | kWh | Nee |
| AV | 1 | PIDOutput | % | Nee |
| BI | 0 | Bezettingssensor | — | Nee |
| BI | 1 | Deursensor | — | Nee |
| BI | 2 | Raamcontact | — | Nee |
| BO | 0 | Verlichting | — | **Ja** |
| BO | 1 | AlarmRelais | — | **Ja** |
| BV | 0 | Nachtmodus | — | **Ja** |
| BV | 1 | Onderhoudsmodus | — | **Ja** |

## Simulatie Gedrag

- **Temperatuur**: sinusvormig patroon + Gaussische ruis (simuleert dag/nacht)
- **Luchtvochtigheid**: invers gecorreleerd met temperatuur
- **CO2**: stijgt bij bezetting, daalt bij afwezigheid
- **Energie**: teller die altijd stijgt (realistisch stroomverbruik)
- **PID Output**: reageert op verschil tussen setpoint en actuele temperatuur
- **Binaire sensoren**: wisselen willekeurig met realistische intervallen

## Installatie

```bash
# Maak een virtual environment (aanbevolen)
python3 -m venv venv
source venv/bin/activate

# Installeer afhankelijkheden
pip install -r requirements.txt
```

## Gebruik

### 1. Start het virtuele apparaat

```bash
# Met standaard instellingen
python virtual_bacnet_device.py

# Of met aangepaste configuratie
source config.env
python virtual_bacnet_device.py
```

### 2. Configuratie via omgevingsvariabelen

| Variabele | Standaard | Beschrijving |
|-----------|-----------|--------------|
| `BACNET_DEVICE_ID` | `599` | BACnet device instance nummer |
| `BACNET_DEVICE_NAME` | `VirtueleThermostaat` | Apparaatnaam |
| `BACNET_ADDRESS` | `0.0.0.0/24` | IP-adres/subnetmasker |
| `BACNET_PORT` | `47808` | UDP poort (0xBAC0) |
| `UPDATE_INTERVAL` | `5` | Seconden tussen updates |

### 3. Test met de meegeleverde test-client

```bash
# In een tweede terminal
python test_client.py <ip-van-je-machine>
# Voorbeeld:
python test_client.py 192.168.1.100
```

### 4. Test met andere tools

```bash
# BACpypes console (read property)
python -m bacpypes ReadProperty <device-ip> analogInput:0 presentValue

# YABE (Yet Another BACnet Explorer) — Windows GUI tool
# Wireshark met BACnet filter: "bacnet"
```

## VOLTTRON Integratie

### BACnet Proxy Agent configureren

```json
{
    "device_address": "192.168.1.100",
    "max_apdu_length": 1024
}
```

### BACnet Driver configureren

Gebruik het meegeleverde `volttron_bacnet_registry.csv` bestand als registry
configuratie voor de VOLTTRON BACnet driver:

```json
{
    "driver_config": {
        "device_address": "<ip-virtueel-apparaat>",
        "device_id": 599
    },
    "driver_type": "bacnet",
    "registry_config": "config://bacnet_registry.csv",
    "interval": 10,
    "timezone": "Europe/Amsterdam"
}
```

### Auto-configuratie met grab_bacnet_config.py

Gebruik VOLTTRON's BACnet auto-configuratie tool om automatisch de
objectenlijst op te halen:

```bash
# Vanuit de VOLTTRON-omgeving
python scripts/grab_bacnet_config.py \
    --address <ip-virtueel-apparaat> \
    --device-id 599 \
    --out-file bacnet_exports.csv
```

## Probleemoplossing

### Poort al in gebruik
```bash
# Controleer of poort 47808 al in gebruik is
sudo lsof -i :47808
# Gebruik een andere poort
export BACNET_PORT=47809
```

### Geen apparaten gevonden bij Who-Is
- Controleer firewall-regels (UDP poort 47808 moet open zijn)
- Zorg dat het apparaat op hetzelfde subnet zit
- Probeer een specifiek IP-adres in plaats van `0.0.0.0`

### Permissie fout
```bash
# BACnet gebruikt poort 47808 (> 1024), geen root nodig
# Maar als je poort < 1024 wilt gebruiken:
sudo python virtual_bacnet_device.py
```

## Bestandsoverzicht

| Bestand | Beschrijving |
|---------|-------------|
| `virtual_bacnet_device.py` | Hoofdscript — het virtuele BACnet apparaat |
| `test_client.py` | Testscript om het apparaat te bevragen |
| `config.env` | Configuratie (omgevingsvariabelen) |
| `volttron_bacnet_registry.csv` | VOLTTRON BACnet driver registry |
| `requirements.txt` | Python afhankelijkheden |
