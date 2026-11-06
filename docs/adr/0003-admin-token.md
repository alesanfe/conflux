# ADR-0003: Token admin local por defecto

## Decisión
En el primer arranque se genera `admin_token` de 256 bits (`secrets.
token_urlsafe(32)`) y se persiste en `config.json` (0600 POSIX).
`/admin`, `/configure` y `POST /reload` lo exigen vía `X-Admin-Token` o
cookie `conflux_auth` (HttpOnly+SameSite=Strict). Rotación:
`conflux rotate-admin-token`.

## Por qué
Sin token, cualquier proceso local podía guardar config (SSRF hacia dentro)
y leer la configuración. El token impide acciones administrativas ajenas
aunque otro proceso del usuario hable con el proxy.

## Trade-off
El usuario lee el token de su propio `config.json` para el login — fricción
mínima una vez, mitigada por la cookie de sesión del navegador.

## Por qué no en la URL
`?token=` se filtra a historial, Referer y logs. El token viaja una sola
vez en el body del POST /login; la cookie es la sesión.
