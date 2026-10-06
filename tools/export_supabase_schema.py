"""Generate versioned PostgreSQL bootstrap from the repository migrations."""
from pathlib import Path
import os
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ)
env['WORLD_MUSIC_DATABASE_URL'] = 'postgresql+psycopg://localhost/world_music'
result = subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head', '--sql'], cwd=root, env=env, capture_output=True, text=True, check=True)
(root / 'supabase' / 'world_music.sql').write_text('-- WORLD MUSIC OS: fresh database only. Generated from Alembic.\n-- Backend-only access; no public API policies.\n' + result.stdout, encoding='utf-8')
print('Generated supabase/world_music.sql')
