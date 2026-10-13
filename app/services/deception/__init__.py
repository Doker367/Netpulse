"""NetPulse — Deception (red señuelo con dispositivos simulados).

Subpaquete que agrupa la lógica del honeypot:
- ``topology_svc``  : red declarada (segmentos/VLANs + dispositivos).
- ``store_svc``     : telemetría append-only de eventos de atacantes.
- ``personas``      : perfiles por rol (Fase B).
- ``engine_svc``    : supervisor de listeners (Fase B).
- ``listeners``     : listeners nativos asyncio (Fase B).
- ``integrations``  : parseo de Cowrie/OpenCanary (Fase C).
"""
