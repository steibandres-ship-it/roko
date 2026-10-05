from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PlaylistConfig
from ..playlists import load_playlists
from .models import OrganicCampaignReport
from .providers.base import ProviderStatus
from .public_playlists import public_playlist_registry_view


def _market_targets(playlist: PlaylistConfig) -> list[dict[str, Any]]:
    profile = playlist.world_music
    weights = (profile.market_vector if profile else {}) or {}
    if not weights:
        weights = {market: 1.0 for market in playlist.markets}
    return [
        {"market_code": market, "configured_weight": round(float(weight), 4)}
        for market, weight in sorted(weights.items(), key=lambda entry: (-entry[1], entry[0]))[:5]
    ]


def campaign_report_view(report: OrganicCampaignReport) -> dict[str, Any]:
    followers_delta = None
    if report.followers_start is not None and report.followers_end is not None:
        followers_delta = report.followers_end - report.followers_start
    click_rate = None
    if report.views and report.link_clicks is not None:
        click_rate = round(report.link_clicks / report.views * 100, 2)
    return {
        "id": report.id,
        "channel": report.channel,
        "source_name": report.source_name,
        "followers_source": report.followers_source,
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "views": report.views,
        "reach": report.reach,
        "link_clicks": report.link_clicks,
        "followers_start": report.followers_start,
        "followers_end": report.followers_end,
        "followers_delta": followers_delta,
        "link_click_rate_percent": click_rate,
        "notes": report.notes,
        "recorded_at": report.created_at.isoformat(),
    }


def _pipeline(provider_ready: bool, destination_ready: bool, report_ready: bool) -> list[dict[str, str]]:
    signal_state = "WAITING_FOR_AUTHORIZED_SIGNALS" if provider_ready else "BLOCKED_NO_AUTHORIZED_PROVIDER"
    evidence_state = "WAITING_FOR_QUALIFYING_EVIDENCE" if provider_ready else "BLOCKED_NO_AUTHORIZED_PROVIDER"
    return [
        {
            "stage": "DETECCIÓN",
            "state": signal_state,
            "detail": "Requiere una captura vigente de un proveedor conectado con derechos de uso confirmados.",
        },
        {
            "stage": "VALIDACIÓN",
            "state": evidence_state,
            "detail": "Verificar identidad, encaje de género y mercado, frescura, cobertura y confianza.",
        },
        {
            "stage": "COMPOSICIÓN",
            "state": "REVISIÓN_EDITORIAL",
            "detail": "Usar el borrador local de playlist y escuchar las incorporaciones antes de aprobarlas.",
        },
        {
            "stage": "PROMOCIÓN / DEGRADACIÓN",
            "state": "REQUIERE_EVIDENCIA_Y_APROBACIÓN",
            "detail": "No mover canciones de posición sin historial comparable y decisión humana.",
        },
        {
            "stage": "PROPAGACIÓN",
            "state": "OBJETIVOS_PLANIFICADOS",
            "detail": "Los mercados provienen del perfil editorial; no representan rutas observadas ni causalidad.",
        },
        {
            "stage": "DESTINO",
            "state": "COMPARTIR_MANUALMENTE" if destination_ready else "ENLACE_NO_REGISTRADO",
            "detail": "La difusión orgánica requiere compartir manualmente el enlace en canales propios o autorizados.",
        },
        {
            "stage": "FEEDBACK",
            "state": "MÉTRICAS_REGISTRADAS" if report_ready else "SIN_MÉTRICAS_DE_AUDIENCIA",
            "detail": "Cifras registradas manualmente con fuente y periodo." if report_ready else (
                "Registrar resultados de analítica nativa del canal y el conteo visible de seguidores al inicio y cierre."
            ),
        },
    ]


def campaign_registry_view(provider_statuses: list[ProviderStatus], session: Session | None = None) -> dict[str, Any]:
    """Build one non-publishing organic campaign plan for each public playlist."""
    playlist_network = public_playlist_registry_view(provider_statuses)
    public_items = {item["slug"]: item for item in playlist_network["items"]}
    configs = [playlist for playlist in load_playlists() if playlist.public]
    provider_ready = any(status.state in {"CONNECTED", "DEGRADED"} for status in provider_statuses)
    items: list[dict[str, Any]] = []

    for playlist in configs:
        public_item = public_items.get(playlist.slug, {})
        dna = playlist.world_music
        url = public_item.get("spotify_url")
        reports = []
        if session is not None:
            reports = list(
                session.scalars(
                    select(OrganicCampaignReport)
                    .where(OrganicCampaignReport.playlist_slug == playlist.slug)
                    .order_by(OrganicCampaignReport.period_end.desc(), OrganicCampaignReport.created_at.desc())
                    .limit(8)
                ).all()
            )
        latest_report = campaign_report_view(reports[0]) if reports else None
        market_targets = _market_targets(playlist)
        description = playlist.description.strip()
        post_copy = f"{playlist.name} — {description} Escúchala y síguela en Spotify: {url}" if url else (
            f"{playlist.name} — {description} Enlace de Spotify pendiente de registrar."
        )
        measured = latest_report or {}
        views = measured.get("views")
        reach = measured.get("reach")
        clicks = measured.get("link_clicks")
        follower_delta = measured.get("followers_delta")
        rate = measured.get("link_click_rate_percent")
        source_label = (
            f"Fuente: {measured.get('source_name')} · {measured.get('period_start')}–{measured.get('period_end')}"
            if latest_report
            else "Registra una lectura manual para ver el dato y su fuente."
        )
        items.append(
            {
                "campaign_id": f"organic-{playlist.slug}",
                "campaign_name": f"Descubrimiento orgánico · {playlist.name}",
                "status": "PLAN_REVISABLE",
                "playlist_slug": playlist.slug,
                "playlist_name": playlist.name,
                "playlist_type": dna.playlist_type if dna else "UNCONFIGURED",
                "objective": "Aumentar el descubrimiento orgánico hacia la playlist; definir metas numéricas después de capturar una línea base verificable.",
                "audience": {
                    "genres": list((dna.genre_vector if dna else {}).keys()),
                    "subgenres": list((dna.subgenre_vector if dna else {}).keys()),
                    "listening_context": description,
                },
                "market_targets": market_targets,
                "market_target_basis": "Pesos de mercado configurados en el DNA editorial; no son estimaciones de audiencia.",
                "pipeline": _pipeline(provider_ready, bool(url), bool(latest_report)),
                "seven_day_playbook": [
                    {
                        "day": 1,
                        "action": "Publicar un video vertical corto con imagen propia, una idea clara sobre la escena y un llamado directo a abrir o seguir la playlist.",
                        "channel": "Reels / TikTok / Shorts · publicación manual",
                    },
                    {
                        "day": 2,
                        "action": "Hacer una pregunta o encuesta breve sobre el género para invitar conversación real y aprender qué subángulo interesa.",
                        "channel": "Historias o comunidad propia",
                    },
                    {
                        "day": 5,
                        "action": "Presentar una canción de la lista con contexto editorial y elegir una vía: invitar al artista a un repost opcional con autorización, o recomendarla en una comunidad que permita compartir playlists.",
                        "channel": "Canal propio o del artista · con autorización",
                    },
                    {
                        "day": 7,
                        "action": "Registrar vistas, alcance, clics y seguidores al inicio y al cierre. Repetir el formato con mejor tasa de clics si la respuesta fue real y relevante.",
                        "channel": "Revisión de resultados",
                    },
                ],
                "content_angles": [
                    f"¿Qué hace distinta a {playlist.name}? Presentar su escena y ocasión de escucha.",
                    "Una recomendación editorial breve con una canción incluida y el motivo de su encaje.",
                    "Una pregunta a la audiencia sobre el próximo subgénero o país que quiere descubrir.",
                ],
                "share_copy": post_copy,
                "destination": {"name": "Spotify", "url": url, "publication": "MANUAL_ONLY" if url else "NOT_REGISTERED"},
                "measurement": [
                    {"metric": "vistas / impresiones del contenido", "value": views, "state": "CAPTURED" if views is not None else "NOT_CAPTURED", "source_needed": source_label if views is not None else "Analítica nativa del canal; no son vistas de Spotify"},
                    {"metric": "alcance orgánico", "value": reach, "state": "CAPTURED" if reach is not None else "NOT_CAPTURED", "source_needed": source_label if reach is not None else "Analítica nativa del canal"},
                    {"metric": "clics al enlace Spotify", "value": clicks, "state": "CAPTURED" if clicks is not None else "NOT_CAPTURED", "source_needed": source_label if clicks is not None else "Analítica nativa del canal o del enlace"},
                    {"metric": "clics / vistas", "value": f"{rate:.2f}%" if rate is not None else None, "state": "CAPTURED" if rate is not None else "NOT_CAPTURED", "source_needed": "Clics divididos por vistas/impresiones registradas en el mismo periodo"},
                    {"metric": "cambio neto de seguidores", "value": f"{follower_delta:+,}" if follower_delta is not None else None, "state": "CAPTURED" if follower_delta is not None else "NOT_CAPTURED", "source_needed": f"Conteos manuales de Spotify · cierre {measured.get('followers_end')} · {measured.get('period_start')}–{measured.get('period_end')}" if follower_delta is not None else "Conteo visible en Spotify al inicio y al cierre; variación de la playlist, sin atribución causal"},
                ],
                "latest_report": latest_report,
                "recent_reports": [campaign_report_view(report) for report in reports],
                "blockers": ([] if latest_report else ["Registra el primer periodo para crear una línea base verificable."]) + [
                    "La analítica de vistas y alcance viene de cada red; los clics son una señal de visita, no una reproducción confirmada en Spotify.",
                    "La publicación y las colaboraciones se realizan manualmente desde canales propios o autorizados.",
                ],
                "automation_enabled": False,
                "spotify_writes_enabled": False,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "campaign_count": len(items),
        "plan_state": "PLANS_READY_EVIDENCE_REQUIRED",
        "provider_state": playlist_network["data_state"],
        "execution_enabled": False,
        "spotify_writes_enabled": False,
        "method": "Planes orgánicos manuales de siete días. Los reportes de vistas, alcance y clics se ingresan desde analítica nativa; los cambios de seguidores se registran desde los conteos observados en Spotify. Los datos no disponibles quedan vacíos.",
        "items": items,
    }
