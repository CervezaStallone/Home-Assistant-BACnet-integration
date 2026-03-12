#!/usr/bin/env python3
"""
BACnet Test Client (bacpypes3)
==============================

Eenvoudig testscript om te verifiëren dat het virtuele BACnet apparaat
correct werkt. Voert de volgende acties uit:

  1. Who-Is broadcast → vindt apparaten op het netwerk
  2. Read Property → leest individuele waarden
  3. Write Property → schrijft een waarde naar een output
  4. Wacht en leest opnieuw om veranderende waarden te zien

Gebruik:
  python test_client.py <device-adres>

Voorbeeld:
  python test_client.py 192.168.1.100
"""

import asyncio
import argparse
import sys

from bacpypes3.app import Application
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import ObjectIdentifier, Real
from bacpypes3.constructeddata import AnyAtomic
from bacpypes3.apdu import (
    WhoIsRequest, ReadPropertyRequest, WritePropertyRequest,
)


TARGET_DEVICE_ID = 599


async def main():
    if len(sys.argv) < 2:
        print("Gebruik: python test_client.py <ip-adres-van-virtueel-apparaat>")
        print("Voorbeeld: python test_client.py 192.168.1.100")
        print("\nTip: Start eerst het virtuele apparaat met:")
        print("  python virtual_bacnet_device.py")
        sys.exit(1)

    target_address = sys.argv[1]

    # Maak een client applicatie aan met een ander device ID
    args = argparse.Namespace(
        name="TestClient",
        instance=998,
        address="host:0",  # willekeurige poort
        vendoridentifier=999,
        network=0,
        foreign=None,
        bbmd=None,
        ttl=30,
        color=None,
        debug=[],
        route_aware=None,
    )
    app = Application.from_args(args)

    print(f"\n=== BACnet Test Client ===")
    print(f"Target: {target_address} (Device ID: {TARGET_DEVICE_ID})")

    # --- Who-Is ---
    print("\n--- WHO-IS Broadcast ---")
    try:
        i_ams = await app.who_is()
        await asyncio.sleep(3)
        for iam in i_ams:
            print(f"  Gevonden: {iam}")
    except Exception as e:
        print(f"  Who-Is resultaat: {e}")

    # --- Read Properties ---
    reads = [
        ("analog-input", 0, "ZoneTemperatuur", "°C"),
        ("analog-input", 1, "Luchtvochtigheid", "%RH"),
        ("analog-input", 2, "CO2Concentratie", "ppm"),
        ("analog-input", 3, "BuitenTemperatuur", "°C"),
        ("analog-input", 4, "Luchtdruk", "hPa"),
        ("analog-output", 0, "TemperatuurSetpoint", "°C"),
        ("analog-output", 1, "VentilatorSnelheid", "%"),
        ("analog-value", 0, "EnergieTotaal", "kWh"),
        ("analog-value", 1, "PIDOutput", "%"),
        ("binary-input", 0, "Bezettingssensor", ""),
        ("binary-output", 0, "Verlichting", ""),
        ("binary-value", 0, "Nachtmodus", ""),
    ]

    print("\n--- READ PROPERTY (alle objecten) ---")
    for obj_type, obj_inst, name, unit in reads:
        try:
            value = await app.read_property(
                Address(target_address),
                ObjectIdentifier(f"{obj_type},{obj_inst}"),
                "present-value",
            )
            print(f"  {name:<22s} = {value} {unit}")
        except Exception as e:
            print(f"  {name:<22s} = FOUT: {e}")

    # --- Write Property ---
    print("\n--- WRITE PROPERTY: TemperatuurSetpoint = 24.0 ---")
    try:
        await app.write_property(
            Address(target_address),
            ObjectIdentifier("analog-output,0"),
            "present-value",
            Real(24.0),
        )
        print("  Succesvol geschreven!")

        # Verifieer
        value = await app.read_property(
            Address(target_address),
            ObjectIdentifier("analog-output,0"),
            "present-value",
        )
        print(f"  Verificatie: TemperatuurSetpoint = {value} °C")
    except Exception as e:
        print(f"  Schrijffout: {e}")

    # --- Wacht en lees opnieuw ---
    print("\n--- WACHT 6 seconden en lees opnieuw... ---")
    await asyncio.sleep(6)

    print("\n--- RE-READ (waarden moeten veranderd zijn) ---")
    for obj_type, obj_inst, name, unit in reads[:3]:
        try:
            value = await app.read_property(
                Address(target_address),
                ObjectIdentifier(f"{obj_type},{obj_inst}"),
                "present-value",
            )
            print(f"  {name:<22s} = {value} {unit}")
        except Exception as e:
            print(f"  {name:<22s} = FOUT: {e}")

    print("\n=== Alle tests voltooid ===\n")
    app.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nGestopt.")
