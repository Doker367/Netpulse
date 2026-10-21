# NetBox — puesta en marcha y tokens

NetBox 4.6 usa tokens **v2** (HMAC + peppers) y ya no acepta el esquema
`Authorization: Token ...` para tokens nuevos.

## Arranque automático (recomendado)

Con estos valores en `.env`, el contenedor
`netboxcommunity/netbox` crea el superusuario **y** su token al primer arranque
(ver `/opt/netbox/super_user.py` en la imagen):

```bash
NETBOX_SUPERUSER_NAME=admin
NETBOX_SUPERUSER_PASSWORD=<única>
NETBOX_SECRET_KEY=$(openssl rand -hex 32)          # >=50 caracteres
NETBOX_API_TOKEN_PEPPER_1=$(openssl rand -hex 32)  # >=50 caracteres
NETBOX_API_TOKEN=<40 caracteres>      # token en claro: NetBox NO lo guarda legible
NETBOX_API_KEY=<12 caracteres>        # Token.key es varchar(12)
NETBOX_DB_PASSWORD=<única>
```

```bash
docker compose -f docker-compose.netbox.yml up -d
docker logs netpulse-netbox | grep "API Token"
# 💡 API Token: use with 'Bearer nbt_<key>.<Your token>'
```

Errores típicos si los valores no cumplen las restricciones:

| Síntoma | Causa |
|---|---|
| `⏳ Waiting on DB...` en bucle con PostgreSQL sano | `SECRET_KEY` o `API_TOKEN_PEPPER_*` < 50 caracteres (el error real queda oculto en el log) |
| `StringDataRightTruncation: value too long for type character varying(12)` | `NETBOX_API_KEY` de más de 12 caracteres |
| `403 Invalid v1 token` al llamar la API | se envió `Authorization: Token <token>` con un token v2 |

## Integración con NetPulse

`app/core/settings.py::netbox_auth_header()` elige el esquema según el valor:

- valor que empieza por `nbt_` → `Authorization: Bearer nbt_<key>.<token>`
- cualquier otro valor → `Authorization: Token <token>` (tokens v1 legados)

Por eso `NETBOX_TOKEN` (lo que lee la API de NetPulse desde `.env`) debe ser la
credencial **completa** `nbt_<key>.<token>` cuando el token es v2:

```bash
# .env
NETBOX_TOKEN=nbt_<key>.<token>
```

Alternativa por archivo (la usa `app/services/integrations/netbox_sync.py`,
con prioridad sobre la variable de entorno):

```bash
printf 'nbt_<key>.<token>\n' > config/.netbox_token
chmod 600 config/.netbox_token
```

## Creación manual del token (Web UI)

1. Abra http://localhost:8000 e inicie sesión con `NETBOX_SUPERUSER_NAME` y
   `NETBOX_SUPERUSER_PASSWORD` (no hay contraseña por defecto).
2. Menú de usuario (arriba a la derecha) → **API Tokens** → **Add a token**.
3. Marque *Write enabled* y copie el valor generado; tiene el formato
   `nbt_<key>.<token>`.
4. Guárdelo en `config/.netbox_token` o en `NETBOX_TOKEN`.

> Los tokens v2 no se pueden recuperar de la base de datos más tarde: NetBox
> solo almacena el HMAC (`hmac_digest`) y la clave corta (`key`). Guarde el
> valor en un gestor de secretos.

## Verificación

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer nbt_<key>.<token>" \
  http://localhost:8000/api/status/
```
