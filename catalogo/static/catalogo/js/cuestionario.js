// cuestionarios.js
import { api } from './api.js';

const CAT_BASE = "/dashboard/admin/catalogo/";
const API_LIST  = CAT_BASE + "api/cuestionarios/";
const API_ITEM  = id => CAT_BASE + `api/cuestionarios/${id}/`;
const API_DUP   = id => CAT_BASE + `api/cuestionarios/${id}/duplicar/`;

let cuestionarios = [];
let editId = null;

export async function loadCuestionarios(){
  const out = await api(API_LIST);
  // tu API lista normalmente devuelve {ok:true, results:[...]}
  const results = out?.results || [];
  cuestionarios = results;
  renderCuestionarios();
}

function chipPub(estado, activo){
  // si quieres, muestra inactivo aunque sea published
  if(estado === 'published' && activo) return '<span class="badge-soft badge-ok">Publicado</span>';
  if(estado === 'published' && !activo) return '<span class="badge-soft badge-warn">Publicado (inactivo)</span>';
  return '<span class="badge-soft">Borrador</span>';
}

function fmtFecha(iso){
  return iso ? new Date(iso).toLocaleDateString() : '—';
}

export function renderCuestionarios(){
  const tb = document.querySelector('#tablaCuestionarios tbody');
  if(!tb) return;

  tb.innerHTML = '';

  cuestionarios.forEach(c=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${c.codigo ?? ''}</td>
      <td>${c.nombre ?? ''}</td>
      <td>${c.version ?? ''}</td>
      <td>${chipPub(c.estado, c.activo)}</td>
      <td>${c.items ?? 0}</td>
      <td>${fmtFecha(c.updated)}</td>
      <td class="actions">
        <button class="btn btn-sm btn-outline-primary" data-edit="${c.id}">Editar</button>
        <button class="btn btn-sm btn-outline-secondary" data-pub="${c.id}">
          ${c.estado === 'published' ? 'Despublicar' : 'Publicar'}
        </button>
        <button class="btn btn-sm btn-outline-secondary" data-dup="${c.id}">Duplicar</button>
        <button class="btn btn-sm btn-outline-danger" data-del="${c.id}">Eliminar</button>
        <a class="btn btn-sm" href="${CAT_BASE}cuestionarios/${c.id}/editar/?step=preguntas">Editar preguntas</a>
      </td>`;
    tb.appendChild(tr);
  });

  // Delegación de eventos (un solo handler)
  tb.onclick = async (e)=>{
    const btn = e.target.closest('button, a');
    if(!btn) return;

    const id = btn.dataset.edit || btn.dataset.pub || btn.dataset.dup || btn.dataset.del;
    if(!id) return;

    try{
      if(btn.dataset.edit){
        await openEdit(+id);

      }else if(btn.dataset.pub){
        const row = cuestionarios.find(x => x.id === +id);
        const next = (row && row.estado === 'published') ? 'draft' : 'published';

        // ✅ IMPORTANTE: manda activo también (y el backend lo refuerza)
        await api(API_ITEM(+id), {
          method:'PATCH',
          json:{ estado: next, activo: (next === 'published') }
        });

        await loadCuestionarios();

      }else if(btn.dataset.dup){
        await api(API_DUP(+id), {method:'POST'});
        await loadCuestionarios();

      }else if(btn.dataset.del){
        if(confirm('¿Eliminar cuestionario?')){
          await api(API_ITEM(+id), {method:'DELETE'});
          await loadCuestionarios();
        }
      }
    }catch(err){
      alert(err?.message || String(err));
    }
  };
}

export function attachModalHandlers(){
  const dlg  = document.getElementById('dlgNuevoCuest');
  const form = document.getElementById('formCuest');

  document.getElementById('openNuevoCuest')?.addEventListener('click', ()=>{
    editId = null;
    form.reset();

    // si tienes campo "items" solo lectura
    if(form.items){
      form.items.setAttribute('disabled','disabled');
    }

    dlg.querySelector('h4').textContent = 'Nuevo cuestionario';
    dlg.showModal();
  });

  document.getElementById('btnGuardarCuest')?.addEventListener('click', async ()=>{
    const f = Object.fromEntries(new FormData(form).entries());
    if(!f.codigo || !f.nombre) return alert('Código y nombre son obligatorios');

    const payload = {
      codigo: (f.codigo || '').trim().toUpperCase(),
      nombre: (f.nombre || '').trim(),
      version:(f.version || '1.0').trim(),
      estado: (f.estado || 'draft'),
      descripcion: (f.descripcion || ''),
      // por consistencia, draft suele ser activo=false
      activo: (f.estado === 'published'),
      algoritmo: (f.algoritmo || 'SUM')
    };

    try{
      if(editId){
        await api(API_ITEM(editId), {method:'PATCH', json: payload});
      }else{
        // tu POST list probablemente exista: API_LIST
        await api(API_LIST, {method:'POST', json: payload});
      }
      dlg.close();
      await loadCuestionarios();
    }catch(err){
      alert(err?.message || String(err));
    }
  });
}

async function openEdit(id){
  editId = id;

  // ✅ tu detalle devuelve {ok:true, item:{...}}
  const out = await api(API_ITEM(id));
  const c = out?.item || out; // fallback si algún día cambias respuesta

  const dlg  = document.getElementById('dlgNuevoCuest');
  const form = document.getElementById('formCuest');

  form.codigo.value = c.codigo || '';
  form.nombre.value = c.nombre || '';
  form.version.value = c.version || '';
  if(form.estado) form.estado.value = c.estado || 'draft';
  if(form.algoritmo) form.algoritmo.value = c.algoritmo || 'SUM';

  if(form.items){
    form.items.value = c.items ?? '';
    form.items.setAttribute('disabled','disabled');
  }

  dlg.querySelector('h4').textContent = 'Editar cuestionario';
  dlg.showModal();
}
