# World Music · Fan Connect

Propuesta inicial para un módulo de comunicación por correo integrado en el panel de artistas. Pendiente de definir si se construye un panel nuevo o se integra en uno existente.

## Objetivo

Permitir que cada artista convierta el interés generado por sus pre-saves en una audiencia con la que pueda compartir nuevos lanzamientos, merchandising y novedades.

## Primera versión

- **Audiencia:** contactos únicos por artista, con fecha de suscripción, origen y pistas asociadas a sus pre-saves. Un fan puede seguir a varios artistas sin compartir sus datos entre sus cuentas.
- **Captación:** ofrecer una suscripción opcional a novedades durante el pre-save y registrar la aceptación por separado. Completar un pre-save no suscribe automáticamente al fan.
- **Campañas:** crear y guardar borradores con asunto, texto de vista previa, imagen, mensaje y botón hacia un lanzamiento o tienda.
- **Destinatarios:** elegir todos los suscriptores activos del artista o los asociados a una pista concreta; mostrar el total antes del envío y excluir duplicados, bajas y direcciones bloqueadas.
- **Vista previa:** revisar el correo y enviar una prueba a una dirección elegida por el artista antes de confirmar una campaña.
- **Envío:** confirmar una campaña desde el panel, procesarla en segundo plano y mostrar progreso y errores. Reintentar sin enviar mensajes duplicados.
- **Resultados:** mostrar entregas reportadas por el proveedor, rebotes, clics y bajas. Si se incorporan aperturas, identificarlas como una estimación.
- **Baja:** incluir un enlace de cancelación en cada correo y aplicar la exclusión también a campañas pendientes.

## Recorrido principal

1. El fan llega a una página de pre-save y puede aceptar recibir novedades del artista.
2. El sistema registra el pre-save y, cuando corresponde, la suscripción con su origen y fecha.
3. El artista abre «Fans y correos» en su panel y selecciona «Crear campaña».
4. Elige una plantilla de lanzamiento, merchandising o novedades, redacta el contenido y selecciona una audiencia.
5. Revisa el mensaje, el remitente y el número de destinatarios; confirma el envío.
6. El panel muestra el avance y los resultados disponibles.

## Pantallas

- **Resumen:** audiencia suscrita y campañas recientes con su estado.
- **Fans:** lista, búsqueda y filtros por pista de origen y estado de suscripción.
- **Campañas:** borradores, campañas en proceso, enviadas y fallidas.
- **Editor:** contenido, audiencia, vista previa y confirmación.
- **Detalle de campaña:** resultados, destinatarios procesados y errores.
- **Configuración:** nombre del remitente, dirección de respuesta e identidad de envío verificada.

## Datos e integración

Entidades previstas: artista, contacto, pre-save, suscripción, campaña, destinatario de campaña y evento de correo. Cada registro debe pertenecer a un artista o referenciar su cuenta para limitar el acceso desde el servidor.

La integración necesita identificar el sistema actual de acceso al panel, la fuente de los eventos de pre-save, el almacenamiento disponible y el proveedor de correo. Los eventos del proveedor deben validarse y procesarse de forma idempotente. La clave por campaña y contacto debe impedir que un reintento duplique un envío.

Antes de procesar cada destinatario se debe volver a comprobar su suscripción y su estado de exclusión. Los contactos históricos sin aceptación registrada no forman parte de la audiencia enviable.

## Fuera de la primera versión

Programación de campañas, secuencias automáticas, pruebas A/B, atribución de ventas, segmentación avanzada y cobro por uso. Se pueden incorporar después de validar el flujo completo de captación y envío.

## Condiciones para considerar operativa la función

- El artista solo puede consultar y utilizar su propia audiencia.
- La suscripción es independiente del pre-save y queda registrada.
- El editor guarda borradores y permite revisar contenido y destinatarios.
- El envío utiliza un proveedor conectado y una identidad de remitente verificada.
- Los reintentos no duplican correos y las bajas impiden nuevos envíos.
- Los resultados provienen de eventos reales del proveedor.

Este documento define el alcance propuesto; todavía no constituye una integración implementada ni habilita envíos reales.
