# Colección editorial en diez idiomas

El usuario autorizó expresamente crear 100 playlists y aclaró que los idiomas corresponden a los nombres y descripciones; las versiones pueden compartir canciones. Esta colección contiene diez selecciones musicales con diez presentaciones: español, inglés, portugués, francés, alemán, italiano, neerlandés, japonés, coreano y chino simplificado.

## Canciones y selección

La captura `data/editorial_expansion/source_playlists.json` proviene de las diez playlists originales leídas mediante la sesión autorizada de Spotify. Las selecciones de `app/editorial_expansion.py` se revisaron editorialmente para retirar resultados ajenos al género presentes en esas listas. Cada referencia conserva la playlist de origen y la posición en esa captura. No volver a capturar el archivo con otra ordenación sin revisar esas referencias.

Cada selección tiene entre 35 y 50 pistas. Se eliminan duplicados por URI, ISRC y título/artista normalizados, incluidos remasters. Hay un máximo de cuatro canciones por artista principal; la secuencia alterna artistas cuando es posible. La selección es editorial y no incorpora un TrendScore, una estimación de rendimiento o una afirmación de popularidad. El idioma de presentación no es el idioma de las grabaciones.

## Publicación y recuperación

`python -m app.editorial_expansion` prepara el plan local. `python -m app.editorial_expansion --apply --limit 10` publica hasta diez pendientes de ese plan. El archivo `data/editorial_expansion/publication.json` registra cada operación y permite continuar sin duplicar listas.

El publicador comprueba el propietario, crea la lista privada, carga la selección, vuelve a leer todas las canciones y después confirma la visibilidad pública y los textos. Una colisión con una lista existente detiene la operación. Una creación con respuesta perdida solo se recupera si existe una intención de creación previa y coinciden exactamente propietario, nombre y descripción. Contenido inesperado en una lista pendiente se conserva y detiene la operación.

Las configuraciones de las listas publicadas se guardan en `playlists/` y los enlaces en el registro habitual. Las pistas editoriales quedan bloqueadas y la rotación automática desactivada. Una futura actualización por tendencias necesita candidatos respaldados y revisión; esta colección no cambia los controles de `world playlist-launch` ni convierte los 100 conceptos anteriores en lanzamientos basados en métricas.

## Enlaces visibles

`/static/created-playlists.html` muestra únicamente publicaciones confirmadas. Lee el manifiesto público `created-playlists.json` cada quince segundos. Ese manifiesto contiene nombres, enlaces, idioma de presentación, número de pistas y estado; no contiene credenciales ni datos de proveedores de métricas.
