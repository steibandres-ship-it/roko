"""Local operator fan CRM. SMTP acceptance is not delivery confirmation."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import PROJECT_ROOT
from .db import get_session
from .models import Artist, now_utc


from .fan_models import FanContact, FanCampaign, FanRecipient


def settings():
    return {**dotenv_values(PROJECT_ROOT / ".env"), **os.environ}


def mail_config():
    config = settings()
    required = ("FANS_SMTP_HOST", "FANS_SMTP_USER", "FANS_SMTP_PASSWORD", "FANS_FROM", "FANS_PUBLIC_URL")
    if not all(config.get(key) for key in required):
        raise HTTPException(503, "Configura SMTP, remitente y URL pública de bajas antes de enviar.")
    parsed = urlparse(config["FANS_PUBLIC_URL"])
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise HTTPException(503, "FANS_PUBLIC_URL debe ser una URL HTTPS pública sin query ni fragmento.")
    return config


def local_operator(request: Request):
    # Reuse the same local-only access boundary as the existing dashboard.
    from .api import _require_local_same_origin
    _require_local_same_origin(request, require_origin=request.method != "GET")


router = APIRouter()
admin = APIRouter(prefix="/api/fans", dependencies=[Depends(local_operator)], tags=["fans"])


class ContactInput(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=120)
    source: str = Field(min_length=1, max_length=240)
    consent: bool

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        import re
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
            raise ValueError("Correo inválido")
        return value


class CampaignInput(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    source: str = Field(default="", max_length=240)

    @field_validator("subject")
    @classmethod
    def valid_subject(cls, value):
        if "\r" in value or "\n" in value or not value.strip():
            raise ValueError("Asunto inválido")
        return value.strip()


def artist_exists(session, artist_id):
    if session.get(Artist, artist_id) is None:
        raise HTTPException(404, "Artista no encontrado")


def campaign_for(session, artist_id, campaign_id):
    row = session.get(FanCampaign, campaign_id)
    if row is None or row.artist_id != artist_id:
        raise HTTPException(404, "Campaña no encontrada")
    return row


@router.get("/fans", include_in_schema=False)
def page():
    return FileResponse(Path(__file__).parent / "static" / "fans.html")


@admin.get("/artists")
def artists(session: Session = Depends(get_session)):
    return [{"id": a.id, "name": a.name} for a in session.scalars(select(Artist).order_by(Artist.name))]


@admin.get("/{artist_id}")
def overview(artist_id: str, session: Session = Depends(get_session)):
    artist_exists(session, artist_id)
    contacts = list(session.scalars(select(FanContact).where(FanContact.artist_id == artist_id)))
    campaigns = list(session.scalars(select(FanCampaign).where(FanCampaign.artist_id == artist_id)))
    result = []
    for c in campaigns:
        counts = {}
        for state in session.scalars(select(FanRecipient.status).where(FanRecipient.campaign_id == c.id)):
            counts[state] = counts.get(state, 0) + 1
        result.append({"id": c.id, "subject": c.subject, "body": c.body, "source": c.source, "status": c.status, "counts": counts})
    return {"contacts": [{"id": c.id, "email": c.email, "name": c.name, "source": c.source, "status": c.status, "consent_at": c.consent_at} for c in contacts], "campaigns": result}


@admin.post("/{artist_id}/contacts")
def add_contact(artist_id: str, data: ContactInput, session: Session = Depends(get_session)):
    artist_exists(session, artist_id)
    if not data.consent:
        raise HTTPException(422, "Se necesita aceptación explícita para registrar una suscripción.")
    existing = session.scalar(select(FanContact).where(FanContact.artist_id == artist_id, FanContact.email == data.email))
    if existing:
        raise HTTPException(409, "El contacto ya existe; una baja no se reactiva al importar.")
    row = FanContact(artist_id=artist_id, email=data.email, name=data.name, source=data.source, consent_at=now_utc().isoformat())
    session.add(row)
    session.commit()
    return {"id": row.id}


@admin.post("/{artist_id}/campaigns")
def create_campaign(artist_id: str, data: CampaignInput, session: Session = Depends(get_session)):
    artist_exists(session, artist_id)
    row = FanCampaign(artist_id=artist_id, **data.model_dump())
    session.add(row)
    session.commit()
    return {"id": row.id}


@admin.post("/{artist_id}/campaigns/{campaign_id}/queue")
def queue(artist_id: str, campaign_id: str, session: Session = Depends(get_session)):
    mail_config()
    row = campaign_for(session, artist_id, campaign_id)
    claimed = session.execute(update(FanCampaign).where(FanCampaign.id == row.id, FanCampaign.status == "draft").values(status="queued"))
    if claimed.rowcount != 1:
        raise HTTPException(409, "Esta campaña ya fue confirmada.")
    query = select(FanContact).where(FanContact.artist_id == artist_id, FanContact.status == "subscribed")
    if row.source:
        query = query.where(FanContact.source == row.source)
    contacts = list(session.scalars(query))
    if not contacts:
        session.rollback()
        raise HTTPException(422, "No hay suscriptores activos en esta audiencia.")
    for contact in contacts:
        session.add(FanRecipient(campaign_id=row.id, contact_id=contact.id))
    session.commit()
    return {"queued": len(contacts)}


@admin.post("/{artist_id}/campaigns/{campaign_id}/process")
def process(artist_id: str, campaign_id: str, session: Session = Depends(get_session)):
    config = mail_config()
    campaign = campaign_for(session, artist_id, campaign_id)
    if campaign.status != "queued":
        raise HTTPException(409, "Confirma la campaña antes de procesarla.")
    ids = list(session.scalars(select(FanRecipient.id).where(FanRecipient.campaign_id == campaign_id, FanRecipient.status == "pending").limit(20)))
    for recipient_id in ids:
        claimed = session.execute(update(FanRecipient).where(FanRecipient.id == recipient_id, FanRecipient.status == "pending").values(status="sending"))
        session.commit()
        if claimed.rowcount != 1:
            continue
        recipient = session.get(FanRecipient, recipient_id)
        contact = session.get(FanContact, recipient.contact_id, populate_existing=True)
        if contact.status != "subscribed":
            recipient.status = "suppressed"
            session.commit()
            continue
        message = EmailMessage()
        message["From"] = config["FANS_FROM"]
        message["To"] = contact.email
        message["Subject"] = campaign.subject
        unsubscribe = config["FANS_PUBLIC_URL"].rstrip("/") + "/fans/unsubscribe/" + contact.token
        message["List-Unsubscribe"] = f"<{unsubscribe}>"
        message.set_content(campaign.body + "\n\nCancelar suscripción: " + unsubscribe)
        try:
            with smtplib.SMTP(config["FANS_SMTP_HOST"], int(config.get("FANS_SMTP_PORT") or 587), timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(config["FANS_SMTP_USER"], config["FANS_SMTP_PASSWORD"])
                smtp.send_message(message)
            recipient.status = "accepted"
        except Exception:
            # SMTP can accept a message before a connection fails. Never retry blindly.
            recipient.status = "uncertain"
        session.commit()
    return {"processed": len(ids), "note": "Aceptado por SMTP no significa entregado. Estados inciertos requieren revisión manual."}


@router.get("/fans/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str):
    return '<html lang="es"><meta charset="utf-8"><title>Cancelar suscripción</title><h1>Cancelar suscripción</h1><form method="post"><button>Confirmar baja de los correos del artista</button></form></html>'


@router.post("/fans/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, session: Session = Depends(get_session)):
    contact = session.scalar(select(FanContact).where(FanContact.token == token))
    if contact is None:
        raise HTTPException(404, "Enlace no válido")
    contact.status = "unsubscribed"
    session.commit()
    return '<html lang="es"><meta charset="utf-8"><h1>Suscripción cancelada</h1><p>No recibirás nuevos correos de este artista.</p></html>'


router.include_router(admin)
