let published = [];
const select = (id) => document.getElementById(id);
function renderPublished() {
  const query = select('search').value.trim().toLocaleLowerCase();
  const lang = select('language').value;
  const items = published.filter(p => (!lang || p.metadata_language === lang) && p.name.toLocaleLowerCase().includes(query));
  const root = select('cards'); root.replaceChildren();
  for (const p of items) {
    const card = document.createElement('article');
    const tag = document.createElement('span'); tag.className = 'tag'; tag.textContent = `Publicada · ${p.metadata_language.toUpperCase()}`;
    const title = document.createElement('h2'); title.textContent = p.name;
    const detail = document.createElement('p'); detail.textContent = `${p.track_count} canciones · Confirmada en Spotify`;
    const link = document.createElement('a'); link.textContent = 'Escuchar en Spotify ↗';
    if (!/^https:\/\/open\.spotify\.com\/playlist\/[A-Za-z0-9]+$/.test(p.url)) continue;
    link.href = p.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    card.append(tag, title, detail, link); root.append(card);
  }
  select('status').textContent = `${items.length} listas visibles · ${published.length} publicadas en total.`;
}
async function refreshPublished() {
  try {
    const response = await fetch('/static/created-playlists.json', {cache:'no-store'});
    if (!response.ok) throw new Error('No publication manifest');
    const data = await response.json();
    published = data.items.filter(p => p.status === 'PUBLISHED');
    select('count').textContent = published.length;
    renderPublished();
  } catch (_) { select('status').textContent = 'La publicación está en preparación. Esta página se actualizará automáticamente.'; }
}
select('search').addEventListener('input', renderPublished);
select('language').addEventListener('change', renderPublished);
refreshPublished(); setInterval(refreshPublished, 15000);
