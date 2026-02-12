// admin_dashboard.js
import { loadCuestionarios, attachModalHandlers } from './cuestionarios.js';

window.addEventListener('DOMContentLoaded', ()=>{
  attachModalHandlers();
  loadCuestionarios();
  // aquí puedes llamar también a renderUsuarios(), drawML(), etc. si luego los modularizas
});
