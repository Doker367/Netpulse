"""Normalización de eventos de honeypots reales (Fase C).

Cada módulo traduce el formato de log nativo de un honeypot (Cowrie,
OpenCanary) al contrato de telemetría de NetPulse, para que el tailer
los persista como cualquier otro evento de deception.
"""
