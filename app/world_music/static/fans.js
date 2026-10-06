const artist = document.querySelector('#artist');
const notice = document.querySelector('#notice');
let snapshot = {contacts: [], campaigns: []};
async function api(path, data) {
  const response = await fetch('/api/fans' + path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Revisa los campos del formulario.');
  return value;
}
function element(tag, text) { const node = document.createElement(tag); node.textContent = text; return node; }
async function refresh() {
  if (!artist.value) return;
  const selected = artist.value;
  const data = await api('/' + selected);
  if (selected !== artist.value) return;
  snapshot = data;
  document.querySelector('#audience-title').textContent = `Audiencia · ${data.contacts.filter(c=>c.status==='subscribed').length} suscriptores activos`;
  const contacts = document.querySelector('#contacts'); contacts.replaceChildren();
  for (const c of data.contacts) { const row=element('div',`${c.name || c.email} · ${c.email} · ${c.source} · ${c.status==='subscribed'?'Suscrito':'Baja'}`);row.className='item';contacts.append(row); }
  if (!data.contacts.length) contacts.append(element('p','Todavía no hay contactos para este artista.'));
  const campaigns = document.querySelector('#campaigns'); campaigns.replaceChildren();
  for (const c of data.campaigns) {
    const row=element('article','');row.className='item'; row.append(element('h3',c.subject),element('pre',c.body));
    const audience=data.contacts.filter(f=>f.status==='subscribed'&&(!c.source||f.source===c.source)).length;
    const labels={pending:'Pendientes',sending:'En proceso o interrumpidos',accepted:'Aceptados por SMTP',uncertain:'Revisar con proveedor',suppressed:'Excluidos por baja'};
    row.append(element('p',c.status==='draft'?`Borrador · ${audience} destinatarios actuales`:Object.entries(c.counts).map(([k,v])=>`${labels[k]||k}: ${v}`).join(' · ')));
    const button=element('button',c.status==='draft'?'Confirmar y poner en cola':'Procesar siguiente lote');
    button.disabled=c.status!=='draft'&&!c.counts.pending;
    button.onclick=async()=>{if(c.status==='draft'&&!confirm(`¿Confirmar la campaña “${c.subject}” para ${audience} suscriptores?`))return;button.disabled=true;try{await api(`/${artist.value}/campaigns/${c.id}/${c.status==='draft'?'queue':'process'}`,{});notice.textContent='Operación completada.';await refresh();}catch(e){notice.textContent=e.message;button.disabled=false;}};
    row.append(button);campaigns.append(row);
  }
  if(!data.campaigns.length) campaigns.append(element('p','Crea tu primer borrador de lanzamiento o novedades.'));
}
for(const [id,path] of [['contact','contacts'],['campaign','campaigns']]) {
  document.getElementById(id).onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;const data=Object.fromEntries(new FormData(form));if(id==='contact')data.consent=form.elements.consent.checked;const button=form.querySelector('button');button.disabled=true;try{if(!artist.value)throw new Error('Primero necesitas un artista en el catálogo.');await api(`/${artist.value}/${path}`,data);form.reset();notice.textContent='Guardado correctamente.';await refresh();}catch(e){notice.textContent=e.message;}finally{button.disabled=false;}};
}
document.querySelector('#template').onchange=event=>{if(event.target.value)document.querySelector('#campaign').elements.subject.value=event.target.value;};
artist.onchange=()=>refresh().catch(e=>notice.textContent=e.message);
(async()=>{try{const artists=await api('/artists');for(const a of artists){const option=element('option',a.name);option.value=a.id;artist.append(option);}if(!artists.length)notice.textContent='No hay artistas en el catálogo. Importa el catálogo desde World Music para comenzar.';await refresh();}catch(e){notice.textContent=e.message;}})();
