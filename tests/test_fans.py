from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import pytest
from app.world_music.api import app
from app.world_music.db import get_session
from app.world_music.fans import FanContact, FanCampaign, FanRecipient
from app.world_music.models import Base, Artist, IngestionBatch, now_utc

@pytest.fixture
def client(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch = IngestionBatch(id='batch', provider_name='test', rights_basis='owned', captured_at=now_utc(), data_confidence=1, content_hash='test')
        session.add(batch)
        session.add(Artist(id='artist', name='Artista', source_batch_id='batch'))
        session.commit()
    def dependency():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_session] = dependency
    for key, value in {'FANS_SMTP_HOST':'smtp.example.com','FANS_SMTP_USER':'user','FANS_SMTP_PASSWORD':'test','FANS_FROM':'artist@example.com','FANS_PUBLIC_URL':'https://fans.example.com'}.items():
        monkeypatch.setenv(key, value)
    with TestClient(app, client=('127.0.0.1', 1234), headers={'origin':'http://testserver'}) as test_client:
        yield test_client, engine
    app.dependency_overrides.clear()
    engine.dispose()

def test_consent_duplicates_and_cross_origin(client):
    c, _ = client
    data = {'email':'fan@example.com','source':'song','consent':False}
    assert c.post('/api/fans/artist/contacts', json=data).status_code == 422
    data['consent'] = True
    assert c.post('/api/fans/artist/contacts', json=data).status_code == 200
    assert c.post('/api/fans/artist/contacts', json=data).status_code == 409
    assert c.post('/api/fans/artist/contacts', json=data, headers={'origin':'https://other.example'}).status_code == 403

def test_queue_is_unique_and_unsubscribe_suppresses(client):
    c, engine = client
    c.post('/api/fans/artist/contacts', json={'email':'fan@example.com','source':'song','consent':True})
    campaign_id = c.post('/api/fans/artist/campaigns', json={'subject':'Nuevo tema','body':'Escúchalo','source':'song'}).json()['id']
    path = f'/api/fans/artist/campaigns/{campaign_id}'
    assert c.post(path + '/queue', json={}).json()['queued'] == 1
    assert c.post(path + '/queue', json={}).status_code == 409
    assert c.post(f'/api/fans/other/campaigns/{campaign_id}/queue', json={}).status_code == 404
    with Session(engine) as session:
        token = session.query(FanContact).one().token
    assert c.get('/fans/unsubscribe/' + token).status_code == 200
    assert c.post('/fans/unsubscribe/' + token).status_code == 200
    assert c.post(path + '/process', json={}).status_code == 200
    with Session(engine) as session:
        assert session.query(FanRecipient).one().status == 'suppressed'
