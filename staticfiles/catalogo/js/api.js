// api.js
export function getCookie(name){
  const v = ('; '+document.cookie).split('; '+name+'=');
  if (v.length === 2) return v.pop().split(';').shift();
}
const CSRF = () => getCookie('csrftoken');

export async function api(url, {method='GET', json, headers={}, ...rest} = {}){
  const opts = {
    method,
    credentials: 'same-origin',
    headers: {
      'X-CSRFToken': CSRF(),
      ...(json ? {'Content-Type': 'application/json'} : {}),
      ...headers
    },
    ...(json ? {body: JSON.stringify(json)} : {}),
    ...rest
  };
  const res = await fetch(url, opts);
  const data = await res.json().catch(()=> ({}));
  if(!res.ok || data.ok === false){
    const msg = data.error || JSON.stringify(data.errors || data) || res.statusText;
    throw new Error(msg);
  }
  return data;
}
