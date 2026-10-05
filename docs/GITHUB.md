# Instalar una copia del repositorio

Requiere Python 3.12 o posterior y Git. Después de clonar el repositorio y entrar en su carpeta, ejecuta en PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe -m app world db-upgrade
.venv\Scripts\python.exe -m app world status
.venv\Scripts\python.exe -m app world serve --port 8002
```

El panel estará en http://127.0.0.1:8002/dashboard. Configura las credenciales que correspondan en el `.env` local. El ejemplo conserva vacías las credenciales y desactivadas las declaraciones de permisos de proveedores.

## Qué se versiona

- Aplicación, interfaz visual, migraciones y puente de proveedores.
- Pruebas, dependencias y documentación.
- Definiciones de canciones y textos de las playlists, con sus portadas.

## Estado de cada instalación

La base de datos, métricas autorizadas, sesiones OAuth, correspondencia privada, logs, copias de seguridad y registro de identificadores de publicación están excluidos de Git. El archivo `.env` también está excluido. Las definiciones de playlists no contienen credenciales.

Una instalación clonada comienza sin estos datos. No ejecutes publicaciones masivas para recuperar listas existentes: primero comprueba la cuenta autorizada y recupera o restaura su registro local. La colección editorial multilingüe requiere además la captura local revisada y su plan; esos archivos se conservan en `data/editorial_expansion/` en la instalación original.

El repositorio no transfiere las licencias o autorizaciones de un proveedor a otra cuenta. Las actualizaciones basadas en tendencias conservan sus controles de identidad, procedencia y frescura.

## Pruebas

```powershell
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest
```

El panel y la API están diseñados para ejecutarse localmente. Crear este repositorio no publica el servicio web ni las métricas de proveedores en Internet.
