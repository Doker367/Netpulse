# NetBox — Token creation (manual via Web UI)

NetBox 4.6 usa un nuevo sistema de tokens (HMAC + peppers).
La creación programática requiere configuración adicional de API_TOKEN_PEPPERS.

## Para crear el token manualmente:

1. Abrí http://localhost:8000
2. Login: admin / admin
3. Click en tu usuario (arriba derecha) → "API Tokens"
4. "Add a token" → marcá "Write enabled"
5. Copiá el token generado
6. Guardalo: echo "TU-TOKEN" > config/.netbox_token

## Para crear vía Django shell (requiere fix de peppers):

El problema es que API_TOKEN_PEPPERS debe ser un dict con:
- Claves: enteros (1, 2, ...)
- Valores: strings de al menos 50 caracteres
- Debe estar en configuration.py (no extra.py)

Fix: editar docker/netbox/configuration.py y agregar:
API_TOKEN_PEPPERS = {1: "x" * 50}
