"""Publish the user's approved ten curated selections in ten metadata languages.

This editorial workflow makes no metric, growth, song-language or trend claims.
It does not change the evidence gates of world playlist-launch.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html import unescape
import json
import re
import sys
import time

from .config import DATA_DIR, PLAYLISTS_DIR, PROJECT_ROOT, load_settings
from .models import PlaylistConfig, RotationConfig, TrackSpec
from .spotify_client import SpotifyClient
from .storage import JsonStore, PlaylistRegistry, utc_now
from .tracks import normalize_text, normalized_title

ROOT = DATA_DIR / "editorial_expansion"
STATIC = PROJECT_ROOT / "app" / "world_music" / "static"
LOCALES = "es en pt fr de it nl ja ko zh".split()
SOURCES = ["indie-rock-worldwide", "latin-indie-rock", "latin-urban-takeover", "latin-viral-100", "malianteo-worldwide", "next-latin-stars", "next-rock-generation", "perreo-mundial", "reggaeton-worldwide", "rnb-latino-nights"]
# Positions refer only to the locally saved, live Spotify source snapshot.
# Explicit selections remove unrelated keyword-search results in the old lists.
THEMES = [
    ("indie-borders", 40, [(0,"1-4,6-10,12,16-22,28-31,35-37,53,64,67,71,74,78,81,88,89,97-100"),(6,"1,3-5,11,13,14,35,46,76,91,93,98")],
     "Indie sin Fronteras|Indie Without Borders|Indie sem Fronteiras|Indie sans frontières|Indie ohne Grenzen|Indie senza confini|Indie zonder grenzen|国境を越えるインディー|국경 없는 인디|跨越国界的独立音乐"),
    ("latin-rock", 45, [(1,"1-14,16-27,29,30,33-36,39,43-48,51,52,55,59,61,69,72-74,78-80,86,88,93,95-99"),(0,"25,26,32-34,38-40,54,57,58,75")],
     "Rock Latino para Cantar|Latin Rock Singalong|Rock Latino para Cantar Junto|Rock latino à chanter|Latin Rock zum Mitsingen|Rock latino da cantare|Latin rock om mee te zingen|歌いたくなるラテンロック|함께 부르는 라틴 록|一起唱的拉丁摇滚"),
    ("urban-pulse", 50, [(2,"1-29,32-34,37,39,40,42,46,52,54,57-61,63,64,67,71,72,75,76,79,81,82"),(5,"1-15,20,21,23")],
     "Pulso Urbano Latino|Latin Urban Pulse|Pulso Urbano Latino|Pulsations urbaines latines|Latin Urban: der Puls|Il ritmo urban latino|De Latin Urban-puls|ラテンアーバンの鼓動|라틴 어반의 맥박|拉丁都市脉动"),
    ("latin-party", 45, [(3,"6-8,10-20,26-29,34-36,43-45,51-54,60-66,70,71,91,93,98-100"),(5,"6,7,9,20,21"),(9,"1,2,11,15-21,23,25")],
     "Fiesta Latina: Una Más|Latin Party: One More Song|Festa Latina: Só Mais Uma|Fiesta latina : encore un titre|Latin Party: noch ein Song|Festa latina: ancora una|Latin party: nog één nummer|ラテンパーティー：もう一曲|라틴 파티: 한 곡 더|拉丁派对：再来一首"),
    ("latin-trap", 45, [(4,"6-8,10-21,25-27,30,34-47,49,53,57,62,63,68,75,78,80,85,87,89,92-100"),(2,"1-5,16-20")],
     "Trap Latino: Sin Filtro|Latin Trap: Unfiltered|Trap Latino: Sem Filtro|Trap latino sans filtre|Latin Trap: ungefiltert|Trap latino senza filtri|Latin trap zonder filter|ラテントラップ：ありのまま|라틴 트랩: 필터 없이|拉丁陷阱说唱：原汁原味"),
    ("chile-urban", 45, [(7,"27-30,32,39,40,49-61,63,68"),(2,"23-26,32,34,42,54,59,63,64,72,75,76,81"),(4,"7,17,24,38,40-42,46,47,51,52,57,58,61,65,68,69,75,76,80,81,87,89,90,101"),(9,"6-10")],
     "Chile Urbano: De Noche|Chile Urban After Dark|Chile Urbano: À Noite|Chili urbain après minuit|Chile Urban bei Nacht|Chile urban di notte|Chile Urban in de nacht|チリ・アーバンナイト|칠레 어반 나이트|智利都市之夜"),
    ("alternative-guitars", 40, [(6,"1,3-5,9-11,13,14,25,31,33-35,41,43,45,46,76,81-85,90-93,98"),(0,"1-4,6,8,12,16-20,28,29,36,37,64,67,71,74,78,81,88")],
     "Guitarras Alternativas|Alternative Guitar Escape|Guitarras Alternativas|Évasion rock alternatif|Alternative Gitarrenwelten|Fuga nel rock alternativo|Alternatieve gitaren|オルタナ・ギターの世界|얼터너티브 기타 여행|另类吉他之旅"),
    ("perreo-night", 45, [(7,"1-25,27-30,32,39-43,46,48-65,68,69,71,74,75,80,84,89,92,94,100")],
     "Perreo Hasta el Cierre|Perreo Until Closing|Perreo Até Fechar|Perreo jusqu'à la fermeture|Perreo bis zum Schluss|Perreo fino alla chiusura|Perreo tot sluitingstijd|閉店までペレオ|마지막까지 페레오|派对不散场：Perreo"),
    ("reggaeton-roots", 40, [(8,"1-20,23,26,29,32-34,37,39,42"),(7,"11-25,35,41,48"),(2,"12-15"),(4,"1-5,22,23")],
     "Reggaetón: Coros que Vuelven|Reggaeton: Hooks You Remember|Reggaeton: Refrões que Voltam|Reggaeton : refrains inoubliables|Reggaeton: Refrains, die bleiben|Reggaeton: ritornelli da ricordare|Reggaeton: refreinen die blijven|レゲトン：忘れられないサビ|레게톤: 기억에 남는 후렴|雷鬼顿：难忘的副歌"),
    ("latin-late-night", 35, [(9,"1,2,5-7,9-25,36,37,42,53,57,61-63"),(5,"11,12,20,21,23"),(2,"27,34,42,59,72,81")],
     "Noches Latinas: R&B y Urbano|Latin Nights: R&B and Urban|Noites Latinas: R&B e Urbano|Nuits latines : R&B et urban|Lateinamerikanische Nächte: R&B|Notti latine: R&B e urban|Latijnse nachten: R&B en urban|ラテンの夜：R&B＆アーバン|라틴의 밤: R&B와 어반|拉丁之夜：R&B与都市音乐"),
]
COPY = {
 "es": "{title}. Con {artists} y más. Dale play, encuentra ese tema que quieres repetir y guarda la lista para volver.",
 "en": "{title}. Featuring {artists} and more. Press play, find your next repeat listen and save this playlist for later.",
 "pt": "{title}. Com {artists} e muito mais. Aperte o play, encontre aquela música para repetir e salve a playlist para voltar.",
 "fr": "{title}. Avec {artists} et bien d'autres. Lancez la musique, trouvez le titre à écouter en boucle et enregistrez cette playlist.",
 "de": "{title}. Mit {artists} und mehr. Drück auf Play, entdecke deinen nächsten Lieblingssong und speichere die Playlist.",
 "it": "{title}. Con {artists} e tanti altri. Premi play, trova il brano da riascoltare e salva la playlist per tornarci.",
 "nl": "{title}. Met {artists} en meer. Druk op play, ontdek je volgende favoriet en sla deze playlist op voor later.",
 "ja": "{title}。{artists}などをセレクト。再生して、何度も聴きたくなる一曲を見つけよう。プレイリストを保存して、またこの音楽へ。",
 "ko": "{title}. {artists} 등의 음악을 담았습니다. 재생을 누르고 다시 듣고 싶은 곡을 찾아보세요. 플레이리스트를 저장해 언제든 돌아오세요.",
 "zh": "{title}。精选{artists}等音乐人的作品。点击播放，发现想要循环聆听的歌曲，收藏歌单，随时回来享受音乐。",
}


def positions(spec):
    for part in spec.split(","):
        bounds = [int(x) for x in part.split("-")]
        yield from range(bounds[0] - 1, bounds[-1])


def song_key(track):
    title = re.sub(r"\s*[-–]\s*(?:\d{4}\s*)?(?:remaster.*|bonus track.*)$", "", track["title"], flags=re.I)
    title = re.sub(r"\s*\(with .*?\)", "", title, flags=re.I)
    return (normalize_text(track["artists"][0]), normalized_title(title))


def select_tracks(snapshot, groups, target):
    pools = {item["slug"]: item["tracks"] for item in snapshot["items"]}
    pending, uris, keys, isrcs = [], set(), set(), set()
    for source, indices in groups:
        for position in positions(indices):
            track = pools[SOURCES[source]][position]
            key, isrc = song_key(track), track.get("isrc")
            if track["uri"] in uris or key in keys or (isrc and isrc in isrcs):
                continue
            uris.add(track["uri"]); keys.add(key)
            if isrc: isrcs.add(isrc)
            pending.append({**track, "source_playlist": SOURCES[source], "source_position": position + 1})
    selected, counts = [], Counter()
    while pending and len(selected) < target:
        possible = [t for t in pending if counts[t["artists"][0]] < 4]
        if not possible: break
        previous = set(selected[-1]["artists"]) if selected else set()
        separated = [t for t in possible if not previous.intersection(t["artists"])]
        choices = separated or possible
        track = min(choices, key=lambda t: counts[t["artists"][0]])
        selected.append(track); pending.remove(track); counts[track["artists"][0]] += 1
    if len(selected) < 30:
        raise ValueError(f"Only {len(selected)} curated tracks; review selection")
    return selected


def build_plan():
    raw = (ROOT / "source_playlists.json").read_bytes()
    snapshot = json.loads(raw)
    plans = []
    for theme, target, groups, translated in THEMES:
        tracks = select_tracks(snapshot, groups, target)
        names = translated.split("|")
        assert len(names) == len(LOCALES)
        artists = ", ".join(dict.fromkeys(t["artists"][0] for t in tracks[:3]))
        for language, title in zip(LOCALES, names):
            config = PlaylistConfig(
                slug=f"wm-{theme}-{language}", name=f"{title} [{language.upper()}]",
                description=COPY[language].format(title=title, artists=artists),
                public=True, target_tracks=len(tracks), rotation=RotationConfig(enabled=False),
                tracks=[TrackSpec(spotify_uri=t["uri"],title=t["title"],artist=t["artists"][0],spotify_catalog_verified=True,locked=True) for t in tracks],
            )
            plans.append({"theme":theme,"metadata_language":language,"mode":"EDITORIAL_LOCALIZED", "config":config.model_dump(mode="json"),"tracks":tracks})
    result = {"created_at":utc_now(),"owner_id":snapshot["owner_id"],"source_sha256":hashlib.sha256(raw).hexdigest(),"items":plans,
              "method":"Manually selected catalog tracks from the owner's live playlists; song/ISRC/URI deduplication; at most four tracks per primary artist; artists spaced where possible. Languages describe playlist metadata only. No trend scores or performance claims."}
    JsonStore(ROOT / "plan.json").write(result)
    return result


def write_public_index(journal):
    items = [{k:v for k,v in entry.items() if k in {"name","url","track_count","metadata_language","theme","status","verified_at"}} for entry in journal.values()]
    JsonStore(STATIC / "created-playlists.json").write({"updated_at":utc_now(),"target":100,"published":sum(x.get("status")=="PUBLISHED" for x in items),"items":items})


def publish(plan, limit):
    store = JsonStore(ROOT / "publication.json")
    journal = store.read({})
    registry = PlaylistRegistry()
    with SpotifyClient(load_settings(), max_retries=0, max_retry_after=1) as client:
        owner = client.get_me()["id"]
        if owner != plan["owner_id"]: raise ValueError("Spotify account differs from source owner")
        known = client.get_playlists()
        created = 0
        for item in plan["items"]:
            config = PlaylistConfig.model_validate(item["config"])
            previous = journal.get(config.slug)
            if previous and previous.get("status") == "PUBLISHED": continue
            if created >= limit: break
            entry = journal.setdefault(config.slug, {"name":config.name,"theme":item["theme"],"metadata_language":item["metadata_language"],"status":"PREPARED"})
            uris = [t.spotify_uri for t in config.tracks]
            if len(uris) < 30 or len(uris) != len(set(uris)): raise ValueError("Invalid seed selection")
            try:
                playlist_id = entry.get("spotify_playlist_id")
                if not playlist_id:
                    matches = [p for p in known if p.get("name")==config.name and (p.get("owner") or {}).get("id")==owner]
                    if matches:
                        if len(matches)!=1 or not previous or not previous.get("create_attempted_at") or previous.get("status") not in {"CREATING","ERROR"}:
                            raise ValueError("Existing playlist name needs reconciliation")
                        if unescape(matches[0].get("description") or "") != config.description: raise ValueError("Recovery description mismatch")
                        playlist_id=matches[0]["id"]
                    else:
                        entry.update(status="CREATING",create_attempted_at=utc_now()); store.write(journal)
                        remote=client.create_playlist(config.name,config.description,False)
                        playlist_id=remote["id"]; known.append(remote)
                    entry.update(spotify_playlist_id=playlist_id,url=f"https://open.spotify.com/playlist/{playlist_id}",status="SEEDING")
                    store.write(journal)
                details=client.get_playlist(playlist_id)
                if (details.get("owner") or {}).get("id")!=owner: raise ValueError("Ownership mismatch")
                def read_uris():
                    return [(row.get("item") or row.get("track") or {}).get("uri") for row in client.get_playlist_items(playlist_id)]
                current=read_uris()
                if current and current!=uris: raise ValueError("Unexpected existing contents; stopped to protect edits")
                if not current: client.replace_items(playlist_id,uris)
                if read_uris()!=uris: raise ValueError("Spotify did not confirm all tracks in order")
                client.update_playlist(playlist_id,name=config.name,description=config.description,public=True)
                details=client.get_playlist(playlist_id)
                if details.get("public") is not True or details.get("name")!=config.name or unescape(details.get("description") or "")!=config.description:
                    raise ValueError("Spotify did not confirm publication metadata")
                JsonStore(PLAYLISTS_DIR / f"{config.slug}.json").write(config.model_dump(mode="json"))
                registry.update(config.slug,{"spotify_playlist_id":playlist_id,"spotify_url":entry["url"],"owner_id":owner,"launch_state":"ACTIVE","editorial_mode":"LOCALIZED_SELECTION","metadata_language":item["metadata_language"],"source_theme":item["theme"],"last_synced":utc_now(),"locked_uris":uris})
                entry.update(status="PUBLISHED",track_count=len(uris),verified_at=utc_now())
                entry.pop("error",None)
                store.write(journal); write_public_index(journal)
                created+=1
                print(f"PUBLISHED {sum(e.get('status')=='PUBLISHED' for e in journal.values())}/100 {config.slug} {len(uris)} {entry['url']}",flush=True)
                time.sleep(0.5)
            except Exception as exc:
                entry.update(status="ERROR",error={"type":type(exc).__name__,"http_status":getattr(exc,"status_code",None)})
                store.write(journal); write_public_index(journal)
                raise
    return journal


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--limit",type=int,default=100)
    args=parser.parse_args()
    if not 1<=args.limit<=100: parser.error("limit must be 1..100")
    saved=JsonStore(ROOT / "plan.json").read(None)
    plan=saved if args.apply and saved else build_plan()
    print(f"Prepared {len(plan['items'])} localized editorial playlists",flush=True)
    for item in plan["items"][::10]:
        print(item['theme'],len(item['tracks']),'tracks',len({t['artists'][0] for t in item['tracks']}),'primary artists',flush=True)
    if args.apply: publish(plan,args.limit)
