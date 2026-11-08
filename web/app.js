'use strict';
/* Mídia SW — interface. Toda conta vem do servidor; aqui só se exibe, ordena e edita.
   Esconder botão é cortesia: a barreira de permissão é no servidor. */

const S = {
  me: null, caps: new Set(), anos: [],
  ano: +(lerLocal('ano') || new Date().getFullYear()),
  reap: lerLocal('reap') !== '0',
  grupos: false,
  pendente: null,         // alterações não salvas {n, salvar, descartar}
  config: {},
};
const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
const MESES_X = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro'];
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pode = c => S.caps.has(c);

function lerLocal(k) { try { return localStorage.getItem('midia.' + k); } catch { return null; } }
function gravarLocal(k, v) { try { localStorage.setItem('midia.' + k, v); } catch { } }

// ---------- números: centavos inteiros sempre ----------
// Milhões: 2 casas só nas telas de diretor (ficha do cliente, "meus clientes"); 1 casa no resto.
// É só exibição — o valor por baixo é sempre o exato em centavos.
const NF = {
  1: new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
  2: new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
};
const nf2 = NF[2];
S.casas = 2;
const fmtM = c => c ? NF[S.casas].format(c / 1e8) : '–';
const fmtMM = c => 'R$ ' + NF[S.casas].format((c || 0) / 1e8) + 'M';
const fmtR = c => nf2.format((c || 0) / 100);                // reais exatos
const pct = x => x == null ? '–' : Math.round(x * 100) + '%';

/** Texto digitado → centavos (inteiro) ou null se vazio. Aceita 1.234,56 · 1234.56 · 1,5M · 300k. */
function lerValor(txt) {
  let s = String(txt ?? '').trim().replace(/R\$|\s/gi, '');
  if (!s) return null;
  let mult = 1;
  const suf = s.match(/(mi|m|k|mil)$/i);
  if (suf) { mult = /^k|mil$/i.test(suf[1]) ? 1e3 : 1e6; s = s.slice(0, -suf[1].length); }
  const neg = s.startsWith('-'); if (neg) s = s.slice(1);
  if (s.includes(',')) s = s.replace(/\./g, '').replace(',', '.');
  else if ((s.match(/\./g) || []).length > 1) s = s.replace(/\./g, '');
  else if (/^\d{1,3}\.\d{3}$/.test(s) && mult === 1) s = s.replace('.', '');
  if (!/^\d*\.?\d*$/.test(s) || s === '.' ) throw new Error('valor inválido: ' + txt);
  let c;
  if (mult === 1) {
    const [i, f = ''] = s.split('.');
    c = (+i || 0) * 100 + (+(f + '00').slice(0, 2)) + ((+(f[2] || 0)) >= 5 ? 1 : 0);
  } else c = Math.round(parseFloat(s) * mult * 100);
  return neg ? -c : c;
}
const paraApi = c => c == null ? '' : (c / 100).toFixed(2);

// ---------- infraestrutura ----------
async function api(caminho, { method = 'GET', body, raw, headers = {} } = {}) {
  const sep = caminho.includes('?') ? '&' : '?';
  const url = caminho + (caminho.includes('ano=') ? '' : sep + 'ano=' + S.ano);
  const h = { 'X-Midia': '1', ...headers };
  let corpo = raw;
  if (body !== undefined) { h['Content-Type'] = 'application/json'; corpo = JSON.stringify(body); }
  const r = await fetch(url, { method, headers: h, body: corpo, credentials: 'same-origin' });
  if (r.status === 401) { telaLogin(); throw new Error('faça login'); }
  const tipo = r.headers.get('Content-Type') || '';
  if (!tipo.includes('json')) {
    if (!r.ok) throw new Error('erro ' + r.status);
    return r;
  }
  const j = await r.json();
  if (!r.ok) throw new Error(j.erro || ('erro ' + r.status));
  return j;
}

function aviso(msg, erro = false) {
  const el = $('#aviso');
  el.textContent = msg; el.className = erro ? 'erro' : ''; el.hidden = false;
  clearTimeout(aviso.t); aviso.t = setTimeout(() => el.hidden = true, erro ? 7000 : 3500);
}

function modal(html, aoMontar) {
  const m = $('#modal');
  $('#modal-corpo').innerHTML = html;
  m.hidden = false;
  const fechar = () => { m.hidden = true; $('#modal-corpo').innerHTML = ''; };
  m.onclick = e => { if (e.target === m) fechar(); };
  $$('[data-fechar]', m).forEach(b => b.onclick = fechar);
  aoMontar && aoMontar($('#modal-corpo'), fechar);
  const f = $('input,select,textarea', m); f && f.focus();
  return fechar;
}

function confirmarForte(titulo, texto, palavra) {
  return new Promise(res => {
    modal(`<h3>${esc(titulo)}</h3><p>${texto}</p>
      <div class="form"><label>Para confirmar, digite <b>${esc(palavra)}</b><input id="cf"></label>
      <div class="botoes"><button class="fantasma" data-fechar>Cancelar</button><button class="perigo" id="cf-ok" disabled>Confirmar</button></div></div>`,
      (el, fechar) => {
        $('#cf', el).oninput = e => $('#cf-ok', el).disabled = e.target.value.trim().toUpperCase() !== palavra.toUpperCase();
        $('#cf-ok', el).onclick = () => { fechar(); res($('#cf', el)?.value || palavra); };
        $$('[data-fechar]', el).forEach(b => b.addEventListener('click', () => res(null)));
      });
  });
}

function baixar(caminho) {
  const sep = caminho.includes('?') ? '&' : '?';
  api(caminho + sep + 'ano=' + S.ano).then(async r => {
    const blob = await r.blob();
    const nome = decodeURIComponent((r.headers.get('Content-Disposition') || '').split("''")[1] || 'midia.xlsx');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = nome; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }).catch(e => aviso(e.message, true));
}

function definirPendente(p) {
  S.pendente = p && p.n > 0 ? p : null;
  const b = $('#barra');
  if (!S.pendente) { b.hidden = true; return; }
  b.hidden = false;
  $('#barra-txt').textContent = `${p.n} ${p.n === 1 ? 'alteração não salva' : 'alterações não salvas'}`;
  $('#barra-salvar').onclick = async () => {
    $('#barra-salvar').disabled = true;
    try { await p.salvar(); definirPendente(null); aviso('Salvo.'); rotear(); }
    catch (e) { aviso(e.message, true); }
    finally { $('#barra-salvar').disabled = false; }
  };
  $('#barra-descartar').onclick = () => { definirPendente(null); rotear(); };
}
window.addEventListener('beforeunload', e => { if (S.pendente) { e.preventDefault(); e.returnValue = ''; } });

// ---------- login ----------
async function telaLogin() {
  $('#topo').hidden = true;
  definirPendente(null);
  const cfg = await fetch('/api/config').then(r => r.json());
  S.config = cfg;
  const erroLogin = new URLSearchParams(location.search).get('erro');
  if (erroLogin) history.replaceState(null, '', '/' + location.hash);
  let html = `<div class="login"><h1>Mídia SW</h1><p class="suave">Entre com sua conta @${esc(cfg.dominio)}.</p>
    ${erroLogin ? `<p class="alerta">${esc(erroLogin)}</p>` : ''}<div id="g"></div>`;
  if (cfg.login_dev) html += `<p class="pequeno suave" style="margin-top:20px">Login de desenvolvimento (só funciona em localhost):</p><div class="pessoas" id="dev"></div>`;
  $('#app').innerHTML = html + '</div>';
  if (cfg.google_client_id) {
    const s = document.createElement('script');
    s.src = 'https://accounts.google.com/gsi/client';
    s.onerror = () => { $('#g').innerHTML = '<p class="alerta">Não consegui carregar o login do Google. Verifique a internet ou bloqueadores de anúncio.</p>'; };
    s.onload = () => {
      // modo redirecionamento: sem janela popup (que o navegador pode bloquear)
      google.accounts.id.initialize({
        client_id: cfg.google_client_id, hd: cfg.dominio,
        ux_mode: 'redirect', login_uri: location.origin + '/login/google',
      });
      google.accounts.id.renderButton($('#g'), { theme: 'outline', size: 'large', text: 'signin_with', locale: 'pt-BR' });
    };
    document.head.appendChild(s);
  } else if (!cfg.login_dev) {
    $('#g').innerHTML = '<p class="alerta">Login Google ainda não configurado (MIDIA_GOOGLE_CLIENT_ID).</p>';
  }
  if (cfg.login_dev) {
    const r = await fetch('/api/dev-pessoas');
    if (r.ok) {
      const ps = await r.json();
      $('#dev').innerHTML = ps.map(p => `<button data-e="${esc(p.email)}"><b>${esc(p.nome)}</b> <span class="suave pequeno">${esc(p.papel)} · ${esc(p.email)}</span></button>`).join('');
      $$('#dev button').forEach(b => b.onclick = async () => {
        const x = await fetch('/api/login-dev', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: b.dataset.e }) });
        if (x.ok) iniciar(); else aviso((await x.json()).erro, true);
      });
    } else $('#dev').innerHTML = '<p class="suave pequeno">Indisponível fora de localhost.</p>';
  }
}

async function iniciar() {
  let me;
  // Logo depois do login Google a sessão pode levar um instante para valer:
  // tenta algumas vezes antes de voltar para a tela de login.
  let acabouDeLogar = false;
  try { acabouDeLogar = sessionStorage.getItem('midia.login') === '1'; sessionStorage.removeItem('midia.login'); } catch { }
  for (let i = 0; acabouDeLogar && i < 6; i++) {
    const r = await fetch('/api/me?ano=' + S.ano, { credentials: 'same-origin', cache: 'no-store' });
    if (r.ok) break;
    try { const m = (await r.clone().json()).motivo; console.warn('login: tentativa', i + 1, m); sessionStorage.setItem('midia.diag', (sessionStorage.getItem('midia.diag') || '') + `${i + 1}:${m} `); } catch { }
    $('#app').innerHTML = '<p class="carregando">Entrando…</p>';
    await new Promise(res => setTimeout(res, 700));
  }
  try { me = await api('/api/me'); } catch { return; }
  S.me = me.pessoa; S.caps = new Set(me.capacidades); S.anos = me.anos;
  if (!S.anos.includes(S.ano)) S.ano = new Date().getFullYear();
  $('#topo').hidden = false;
  $('#ano').innerHTML = S.anos.map(a => `<option ${a === S.ano ? 'selected' : ''}>${a}</option>`).join('');
  $('#ano').onchange = e => { if (!sairSemSalvar()) { e.target.value = S.ano; return; } S.ano = +e.target.value; gravarLocal('ano', S.ano); rotear(); };
  $('#reap').checked = S.reap;
  $('#reap').onchange = e => { S.reap = e.target.checked; gravarLocal('reap', S.reap ? '1' : '0'); rotear(); };
  $('#quem').innerHTML = `${esc(S.me.nome || S.me.email)} <span class="etiqueta">${esc(S.me.papel)}</span>`;
  $('#sair').onclick = async () => { await api('/api/logout', { method: 'POST' }).catch(() => { }); telaLogin(); };
  rotear();
}

function montarNav(atual) {
  const itens = [];
  if (pode('visao_geral')) itens.push(['', 'Visão geral']);
  else if (pode('ficha_propria')) itens.push(['', 'Meus clientes']);
  if (pode('pis')) itens.push(['pis', 'PIs']);
  if (pode('equipe')) itens.push(['equipe', 'Equipe']);
  if (pode('depara') || pode('meta') || pode('uso')) itens.push(['admin', 'Administração']);
  $('#nav').innerHTML = itens.map(([h, t]) => `<a href="#/${h}" class="${atual === h ? 'ativo' : ''}">${t}</a>`).join('');
}

function sairSemSalvar() {
  if (!S.pendente) return true;
  if (confirm('Há alterações não salvas. Sair e descartar?')) { definirPendente(null); return true; }
  return false;
}

let hashAnterior = location.hash;
window.addEventListener('hashchange', () => {
  if (!sairSemSalvar()) { history.replaceState(null, '', hashAnterior); return; }
  hashAnterior = location.hash;
  rotear();
});

async function rotear() {
  const partes = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent);
  const [r0, r1] = partes;
  montarNav(r0 || '');
  S.casas = (S.me?.papel === 'diretor' || r0 === 'cliente') ? 2 : 1;
  const app = $('#app');
  app.innerHTML = '<p class="carregando">Carregando…</p>';
  try {
    if (!r0) await telaInicio(app);
    else if (r0 === 'cliente') await telaCliente(app, r1);
    else if (r0 === 'veiculo') await telaVeiculo(app, r1);
    else if (r0 === 'pis') await telaPis(app);
    else if (r0 === 'equipe') await telaEquipe(app);
    else if (r0 === 'admin') await telaAdmin(app, r1 || '');
    else app.innerHTML = '<p>Página não encontrada.</p>';
  } catch (e) {
    if (e.message !== 'faça login') app.innerHTML = `<p class="alerta">${esc(e.message)}</p>`;
  }
}

// ---------- componentes ----------
function kpi(rot, val, { sub = '', cls = '', titulo = '' } = {}) {
  return `<div class="kpi ${cls}" title="${esc(titulo)}"><div class="rot">${rot}</div><div class="val">${val}</div>${sub ? `<div class="sub">${sub}</div>` : ''}</div>`;
}

function kpisCliente(r, lt) {
  const sub = x => lt ? `long tail: ${fmtMM(x)}` : '';
  let h = kpi('Expectativa atual', fmtMM(r.expectativa_atual), { sub: sub(lt?.expectativa_atual), titulo: 'Realizado faturável + projetado faturável' })
    + kpi('Realizado faturável', fmtMM(r.realizado_fat), { sub: sub(lt?.realizado_fat) })
    + kpi('Projetado faturável', fmtMM(r.projetado_fat), { sub: sub(lt?.projetado_fat) })
    + kpi('Livre', fmtMM(r.livre), { titulo: 'Soma da linha LIVRE nos meses abertos' });
  if (S.reap) h += kpi('Reaplicação', fmtMM(r.reap), { cls: 'reap', sub: lt ? sub(lt.reap) : 'fora das contas de dinheiro' });
  if (r.expectativa_inicial != null) h += kpi('Expectativa inicial', fmtMM(r.expectativa_inicial), { cls: 'cinza', sub: r.expectativa_inicial ? `diferença ${r.diferenca >= 0 ? '+' : ''}${fmtMM(r.diferenca)}` : '' });
  return `<div class="kpis">${h}</div>`;
}

function linhaAmbar(r, fechado) {
  const ab = (r.aberto_fat || 0) + (S.reap ? (r.aberto_reap || 0) : 0);
  if (!ab) return '';
  return `<div class="ambar-linha">Já emitido em PIs nos meses abertos (depois de ${fechado ? MESES[fechado - 1] : '—'}): <b>${fmtMM(r.aberto_fat)}</b> faturável${S.reap && r.aberto_reap ? ` + ${fmtMM(r.aberto_reap)} reaplicação` : ''}. Só vira realizado quando o mês for fechado.</div>`;
}

/** Tabela ordenável e com colunas redimensionáveis. cols: [{k, rot, num, fmt, cls}] */
function tabela(el, { id, cols, linhas, total, padrao, link, classeLinha }) {
  let ord = JSON.parse(lerLocal('ord.' + id) || 'null') || padrao || { k: cols[0].k, d: -1 };
  const larg = JSON.parse(lerLocal('larg.' + id) || '{}');
  let redimensionando = false;
  function desenhar() {
    const ls = [...linhas].sort((a, b) => {
      const x = a[ord.k], y = b[ord.k];
      if (typeof x === 'string' || typeof y === 'string') return ord.d * String(x ?? '').localeCompare(String(y ?? ''), 'pt-BR');
      return ord.d * ((x ?? -Infinity) - (y ?? -Infinity));
    });
    el.innerHTML = `<div class="rolagem"><table><colgroup>${cols.map(c => `<col style="${larg[c.k] ? 'width:' + larg[c.k] + 'px' : ''}">`).join('')}</colgroup>
      <thead><tr>${cols.map(c => `<th class="ord ${c.num ? 'num' : ''}" data-k="${c.k}">${c.rot}${ord.k === c.k ? `<span class="seta">${ord.d > 0 ? '▲' : '▼'}</span>` : ''}<span class="redim"></span></th>`).join('')}</tr></thead>
      <tbody>${ls.map(l => `<tr class="${classeLinha ? classeLinha(l) : ''}">${cols.map((c, i) => `<td class="${c.num ? 'num' : ''} ${c.cls || ''}">${i === 0 && link ? `<a href="${link(l)}">${esc(l[c.k])}</a>` : (c.fmt ? c.fmt(l[c.k], l) : esc(l[c.k]))}</td>`).join('')}</tr>`).join('')}</tbody>
      ${total ? `<tfoot><tr>${cols.map((c, i) => `<td class="${c.num ? 'num' : ''}">${i === 0 ? (total._rot || 'Total') : (c.fmt && total[c.k] !== undefined ? c.fmt(total[c.k], total) : '')}</td>`).join('')}</tr></tfoot>` : ''}
      </table></div>`;
    $$('th.ord', el).forEach(th => th.onclick = () => {
      if (redimensionando) return;
      const k = th.dataset.k;
      ord = ord.k === k ? { k, d: -ord.d } : { k, d: cols.find(c => c.k === k).num ? -1 : 1 };
      gravarLocal('ord.' + id, JSON.stringify(ord)); desenhar();
    });
    ativarRedim(el, (k, w) => { larg[k] = w; gravarLocal('larg.' + id, JSON.stringify(larg)); }, v => redimensionando = v);
  }
  desenhar();
}

/** Arrastar a borda redimensiona; o clique de soltar não dispara a ordenação. */
function ativarRedim(el, aoFim, marcar) {
  $$('th .redim', el).forEach(h => h.onmousedown = e => {
    e.preventDefault(); e.stopPropagation();
    const th = h.parentElement, idx = [...th.parentElement.children].indexOf(th);
    const col = $$('col', el)[idx];
    const x0 = e.clientX, w0 = th.getBoundingClientRect().width;
    marcar(true);
    const mover = ev => { col.style.width = Math.max(40, w0 + ev.clientX - x0) + 'px'; };
    const soltar = ev => {
      document.removeEventListener('mousemove', mover); document.removeEventListener('mouseup', soltar);
      aoFim(th.dataset.k ?? idx, Math.max(40, w0 + ev.clientX - x0));
      setTimeout(() => marcar(false), 0);   // engole o click que vem logo após o mouseup
    };
    document.addEventListener('mousemove', mover); document.addEventListener('mouseup', soltar);
  });
  $$('th .redim', el).forEach(h => h.onclick = e => e.stopPropagation());
}

/**
 * A grade. data: {linhas:[{nome, livre, fat[12], reap[12], aberto_fat, aberto_reap, fat_partes, reap_partes}], total, fechado, metas?}
 * opts: {id, rotNome, link(nome), editavel, longTail, aoMudar(edicoes)}
 * Edição: guarda o valor exato em centavos por baixo; grava só células tocadas.
 */
function grade(el, data, opts) {
  const f = data.fechado;
  const ed = new Map();   // "nome|FAT|m" -> centavos|null
  let expandido = false, redimensionando = false;
  let ord = JSON.parse(lerLocal('gord.' + opts.id) || 'null') || { k: 'ano', d: -1 };
  const larg = JSON.parse(lerLocal('glarg.' + opts.id) || '{}');
  const temMeta = !!data.metas;
  const chaveEd = (n, t, m) => `${n}|${t}|${m}`;
  const val = (it, t, m) => { const k = chaveEd(it.nome, t, m); return ed.has(k) ? ed.get(k) ?? 0 : it[t === 'FAT' ? 'fat' : 'reap'][m]; };
  const partes = (it, t) => {
    let r = 0, p = 0;
    for (let m = 0; m < 12; m++) { const v = val(it, t, m); if (m < f) r += v; else p += v; }
    return { realizado: r, projetado: p, ano: r + p };
  };
  const ordVal = it => {
    if (ord.k === 'nome') return it.nome;
    if (ord.k === 'meta') return data.metas?.[it.nome]?.meta ?? -1;
    if (ord.k === 'pct') return data.metas?.[it.nome]?.pct ?? -1;
    if (/^m\d+$/.test(ord.k)) return val(it, 'FAT', +ord.k.slice(1));
    return partes(it, 'FAT')[ord.k];
  };

  function celula(it, t, m, editavelLinha) {
    const v = val(it, t, m);
    const fut = m >= f;
    const cls = `num ${fut ? 'futuro' : ''} ${m === f - 1 ? 'borda-fechado' : ''}`;
    const ab = fut ? it[t === 'FAT' ? 'aberto_fat' : 'aberto_reap']?.[m] : 0;
    const marca = ab ? `<span class="aberto" title="PIs já emitidos neste mês aberto">PI ${fmtM(ab)}</span>` : '';
    if (editavelLinha && fut) {
      const k = chaveEd(it.nome, t, m);
      return `<td class="${cls}"><input data-k="${esc(k)}" value="${v ? fmtM(v) : ''}" class="${ed.has(k) ? 'tocado' : ''}" title="${fmtR(v)}" inputmode="decimal">${marca}</td>`;
    }
    return `<td class="${cls}" title="R$ ${fmtR(v)}">${fmtM(v)}${marca}</td>`;
  }

  function linhasItem(it, extraCls = '', nomeHtml = null) {
    const editavelLinha = opts.editavel && !it.somenteLeitura;
    const tipos = S.reap ? ['FAT', 'REAP'] : ['FAT'];
    const nome = nomeHtml ?? (opts.link && !it.livre ? `<a href="${opts.link(it.nome)}">${esc(it.nome)}</a>` : esc(it.nome));
    return tipos.map((t, i) => {
      const p = partes(it, t);
      const meta = temMeta && t === 'FAT' && !it.livre ? data.metas[it.nome] : null;
      return `<tr class="${extraCls} ${t === 'REAP' ? 'sub-reap' : (S.reap ? 'fat-de-par' : '')}">
        ${i === 0 ? `<td class="nome" rowspan="${tipos.length}" title="${esc(it.nome)}">${nome}</td>` : ''}
        <td class="tipo">${t === 'FAT' ? 'fat' : 'reap'}</td>
        ${Array.from({ length: 12 }, (_, m) => celula(it, t, m, editavelLinha)).join('')}
        <td class="num col-sum" data-s="${esc(it.nome)}|${t}|r">${fmtM(p.realizado)}</td>
        <td class="num col-sum" data-s="${esc(it.nome)}|${t}|p">${fmtM(p.projetado)}</td>
        <td class="num col-sum" data-s="${esc(it.nome)}|${t}|a"><b>${fmtM(p.ano)}</b></td>
        ${temMeta ? (t === 'FAT' ? `<td class="num">${meta ? fmtM(meta.meta) : ''}</td><td class="num ${meta ? (meta.pct >= (f / 12) ? 'meta-ok' : 'meta-baixo') : ''}">${meta ? pct(p.ano / meta.meta) : ''}</td>` : '<td></td><td></td>') : ''}
      </tr>`;
    }).join('');
  }

  function rodape() {
    const tipos = S.reap ? ['FAT', 'REAP'] : ['FAT'];
    const todos = data.linhas;
    return tipos.map((t, i) => {
      const meses = Array.from({ length: 12 }, (_, m) => todos.reduce((s, it) => s + val(it, t, m), 0));
      const r = meses.slice(0, f).reduce((a, b) => a + b, 0), p = meses.slice(f).reduce((a, b) => a + b, 0);
      const mt = data.metas_total;
      return `<tr class="${t === 'REAP' ? 'sub-reap' : ''}">${i === 0 ? `<td class="nome" rowspan="${tipos.length}">Total</td>` : ''}<td class="tipo">${t === 'FAT' ? 'fat' : 'reap'}</td>
        ${meses.map((v, m) => `<td class="num ${m === f - 1 ? 'borda-fechado' : ''}">${fmtM(v)}</td>`).join('')}
        <td class="num">${fmtM(r)}</td><td class="num">${fmtM(p)}</td><td class="num">${fmtM(r + p)}</td>
        ${temMeta ? (t === 'FAT' ? `<td class="num" title="Só veículos com meta; LIVRE fora dos dois lados">${fmtM(mt.meta)}</td><td class="num" title="faturável dos veículos com meta ÷ meta">${pct(mt.pct)}</td>` : '<td></td><td></td>') : ''}</tr>`;
    }).join('');
  }

  function desenhar() {
    const normais = data.linhas.filter(x => !x.livre);
    const livre = data.linhas.find(x => x.livre);
    normais.sort((a, b) => {
      const x = ordVal(a), y = ordVal(b);
      return typeof x === 'string' ? ord.d * x.localeCompare(y, 'pt-BR') : ord.d * (x - y);
    });
    const vis = expandido ? normais : normais.slice(0, 10);
    const th = (k, rot, cls = '') => `<th class="ord ${cls}" data-k="${k}">${rot}${ord.k === k ? `<span class="seta">${ord.d > 0 ? '▲' : '▼'}</span>` : ''}<span class="redim"></span></th>`;
    const colw = k => larg[k] ? `width:${larg[k]}px` : '';
    el.innerHTML = `<div class="rolagem"><table class="grade">
      <colgroup><col style="${colw('nome') || 'width:170px'}"><col style="width:42px">${MESES.map((_, m) => `<col style="${colw('m' + m) || 'width:68px'}">`).join('')}
        <col style="${colw('realizado') || 'width:84px'}"><col style="${colw('projetado') || 'width:84px'}"><col style="${colw('ano') || 'width:84px'}">${temMeta ? '<col style="width:78px"><col style="width:56px">' : ''}</colgroup>
      <thead><tr>${th('nome', opts.rotNome)}<th></th>${MESES.map((n, m) => th('m' + m, n, `num ${m >= f ? 'futuro' : ''} ${m === f - 1 ? 'borda-fechado' : ''}`)).join('')}
        ${th('realizado', 'Realizado', 'num')}${th('projetado', 'Projetado', 'num')}${th('ano', 'Ano', 'num')}${temMeta ? th('meta', 'Meta', 'num') + th('pct', '% meta', 'num') : ''}</tr></thead>
      <tbody>
        ${opts.longTail ? linhasItem({ ...opts.longTail, somenteLeitura: true }, 'longtail', '<span title="Tudo que não é núcleo (marcado no de-para)">Veículos Long Tail</span>') : ''}
        ${vis.map(it => linhasItem(it)).join('')}
        ${livre ? linhasItem(livre, 'livre') : ''}
      </tbody>
      <tfoot>${rodape()}</tfoot></table></div>
      ${normais.length > 10 ? `<div class="mais"><button class="fantasma" id="expandir">${expandido ? 'Mostrar só os 10 maiores' : `Mostrar todos (${normais.length})`}</button></div>` : ''}`;
    $$('th.ord', el).forEach(t => t.onclick = () => {
      if (redimensionando) return;
      const k = t.dataset.k;
      ord = ord.k === k ? { k, d: -ord.d } : { k, d: k === 'nome' ? 1 : -1 };
      gravarLocal('gord.' + opts.id, JSON.stringify(ord)); desenhar();
    });
    ativarRedim(el, (k, w) => { larg[k] = w; gravarLocal('glarg.' + opts.id, JSON.stringify(larg)); }, v => redimensionando = v);
    const b = $('#expandir', el); if (b) b.onclick = () => { expandido = !expandido; desenhar(); };
    $$('input[data-k]', el).forEach(ligarInput);
  }

  function ligarInput(inp) {
    const k = inp.dataset.k;
    const [nome, t, m] = k.split('|');
    const it = data.linhas.find(x => x.nome === nome);
    const original = it[t === 'FAT' ? 'fat' : 'reap'][+m];
    inp.onfocus = () => { const v = val(it, t, +m); inp.value = v ? fmtR(v) : ''; inp.dataset.txt = inp.value; inp.select(); };
    inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); const ins = $$('input[data-k]', el); const i = ins.indexOf(inp); (ins[i + 1] || inp).focus(); } if (e.key === 'Escape') { inp.value = inp.dataset.txt; inp.blur(); } };
    inp.onblur = () => {
      if (inp.value.trim() !== inp.dataset.txt) {   // só o que foi tocado de verdade
        let c;
        try { c = lerValor(inp.value); } catch (e) { aviso(e.message, true); inp.value = inp.dataset.txt; return; }
        if ((c ?? 0) === original) ed.delete(k); else ed.set(k, c);
      }
      const v = val(it, t, +m);
      inp.value = v ? fmtM(v) : ''; inp.title = fmtR(v);
      inp.classList.toggle('tocado', ed.has(k));
      atualizarSomas(it, t);
      opts.aoMudar && opts.aoMudar(ed);
    };
  }

  function atualizarSomas(it, t) {
    const p = partes(it, t);
    const set = (s, v) => { const c = el.querySelector(`[data-s="${CSS.escape(it.nome + '|' + t + '|' + s)}"]`); if (c) c.innerHTML = s === 'a' ? `<b>${fmtM(v)}</b>` : fmtM(v); };
    set('r', p.realizado); set('p', p.projetado); set('a', p.ano);
    $('tfoot', el).innerHTML = rodape();
  }

  desenhar();
  return {
    edicoes: ed,
    adicionarLinha(nome) {
      if (data.linhas.some(x => x.nome.toLowerCase() === nome.toLowerCase())) return false;
      const z = () => Array(12).fill(0);
      data.linhas.push({ nome, livre: nome.toUpperCase() === 'LIVRE', fat: z(), reap: z(), aberto_fat: z(), aberto_reap: z() });
      expandido = true; desenhar(); return true;
    },
  };
}

// ---------- telas ----------
async function telaInicio(app) {
  const d = await api('/api/inicio' + (S.grupos ? '?grupos=1' : ''));
  if (d.tipo === 'operacao') { location.hash = '#/pis'; return; }
  const cons = d.consolidado, t = cons.total;
  const colsCli = [
    { k: 'cliente', rot: 'Cliente' },
    { k: 'expectativa_atual', rot: 'Expectativa atual', num: 1, fmt: fmtM },
    { k: 'realizado_fat', rot: 'Realizado', num: 1, fmt: fmtM },
    { k: 'projetado_fat', rot: 'Projetado', num: 1, fmt: fmtM },
    { k: 'livre', rot: 'Livre', num: 1, fmt: fmtM },
    ...(S.reap ? [{ k: 'reap', rot: 'Reaplicação', num: 1, fmt: fmtM, cls: 'chave-reap' }] : []),
    { k: 'expectativa_inicial', rot: 'Exp. inicial', num: 1, fmt: fmtM, cls: 'suave' },
    { k: 'diferenca', rot: 'Diferença', num: 1, fmt: v => v == null ? '' : `<span class="${v < 0 ? 'meta-baixo' : ''}">${fmtM(v)}</span>` },
    ...(d.tipo === 'visao' ? [{ k: 'grupo', rot: 'Grupo', fmt: v => v ? `<span class="etiqueta">${esc(v)}</span>` : '' }] : []),
  ];
  const linhas = cons.clientes.filter(c => c.expectativa_atual || c.expectativa_inicial || c.reap || c.aberto_fat);
  if (d.tipo === 'diretor') {
    // diretor não tem visão geral: só escolhe o cliente
    const meus = cons.clientes.filter(c => c.ativo || c.expectativa_atual || c.expectativa_inicial)
      .sort((a, b) => b.expectativa_atual - a.expectativa_atual || a.cliente.localeCompare(b.cliente, 'pt-BR'));
    if (meus.length === 1) { location.replace('#/cliente/' + encodeURIComponent(meus[0].cliente)); return; }
    app.innerHTML = `<div class="cab"><h1>Meus clientes</h1><span class="suave">Escolha um cliente para ver e editar a projeção.</span></div>
      ${meus.length ? `<div class="cartoes-clientes">${meus.map(c => `<a class="cartao-cliente" href="#/cliente/${encodeURIComponent(c.cliente)}">
          <span class="nome">${esc(c.cliente)}</span>
          <span class="val">${fmtMM(c.expectativa_atual)}</span>
          <span class="suave pequeno">expectativa atual${c.expectativa_inicial ? ` · combinado ${fmtMM(c.expectativa_inicial)}` : ''}</span>
          <span class="ir">Abrir →</span></a>`).join('')}</div>`
        : '<p class="alerta">Nenhum cliente está ligado a você ainda. Peça a um admin para definir você como diretor do cliente em Equipe.</p>'}`;
    return;
  }
  app.innerHTML = `<div class="cab"><h1>Visão geral ${S.ano}</h1>
      <span class="suave">mês fechado: <b>${d.fechado ? MESES_X[d.fechado - 1] : 'nenhum'}</b></span>
      ${d.pendentes.veiculos || d.pendentes.clientes ? `<a class="etiqueta ambar" href="#/admin/veiculos">${d.pendentes.veiculos} veículos e ${d.pendentes.clientes} clientes pendentes de canonizar</a>` : ''}
      <div class="dir"><label class="pequeno"><input type="checkbox" id="grupos" ${S.grupos ? 'checked' : ''}> incluir grupos (whitelabel) nos totais</label>
      ${pode('exportar') ? '<button class="fantasma" id="xls">Exportar Excel</button>' : ''}</div></div>
    <div class="kpis">
      ${kpi('Expectativa atual', fmtMM(t.expectativa_atual), { titulo: 'Realizado faturável + projetado faturável, somado por cliente' })}
      ${kpi('Realizado faturável', fmtMM(t.realizado_fat))}
      ${kpi('Projetado faturável', fmtMM(t.projetado_fat))}
      ${kpi('Livre', fmtMM(t.livre), { titulo: 'Soma das linhas LIVRE de cada cliente' })}
      ${S.reap ? kpi('Reaplicação', fmtMM(t.reap), { cls: 'reap', sub: 'fora das contas de dinheiro' }) : ''}
      ${kpi('Expectativa inicial', fmtMM(t.expectativa_inicial), { cls: 'cinza' })}
    </div>
    <div class="chat"><input id="perg" placeholder='Pergunte: "livre por cliente", "Tim na JCDecaux em setembro", "maiores veículos em reap"…'><button id="perg-ok">Perguntar</button></div><div id="resp"></div>
    <h2>Clientes</h2><div id="tab"></div>
    <h2>Consolidado por veículo</h2><div id="grade"></div>`;
  $('#grupos').onchange = e => { S.grupos = e.target.checked; rotear(); };
  if ($('#xls')) $('#xls').onclick = () => baixar(`/api/exportar?escopo=visao&reap=${S.reap ? 1 : 0}&grupos=${S.grupos ? 1 : 0}`);
  tabela($('#tab'), {
    id: 'visao', cols: colsCli, linhas, total: t, padrao: { k: 'expectativa_atual', d: -1 },
    link: l => '#/cliente/' + encodeURIComponent(l.cliente), classeLinha: l => l.em_totais ? '' : 'fora',
  });
  grade($('#grade'), d.grade, { id: 'visao', rotNome: 'Veículo', link: n => '#/veiculo/' + encodeURIComponent(n) });
  ligarChat();
}

function ligarChat() {
  const ir = async () => {
    const q = $('#perg').value.trim(); if (!q) return;
    try {
      const r = await api(`/api/chat?q=${encodeURIComponent(q)}&reap=${S.reap ? 1 : 0}&grupos=${S.grupos ? 1 : 0}&casas=${S.casas}`);
      $('#resp').innerHTML = `<div class="resposta">${esc(r.texto)}</div>`;
    } catch (e) { aviso(e.message, true); }
  };
  $('#perg-ok').onclick = ir;
  $('#perg').onkeydown = e => { if (e.key === 'Enter') ir(); };
}

async function telaCliente(app, nome) {
  const d = await api('/api/cliente?nome=' + encodeURIComponent(nome));
  const r = d.resumo;
  const exp = r.expectativa_inicial;
  app.innerHTML = `${!pode('visao_geral') ? '<a class="voltar" href="#/">← Meus clientes</a>' : ''}<div class="cab"><h1>${esc(d.cliente)}</h1>
      <button class="pilula ${d.pode_editar ? 'clicavel' : ''}" id="exp" ${d.pode_editar ? '' : 'disabled'} title="Expectativa inicial: o combinado com o cliente para o ano">Expectativa inicial: <b>${exp ? fmtMM(exp) : 'definir'}</b></button>
      ${r.grupo ? `<span class="etiqueta">${esc(r.grupo)} · fora dos totais</span>` : ''}
      ${r.ativo ? '' : '<span class="etiqueta">inativo</span>'}
      <span class="suave pequeno">diretor: ${esc(r.diretor_email || '—')}</span>
      <div class="dir"><span class="suave pequeno">mês fechado: <b>${d.fechado ? MESES_X[d.fechado - 1] : 'nenhum'}</b></span>${pode('exportar') ? '<button class="fantasma" id="xls">Exportar Excel</button>' : ''}</div></div>
    <div class="pacotes" id="pacotes"></div>
    ${kpisCliente(r, d.long_tail?.resumo)}
    ${linhaAmbar(r, d.fechado)}
    <div id="grade"></div>
    ${d.pode_editar ? `<div class="linha-form" style="margin-top:10px"><input id="novo-v" list="lista-v" placeholder="Adicionar veículo à grade…" style="min-width:260px"><datalist id="lista-v">${['LIVRE', ...d.veiculos].map(v => `<option value="${esc(v)}">`).join('')}</datalist><button class="fantasma" id="add-v">Adicionar linha</button>
      <span class="suave pequeno">Meses depois de ${d.fechado ? MESES[d.fechado - 1] : '—'} são editáveis. Digite em reais (1.234,56) ou com sufixo (1,5M · 300k). Veículo que não está na lista: lance em LIVRE e peça o cadastro.</span></div>` : ''}`;
  if ($('#xls')) $('#xls').onclick = () => baixar(`/api/exportar?escopo=cliente&nome=${encodeURIComponent(d.cliente)}&reap=${S.reap ? 1 : 0}`);
  // pacotes
  const pc = $('#pacotes');
  pc.innerHTML = d.pacotes.map(p => `<div class="pacote" data-id="${esc(p.id)}"><div class="suave pequeno">${esc(p.veiculo || 'sem veículo')} · ${esc(p.status || '')}</div><div class="v">${p.valor != null ? fmtMM(p.valor) : '—'}</div><div class="pequeno">${esc(p.descricao || '')}</div>${p.inicio || p.fim ? `<div class="pequeno suave">${esc((p.inicio || '').slice(0, 10))} → ${esc((p.fim || '').slice(0, 10))}</div>` : ''}</div>`).join('')
    + (d.pode_editar ? '<div class="pacote novo" id="novo-pacote">+ pacote</div>' : '');
  $$('.pacote[data-id]', pc).forEach(el => el.onclick = () => d.pode_editar && editarPacote(d, d.pacotes.find(p => p.id === el.dataset.id)));
  const np = $('#novo-pacote'); if (np) np.onclick = () => editarPacote(d, null);
  // expectativa
  if (d.pode_editar) $('#exp').onclick = () => modal(`<h3>Expectativa inicial ${S.ano} — ${esc(d.cliente)}</h3>
      <div class="form"><label>Valor combinado com o cliente (R$)<input id="v" value="${exp ? fmtR(exp) : ''}"></label>
      <div class="botoes"><button class="fantasma" data-fechar>Cancelar</button><button id="ok">Salvar</button></div></div>`, (el, fechar) => {
    $('#ok', el).onclick = async () => {
      try { const c = lerValor($('#v', el).value); await api('/api/expectativa', { method: 'POST', body: { cliente: d.cliente, ano: S.ano, valor: paraApi(c) } }); fechar(); aviso('Expectativa salva.'); rotear(); }
      catch (e) { aviso(e.message, true); }
    };
  });
  // grade
  const lt = d.long_tail ? { nome: 'Veículos Long Tail', fat: d.long_tail.fat, reap: d.long_tail.reap, aberto_fat: Array(12).fill(0), aberto_reap: Array(12).fill(0) } : null;
  const g = grade($('#grade'), d.grade, {
    id: 'cliente', rotNome: 'Veículo', link: n => '#/veiculo/' + encodeURIComponent(n), editavel: d.pode_editar, longTail: lt,
    aoMudar: ed => definirPendente({
      n: ed.size,
      salvar: () => api('/api/projecao', {
        method: 'POST', body: {
          ano: S.ano, cliente: d.cliente,
          celulas: [...ed].map(([k, c]) => { const [veiculo, tipo, m] = k.split('|'); return { veiculo, tipo, mes: +m + 1, valor: paraApi(c) }; }),
        }
      }),
    }),
  });
  if (d.pode_editar) {
    $('#add-v').onclick = () => {
      const v = $('#novo-v').value.trim(); if (!v) return;
      const ok = v.toUpperCase() === 'LIVRE' || d.veiculos.some(x => x.toLowerCase() === v.toLowerCase());
      if (!ok) { aviso('Veículo não cadastrado. Lance em LIVRE e peça o cadastro ao admin.', true); return; }
      const canon = v.toUpperCase() === 'LIVRE' ? 'LIVRE' : d.veiculos.find(x => x.toLowerCase() === v.toLowerCase());
      if (!g.adicionarLinha(canon)) aviso('Esse veículo já está na grade.'); else $('#novo-v').value = '';
    };
  }
}

function editarPacote(d, p) {
  p = p || {};
  modal(`<h3>${p.id ? 'Editar' : 'Novo'} pacote — ${esc(d.cliente)}</h3>
    <div class="form">
      <label>Veículo<input id="pv" list="pvl" value="${esc(p.veiculo || '')}"><datalist id="pvl">${d.veiculos.map(v => `<option value="${esc(v)}">`).join('')}</datalist></label>
      <label>Valor (R$)<input id="pval" value="${p.valor != null ? fmtR(p.valor) : ''}"></label>
      <div class="linha-form"><label>Início<input type="date" id="pi" value="${esc((p.inicio || '').slice(0, 10))}"></label><label>Fim<input type="date" id="pf" value="${esc((p.fim || '').slice(0, 10))}"></label>
      <label>Status<select id="ps">${['em negociação', 'assinado', 'cancelado', 'encerrado'].map(s => `<option ${s === (p.status || 'em negociação') ? 'selected' : ''}>${s}</option>`).join('')}</select></label></div>
      <label>Descrição<input id="pd" value="${esc(p.descricao || '')}"></label>
      <div class="botoes">${p.id ? '<button class="perigo" id="apagar" style="margin-right:auto">Apagar</button>' : ''}<button class="fantasma" data-fechar>Cancelar</button><button id="ok">Salvar</button></div>
    </div>`, (el, fechar) => {
    $('#ok', el).onclick = async () => {
      try {
        await api('/api/pacote', { method: 'POST', body: { id: p.id, cliente: d.cliente, veiculo: $('#pv', el).value, valor: paraApi(lerValor($('#pval', el).value)), inicio: $('#pi', el).value, fim: $('#pf', el).value, status: $('#ps', el).value, descricao: $('#pd', el).value } });
        fechar(); rotear();
      } catch (e) { aviso(e.message, true); }
    };
    const ap = $('#apagar', el);
    if (ap) ap.onclick = async () => { if (!confirm('Apagar este pacote? (dá para desfazer na Administração)')) return; try { await api('/api/pacote/apagar', { method: 'POST', body: { id: p.id } }); fechar(); rotear(); } catch (e) { aviso(e.message, true); } };
  });
}

async function telaVeiculo(app, nome) {
  const d = await api('/api/veiculo?nome=' + encodeURIComponent(nome) + (S.grupos ? '&grupos=1' : ''));
  const tot = d.grade.total;
  app.innerHTML = `<div class="cab"><h1>${esc(d.veiculo)}</h1>
      ${d.meta ? `<span class="pilula">Meta ${S.ano}: <b>${fmtMM(d.meta.meta)}</b> · ${pct(d.meta.pct)} atingido (só faturável)</span>` : ''}
      <span class="suave pequeno" title="${esc(d.grafias.join(' · '))}">${d.grafias.length} grafias no de-para</span>
      <div class="dir">${pode('exportar') ? '<button class="fantasma" id="xls">Exportar Excel</button>' : ''}</div></div>
    <div class="kpis">${kpi('Faturável no ano', fmtMM(tot.fat_partes.ano), { sub: `realizado ${fmtMM(tot.fat_partes.realizado)} · projetado ${fmtMM(tot.fat_partes.projetado)}` })}
      ${S.reap ? kpi('Reaplicação', fmtMM(tot.reap_partes.ano), { cls: 'reap' }) : ''}</div>
    <div id="grade"></div>`;
  if ($('#xls')) $('#xls').onclick = () => baixar(`/api/exportar?escopo=veiculo&nome=${encodeURIComponent(d.veiculo)}&reap=${S.reap ? 1 : 0}`);
  grade($('#grade'), d.grade, { id: 'veiculo', rotNome: 'Cliente', link: n => '#/cliente/' + encodeURIComponent(n) });
}

// ---------- PIs ----------
async function telaPis(app, busca = '') {
  const d = await api('/api/pis?q=' + encodeURIComponent(busca));
  const hoje = new Date(d.hoje + 'T12:00');
  const maxFechavel = d.ano < hoje.getFullYear() ? 12 : d.ano === hoje.getFullYear() ? hoje.getMonth() : 0;
  const porMes = Object.fromEntries(d.por_mes.map(m => [m.mes, m]));
  app.innerHTML = `<div class="cab"><h1>PIs ${d.ano}</h1>
      <div class="dir"><button class="fantasma" id="modelo">Baixar modelo</button><button class="fantasma" id="novo">+ PI individual</button><button class="perigo" id="apagar">Apagar todos de ${d.ano}</button></div></div>
    <div class="cartao"><b>Fechamento do mês</b>
      <p class="suave pequeno" style="margin:4px 0 10px">O mês fechado decide o que é fato. Meses até ele usam os PIs; depois dele, a projeção. Ele nunca muda sozinho pelo calendário.</p>
      <div class="linha-form"><span>Mês fechado de ${d.ano}:</span>
        <select id="mes" ${d.pode_fechar ? '' : 'disabled'}><option value="0">nenhum</option>${MESES_X.map((m, i) => `<option value="${i + 1}" ${i + 1 === d.fechado ? 'selected' : ''} ${i + 1 > maxFechavel ? 'disabled' : ''}>${m}${porMes[i + 1] ? ` · ${porMes[i + 1].n} PIs` : ' · sem PIs'}</option>`).join('')}</select>
        <button id="fechar" ${d.pode_fechar ? '' : 'disabled'}>Confirmar</button>
        ${!d.pode_fechar ? '<span class="suave pequeno">só admin fecha o mês</span>' : ''}</div>
      ${d.fechado < maxFechavel && porMes[d.fechado + 1] ? `<div class="ambar-linha" style="margin:10px 0 0">${MESES_X[d.fechado]} já terminou e tem ${porMes[d.fechado + 1].n} PIs carregados, mas ainda aparece como projeção. Confira se os PIs estão completos e feche o mês.</div>` : ''}
    </div>
    <div class="soltar" id="soltar">Arraste aqui o .xlsx ou .csv de PIs, ou <label style="text-decoration:underline;cursor:pointer">escolha o arquivo<input type="file" id="arq" accept=".xlsx,.csv" hidden></label>
      <div class="linha-form" style="justify-content:center;margin-top:10px"><label class="pequeno"><input type="radio" name="modo" value="substituir_ano" checked> substituir o ano inteiro dos anos do arquivo</label>
      <label class="pequeno"><input type="radio" name="modo" value="substituir_meses"> substituir só os meses presentes no arquivo</label></div></div>
    <h2>Por mês</h2><div id="pormes"></div>
    <h2>Linhas</h2><div class="linha-form"><input id="busca" placeholder="Buscar número, cliente, veículo…" value="${esc(busca)}" style="min-width:300px"><span class="suave pequeno">${d.total_linhas} linhas${d.total_linhas > 500 ? ' (mostrando 500)' : ''}</span></div>
    <div id="linhas"></div>`;
  tabela($('#pormes'), {
    id: 'pis-mes', padrao: { k: 'mes', d: 1 }, linhas: d.por_mes.map(m => ({ ...m, nome: MESES_X[m.mes - 1], status: m.mes <= d.fechado ? 'fechado' : 'aberto' })),
    cols: [{ k: 'nome', rot: 'Mês' }, { k: 'status', rot: 'Situação', fmt: v => v === 'aberto' ? '<span class="etiqueta ambar">aberto</span>' : '<span class="etiqueta">fechado</span>' }, { k: 'n', rot: 'PIs', num: 1, fmt: v => v }, { k: 'FAT', rot: 'Faturável', num: 1, fmt: fmtM }, { k: 'REAP', rot: 'Reaplicação', num: 1, fmt: fmtM }],
  });
  tabela($('#linhas'), {
    id: 'pis-linhas', padrao: { k: 'mes', d: -1 }, linhas: d.linhas,
    cols: [{ k: 'numero', rot: 'PI' }, { k: 'mes', rot: 'Mês', fmt: v => MESES[v - 1] }, { k: 'cliente', rot: 'Cliente', fmt: (v, l) => esc(v) + (v.toLowerCase() !== l.cliente_bruto.toLowerCase() ? ` <span class="suave pequeno">(${esc(l.cliente_bruto)})</span>` : '') },
    { k: 'veiculo', rot: 'Veículo', fmt: (v, l) => esc(v) + (v.toLowerCase() !== l.veiculo_bruto.toLowerCase() ? ` <span class="suave pequeno">(${esc(l.veiculo_bruto)})</span>` : '') },
    { k: 'tipo', rot: 'Tipo', fmt: v => v === 'FAT' ? 'Veiculado' : '<span class="chave-reap">Reaplicação</span>' }, { k: 'valor', rot: 'Valor (R$)', num: 1, fmt: fmtR },
    { k: 'id', rot: '', fmt: v => `<button class="link" data-ed="${v}">editar</button>` }],
  });
  $$('[data-ed]').forEach(b => b.onclick = () => editarPi(d.linhas.find(l => l.id === +b.dataset.ed)));
  let t; $('#busca').oninput = e => { clearTimeout(t); t = setTimeout(() => telaPis(app, e.target.value).then(() => { const i = $('#busca'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }), 300); };
  $('#modelo').onclick = () => baixar('/api/pis/modelo');
  $('#novo').onclick = () => editarPi(null);
  $('#fechar').onclick = async () => {
    const m = +$('#mes').value;
    if (m === d.fechado) return aviso('Esse já é o mês fechado.');
    const txt = m > d.fechado ? `Fechar até ${m ? MESES_X[m - 1] : '—'}: os PIs desses meses passam a ser o realizado e a projeção deles deixa de aparecer (continua gravada).`
      : `Reabrir: ${m ? 'meses depois de ' + MESES_X[m - 1] : 'todos os meses'} voltam a mostrar a projeção.`;
    if (!confirm(txt + '\n\nConfirmar?')) return;
    try { await api('/api/mes-fechado', { method: 'POST', body: { ano: d.ano, mes: m } }); aviso('Mês fechado atualizado.'); rotear(); } catch (e) { aviso(e.message, true); }
  };
  $('#apagar').onclick = async () => {
    const pal = `APAGAR ${d.ano}`;
    if (!await confirmarForte(`Apagar todos os PIs de ${d.ano}`, `Isso remove ${d.por_mes.reduce((s, m) => s + m.n, 0)} linhas. Dá para desfazer em Administração → Desfazer.`, pal)) return;
    try { const r = await api('/api/pis/apagar-tudo', { method: 'POST', body: { ano: d.ano, confirmacao: pal } }); aviso(`${r.apagadas} PIs apagados.`); rotear(); } catch (e) { aviso(e.message, true); }
  };
  const zona = $('#soltar');
  const enviar = async arq => {
    const modo = $('input[name=modo]:checked').value;
    try {
      const p = await api('/api/pis/previa?modo=' + modo, { method: 'POST', raw: await arq.arrayBuffer(), headers: { 'X-Filename': encodeURIComponent(arq.name), 'Content-Type': 'application/octet-stream' } });
      previaPis(p);
    } catch (e) { aviso(e.message, true); }
  };
  zona.ondragover = e => { e.preventDefault(); zona.classList.add('sobre'); };
  zona.ondragleave = () => zona.classList.remove('sobre');
  zona.ondrop = e => { e.preventDefault(); zona.classList.remove('sobre'); const f = e.dataTransfer.files[0]; if (f) enviar(f); };
  $('#arq').onchange = e => { const f = e.target.files[0]; if (f) enviar(f); e.target.value = ''; };
}

function previaPis(p) {
  const totF = p.por_mes.reduce((s, m) => s + m.FAT, 0), totR = p.por_mes.reduce((s, m) => s + m.REAP, 0);
  const desc = Object.entries(p.descartados);
  modal(`<h3>Conferir antes de gravar — ${esc(p.arquivo)}</h3>
    <p><b>${p.validos}</b> PIs válidos · faturável <b>R$ ${fmtR(totF)}</b> · reaplicação R$ ${fmtR(totR)}</p>
    ${desc.length ? `<p class="suave">Descartados (outros tipos): ${desc.map(([t, n]) => `${esc(t)}: ${n}`).join(' · ')}</p>` : '<p class="suave">Nenhum descartado.</p>'}
    ${p.n_erros ? `<div class="alerta">${p.n_erros} linhas com erro (não serão gravadas):<br>${p.erros.map(esc).join('<br>')}</div>` : ''}
    <div class="rolagem" style="margin:10px 0"><table><thead><tr><th>Mês</th><th class="num">PIs</th><th class="num">Faturável</th><th class="num">Reap</th></tr></thead><tbody>
      ${p.por_mes.map(m => `<tr><td>${m.mes}</td><td class="num">${m.n}</td><td class="num">${fmtR(m.FAT)}</td><td class="num">${fmtR(m.REAP)}</td></tr>`).join('')}</tbody></table></div>
    <p>${p.modo === 'substituir_ano' ? `Vai <b>substituir os anos ${p.anos.join(', ')}</b>` : '<b>Vai substituir só os meses acima</b>'}: saem ${p.sai.linhas} linhas (R$ ${fmtR(p.sai.valor)}).</p>
    ${p.novos_veiculos.length || p.novos_clientes.length ? `<div class="ambar-linha">Nomes novos entram como pendentes (continuam somando): ${[...p.novos_veiculos, ...p.novos_clientes].map(esc).join(', ')}</div>` : ''}
    <div class="form"><div class="botoes"><button class="fantasma" data-fechar>Cancelar</button><button id="ok">Gravar ${p.validos} PIs</button></div></div>`, (el, fechar) => {
    $('#ok', el).onclick = async () => {
      $('#ok', el).disabled = true;
      try { const r = await api('/api/pis/confirmar', { method: 'POST', body: { token: p.token } }); fechar(); aviso(`${r.importadas} PIs gravados. Dá para desfazer em Administração.`); rotear(); }
      catch (e) { aviso(e.message, true); $('#ok', el).disabled = false; }
    };
  });
}

function editarPi(p) {
  p = p || {};
  modal(`<h3>${p.id ? 'Editar PI' : 'Novo PI'}</h3><div class="form">
    <div class="linha-form"><label>Número<input id="n" value="${esc(p.numero || '')}"></label><label>Mês (MM/AAAA)<input id="m" value="${p.mes ? String(p.mes).padStart(2, '0') + '/' + p.ano : '/' + S.ano}"></label>
    <label>Tipo<select id="t"><option ${p.tipo !== 'REAP' ? 'selected' : ''}>Veiculado</option><option ${p.tipo === 'REAP' ? 'selected' : ''}>Reaplicação</option></select></label></div>
    <label>Cliente (como vem no PI)<input id="c" value="${esc(p.cliente_bruto || '')}"></label>
    <label>Veículo (como vem no PI)<input id="v" value="${esc(p.veiculo_bruto || '')}"></label>
    <label>Valor (R$)<input id="val" value="${p.valor != null ? fmtR(p.valor) : ''}"></label>
    <div class="botoes">${p.id ? '<button class="perigo" id="rm" style="margin-right:auto">Remover</button>' : ''}<button class="fantasma" data-fechar>Cancelar</button><button id="ok">Salvar</button></div></div>`, (el, fechar) => {
    $('#ok', el).onclick = async () => {
      try {
        await api('/api/pis/salvar', { method: 'POST', body: { id: p.id, numero: $('#n', el).value, mes: $('#m', el).value, tipo: $('#t', el).value, cliente_bruto: $('#c', el).value, veiculo_bruto: $('#v', el).value, valor: paraApi(lerValor($('#val', el).value)) } });
        fechar(); aviso('PI salvo.'); rotear();
      } catch (e) { aviso(e.message, true); }
    };
    const rm = $('#rm', el);
    if (rm) rm.onclick = async () => { if (!confirm('Remover este PI?')) return; try { await api('/api/pis/remover', { method: 'POST', body: { id: p.id } }); fechar(); rotear(); } catch (e) { aviso(e.message, true); } };
  });
}

// ---------- equipe ----------
async function telaEquipe(app) {
  const d = await api('/api/equipe');
  const papeis = d.pode_papeis_admin ? ['superadmin', 'admin', 'diretor', 'operacao'] : ['diretor', 'operacao'];
  const diretores = d.pessoas.filter(p => p.ativo && ['diretor', 'admin', 'superadmin'].includes(p.papel));
  app.innerHTML = `<div class="cab"><h1>Equipe</h1>${d.pendentes.veiculos || d.pendentes.clientes ? `<a class="etiqueta ambar" href="#/admin/veiculos">${d.pendentes.veiculos} veículos e ${d.pendentes.clientes} clientes pendentes</a>` : ''}
      <div class="dir"><button id="nova">+ pessoa</button></div></div>
    <div class="rolagem"><table><thead><tr><th>Nome</th><th>Email</th><th>Papel</th><th>Ativo</th><th></th></tr></thead><tbody>
      ${d.pessoas.map(p => {
    const travado = !d.pode_papeis_admin && ['admin', 'superadmin'].includes(p.papel);
    return `<tr data-e="${esc(p.email)}" class="${p.ativo ? '' : 'fora'}"><td><input class="nome" value="${esc(p.nome || '')}" ${travado ? 'disabled' : ''}></td><td>${esc(p.email)}</td>
        <td><select class="papel" ${travado ? 'disabled' : ''}>${(travado ? [p.papel] : papeis).map(x => `<option ${x === p.papel ? 'selected' : ''}>${x}</option>`).join('')}</select></td>
        <td><input type="checkbox" class="ativo" ${p.ativo ? 'checked' : ''} ${travado ? 'disabled' : ''}></td><td>${travado ? '<span class="suave pequeno">só superadmin</span>' : '<button class="fantasma salvar">Salvar</button>'}</td></tr>`;
  }).join('')}</tbody></table></div>
    <h2>Clientes — diretor e grupo</h2>
    <p class="suave pequeno">Um cliente, um diretor. Grupo preenchido (ex.: whitelabel) deixa o cliente fora dos totais por padrão. Grupo de negociação (ex.: ZAMP) é só informativo por enquanto.</p>
    <div class="rolagem"><table><thead><tr><th>Cliente</th><th>Diretor</th><th>Grupo (fora dos totais)</th><th>Grupo de negociação</th><th>Ativo</th><th></th></tr></thead><tbody>
      ${d.clientes.map(c => `<tr data-c="${esc(c.cliente)}" class="${c.ativo ? '' : 'fora'}"><td><a href="#/cliente/${encodeURIComponent(c.cliente)}">${esc(c.cliente)}</a>${c.grafias.length > 1 ? ` <span class="suave pequeno">${c.grafias.length} grafias</span>` : ''}</td>
        <td><select class="dir-sel"><option value="">—</option>${diretores.map(p => `<option value="${esc(p.email)}" ${p.email === c.diretor_email ? 'selected' : ''}>${esc(p.nome || p.email)}</option>`).join('')}
          ${c.diretor_email && !diretores.some(p => p.email === c.diretor_email) ? `<option selected value="${esc(c.diretor_email)}">⚠ ${esc(c.diretor_email)} (inativo/fora da equipe)</option>` : ''}</select></td>
        <td><input class="grupo" value="${esc(c.grupo || '')}" placeholder="—"></td><td><input class="gneg" value="${esc(c.grupo_negociacao || '')}" placeholder="—"></td>
        <td><input type="checkbox" class="ativo" ${c.ativo ? 'checked' : ''}></td><td><button class="fantasma salvar">Salvar</button></td></tr>`).join('')}
    </tbody></table></div>`;
  $$('tr[data-e] .salvar').forEach(b => b.onclick = async () => {
    const tr = b.closest('tr');
    try { await api('/api/pessoa', { method: 'POST', body: { email: tr.dataset.e, nome: $('.nome', tr).value, papel: $('.papel', tr).value, ativo: $('.ativo', tr).checked } }); aviso('Salvo.'); rotear(); }
    catch (e) { aviso(e.message, true); }
  });
  $$('tr[data-c] .salvar').forEach(b => b.onclick = async () => {
    const tr = b.closest('tr');
    try { await api('/api/cliente/atributos', { method: 'POST', body: { cliente: tr.dataset.c, diretor_email: $('.dir-sel', tr).value, grupo: $('.grupo', tr).value, grupo_negociacao: $('.gneg', tr).value, ativo: $('.ativo', tr).checked } }); aviso('Salvo.'); rotear(); }
    catch (e) { aviso(e.message, true); }
  });
  $('#nova').onclick = () => modal(`<h3>Nova pessoa</h3><div class="form"><label>Email<input id="e" placeholder="nome@streetwise.com.br"></label><label>Nome<input id="n"></label>
    <label>Papel<select id="p">${papeis.map(x => `<option ${x === 'diretor' ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    <div class="botoes"><button class="fantasma" data-fechar>Cancelar</button><button id="ok">Adicionar</button></div></div>`, (el, fechar) => {
    $('#ok', el).onclick = async () => { try { await api('/api/pessoa', { method: 'POST', body: { email: $('#e', el).value, nome: $('#n', el).value, papel: $('#p', el).value, ativo: true } }); fechar(); rotear(); } catch (e) { aviso(e.message, true); } };
  });
}

// ---------- administração ----------
async function telaAdmin(app, aba) {
  const abas = [
    ['veiculos', 'De-para de veículos', 'depara'], ['fusoes', 'Sugestão de fusões', 'depara'], ['clientes', 'De-para de clientes', 'depara'],
    ['longtail', 'Long tail (Mondelez)', 'depara'], ['colar', 'Colar projeção', 'colar_projecao'], ['metas', 'Metas', 'visao_geral'],
    ['desfazer', 'Desfazer', 'desfazer'], ['uso', 'Uso e log', 'uso'],
  ].filter(a => pode(a[2]));
  if (!abas.length) throw new Error('sem acesso');
  if (!abas.some(a => a[0] === aba)) aba = abas[0][0];
  app.innerHTML = `<div class="cab"><h1>Administração</h1></div><div class="abas">${abas.map(a => `<a href="#/admin/${a[0]}" class="${a[0] === aba ? 'ativo' : ''}">${a[1]}</a>`).join('')}</div><div id="aba"></div>`;
  const el = $('#aba');
  await ({ veiculos: admVeiculos, fusoes: admFusoes, clientes: admClientes, longtail: admLongTail, colar: admColar, metas: admMetas, desfazer: admDesfazer, uso: admUso })[aba](el);
}

async function admVeiculos(el, filtro = '', so = 'todos') {
  const d = await api('/api/depara/veiculos');
  const canons = d.canonicos.map(c => c.canonico);
  const k = s => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  const linhas = d.linhas.filter(l => (!filtro || k(l.nome_bruto + ' ' + l.canonico).includes(k(filtro))) && (so === 'todos' || l.status === 'pendente'));
  const pend = d.linhas.filter(l => l.status === 'pendente').length;
  el.innerHTML = `<p class="suave pequeno">Os PIs ficam gravados crus; corrigir aqui corrige o histórico inteiro, sem novo upload. Comparação ignora maiúsculas e acentos. LIVRE é reservado.</p>
    <div class="linha-form"><input id="f" placeholder="Buscar grafia ou canônico…" value="${esc(filtro)}" style="min-width:280px">
      <select id="so"><option value="todos">todas as grafias</option><option value="pend" ${so !== 'todos' ? 'selected' : ''}>só pendentes (${pend})</option></select>
      <span style="margin-left:auto"></span><input id="massa" list="canons" placeholder="Canônico para as selecionadas" style="min-width:240px"><datalist id="canons">${canons.map(c => `<option value="${esc(c)}">`).join('')}</datalist>
      <button id="aplicar">Aplicar às selecionadas</button><button class="fantasma" id="nova">+ grafia</button></div>
    <div class="rolagem"><table><thead><tr><th><input type="checkbox" id="todas"></th><th>Grafia (como vem no PI)</th><th>Canônico</th><th>Status</th><th class="num">PIs</th><th class="num">Faturável</th><th class="num">Reap</th></tr></thead><tbody>
      ${linhas.sort((a, b) => b.FAT - a.FAT).map(l => `<tr><td><input type="checkbox" class="sel" value="${l.id}"></td><td>${esc(l.nome_bruto)}</td><td>${esc(l.canonico)}</td>
        <td>${l.status === 'pendente' ? '<span class="etiqueta ambar">pendente</span>' : ''}</td><td class="num">${l.pis}</td><td class="num">${fmtM(l.FAT)}</td><td class="num">${fmtM(l.REAP)}</td></tr>`).join('')}
    </tbody></table></div>
    <h2>Canônicos (${d.canonicos.length}) — por movimento</h2>
    <p class="suave pequeno">Renomear um canônico atualiza projeção, metas, pacotes e o núcleo do long tail. Renomear para um canônico que já existe funde os dois.</p>
    <div class="rolagem"><table><thead><tr><th>Canônico</th><th class="num">Grafias</th><th class="num">PIs</th><th class="num">Faturável</th><th class="num">% fat.</th><th></th></tr></thead><tbody>
      ${(() => { const tot = d.canonicos.reduce((s, c) => s + c.FAT, 0) || 1; return d.canonicos.map(c => `<tr><td><a href="#/veiculo/${encodeURIComponent(c.canonico)}">${esc(c.canonico)}</a></td><td class="num">${c.grafias}</td><td class="num">${c.pis}</td><td class="num">${fmtM(c.FAT)}</td><td class="num">${(c.FAT / tot * 100).toFixed(1)}%</td><td><button class="link" data-ren="${esc(c.canonico)}">renomear / fundir</button></td></tr>`).join(''); })()}
    </tbody></table></div>`;
  let t; $('#f').oninput = e => { clearTimeout(t); t = setTimeout(() => admVeiculos(el, e.target.value, $('#so').value).then(() => { const i = $('#f'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }), 250); };
  $('#so').onchange = e => admVeiculos(el, filtro, e.target.value);
  $('#todas').onchange = e => $$('.sel', el).forEach(c => c.checked = e.target.checked);
  $('#aplicar').onclick = async () => {
    const ids = $$('.sel:checked', el).map(c => +c.value), canon = $('#massa').value.trim();
    if (!ids.length || !canon) return aviso('Selecione grafias e informe o canônico.', true);
    if (!canons.some(c => c.toLowerCase() === canon.toLowerCase()) && !confirm(`"${canon}" é um canônico novo. Criar?`)) return;
    try { await api('/api/depara/veiculos', { method: 'POST', body: { ids, canonico: canon } }); aviso(`${ids.length} grafias → ${canon}`); admVeiculos(el, filtro, so); } catch (e) { aviso(e.message, true); }
  };
  $('#nova').onclick = () => modal(`<h3>Nova grafia</h3><div class="form"><label>Grafia (como vem no PI)<input id="b"></label><label>Canônico<input id="c" list="canons2"><datalist id="canons2">${canons.map(c => `<option value="${esc(c)}">`).join('')}</datalist></label>
    <div class="botoes"><button class="fantasma" data-fechar>Cancelar</button><button id="ok">Adicionar</button></div></div>`, (m, fechar) => {
    $('#ok', m).onclick = async () => { try { await api('/api/depara/veiculos/novo', { method: 'POST', body: { nome_bruto: $('#b', m).value, canonico: $('#c', m).value } }); fechar(); admVeiculos(el, filtro, so); } catch (e) { aviso(e.message, true); } };
  });
  $$('[data-ren]', el).forEach(b => b.onclick = () => renomear('veiculo', b.dataset.ren, canons, () => admVeiculos(el, filtro, so)));
}

function renomear(tipo, de, existentes, depois) {
  modal(`<h3>Renomear ${tipo} “${esc(de)}”</h3><div class="form"><label>Novo nome (ou um existente, para fundir)<input id="p" list="ex" value="${esc(de)}"><datalist id="ex">${existentes.map(c => `<option value="${esc(c)}">`).join('')}</datalist></label>
    <div class="botoes"><button class="fantasma" data-fechar>Cancelar</button><button id="ok">Confirmar</button></div></div>`, (m, fechar) => {
    $('#ok', m).onclick = async () => {
      const para = $('#p', m).value.trim();
      const funde = existentes.some(c => c.toLowerCase() === para.toLowerCase() && c.toLowerCase() !== de.toLowerCase());
      if (funde && !confirm(`"${para}" já existe: "${de}" será fundido nele (projeção e metas somadas). Dá para desfazer. Confirmar?`)) return;
      try { await api('/api/depara/renomear', { method: 'POST', body: { tipo, de, para } }); fechar(); aviso('Feito.'); depois(); } catch (e) { aviso(e.message, true); }
    };
  });
}

async function admFusoes(el) {
  const d = await api('/api/depara/sugestoes');
  el.innerHTML = `<p class="suave pequeno">Pares de canônicos com nome parecido, depois de tirar razão social, praça e palavras genéricas (outdoor, shopping, mídia…). É só sugestão: ES Outdoor e Cuiabá Outdoor são empresas diferentes, e subsidiárias (Otima → Eletromidia) não aparecem aqui — isso se resolve perguntando.</p>
    ${d.pares.length ? `<div class="rolagem"><table><thead><tr><th>A</th><th>B</th><th class="num">Similaridade</th><th>Motivo</th><th></th></tr></thead><tbody>
      ${d.pares.map((p, i) => `<tr><td><a href="#/veiculo/${encodeURIComponent(p.a)}">${esc(p.a)}</a></td><td><a href="#/veiculo/${encodeURIComponent(p.b)}">${esc(p.b)}</a></td><td class="num">${p.score}</td><td class="suave pequeno">${esc(p.motivo)}</td>
        <td><button class="fantasma" data-f="${i}" data-para="a">fundir em A</button> <button class="fantasma" data-f="${i}" data-para="b">fundir em B</button> <button class="link" data-ig="${i}">não são o mesmo</button></td></tr>`).join('')}</tbody></table></div>` : '<p>Nenhuma sugestão no momento.</p>'}`;
  $$('[data-f]', el).forEach(b => b.onclick = async () => {
    const p = d.pares[+b.dataset.f]; const [para, de] = b.dataset.para === 'a' ? [p.a, p.b] : [p.b, p.a];
    if (!confirm(`Fundir "${de}" em "${para}"?`)) return;
    try { await api('/api/depara/renomear', { method: 'POST', body: { tipo: 'veiculo', de, para } }); aviso('Fundido.'); admFusoes(el); } catch (e) { aviso(e.message, true); }
  });
  $$('[data-ig]', el).forEach(b => b.onclick = async () => { const p = d.pares[+b.dataset.ig]; await api('/api/depara/sugestoes/ignorar', { method: 'POST', body: { a: p.a, b: p.b } }); admFusoes(el); });
}

async function admClientes(el) {
  const d = await api('/api/depara/clientes');
  const canons = [...new Set(d.linhas.map(l => l.canonico))];
  el.innerHTML = `<p class="suave pequeno">Diretor, grupo e ativo ficam em <a href="#/equipe">Equipe</a>. Aqui se agrupam grafias.</p>
    <div class="linha-form"><input id="massa" list="cc" placeholder="Canônico para as selecionadas"><datalist id="cc">${canons.map(c => `<option value="${esc(c)}">`).join('')}</datalist><button id="aplicar">Aplicar às selecionadas</button></div>
    <div class="rolagem"><table><thead><tr><th></th><th>Grafia</th><th>Canônico</th><th>Status</th><th class="num">PIs</th><th class="num">Faturável</th><th></th></tr></thead><tbody>
      ${d.linhas.map(l => `<tr><td><input type="checkbox" class="sel" value="${l.id}"></td><td>${esc(l.nome_bruto)}</td><td>${esc(l.canonico)}</td><td>${l.status === 'pendente' ? '<span class="etiqueta ambar">pendente</span>' : ''}</td>
        <td class="num">${l.pis}</td><td class="num">${fmtM(l.FAT)}</td><td><button class="link" data-ren="${esc(l.canonico)}">renomear / fundir</button></td></tr>`).join('')}</tbody></table></div>`;
  $('#aplicar').onclick = async () => {
    const ids = $$('.sel:checked', el).map(c => +c.value), canon = $('#massa').value.trim();
    if (!ids.length || !canon) return aviso('Selecione grafias e informe o canônico.', true);
    try { await api('/api/depara/clientes', { method: 'POST', body: { ids, canonico: canon } }); admClientes(el); } catch (e) { aviso(e.message, true); }
  };
  $$('[data-ren]', el).forEach(b => b.onclick = () => renomear('cliente', b.dataset.ren, canons, () => admClientes(el)));
}

async function admLongTail(el) {
  const d = await api('/api/depara/veiculos');
  const canons = d.canonicos.map(c => c.canonico);
  const nuc = new Set(d.long_tail_nucleo.map(n => n.toLowerCase()));
  el.innerHTML = `<p class="suave pequeno">Regra pontual: na ficha do cliente abaixo aparece a linha “Veículos Long Tail”, somando tudo que não está marcado como núcleo. Marcação por dado (não por nome escrito no código): renomear um canônico atualiza esta lista.</p>
    <div class="linha-form"><label>Cliente com long tail <input id="cli" value="${esc(d.long_tail_cliente)}"></label></div>
    <div class="cartao" style="columns:4 200px">${canons.map(c => `<label style="display:block"><input type="checkbox" class="n" value="${esc(c)}" ${nuc.has(c.toLowerCase()) ? 'checked' : ''}> ${esc(c)}</label>`).join('')}</div>
    <button id="ok">Salvar núcleo</button>`;
  $('#ok').onclick = async () => { try { await api('/api/config/long-tail', { method: 'POST', body: { cliente: $('#cli').value, nucleo: $$('.n:checked', el).map(c => c.value) } }); aviso('Salvo.'); } catch (e) { aviso(e.message, true); } };
}

async function admColar(el) {
  el.innerHTML = `<p class="suave pequeno">Cole linhas copiadas do Excel com as colunas <b>Cliente · Veículo · Tipo (FAT/REAP) · Ano · Mês · Valor</b> (cabeçalho opcional). Nada é gravado antes da conferência do total.</p>
    <textarea id="txt" placeholder="Burger King&#9;Eletromidia&#9;FAT&#9;2026&#9;9&#9;1.155.841,61"></textarea>
    <div class="linha-form" style="margin-top:8px"><label class="pequeno"><input type="radio" name="m" value="substituir" checked> substituir o valor das células coladas</label><label class="pequeno"><input type="radio" name="m" value="somar"> somar ao que já existe</label><button id="prev">Pré-visualizar</button></div>
    <div id="res"></div>`;
  $('#prev').onclick = async () => {
    try {
      const p = await api('/api/projecao/colar/previa', { method: 'POST', body: { texto: $('#txt').value, modo: $('input[name=m]:checked').value } });
      $('#res').innerHTML = `<div class="cartao"><p><b>${p.validas}</b> linhas válidas · total <b>R$ ${fmtR(p.total)}</b></p>
        ${p.erros.length ? `<div class="alerta">${p.erros.length} linhas com erro:<br>${p.erros.slice(0, 30).map(esc).join('<br>')}</div>` : ''}
        ${p.avisos.length ? `<div class="ambar-linha">${p.avisos.map(esc).join('<br>')}</div>` : ''}
        <table><thead><tr><th>Cliente</th><th class="num">Total colado</th></tr></thead><tbody>${p.por_cliente.map(([c, v]) => `<tr><td>${esc(c)}</td><td class="num">${fmtR(v)}</td></tr>`).join('')}</tbody></table>
        <div class="linha-form" style="margin-top:10px"><label>Confira o total e digite-o para confirmar <input id="conf" placeholder="${fmtR(p.total)}"></label><button id="ok" disabled>Gravar</button></div></div>`;
      $('#conf').oninput = e => { let c = null; try { c = lerValor(e.target.value); } catch { } $('#ok').disabled = c !== p.total; };
      $('#ok').onclick = async () => { try { await api('/api/projecao/colar/confirmar', { method: 'POST', body: { token: p.token } }); aviso('Projeção gravada. Dá para desfazer.'); $('#res').innerHTML = ''; $('#txt').value = ''; } catch (e) { aviso(e.message, true); } };
    } catch (e) { aviso(e.message, true); }
  };
}

async function admMetas(el) {
  const d = await api('/api/metas');
  const cm = d.comparacao.por_veiculo;
  el.innerHTML = `<p class="suave pequeno">Meta compara só faturável (independe do botão de reaplicação). LIVRE não tem meta. ${d.pode_editar ? '' : 'Só superadmin edita.'}</p>
    <div class="rolagem"><table><thead><tr><th>Veículo</th><th class="num">Meta ${S.ano}</th><th class="num">Faturável</th><th class="num">%</th><th></th></tr></thead><tbody>
      ${d.metas.map(m => `<tr><td><a href="#/veiculo/${encodeURIComponent(m.veiculo)}">${esc(m.veiculo)}</a></td><td class="num">${d.pode_editar ? `<input class="mv" data-v="${esc(m.veiculo)}" value="${fmtR(m.valor)}" style="width:150px;text-align:right">` : fmtR(m.valor)}</td>
        <td class="num">${fmtR(cm[m.veiculo]?.fat || 0)}</td><td class="num">${pct(cm[m.veiculo]?.pct)}</td><td>${d.pode_editar ? `<button class="link" data-rm="${esc(m.veiculo)}">remover</button>` : ''}</td></tr>`).join('')}
    </tbody><tfoot><tr><td>Total</td><td class="num">${fmtR(d.comparacao.total.meta)}</td><td class="num">${fmtR(d.comparacao.total.fat)}</td><td class="num">${pct(d.comparacao.total.pct)}</td><td></td></tr></tfoot></table></div>
    ${d.pode_editar ? `<div class="linha-form" style="margin-top:10px"><input id="nv" list="vv" placeholder="Veículo"><datalist id="vv">${d.veiculos.map(v => `<option value="${esc(v)}">`).join('')}</datalist><input id="nval" placeholder="Meta (R$)"><button id="add">Adicionar / alterar</button></div>` : ''}`;
  if (!d.pode_editar) return;
  const salvar = async (veiculo, valor) => { try { await api('/api/metas', { method: 'POST', body: { ano: S.ano, veiculo, valor } }); aviso('Meta salva.'); admMetas(el); } catch (e) { aviso(e.message, true); } };
  $$('.mv', el).forEach(i => i.onchange = () => { try { salvar(i.dataset.v, paraApi(lerValor(i.value))); } catch (e) { aviso(e.message, true); } });
  $$('[data-rm]', el).forEach(b => b.onclick = () => confirm('Remover meta?') && salvar(b.dataset.rm, ''));
  $('#add').onclick = () => { try { salvar($('#nv').value, paraApi(lerValor($('#nval').value))); } catch (e) { aviso(e.message, true); } };
}

async function admDesfazer(el) {
  const d = await api('/api/desfazer');
  el.innerHTML = `${pode('backup') ? '<div class="linha-form"><button class="fantasma" id="bkp">Baixar backup completo do banco</button><label class="pilula clicavel" style="cursor:pointer">Restaurar um backup…<input type="file" id="rest" accept=".db" hidden></label><span class="suave pequeno">O servidor também guarda um backup por dia (últimos 30).</span></div>' : ''}<p class="suave pequeno">Antes de cada operação destrutiva (upload, apagar PIs, colar projeção, de-para, renomear, remover) o sistema guarda uma cópia. Desfazer restaura essa cópia — e guarda o estado atual antes, para poder voltar.</p>
    <div class="rolagem"><table><thead><tr><th>#</th><th>Quando</th><th>Quem</th><th>Operação</th><th></th></tr></thead><tbody>
    ${d.linhas.map(l => `<tr class="${l.desfeito ? 'fora' : ''}"><td>${l.id}</td><td>${esc(l.quando)}</td><td>${esc(l.quem)}</td><td>${esc(l.descricao)}</td><td>${l.desfeito ? 'desfeito' : `<button class="fantasma" data-id="${l.id}">Desfazer</button>`}</td></tr>`).join('') || '<tr><td colspan="5" class="suave">Nada ainda.</td></tr>'}
    </tbody></table></div>`;
  const bk = $('#bkp', el); if (bk) bk.onclick = () => baixar('/api/backup');
  const rs = $('#rest', el);
  if (rs) rs.onchange = async e => {
    const f = e.target.files[0]; e.target.value = '';
    if (!f) return;
    if (!await confirmarForte('Restaurar backup', `O banco inteiro será substituído por <b>${esc(f.name)}</b>. O banco atual fica guardado em backups no servidor.`, 'RESTAURAR')) return;
    try {
      const r = await api('/api/backup/restaurar', { method: 'POST', raw: await f.arrayBuffer(), headers: { 'Content-Type': 'application/octet-stream' } });
      aviso(`Backup restaurado: ${r.pi} PIs, ${r.projecao} linhas de projeção, ${r.pessoa} pessoas.`);
      setTimeout(() => location.reload(), 1500);
    } catch (err) { aviso(err.message, true); }
  };
  $$('[data-id]', el).forEach(b => b.onclick = async () => {
    const l = d.linhas.find(x => x.id === +b.dataset.id);
    if (!confirm(`Desfazer “${l.descricao}”? Alterações feitas depois nas mesmas tabelas também voltam.`)) return;
    try { await api('/api/desfazer', { method: 'POST', body: { id: l.id } }); aviso('Desfeito.'); admDesfazer(el); } catch (e) { aviso(e.message, true); }
  });
}

async function admUso(el) {
  const [u, lg] = await Promise.all([api('/api/uso'), api('/api/log')]);
  el.innerHTML = `<h2 style="margin-top:0">Quem usa</h2><div id="u"></div><h2>Log de alterações</h2><div class="linha-form"><input id="q" placeholder="Filtrar log…"></div><div id="lg"></div>`;
  tabela($('#u'), { id: 'uso', padrao: { k: 'ultimo', d: -1 }, linhas: u.pessoas, cols: [{ k: 'nome', rot: 'Nome' }, { k: 'email', rot: 'Email' }, { k: 'papel', rot: 'Papel' }, { k: 'dias', rot: 'Dias', num: 1, fmt: v => v }, { k: 'horas', rot: 'Horas de uso', num: 1, fmt: v => v }, { k: 'ultimo', rot: 'Último acesso' }] });
  const desenhar = linhas => tabela($('#lg'), { id: 'log', padrao: { k: 'id', d: -1 }, linhas, cols: [{ k: 'quando', rot: 'Quando' }, { k: 'quem', rot: 'Quem' }, { k: 'acao', rot: 'Ação' }, { k: 'detalhe', rot: 'Detalhe' }] });
  desenhar(lg.linhas);
  let t; $('#q').oninput = e => { clearTimeout(t); t = setTimeout(async () => desenhar((await api('/api/log?q=' + encodeURIComponent(e.target.value))).linhas), 300); };
}

iniciar();
