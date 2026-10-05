from __future__ import annotations

from typing import Any

from ..storage import PlaylistRegistry
from .providers.base import ProviderStatus


def _equal_vector(keys: tuple[str, ...]) -> dict[str, float]:
    weight = round(1 / len(keys), 4)
    vector = {key: weight for key in keys}
    vector[keys[-1]] = round(1 - sum(vector[key] for key in keys[:-1]), 4)
    return vector


def _blueprint(
    slug: str,
    name: str,
    description: str,
    playlist_type: str,
    *,
    markets: tuple[str, ...],
    genres: dict[str, float],
    subgenres: dict[str, float],
    language: str,
    listening_angle: str,
    hub: str,
    differentiation_rule: str,
    energy: str,
    audience: str | None = None,
    objective: str | None = None,
    organic_growth_loop: str | None = None,
    cadence: str = "Revisión editorial semanal; comprobación diaria solo si una fuente autorizada entrega datos recientes.",
    measurement_plan: str = (
        "Comparar charts por la misma fuente, métrica, mercado y ventana. Streams por fuente y colocaciones, "
        "solo desde reportes autorizados de artista o distribuidor; fijar objetivos después de una línea base."
    ),
) -> dict[str, Any]:
    objective_by_type = {
        "RISING": "Desarrollar descubrimiento local con afinidad de mercado comprobada.",
        "BREAKOUT": "Detectar aceleración frente a una línea base comparable del mismo mercado.",
        "NEW_MUSIC": "Dar contexto a novedades dentro de una ventana de lanzamiento verificable.",
        "DISCOVERY": "Facilitar descubrimiento por género, escena y contexto de escucha.",
        "NEXT": "Identificar artistas tempranos con señales locales verificables.",
        "NOW": "Mantener una selección actual con datos de frescura declarados por la fuente.",
    }
    genre_labels = {
        "latin-urban": "urbano latino", "indie-rock": "indie y rock alternativo",
        "r-and-b": "R&B", "afrobeats": "Afrobeats", "brazilian": "música brasileña",
        "electronic": "electrónica", "pop": "pop", "hip-hop": "hip-hop",
        "dance": "música dance", "k-pop": "K-pop", "alternative": "música alternativa",
    }
    genre_label = genre_labels.get(next(iter(genres), ""), next(iter(genres), "música"))
    market_audience = "oyentes de los mercados objetivo" if len(markets) > 1 else f"oyentes de {markets[0]}"
    return {
        "slug": slug,
        "name": name,
        "description": description,
        "playlist_type": playlist_type,
        "markets": list(markets),
        "origin_market": markets[0] if len(markets) == 1 else None,
        "market_vector": _equal_vector(markets),
        "genre_vector": genres,
        "subgenre_vector": subgenres,
        "language": language,
        "energy": energy,
        "listening_angle": listening_angle,
        "hub": hub,
        "differentiation_rule": differentiation_rule,
        "campaign_objective": objective or objective_by_type.get(playlist_type, "Construir audiencia con encaje editorial verificable."),
        "target_audience": audience or f"{market_audience} interesados en {genre_label} y contenido en {language}.",
        "organic_growth_loop": organic_growth_loop or (
            "Compartir manualmente una pieza editorial semanal en canales propios o autorizados; invitar a artistas a difundirla solo si lo desean; revisar alcance, clics y seguidores con datos disponibles y consentidos."
        ),
        "review_cadence": cadence,
        "measurement_plan": measurement_plan,
        "lifecycle": "PLANNED",
    }


# These are editorial concepts, kept outside playlists/*.json until the
# original WORLD MUSIC OS content and evidence gates can be evaluated.
_INITIAL_PLAYLIST_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    _blueprint(
        "reggaeton-chile-rising", "Reggaetón Chile Rising",
        "Reggaetón chileno en ascenso: novedades y artistas locales para descubrir junto a voces urbanas ya conocidas.",
        "RISING", markets=("CL",), genres={"latin-urban": 1.0},
        subgenres={"reggaeton": 1.0}, language="es",
        listening_angle="Pulso de reggaetón en Chile con prioridad a crecimiento local verificable.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="Solo señales con identidad y afinidad chilena comprobadas; no duplicar automáticamente el hub.", energy="alta",
    ),
    _blueprint(
        "reggaeton-mexico-breakout", "Reggaetón México Breakout",
        "Reggaetón y urbano mexicano en ruptura: canciones con impulso comprobable y una selección que mezcla familiaridad y descubrimiento.",
        "BREAKOUT", markets=("MX",), genres={"latin-urban": 1.0},
        subgenres={"reggaeton": 0.7, "latin-trap": 0.3}, language="es",
        listening_angle="Señales de ruptura del mercado mexicano, con revisión de aceleración y frescura.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="La aceleración debe compararse dentro de México y con ventana temporal homogénea.", energy="alta",
    ),
    _blueprint(
        "reggaeton-colombia-rising", "Reggaetón Colombia Rising",
        "Reggaetón colombiano en ascenso: sonidos urbanos locales, lanzamientos recientes y descubrimientos con encaje de mercado.",
        "RISING", markets=("CO",), genres={"latin-urban": 1.0},
        subgenres={"reggaeton": 0.8, "latin-trap": 0.2}, language="es",
        listening_angle="Voces colombianas emergentes conectadas al pulso regional del reggaetón.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="Confirmar país de artista o tracción local; una sola señal regional no basta.", energy="alta",
    ),
    _blueprint(
        "reggaeton-argentina-rising", "Reggaetón Argentina Rising",
        "Reggaetón argentino en ascenso, con cruces urbanos y nuevos artistas que muestran respuesta verificable en el mercado local.",
        "RISING", markets=("AR",), genres={"latin-urban": 1.0},
        subgenres={"reggaeton": 0.65, "latin-trap": 0.35}, language="es",
        listening_angle="Escena argentina urbana emergente, separada por mercado y trayectoria.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="Mantener una identidad argentina clara y evitar que sea una copia del mix latino general.", energy="alta",
    ),
    _blueprint(
        "reggaeton-puerto-rico-rising", "Reggaetón Puerto Rico Rising",
        "Reggaetón puertorriqueño en ascenso: nuevas voces de la isla y sonidos con conexión caribeña comprobada.",
        "RISING", markets=("PR",), genres={"latin-urban": 1.0},
        subgenres={"reggaeton": 0.8, "latin-trap": 0.2}, language="es",
        listening_angle="Escena local puertorriqueña con contexto caribeño.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="Validar procedencia artística y respuesta específica de Puerto Rico antes de separar el spoke.", energy="alta",
    ),
    _blueprint(
        "reggaeton-espana-rising", "Reggaetón España Rising",
        "Reggaetón en España en ascenso: novedades urbanas con contexto local y conexiones iberoamericanas bien seleccionadas.",
        "RISING", markets=("ES",), genres={"latin-urban": 1.0},
        subgenres={"reggaeton": 0.8, "latin-trap": 0.2}, language="es",
        listening_angle="Descubrimiento de reggaetón para oyentes en España, no una lista genérica de hits latinos.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="Separar por respuesta del mercado español y conservar diversidad de procedencias.", energy="alta",
    ),
    _blueprint(
        "trap-latino-breakout", "Trap Latino Breakout",
        "Trap latino en ruptura: nuevas canciones y artistas con crecimiento validado en Latinoamérica, España y audiencias hispanas.",
        "BREAKOUT", markets=("AR", "CL", "CO", "ES", "MX", "PR", "US"),
        genres={"latin-urban": 1.0}, subgenres={"latin-trap": 1.0}, language="es",
        listening_angle="Subgénero específico con prioridad a señales de aceleración, no a popularidad acumulada.",
        hub="Latin Urban Takeover", differentiation_rule="Excluir reggaetón sin rasgos de trap verificados y comparar crecimiento entre mercados equivalentes.", energy="media-alta",
    ),
    _blueprint(
        "dembow-dominicano-rising", "Dembow Dominicano Rising",
        "Dembow dominicano en ascenso: ritmo caribeño, artistas emergentes y novedades con identidad de escena local.",
        "RISING", markets=("DO",), genres={"latin-urban": 1.0},
        subgenres={"dembow": 1.0}, language="es",
        listening_angle="Microgénero y escena dominicana con descubrimiento continuo.",
        hub="Latin Urban Takeover", differentiation_rule="Exigir etiquetado de género y vínculo dominicano; no inferirlos solo por el título.", energy="muy alta",
    ),
    _blueprint(
        "reggaeton-estrenos-30-dias", "Reggaetón · Estrenos 30 Días",
        "Estrenos de reggaetón publicados en los últimos 30 días, ordenados para descubrir novedades sin perder el hilo del género.",
        "NEW_MUSIC", markets=("AR", "CL", "CO", "ES", "MX", "PR", "US"),
        genres={"latin-urban": 1.0}, subgenres={"reggaeton": 1.0}, language="es",
        listening_angle="Ventana de novedad estricta con cadencia editorial frecuente.",
        hub="Reggaeton Worldwide 🌎", differentiation_rule="Fecha de lanzamiento verificada; retirar del ciclo cuando venza la ventana editorial.", energy="variada",
    ),
    _blueprint(
        "rnb-latino-chill", "R&B Latino Chill",
        "R&B latino suave y neo-soul en español: selección cálida para bajar revoluciones, estudiar o cerrar el día.",
        "DISCOVERY", markets=("CL", "CO", "ES", "MX", "PR", "US"),
        genres={"r-and-b": 0.7, "latin": 0.3}, subgenres={"latin-r-and-b": 0.65, "neo-soul": 0.35}, language="es",
        listening_angle="Contexto de escucha relajado y descubrimiento, distinto del R&B nocturno existente.",
        hub="R&B Latino Nights", differentiation_rule="Priorizar energía baja y timbres suaves; si coincide con la lista nocturna, no activar ambas.", energy="baja",
    ),
    _blueprint(
        "indie-argentina-discovery", "Indie Argentina Discovery",
        "Indie argentino y rock alternativo local para descubrir: guitarras, nuevas voces y catálogo de la escena emergente.",
        "DISCOVERY", markets=("AR",), genres={"indie-rock": 0.7, "alternative": 0.3},
        subgenres={"indie-en-espanol": 0.6, "alternative-rock": 0.4}, language="es",
        listening_angle="Descubrimiento local de rock e indie argentino.",
        hub="Indie Rock Worldwide", differentiation_rule="Requerir suficiente repertorio argentino distinto del catálogo de Latin Indie Rock.", energy="media",
    ),
    _blueprint(
        "indie-chile-rising", "Indie Chile Rising",
        "Indie chileno en ascenso: rock alternativo y nuevas canciones locales con tracción de mercado validada.",
        "RISING", markets=("CL",), genres={"indie-rock": 0.7, "alternative": 0.3},
        subgenres={"indie-en-espanol": 0.6, "alternative-rock": 0.4}, language="es",
        listening_angle="Escena indie chilena con prioridad a crecimiento local y novedad.",
        hub="Indie Rock Worldwide", differentiation_rule="Confirmar identidad local y solapamiento bajo frente a Latin Indie Rock antes de activarla.", energy="media",
    ),
    _blueprint(
        "afrobeats-nigeria-next", "Afrobeats Nigeria NEXT",
        "Próximas voces del Afrobeats nigeriano: descubrimientos con señales iniciales verificables y contexto de escena.",
        "NEXT", markets=("NG",), genres={"afrobeats": 1.0},
        subgenres={"afrobeats": 0.7, "afro-fusion": 0.3}, language="en",
        listening_angle="Talento en etapa temprana dentro de la escena nigeriana.",
        hub="Global Discovery", differentiation_rule="No usar popularidad global como sustituto de señales locales ni clasificar etapas sin evidencia.", energy="media-alta",
    ),
    _blueprint(
        "funk-brasileiro-discovery", "Funk Brasileiro Discovery",
        "Funk brasileiro para descubrir: batidas de la escena local, lanzamientos recientes y artistas emergentes.",
        "DISCOVERY", markets=("BR",), genres={"brazilian": 0.55, "dance": 0.45},
        subgenres={"funk-brasileiro": 1.0}, language="pt",
        listening_angle="Descubrimiento de funk brasileiro con contexto local y energía de pista.",
        hub="Global Discovery", differentiation_rule="Validar subgénero y mercado brasileño; separar funk de otros estilos dance por metadatos confiables.", energy="alta",
    ),
    _blueprint(
        "techno-alemania-now", "Techno Alemania NOW",
        "Techno actual de Alemania: lanzamientos y canciones con actividad reciente para una sesión electrónica continua.",
        "NOW", markets=("DE",), genres={"electronic": 0.6, "dance": 0.4},
        subgenres={"techno": 1.0}, language="instrumental/multi",
        listening_angle="Actualidad de techno alemán con continuidad de mezcla y frescura.",
        hub="Global Electronic", differentiation_rule="La secuencia necesita tempo y energía reales por pista; la tendencia debe venir de charts autorizados.", energy="alta",
    ),
    _blueprint(
        "worldwide-pop-pulse", "Worldwide Pop Pulse",
        "Pop internacional actual, con cruces regionales seleccionados para una escucha global coherente.",
        "NOW", markets=("AR", "BR", "DE", "ES", "FR", "GB", "ID", "JP", "KR", "MX", "US"),
        genres={"pop": 0.8, "dance": 0.2}, subgenres={"pop": 0.7, "electropop": 0.3}, language="multi",
        listening_angle="Actualidad pop en varios territorios, separando popularidad acumulada de señal reciente.",
        hub="Global Pop", differentiation_rule="Incluir solo pistas con metadatos e identidad válidos; comparar actividad por mercado y ventana equivalentes.", energy="variada",
        audience="Oyentes de pop internacional que alternan éxitos familiares y novedades de distintos mercados.",
        objective="Crear una puerta global al pop actual, preservando variedad territorial y frescura verificable.",
    ),
    _blueprint(
        "global-hip-hop-rnb-pulse", "Global Hip-Hop & R&B Pulse",
        "Hip-hop y R&B contemporáneos de distintas escenas, ordenados por afinidad sonora y contexto regional.",
        "NOW", markets=("CA", "FR", "GB", "JP", "KR", "NG", "US", "ZA"),
        genres={"hip-hop": 0.55, "r-and-b": 0.45}, subgenres={"hip-hop": 0.55, "contemporary-r-and-b": 0.45}, language="multi",
        listening_angle="Pulso internacional de hip-hop y R&B con espacio para escenas regionales.",
        hub="Global Urban", differentiation_rule="No mezclar señales de plataformas o mercados; exigir metadatos de género confiables y diversidad de artistas.", energy="media-alta",
        audience="Oyentes globales de rap y R&B que quieren descubrir escenas además de éxitos anglófonos.",
        objective="Aumentar el descubrimiento entre escenas de hip-hop y R&B con contexto claro por territorio.",
    ),
    _blueprint(
        "afrobeats-amapiano-worldwide", "Afrobeats & Amapiano Worldwide",
        "Afrobeats y amapiano con representación de escenas africanas y diásporas, sin perder sus identidades locales.",
        "DISCOVERY", markets=("GB", "GH", "KE", "NG", "ZA", "US"),
        genres={"afrobeats": 0.6, "dance": 0.4}, subgenres={"afrobeats": 0.55, "amapiano": 0.45}, language="multi",
        listening_angle="Descubrimiento transregional de Afrobeats y amapiano con procedencia de escena visible.",
        hub="Global Discovery", differentiation_rule="Verificar subgénero y vínculo de escena; no inferir origen por idioma, título o popularidad global.", energy="media-alta",
        audience="Oyentes de Afrobeats y amapiano en África y mercados de diáspora.",
        objective="Conectar escenas y descubrimientos de Afrobeats/amapiano sin borrar sus diferencias regionales.",
    ),
    _blueprint(
        "global-dancefloor-pulse", "Global Dancefloor Pulse",
        "Dance y electrónica para la pista, con secuencia editorial por energía y continuidad de escucha.",
        "NOW", markets=("AU", "DE", "ES", "FR", "GB", "NL", "US"),
        genres={"dance": 0.6, "electronic": 0.4}, subgenres={"house": 0.4, "dance-pop": 0.35, "techno": 0.25}, language="multi",
        listening_angle="Novedades dance internacionales con una curva de energía pensada para escucha continua.",
        hub="Global Electronic", differentiation_rule="Confirmar subgénero, tempo y energía con datos de pista permitidos; no inferir mezcla o compatibilidad solo por charts.", energy="alta",
        audience="Oyentes globales de dance y electrónica que buscan una secuencia apta para entrenar, salir o mantener energía.",
        objective="Ofrecer una selección dance reciente que sostenga continuidad sonora y descubrimiento internacional.",
    ),
    _blueprint(
        "k-pop-asian-pop-worldwide", "K-Pop & Asian Pop Worldwide",
        "K-pop y pop de Asia oriental para audiencias globales, con espacio para novedades y cruces de mercado.",
        "DISCOVERY", markets=("AU", "BR", "GB", "ID", "JP", "KR", "MX", "US"),
        genres={"pop": 0.75, "dance": 0.25}, subgenres={"k-pop": 0.65, "j-pop": 0.2, "asian-pop": 0.15}, language="multi",
        listening_angle="Pop asiático internacional con identidad de escena y señales por mercado.",
        hub="Global Pop", differentiation_rule="Verificar artista, versión y créditos; distinguir lanzamientos y mercados sin duplicar versiones de una misma pista.", energy="variada",
        audience="Oyentes internacionales de K-pop y pop asiático, tanto seguidores de larga data como nuevos oyentes.",
        objective="Facilitar el acceso a pop asiático actual y descubrimientos regionales con metadatos correctos.",
    ),
)


# Each scene receives four genuinely different editorial jobs. The shared
# templates keep the portfolio consistent; evidence, metadata, and overlap
# gates decide which concepts can ever become a playlist.
_ANGLE_RECIPES: tuple[dict[str, str], ...] = (
    {
        "type": "RISING",
        "suffix": "rising",
        "label": "Rising",
        "description": "Lectura de crecimiento dentro del mercado objetivo, con contexto de escena y espacio para artistas en desarrollo.",
        "angle": "Priorizar aceleración reciente y persistencia frente a capturas anteriores comparables del mismo mercado.",
        "rule": "Requiere al menos dos capturas recientes comparables de la misma fuente, mercado y métrica; una posición aislada no basta.",
        "objective": "Convertir señales locales sostenidas en descubrimiento editorial para oyentes de esta escena.",
        "growth": "Cada semana, compartir manualmente una selección local con una explicación breve en canales propios; pedir difusión a artistas solo con su consentimiento y revisar clics y seguidores con analítica disponible.",
    },
    {
        "type": "BREAKOUT",
        "suffix": "breakout",
        "label": "Breakout",
        "description": "Selección de posibles rupturas que separa una aceleración reciente de la popularidad acumulada.",
        "angle": "Buscar aceleración que persiste en más de una captura, sin comparar territorios o ventanas incompatibles.",
        "rule": "Exige crecimiento o mejora de puesto comparable y persistencia observada; las señales incompletas permanecen fuera del ranking.",
        "objective": "Dar contexto a canciones en aceleración y llevar oyentes afines hacia descubrimientos verificables.",
        "growth": "Publicar manualmente una historia editorial sobre una selección y su contexto de escena; usar un enlace directo y revisar resultados antes de ampliar la rotación.",
    },
    {
        "type": "NEW_MUSIC",
        "suffix": "estrenos-30-dias",
        "label": "Estrenos · 30 días",
        "description": "Ventana de novedades con fecha de publicación comprobada y recambio cuando cumple 30 días.",
        "angle": "Mantener una puerta de entrada clara para lanzamientos recientes sin presentarlos como éxitos o tendencias por defecto.",
        "rule": "La fecha de lanzamiento debe estar verificada; retirar la pista al vencer la ventana y confirmar identidad de versión y artista.",
        "objective": "Hacer descubribles las novedades de esta escena con una rutina editorial frecuente.",
        "growth": "Preparar manualmente una selección semanal de estrenos con notas editoriales; ofrecer a cada artista una invitación opcional para compartir el enlace.",
    },
    {
        "type": "DISCOVERY",
        "suffix": "discovery",
        "label": "Discovery",
        "description": "Ángulo de descubrimiento con foco en encaje de género, diversidad de artistas y señales autorizadas.",
        "angle": "Equilibrar familiaridad y descubrimiento sin confundir poca popularidad con calidad ni inventar etapas artísticas.",
        "rule": "Exige identidad canónica, metadatos de género y mercado con confianza suficiente; comprobar solapamiento antes de activarla.",
        "objective": "Ayudar a oyentes interesados en esta escena a encontrar artistas y canciones nuevas con encaje comprobable.",
        "growth": "Compartir manualmente una selección editorial curada en comunidades y canales propios pertinentes; mantener una invitación clara a escuchar y seguir, sin automatizar interacciones.",
    },
)


# Broad, distinct scene families extend the original 20 concepts to exactly
# 100. Vectors describe editorial intent, never measured audience shares.
_EXPANSION_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "slug": "corridos-tumbados-mexico", "name": "Corridos Tumbados México",
        "scene": "corridos tumbados mexicanos", "markets": ("MX", "US"),
        "genres": {"latin-urban": 0.55, "latin": 0.45}, "subgenres": {"corridos-tumbados": 0.7, "regional-mexicano": 0.3},
        "language": "es", "hub": "Latin Roots Worldwide", "energy": "media-alta",
        "audience": "Oyentes de corridos tumbados en México y comunidades mexicanas en Estados Unidos.",
        "identity_rule": "Verificar subgénero, versión y afinidad con México; no inferir procedencia a partir del título.",
    },
    {
        "slug": "salsa-colombia", "name": "Salsa Colombia",
        "scene": "salsa colombiana y su circuito regional", "markets": ("CO", "PR", "US"),
        "genres": {"latin": 0.65, "dance": 0.35}, "subgenres": {"salsa": 0.85, "latin-dance": 0.15},
        "language": "es", "hub": "Latin Roots Worldwide", "energy": "alta",
        "audience": "Oyentes de salsa en Colombia, el Caribe y comunidades latinas de Estados Unidos.",
        "identity_rule": "Separar salsa de otros ritmos tropicales mediante metadatos de género fiables y respuesta por mercado.",
    },
    {
        "slug": "bachata-dominicana", "name": "Bachata Dominicana",
        "scene": "bachata dominicana y su diáspora", "markets": ("DO", "US", "ES"),
        "genres": {"latin": 0.65, "world": 0.35}, "subgenres": {"bachata": 1.0},
        "language": "es", "hub": "Latin Roots Worldwide", "energy": "media",
        "audience": "Oyentes de bachata en República Dominicana, España y comunidades dominicanas en Estados Unidos.",
        "identity_rule": "Confirmar bachata por metadatos y contexto de escena; no mezclar con pop latino solo por idioma.",
    },
    {
        "slug": "cumbia-cono-sur", "name": "Cumbia Cono Sur",
        "scene": "cumbia del Cono Sur y escenas conectadas", "markets": ("AR", "CL", "PE"),
        "genres": {"latin": 0.65, "dance": 0.35}, "subgenres": {"cumbia": 0.8, "cumbia-andina": 0.2},
        "language": "es", "hub": "Latin Roots Worldwide", "energy": "alta",
        "audience": "Oyentes de cumbia en Argentina, Chile y Perú.",
        "identity_rule": "Conservar los subestilos y la procedencia de cada escena; medir solapamiento frente a hubs latinos.",
    },
    {
        "slug": "pop-mexicano", "name": "Pop Mexicano Actual",
        "scene": "pop mexicano contemporáneo", "markets": ("MX", "US"),
        "genres": {"pop": 0.7, "latin": 0.3}, "subgenres": {"latin-pop": 0.8, "spanish-pop": 0.2},
        "language": "es", "hub": "Global Pop", "energy": "variada",
        "audience": "Oyentes de pop en México y comunidades hispanohablantes de Estados Unidos.",
        "identity_rule": "Usar señales de México para el ángulo local y evitar replicar sin criterio el hub de pop global.",
    },
    {
        "slug": "latin-alternative-mx", "name": "Latin Alternative: Nuevas Voces",
        "scene": "alternativa latina entre México, los Andes y España", "markets": ("MX", "CO", "CL", "ES"),
        "genres": {"alternative": 0.6, "latin": 0.4}, "subgenres": {"latin-alternative": 0.6, "indie-en-espanol": 0.4},
        "language": "es", "hub": "Latin Alternative", "energy": "media",
        "audience": "Oyentes de música alternativa en español que buscan cruces entre escenas latinoamericanas y España.",
        "identity_rule": "Exigir encaje alternativo verificable y diversidad de artistas; comparar el solapamiento con las listas indie actuales.",
    },
    {
        "slug": "rap-chile", "name": "Rap Chileno",
        "scene": "rap y hip-hop de Chile", "markets": ("CL",),
        "genres": {"hip-hop": 0.75, "latin-urban": 0.25}, "subgenres": {"latin-rap": 1.0},
        "language": "es", "hub": "Latin Urban Takeover", "energy": "media-alta",
        "audience": "Oyentes de rap chileno y público interesado en hip-hop en español.",
        "identity_rule": "Exigir país del artista o respuesta local verificable y evitar duplicar el urbano latino general.",
    },
    {
        "slug": "dembow-caribe", "name": "Dembow Caribe",
        "scene": "dembow dominicano y caribeño", "markets": ("DO", "PR", "US"),
        "genres": {"latin-urban": 1.0}, "subgenres": {"dembow": 0.75, "reggaeton": 0.25},
        "language": "es", "hub": "Latin Urban Takeover", "energy": "muy alta",
        "audience": "Oyentes de dembow en el Caribe y comunidades caribeñas de Estados Unidos.",
        "identity_rule": "Confirmar dembow y mercado con metadatos; separar el catálogo de la propuesta dominicana ya planificada.",
    },
    {
        "slug": "hip-hop-underground-us", "name": "Hip-Hop Underground: Estados Unidos",
        "scene": "hip-hop independiente y underground estadounidense", "markets": ("US", "CA"),
        "genres": {"hip-hop": 1.0}, "subgenres": {"underground-rap": 0.65, "alternative-hip-hop": 0.35},
        "language": "en", "hub": "Global Urban", "energy": "variada",
        "audience": "Oyentes de hip-hop independiente en Estados Unidos y Canadá.",
        "identity_rule": "No usar baja popularidad como sustituto de calidad; exigir metadatos, variedad de artistas y evidencia elegible.",
    },
    {
        "slug": "rap-francais", "name": "Rap Français: Scène en Mouvement",
        "scene": "rap francófono de Francia y países vecinos", "markets": ("FR", "BE", "CH"),
        "genres": {"hip-hop": 0.8, "pop": 0.2}, "subgenres": {"french-rap": 1.0},
        "language": "fr", "hub": "Global Urban", "energy": "media-alta",
        "audience": "Oyentes de rap francófono en Francia, Bélgica y Suiza.",
        "identity_rule": "Verificar idioma, escena y mercado; no extrapolar una señal francesa a todos los territorios francófonos.",
    },
    {
        "slug": "neo-soul-rnb", "name": "Neo-Soul & R&B Select",
        "scene": "neo-soul y R&B contemporáneo", "markets": ("US", "GB", "CA"),
        "genres": {"r-and-b": 0.85, "soul": 0.15}, "subgenres": {"neo-soul": 0.65, "contemporary-r-and-b": 0.35},
        "language": "en", "hub": "Global R&B", "energy": "baja",
        "audience": "Oyentes de R&B contemporáneo y neo-soul en Norteamérica y Reino Unido.",
        "identity_rule": "Priorizar etiquetas de género fiables y revisión sonora humana; medir distancia frente a R&B Latino Nights.",
    },
    {
        "slug": "afrobeats-ghana", "name": "Afrobeats Ghana",
        "scene": "Afrobeats ghanés y conexiones de África occidental", "markets": ("GH", "NG", "GB"),
        "genres": {"afrobeats": 1.0}, "subgenres": {"afrobeats": 0.75, "afro-fusion": 0.25},
        "language": "en", "hub": "Global Discovery", "energy": "media-alta",
        "audience": "Oyentes de Afrobeats ghanés en Ghana, Nigeria y comunidades de Reino Unido.",
        "identity_rule": "Confirmar escena y territorio con datos de artista; el idioma no sirve por sí solo para inferir procedencia.",
    },
    {
        "slug": "amapiano-south-africa", "name": "Amapiano South Africa",
        "scene": "amapiano sudafricano y su circuito de diáspora", "markets": ("ZA", "GB"),
        "genres": {"dance": 0.65, "afrobeats": 0.35}, "subgenres": {"amapiano": 1.0},
        "language": "multi", "hub": "Global Electronic", "energy": "media-alta",
        "audience": "Oyentes de amapiano en Sudáfrica y comunidades de diáspora en Reino Unido.",
        "identity_rule": "Verificar subgénero y respuesta por mercado; mantener amapiano separado de Afrobeats cuando los datos lo permitan.",
    },
    {
        "slug": "afro-house-diaspora", "name": "Afro House: África y Diáspora",
        "scene": "afro house en África austral y mercados de diáspora", "markets": ("ZA", "AO", "PT", "GB"),
        "genres": {"dance": 0.65, "electronic": 0.35}, "subgenres": {"afro-house": 1.0},
        "language": "multi", "hub": "Global Electronic", "energy": "alta",
        "audience": "Oyentes de afro house en África austral, Portugal y Reino Unido.",
        "identity_rule": "Exigir metadatos de afro house y comprobar procedencia; no inferirla por el idioma o el nombre del artista.",
    },
    {
        "slug": "sertanejo-brasil", "name": "Sertanejo Brasil",
        "scene": "sertanejo contemporáneo de Brasil", "markets": ("BR",),
        "genres": {"brazilian": 0.8, "country": 0.2}, "subgenres": {"sertanejo": 1.0},
        "language": "pt", "hub": "Global Discovery", "energy": "variada",
        "audience": "Oyentes de sertanejo en Brasil.",
        "identity_rule": "Usar metadatos del subgénero y del mercado brasileño; no mezclarlo con country anglófono por similitud superficial.",
    },
    {
        "slug": "mpb-moderna", "name": "MPB Moderna",
        "scene": "MPB y pop brasileño contemporáneo", "markets": ("BR", "PT"),
        "genres": {"brazilian": 0.85, "pop": 0.15}, "subgenres": {"mpb": 0.7, "brazilian-pop": 0.3},
        "language": "pt", "hub": "Global Discovery", "energy": "media",
        "audience": "Oyentes de MPB contemporánea en Brasil y Portugal.",
        "identity_rule": "Separar MPB de pop brasileño amplio solo cuando las etiquetas y la selección audible lo sostengan.",
    },
    {
        "slug": "j-pop-japan", "name": "J-Pop Japan Discovery",
        "scene": "J-pop de Japón y cruces de Asia oriental", "markets": ("JP", "KR", "US"),
        "genres": {"pop": 0.8, "dance": 0.2}, "subgenres": {"j-pop": 0.8, "city-pop": 0.2},
        "language": "ja", "hub": "Global Pop", "energy": "variada",
        "audience": "Oyentes internacionales de J-pop y pop japonés contemporáneo.",
        "identity_rule": "Verificar artista, idioma y versión; no duplicar versiones alternativas de una pista.",
    },
    {
        "slug": "k-pop-korea", "name": "K-Pop Korea: Local Signals",
        "scene": "K-pop de Corea del Sur con respuesta en mercados cercanos", "markets": ("KR", "JP", "US"),
        "genres": {"pop": 0.7, "dance": 0.3}, "subgenres": {"k-pop": 1.0},
        "language": "ko", "hub": "Global Pop", "energy": "variada",
        "audience": "Oyentes de K-pop en Corea del Sur, Japón y Estados Unidos.",
        "identity_rule": "Separar la señal local coreana de la popularidad global y verificar versión y créditos.",
    },
    {
        "slug": "punjabi-pop", "name": "Punjabi Pop: South Asia & Diaspora",
        "scene": "pop punjabi y circuitos de diáspora", "markets": ("IN", "GB", "CA", "US"),
        "genres": {"pop": 0.6, "world": 0.4}, "subgenres": {"punjabi-pop": 1.0},
        "language": "pa", "hub": "Global Pop", "energy": "variada",
        "audience": "Oyentes de pop punjabi en el sur de Asia, Canadá, Reino Unido y Estados Unidos.",
        "identity_rule": "Confirmar idioma, escena y mercados; mantener diversidad de artistas y evitar versiones duplicadas.",
    },
    {
        "slug": "uk-garage-bass", "name": "UK Garage & Bass",
        "scene": "UK garage y bass británico", "markets": ("GB", "NL", "DE"),
        "genres": {"electronic": 0.55, "dance": 0.45}, "subgenres": {"uk-garage": 0.65, "drum-and-bass": 0.35},
        "language": "en", "hub": "Global Electronic", "energy": "alta",
        "audience": "Oyentes de garage y bass en Reino Unido, Países Bajos y Alemania.",
        "identity_rule": "Separar los subgéneros mediante etiquetas fiables y validar energía o continuidad con revisión sonora.",
    },
)


_EXPANSION_PHASES = (
    "Fase 4 · Raíces latinas",
    "Fase 5 · Urbano e independiente",
    "Fase 6 · R&B, África y Brasil",
    "Fase 7 · Asia, India y Europa",
)


def _expanded_blueprints() -> tuple[dict[str, Any], ...]:
    items: list[dict[str, Any]] = []
    for family_index, family in enumerate(_EXPANSION_FAMILIES):
        phase = 4 + family_index // 5
        wave = _EXPANSION_PHASES[phase - 4]
        for recipe in _ANGLE_RECIPES:
            scene = str(family["scene"])
            label = str(recipe["label"])
            items.append(_blueprint(
                f"{family['slug']}-{recipe['suffix']}",
                f"{family['name']} · {label}",
                f"{recipe['description']} Escena objetivo: {scene}.",
                recipe["type"],
                markets=tuple(family["markets"]),
                genres=dict(family["genres"]),
                subgenres=dict(family["subgenres"]),
                language=str(family["language"]),
                listening_angle=f"{label}: {recipe['angle']} Escena: {scene}.",
                hub=str(family["hub"]),
                differentiation_rule=f"{family['identity_rule']} {recipe['rule']}",
                energy=str(family["energy"]),
                audience=str(family["audience"]),
                objective=f"{recipe['objective']} Enfoque: {scene}.",
                organic_growth_loop=str(recipe["growth"]),
                cadence=("Revisión editorial semanal; verificar frescura antes de cada cambio. Las publicaciones promocionales son manuales y opcionales."),
                measurement_plan=("Registrar línea base y cambios observados solo con analítica autorizada; separar alcance, clics y seguidores, y dejar campos sin dato cuando la fuente no los entregue."),
            ) | {"portfolio_phase": phase, "portfolio_wave": wave})
    return tuple(items)


_EXPANDED_BLUEPRINTS = _expanded_blueprints()
PLAYLIST_BLUEPRINTS: tuple[dict[str, Any], ...] = (*_INITIAL_PLAYLIST_BLUEPRINTS, *_EXPANDED_BLUEPRINTS)
if len(PLAYLIST_BLUEPRINTS) != 100 or len({item["slug"] for item in PLAYLIST_BLUEPRINTS}) != 100:
    raise RuntimeError("The editorial portfolio must contain exactly 100 unique playlist concepts")


# Editorial activation order: validate focused scenes first, expand into
# cross-market formats next, and build global hubs after evidence is stable.
# This is an operating sequence, not a popularity or trend ranking.
PORTFOLIO_SEQUENCE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Fase 1 · Escenas locales", (
        "reggaeton-chile-rising",
        "reggaeton-colombia-rising",
        "reggaeton-argentina-rising",
        "reggaeton-puerto-rico-rising",
        "reggaeton-espana-rising",
        "dembow-dominicano-rising",
        "indie-chile-rising",
        "indie-argentina-discovery",
        "funk-brasileiro-discovery",
        "afrobeats-nigeria-next",
        "techno-alemania-now",
    )),
    ("Fase 2 · Formatos regionales", (
        "rnb-latino-chill",
        "reggaeton-estrenos-30-dias",
        "trap-latino-breakout",
        "reggaeton-mexico-breakout",
    )),
    ("Fase 3 · Hubs globales", (
        "afrobeats-amapiano-worldwide",
        "k-pop-asian-pop-worldwide",
        "global-hip-hop-rnb-pulse",
        "global-dancefloor-pulse",
        "worldwide-pop-pulse",
    )),
)
_PORTFOLIO_ORDER: dict[str, dict[str, Any]] = {}
_portfolio_order = 0
for _phase, (_wave_name, _slugs) in enumerate(PORTFOLIO_SEQUENCE, start=1):
    for _slug in _slugs:
        _portfolio_order += 1
        _PORTFOLIO_ORDER[_slug] = {
            "portfolio_order": _portfolio_order,
            "portfolio_phase": _phase,
            "portfolio_wave": _wave_name,
        }
for _blueprint_item in _EXPANDED_BLUEPRINTS:
    _portfolio_order += 1
    _PORTFOLIO_ORDER[_blueprint_item["slug"]] = {
        "portfolio_order": _portfolio_order,
        "portfolio_phase": _blueprint_item["portfolio_phase"],
        "portfolio_wave": _blueprint_item["portfolio_wave"],
    }


def ordered_playlist_blueprints() -> tuple[dict[str, Any], ...]:
    return tuple(
        {**item, **_PORTFOLIO_ORDER[item["slug"]]}
        for item in sorted(
            PLAYLIST_BLUEPRINTS,
            key=lambda item: _PORTFOLIO_ORDER[item["slug"]]["portfolio_order"],
        )
    )


def playlist_blueprint_registry_view(
    provider_statuses: list[ProviderStatus],
    *,
    existing_playlist_count: int,
    catalog_track_count: int,
    catalog_artist_count: int,
    launch_plans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Expose source-backed launch readiness; this route never writes to Spotify."""
    usable_trend_sources = {
        status.provider_name
        for status in provider_statuses
        if status.provider_name in {"Soundcharts", "Chartmetric"}
        and status.state in {"CONNECTED", "DEGRADED"}
    }
    shared_blockers: list[str] = []
    if not usable_trend_sources:
        shared_blockers.append("authorized_trend_provider_not_connected")
    if catalog_track_count == 0 or catalog_artist_count == 0:
        shared_blockers.append("candidate_track_artist_catalog_empty")
    if launch_plans is None:
        shared_blockers.extend([
            "playlist_quality_and_artist_diversity_not_evaluated",
            "distinctiveness_against_existing_playlists_not_evaluated",
            "track_level_data_confidence_not_evaluated",
        ])
    plan_by_slug = {item["slug"]: item for item in launch_plans or []}
    registry = PlaylistRegistry()
    active_count = sum(
        (registry.get(item["slug"]) or {}).get("launch_state") == "ACTIVE"
        for item in PLAYLIST_BLUEPRINTS
    )
    ordered_blueprints = ordered_playlist_blueprints()
    return {
        "existing_playlist_count": existing_playlist_count,
        "concept_count": len(PLAYLIST_BLUEPRINTS),
        "activated_concept_count": active_count,
        "ready_concept_count": sum(item.get("status") == "READY_TO_LAUNCH" for item in launch_plans or []),
        "status": "ACTIVE" if active_count == len(PLAYLIST_BLUEPRINTS) else "PARTIALLY_ACTIVE" if active_count else "PLANNED_REVIEW_ONLY",
        "provider_basis": sorted(usable_trend_sources),
        "candidate_catalog": {
            "tracks": catalog_track_count,
            "artists": catalog_artist_count,
        },
        "shared_blockers": shared_blockers,
        "launch_policy": {
            "max_new_playlists_per_review": 10,
            "sequence": "Editorial batches; review source quality, audience response and playlist overlap before opening the next batch.",
            "organic_growth": "Manual sharing through owned or authorized channels; no automated posts, playback, follows or guaranteed placement.",
            "spotify_writes_enabled": False,
        },
        "creation_gate": [
            "minimum_tracks_and_unique_artists",
            "editorial_quality_and_market_fit",
            "distinctiveness_from_existing_lists",
            "source_rights_freshness_coverage_and_confidence",
        ],
        "items": [
            {
                **blueprint,
                "activation_status": "ACTIVE" if (registry.get(blueprint["slug"]) or {}).get("launch_state") == "ACTIVE" else plan_by_slug.get(blueprint["slug"], {}).get("status", "BLOCKED_PENDING_EVIDENCE"),
                "spotify_url": (registry.get(blueprint["slug"]) or {}).get("spotify_url"),
                "candidate_count": plan_by_slug.get(blueprint["slug"], {}).get("candidate_count", 0),
                "unique_artist_count": plan_by_slug.get(blueprint["slug"], {}).get("unique_artist_count", 0),
                "mean_score_confidence": plan_by_slug.get(blueprint["slug"], {}).get("mean_score_confidence", 0),
                "highest_overlap": plan_by_slug.get(blueprint["slug"], {}).get("highest_overlap"),
                "highest_concept_overlap": plan_by_slug.get(blueprint["slug"], {}).get("highest_concept_overlap"),
                "source_providers": plan_by_slug.get(blueprint["slug"], {}).get("source_providers", []),
                "blockers": [] if (registry.get(blueprint["slug"]) or {}).get("launch_state") == "ACTIVE" else list(dict.fromkeys([*shared_blockers, *plan_by_slug.get(blueprint["slug"], {}).get("blockers", [])])),
            }
            for blueprint in ordered_blueprints
        ],
    }
