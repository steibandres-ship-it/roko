# Supabase · World Music

Proyecto: https://supabase.com/dashboard/project/kheknsnsrwlagmdiqsfs
Repositorio: https://github.com/steibandres-ship-it/roko

## Esquema

`world_music.sql` se genera desde las migraciones Alembic del repositorio, incluidas
las modificaciones locales de fans. Contiene 22 tablas de aplicación y la tabla de
control `alembic_version`. Instalar solamente en una base nueva: no es un script
para reemplazar tablas existentes. La transacción evita instalaciones parciales.

- Catálogo: artistas, lanzamientos, pistas, idiomas, géneros, mercados e identificadores.
- Inteligencia: capturas de métricas, ejecuciones de proveedores, scores y propagación.
- Campañas: reportes orgánicos, contactos de fans, campañas y destinatarios.
- 22 índices adicionales para relaciones, audiencia activa, cola y métricas por proveedor.
- RLS habilitado; sin políticas para acceso desde clientes. Privilegios retirados a
  `PUBLIC`, `anon` y `authenticated` para las tablas de la aplicación.

El backend FastAPI existente consulta PostgreSQL por SQLAlchemy. No necesita la
Data API ni una clave pública de Supabase. Esta configuración conserva el modelo
actual de operador local; no agrega cuentas ni autorización por artista.

## Conexión local

En Supabase, Connect → Session pooler, copia el host exacto y el usuario del proyecto.
Usa puerto 5432 para compatibilidad IPv4 y una conexión persistente del servidor.
Guarda en `.env` (ignorado por Git), sustituyendo los campos entre corchetes:

```dotenv
WORLD_MUSIC_DATABASE_URL=postgresql+psycopg://postgres.kheknsnsrwlagmdiqsfs:[PASSWORD_URL_ENCODED]@[SESSION_POOLER_HOST]:5432/postgres?sslmode=require
```

No incluyas la contraseña en Git ni en código del navegador. El propietario `postgres`
es adecuado para instalar el esquema; para un backend público se debe provisionar
un rol de servicio limitado y autenticación real antes de exponer rutas administrativas.
No se han trasladado datos locales ni cambiado automáticamente `.env`.

Si instalaste el SQL completo, la revisión Alembic ya queda registrada. Para futuras
actualizaciones usa `python -m alembic upgrade head`; no ejecutes otra vez el bootstrap.
Generación reproducible: `python tools/export_supabase_schema.py`.

## Verificación

Ejecuta `verify.sql` en SQL Editor. Debe devolver 23 tablas contando Alembic, todas
con RLS, ninguna con permisos CRUD para anon/authenticated, y la revisión
`a03preusers01`. Los índices deben coincidir con el archivo generado.

Fuentes: https://supabase.com/docs/guides/database/connecting-to-postgres
https://supabase.com/docs/guides/database/postgres/row-level-security

## Instalación verificada

El 6 de octubre de 2026 se ejecutó el bootstrap en el proyecto indicado.
SQL Editor confirmó: revisión f92a206supa01, 21 tablas, 21 con RLS,
0 tablas con permisos para anon/authenticated y 69 índices totales.
Región elegida al crear el proyecto: us-west-2 (Oregon), compute Nano.
El backend local continúa usando su configuración previa hasta establecer la URL.

La migración de preusuarios a03preusers01 se aplicó posteriormente: preusers y
preuser_imports verificadas con RLS y sin acceso para anon/authenticated.
