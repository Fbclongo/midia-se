"""Rotas da API. Toda checagem de permissão acontece aqui, no servidor."""
import csv
import datetime
import io
import json
import secrets
import time
import urllib.parse
import urllib.request
import uuid

from . import auth, calc, chat, db, fusoes, importacao, xlsx
from .auth import exigir, pode, Proibido
from .db import Transacao, log, snapshot
from .util import chave, centavos, agora, LIVRE, eh_livre, MESES, mes_ano, tipo_pi, numero_pi

ROTAS = {}
PREVIAS = {}   # token -> (quando, quem, dados) de uploads/colagens aguardando confirmação


class Erro(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def rota(metodo, caminho, publica=False):
    def dec(fn):
        ROTAS[(metodo, caminho)] = (fn, publica)
        return fn
    return dec


class Req:
    def __init__(self, conn, pessoa, query, body, headers, raw=None):
        self.conn, self.pessoa, self.query, self.body, self.headers, self.raw = conn, pessoa, query, body, headers, raw

    def q(self, k, padrao=None):
        return self.query.get(k, padrao)

    def ano(self):
        try:
            return int(self.q('ano') or self.body.get('ano') or datetime.date.today().year)
        except (TypeError, ValueError):
            raise Erro('ano inválido')

    @property
    def email(self):
        return self.pessoa['email']


def _previa_guardar(quem, dados):
    agora_s = time.time()
    for k in [k for k, v in PREVIAS.items() if agora_s - v[0] > 3600]:
        PREVIAS.pop(k, None)
    tok = secrets.token_urlsafe(16)
    PREVIAS[tok] = (agora_s, quem, dados)
    return tok


def _previa_pegar(tok, quem):
    v = PREVIAS.pop(tok, None)
    if not v or v[1] != quem:
        raise Erro('pré-visualização expirou; envie de novo')
    return v[2]


# ---------------- sessão ----------------

def pessoa_da_sessao(conn, token):
    if not token:
        return None
    r = conn.execute('SELECT p.* FROM sessao s JOIN pessoa p ON lower(p.email)=lower(s.email) WHERE s.token=?', (token,)).fetchone()
    return dict(r) if r and r['ativo'] else None


def criar_sessao(conn, email):
    tok = secrets.token_urlsafe(32)
    conn.execute('INSERT INTO sessao VALUES (?,?,?)', (tok, email.lower(), agora()))
    return tok


def registrar_acesso(conn, p):
    hora = datetime.datetime.now().strftime('%Y-%m-%d %H')
    r = conn.execute("SELECT 1 FROM acesso WHERE email=? AND substr(quando,1,13)=?", (p['email'], hora)).fetchone()
    if not r:
        conn.execute('INSERT INTO acesso VALUES (?,?,?,?)', (agora(), p['email'], p['nome'], p['papel']))


@rota('GET', '/api/config', publica=True)
def config_publica(r):
    from .server import CONFIG
    return {'google_client_id': CONFIG['google_client_id'], 'login_dev': CONFIG['login_dev'],
            'dominio': CONFIG['dominio']}


@rota('GET', '/api/me')
def me(r):
    registrar_acesso(r.conn, r.pessoa)
    anos = {datetime.date.today().year}
    for t in ('pi', 'projecao', 'expectativa', 'meta'):
        anos |= {x[0] for x in r.conn.execute(f'SELECT DISTINCT ano FROM {t}')}
    anos.add(max(anos) + 1)
    return {'pessoa': r.pessoa, 'capacidades': sorted(auth.capacidades(r.pessoa)), 'anos': sorted(anos)}


# ---------------- visão / ficha / veículo ----------------

def visiveis(a, p):
    if pode(p, 'ficha_todas'):
        return a.clientes()
    return [c for c in a.clientes() if auth.pode_ver_cliente(p, a.dp.info_cliente(c))]


def filtro_carteira(r):
    """Qual parte da carteira a visão geral mostra: 'diretos' (padrão), 'whitelabel' ou 'todos'."""
    f = r.q('filtro') or ('todos' if r.q('grupos') == '1' else 'diretos')
    return f if f in ('diretos', 'whitelabel', 'todos') else 'diretos'


def filtrar_carteira(a, clientes, filtro):
    """Cliente com grupo preenchido (whitelabel) entra só em 'whitelabel' e 'todos'."""
    if filtro == 'todos':
        return list(clientes)
    wl = filtro == 'whitelabel'
    return [c for c in clientes if bool(a.dp.info_cliente(c)['grupo']) == wl]


@rota('GET', '/api/inicio')
def inicio(r):
    """Visão geral (admin) ou lista dos clientes do diretor."""
    a = calc.Ano(r.conn, r.ano())
    if pode(r.pessoa, 'visao_geral'):
        filtro = filtro_carteira(r)
        lista = filtrar_carteira(a, a.clientes(), filtro)
        # totais = soma só dos clientes do filtro escolhido
        cons = a.consolidado(clientes=lista, incluir_grupos=True)
        grade = calc.montar_grade(a, a.grade(clientes=set(lista)), metas=True)
        return {'tipo': 'visao', 'filtro': filtro, 'fechado': a.fechado, 'consolidado': cons, 'grade': grade,
                'pendentes': _pendentes(r.conn), 'tarefas': _tarefas(r.conn, a.dp),
                'hoje': datetime.date.today().isoformat()}
    if pode(r.pessoa, 'ficha_propria'):
        meus = visiveis(a, r.pessoa)
        cons = a.consolidado(clientes=meus, incluir_grupos=True)
        return {'tipo': 'diretor', 'fechado': a.fechado, 'consolidado': cons}
    if pode(r.pessoa, 'pis'):
        return {'tipo': 'operacao'}
    raise Proibido('sem acesso')


def _pendentes(conn):
    return {'veiculos': conn.execute("SELECT COUNT(*) FROM veiculo WHERE status='pendente'").fetchone()[0],
            'clientes': conn.execute("SELECT COUNT(*) FROM cliente WHERE status='pendente'").fetchone()[0]}


def _cliente_ou_404(a, nome, p):
    c = a.dp.cliente(nome)
    if c not in a.clientes():
        raise Erro('cliente não encontrado', 404)
    if not auth.pode_ver_cliente(p, a.dp.info_cliente(c)):
        raise Proibido('cliente de outro diretor')
    return c


@rota('GET', '/api/cliente')
def cliente(r):
    a = calc.Ano(r.conn, r.ano())
    c = _cliente_ou_404(a, r.q('nome', ''), r.pessoa)
    linhas = a.grade(cliente=c)
    out = {'cliente': c, 'fechado': a.fechado, 'resumo': a.resumo_cliente(c),
           'grade': calc.montar_grade(a, linhas),
           'pode_editar': bool(auth.pode_editar_cliente(r.pessoa, a.dp.info_cliente(c))),
           'veiculos': sorted(set(a.dp.veic_canon.values()), key=chave),
           'pacotes': [dict(x) for x in r.conn.execute('SELECT * FROM pacote WHERE chave(cliente)=? ORDER BY quando', (chave(c),))],
           'expectativa': dict(r.conn.execute('SELECT * FROM expectativa WHERE chave(cliente)=? AND ano=?', (chave(c), a.ano)).fetchone() or {})}
    if a.tem_long_tail(c):
        lt = calc.soma_linhas(linhas, a.eh_long_tail)
        out['long_tail'] = {'nome': 'Veículos Long Tail', 'fat': lt['FAT'], 'reap': lt['REAP'],
                            'fat_partes': a.partes(lt['FAT']), 'reap_partes': a.partes(lt['REAP']),
                            'resumo': a.resumo_cliente(c, filtro_veiculo=a.eh_long_tail)}
    return out


@rota('GET', '/api/veiculo')
def veiculo(r):
    a = calc.Ano(r.conn, r.ano())
    v = a.dp.veiculo(r.q('nome', ''))
    vis = set(visiveis(a, r.pessoa))
    if not vis:
        raise Proibido('sem acesso')
    incl = set(filtrar_carteira(a, vis, filtro_carteira(r))) if pode(r.pessoa, 'visao_geral') else vis
    linhas = a.grade(por='cliente', veiculo=v, clientes=incl)
    g = calc.montar_grade(a, linhas)
    porv = a.grade(veiculo=v, clientes=incl)
    meta = a.comparacao_meta(porv)['por_veiculo'].get(v) if pode(r.pessoa, 'visao_geral') else None
    grafias = [x['nome_bruto'] for x in a.dp.veic_linhas if a.dp.veiculo(x['canonico']) == v]
    return {'veiculo': v, 'fechado': a.fechado, 'grade': g, 'meta': meta, 'grafias': grafias,
            'livre': v == LIVRE}


# ---------------- edição de projeção, expectativa, pacotes ----------------

def _cliente_editavel(r, a, nome):
    c = _cliente_ou_404(a, nome, r.pessoa)
    if not auth.pode_editar_cliente(r.pessoa, a.dp.info_cliente(c)):
        raise Proibido('sem permissão para editar este cliente')
    return c


@rota('POST', '/api/projecao')
def salvar_projecao(r):
    """Grava só as células tocadas. Valor exato em reais (string) ou vazio para apagar.
    Meses fechados não são editáveis (e a projeção deles nunca é apagada)."""
    a = calc.Ano(r.conn, r.ano())
    c = _cliente_editavel(r, a, r.body.get('cliente', ''))
    cels = r.body.get('celulas') or []
    with Transacao(r.conn):
        n = 0
        for x in cels:
            mes = int(x['mes'])
            if not 1 <= mes <= 12:
                raise Erro('mês inválido')
            if mes <= a.fechado:
                raise Erro(f'{MESES[mes - 1]} já está fechado — projeção de mês fechado não se edita')
            if not a.dp.veiculo_conhecido(x['veiculo']):
                raise Erro(f'veículo não cadastrado: {x["veiculo"]}. Lance em LIVRE e peça o cadastro.')
            v = a.dp.veiculo(x['veiculo'])
            tipo = 'REAP' if x.get('tipo') == 'REAP' else 'FAT'
            val = x.get('valor')
            r.conn.execute('DELETE FROM projecao WHERE chave(cliente)=? AND chave(veiculo)=? AND tipo=? AND ano=? AND mes=?',
                           (chave(c), chave(v), tipo, a.ano, mes))
            if val not in (None, ''):
                cv = centavos(val)
                if cv != 0:
                    r.conn.execute('INSERT INTO projecao (cliente, veiculo, tipo, ano, mes, valor, quem, quando) VALUES (?,?,?,?,?,?,?,?)',
                                   (c, v, tipo, a.ano, mes, cv, r.email, agora()))
            n += 1
        log(r.conn, r.email, 'PROJECAO', f'{c}: {n} celulas')
    return {'ok': True, 'celulas': n}


@rota('POST', '/api/expectativa')
def salvar_expectativa(r):
    a = calc.Ano(r.conn, r.ano())
    c = _cliente_editavel(r, a, r.body.get('cliente', ''))
    with Transacao(r.conn):
        r.conn.execute('DELETE FROM expectativa WHERE chave(cliente)=? AND ano=?', (chave(c), a.ano))
        if r.body.get('valor') not in (None, ''):
            v = centavos(r.body['valor'])
            r.conn.execute('INSERT INTO expectativa VALUES (?,?,?,?,?)', (c, a.ano, v, r.email, agora()))
        log(r.conn, r.email, 'EXPECTATIVA', f'{c} {a.ano} = {r.body.get("valor")}')
    return {'ok': True}


@rota('POST', '/api/pacote')
def salvar_pacote(r):
    a = calc.Ano(r.conn, r.ano())
    b = r.body
    c = _cliente_editavel(r, a, b.get('cliente', ''))
    v = a.dp.veiculo(b['veiculo']) if b.get('veiculo') else None
    pid = b.get('id') or uuid.uuid4().hex[:8]
    with Transacao(r.conn):
        if b.get('id'):
            antigo = r.conn.execute('SELECT * FROM pacote WHERE id=?', (pid,)).fetchone()
            if not antigo or chave(antigo['cliente']) != chave(c):
                raise Erro('pacote não encontrado')
        r.conn.execute('INSERT OR REPLACE INTO pacote VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (pid, c, v, centavos(b['valor']) if b.get('valor') not in (None, '') else None,
                        b.get('inicio') or None, b.get('fim') or None, b.get('descricao') or '',
                        b.get('status') or 'em negociação', r.email, agora()))
        log(r.conn, r.email, 'PACOTE', f'{c} / {v}')
    return {'ok': True, 'id': pid}


@rota('POST', '/api/pacote/apagar')
def apagar_pacote(r):
    a = calc.Ano(r.conn, r.ano())
    p = r.conn.execute('SELECT * FROM pacote WHERE id=?', (r.body.get('id'),)).fetchone()
    if not p:
        raise Erro('pacote não encontrado')
    _cliente_editavel(r, a, p['cliente'])
    with Transacao(r.conn):
        snapshot(r.conn, r.email, f'Apagar pacote {p["cliente"]} / {p["veiculo"]}', {'pacote': ('id=?', (p['id'],))})
        r.conn.execute('DELETE FROM pacote WHERE id=?', (p['id'],))
        log(r.conn, r.email, 'PACOTE_APAGADO', f'{p["cliente"]} / {p["veiculo"]}')
    return {'ok': True}


# ---------------- PIs ----------------

@rota('GET', '/api/pis')
def listar_pis(r):
    exigir(r.pessoa, 'pis')
    ano = r.ano()
    a = calc.Ano(r.conn, ano)
    busca = chave(r.q('q', ''))
    linhas = []
    for x in r.conn.execute('SELECT * FROM pi WHERE ano=? ORDER BY mes DESC, id DESC', (ano,)):
        d = dict(x)
        d['cliente'] = a.dp.cliente(x['cliente_bruto'])
        d['veiculo'] = a.dp.veiculo(x['veiculo_bruto'])
        if busca and busca not in chave(' '.join(str(d[k]) for k in ('numero', 'cliente', 'veiculo', 'cliente_bruto', 'veiculo_bruto'))):
            continue
        linhas.append(d)
    por_mes = {}
    for x in r.conn.execute('SELECT mes, tipo, COUNT(*) n, SUM(valor) s FROM pi WHERE ano=? GROUP BY 1,2', (ano,)):
        m = por_mes.setdefault(x['mes'], {'mes': x['mes'], 'n': 0, 'FAT': 0, 'REAP': 0})
        m['n'] += x['n']
        m[x['tipo']] += x['s']
    return {'ano': ano, 'fechado': a.fechado, 'total_linhas': len(linhas), 'linhas': linhas[:500],
            'por_mes': sorted(por_mes.values(), key=lambda m: m['mes']),
            'pode_fechar': pode(r.pessoa, 'fechar_mes'), 'pendentes': _pendentes(r.conn),
            'hoje': datetime.date.today().isoformat()}


@rota('POST', '/api/pis/previa')
def previa_pis(r):
    exigir(r.pessoa, 'pis')
    nome = urllib.parse.unquote(r.headers.get('X-Filename', 'arquivo.xlsx'))
    try:
        linhas = importacao.ler_arquivo(r.raw or b'', nome)
        pis, desc, erros = importacao.interpretar_pis(linhas)
    except ValueError as e:
        raise Erro(str(e))
    modo = r.q('modo', 'substituir_ano')
    anos = sorted({p['ano'] for p in pis})
    meses = sorted({(p['ano'], p['mes']) for p in pis})
    resumo = {}
    for p in pis:
        k = f'{p["mes"]:02d}/{p["ano"]}'
        x = resumo.setdefault(k, {'mes': k, 'n': 0, 'FAT': 0, 'REAP': 0})
        x['n'] += 1
        x[p['tipo']] += p['valor']
    # o que será apagado
    if modo == 'substituir_ano':
        sai = r.conn.execute(f'SELECT COUNT(*), COALESCE(SUM(valor),0) FROM pi WHERE ano IN ({",".join("?" * len(anos))})', anos).fetchone() if anos else (0, 0)
    else:
        sai = (0, 0)
        for an, me in meses:
            x = r.conn.execute('SELECT COUNT(*), COALESCE(SUM(valor),0) FROM pi WHERE ano=? AND mes=?', (an, me)).fetchone()
            sai = (sai[0] + x[0], sai[1] + x[1])
    dp = calc.Depara(r.conn)
    novos_v = sorted({p['veiculo_bruto'] for p in pis if not dp.veiculo_conhecido(p['veiculo_bruto'])}, key=chave)
    novos_c = sorted({p['cliente_bruto'] for p in pis if chave(p['cliente_bruto']) not in dp.cli_por_chave and chave(p['cliente_bruto']) not in dp.cli_canon}, key=chave)
    tok = _previa_guardar(r.email, {'pis': pis, 'modo': modo, 'arquivo': nome})
    return {'token': tok, 'arquivo': nome, 'modo': modo, 'validos': len(pis), 'descartados': desc, 'erros': erros[:50],
            'n_erros': len(erros), 'por_mes': sorted(resumo.values(), key=lambda x: x['mes'][3:] + x['mes'][:2]),
            'sai': {'linhas': sai[0], 'valor': sai[1]}, 'anos': anos,
            'novos_veiculos': novos_v, 'novos_clientes': novos_c}


@rota('POST', '/api/pis/confirmar')
def confirmar_pis(r):
    exigir(r.pessoa, 'pis')
    d = _previa_pegar(r.body.get('token'), r.email)
    with Transacao(r.conn):
        sid, nv, nc = importacao.gravar_pis(r.conn, r.email, d['pis'], d['modo'], f'Upload de PIs: {d["arquivo"]} ({d["modo"]})')
        log(r.conn, r.email, 'UPLOAD_PIS', {'arquivo': d['arquivo'], 'modo': d['modo'], 'importadas': len(d['pis']),
                                            'veiculos_novos': nv, 'clientes_novos': nc})
    return {'ok': True, 'snapshot': sid, 'importadas': len(d['pis'])}


@rota('POST', '/api/pis/apagar-tudo')
def apagar_pis(r):
    exigir(r.pessoa, 'pis')
    ano = r.ano()
    if (r.body.get('confirmacao') or '').strip().upper() != f'APAGAR {ano}':
        raise Erro(f'digite APAGAR {ano} para confirmar')
    with Transacao(r.conn):
        sid = snapshot(r.conn, r.email, f'Apagar todos os PIs de {ano}', {'pi': ('ano=?', (ano,))})
        n = r.conn.execute('DELETE FROM pi WHERE ano=?', (ano,)).rowcount
        log(r.conn, r.email, 'APAGAR_PIS', f'{ano}: {n} linhas')
    return {'ok': True, 'apagadas': n, 'snapshot': sid}


@rota('POST', '/api/pis/salvar')
def salvar_pi(r):
    exigir(r.pessoa, 'pis')
    b = r.body
    tipo = tipo_pi(b.get('tipo'))
    if not tipo:
        raise Erro('tipo deve ser Veiculado ou Reaplicação')
    try:
        ano, mes = mes_ano(b['mes'])
        valor = centavos(b['valor'])
    except (ValueError, KeyError) as e:
        raise Erro(str(e))
    cli, vei = ' '.join(str(b.get('cliente_bruto', '')).split()), ' '.join(str(b.get('veiculo_bruto', '')).split())
    if not cli or not vei:
        raise Erro('cliente e veículo são obrigatórios')
    with Transacao(r.conn):
        if b.get('id'):
            snapshot(r.conn, r.email, f'Editar PI #{b["id"]}', {'pi': ('id=?', (int(b['id']),))})
            r.conn.execute('UPDATE pi SET ano=?, mes=?, numero=?, cliente_bruto=?, veiculo_bruto=?, tipo=?, valor=? WHERE id=?',
                           (ano, mes, numero_pi(b.get('numero', '')), cli, vei, tipo, valor, int(b['id'])))
            acao = 'PI_EDITADO'
        else:
            r.conn.execute('INSERT INTO pi (ano, mes, numero, cliente_bruto, veiculo_bruto, tipo, valor) VALUES (?,?,?,?,?,?,?)',
                           (ano, mes, numero_pi(b.get('numero', '')), cli, vei, tipo, valor))
            acao = 'PI_ADICIONADO'
        importacao.registrar_nomes_novos(r.conn, [{'cliente_bruto': cli, 'veiculo_bruto': vei}])
        log(r.conn, r.email, acao, f'{b.get("numero")} {cli} / {vei} {mes:02d}/{ano} {valor / 100:.2f}')
    return {'ok': True}


@rota('POST', '/api/pis/remover')
def remover_pi(r):
    exigir(r.pessoa, 'pis')
    pid = int(r.body.get('id'))
    with Transacao(r.conn):
        x = r.conn.execute('SELECT * FROM pi WHERE id=?', (pid,)).fetchone()
        if not x:
            raise Erro('PI não encontrado')
        snapshot(r.conn, r.email, f'Remover PI {x["numero"]} ({x["cliente_bruto"]} / {x["veiculo_bruto"]})', {'pi': ('id=?', (pid,))})
        r.conn.execute('DELETE FROM pi WHERE id=?', (pid,))
        log(r.conn, r.email, 'PI_REMOVIDO', f'{x["numero"]} {x["cliente_bruto"]} / {x["veiculo_bruto"]}')
    return {'ok': True}


@rota('GET', '/api/pis/modelo')
def modelo_pis(r):
    exigir(r.pessoa, 'pis')
    dados = xlsx.escrever({'PIs': [['Numero do PI', 'Cliente', 'Veiculo', 'Tipo', 'Valor', 'Mes veiculacao'],
                                   ['240539', 'Bacio', 'GENERAL DIG', 'Veiculado', 67704.00, '01/2026'],
                                   ['240540', 'Bacio', 'GENERAL DIG', 'Reaplicação', 1000.50, '01/2026']]})
    return (dados, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'modelo_PIs.xlsx')


@rota('POST', '/api/mes-fechado')
def fechar_mes(r):
    exigir(r.pessoa, 'fechar_mes')
    ano, mes = r.ano(), int(r.body.get('mes'))
    if not calc.pode_fechar(ano, mes):
        raise Erro('não dá para fechar um mês que ainda não terminou')
    with Transacao(r.conn):
        antes = calc.mes_fechado(r.conn, ano)
        db.set_config(r.conn, f'mes_fechado:{ano}', mes)
        log(r.conn, r.email, 'MES_FECHADO', f'{ano}: {antes} -> {mes}')
    return {'ok': True, 'antes': antes, 'agora': mes}


# ---------------- equipe e clientes ----------------

@rota('GET', '/api/equipe')
def equipe(r):
    exigir(r.pessoa, 'equipe')
    a = calc.Ano(r.conn, r.ano())
    pessoas = [dict(x) for x in r.conn.execute('SELECT * FROM pessoa ORDER BY ativo DESC, papel, nome')]
    clientes = []
    for c in sorted(set(a.dp.cli_canon.values()), key=chave):
        info = a.dp.info_cliente(c)
        clientes.append({'cliente': c, **info,
                         'grafias': [x['nome_bruto'] for x in r.conn.execute('SELECT nome_bruto FROM cliente WHERE chave(canonico)=?', (chave(c),))]})
    return {'pessoas': pessoas, 'clientes': clientes, 'pendentes': _pendentes(r.conn),
            'pode_papeis_admin': pode(r.pessoa, 'papeis_admin')}


@rota('POST', '/api/pessoa')
def salvar_pessoa(r):
    b = r.body
    email = (b.get('email') or '').strip().lower()
    if '@' not in email:
        raise Erro('email inválido')
    papel, ativo = b.get('papel'), bool(b.get('ativo', True))
    with Transacao(r.conn):
        auth.checar_mudanca_papel(r.conn, r.pessoa, email, papel, ativo)
        r.conn.execute('INSERT INTO pessoa (email, nome, papel, ativo) VALUES (?,?,?,?) '
                       'ON CONFLICT(email) DO UPDATE SET nome=excluded.nome, papel=excluded.papel, ativo=excluded.ativo',
                       (email, b.get('nome') or email.split('@')[0], papel, 1 if ativo else 0))
        log(r.conn, r.email, 'EQUIPE', f'{email} -> {papel}{"" if ativo else " (inativo)"}')
    return {'ok': True}


@rota('POST', '/api/cliente/atributos')
def atributos_cliente(r):
    exigir(r.pessoa, 'equipe')
    b = r.body
    c = calc.Depara(r.conn).cliente(b.get('cliente', ''))
    campos = {}
    if 'diretor_email' in b:
        d = (b['diretor_email'] or '').strip().lower() or None
        if d and not r.conn.execute("SELECT 1 FROM pessoa WHERE email=?", (d,)).fetchone():
            raise Erro('diretor não está na equipe')
        campos['diretor_email'] = d
    if 'grupo' in b:
        exigir(r.pessoa, 'grupos')
        campos['grupo'] = (b['grupo'] or '').strip() or None
    if 'grupo_negociacao' in b:
        campos['grupo_negociacao'] = (b['grupo_negociacao'] or '').strip() or None
    if 'ativo' in b:
        campos['ativo'] = 1 if b['ativo'] else 0
    if not campos:
        raise Erro('nada para alterar')
    with Transacao(r.conn):
        sets = ', '.join(f'{k}=?' for k in campos)
        r.conn.execute(f'UPDATE cliente SET {sets} WHERE chave(canonico)=?', list(campos.values()) + [chave(c)])
        log(r.conn, r.email, 'CLIENTE', f'{c}: {campos}')
    return {'ok': True}


@rota('POST', '/api/cliente/excluir')
def excluir_cliente(r):
    """Apaga um cliente que não movimenta dinheiro (sem PIs e sem projeção).
    Leva junto expectativa, pacotes e To Do's. Guarda cópia para desfazer."""
    exigir(r.pessoa, 'equipe')
    dp = calc.Depara(r.conn)
    c = dp.cliente(r.body.get('cliente') or '')
    k = chave(c)
    if k not in dp.cli_canon:
        raise Erro('cliente não encontrado')
    grafias = [x['chave'] for x in r.conn.execute('SELECT chave FROM cliente WHERE chave(canonico)=?', (k,))]
    n_pi = sum(1 for x in r.conn.execute('SELECT cliente_bruto FROM pi') if dp.cliente(x[0]) == c)
    n_proj = sum(1 for x in r.conn.execute('SELECT cliente FROM projecao') if dp.cliente(x[0]) == c)
    if n_pi or n_proj:
        raise Erro(f'{c} tem {n_pi} PIs e {n_proj} células de projeção — não dá para excluir. '
                   'Se é o mesmo cliente com outro nome, use o de-para de clientes para juntar.')
    with Transacao(r.conn):
        sid = snapshot(r.conn, r.email, f'Excluir cliente {c}', {
            'cliente': ('chave(canonico)=?', (k,)), 'expectativa': ('chave(cliente)=?', (k,)),
            'pacote': ('chave(cliente)=?', (k,)), 'tarefa': ('chave(cliente)=?', (k,))})
        for t, col in (('expectativa', 'cliente'), ('pacote', 'cliente'), ('tarefa', 'cliente')):
            r.conn.execute(f'DELETE FROM {t} WHERE chave({col})=?', (k,))
        r.conn.execute('DELETE FROM cliente WHERE chave(canonico)=?', (k,))
        log(r.conn, r.email, 'CLIENTE_EXCLUIDO', f'{c} ({len(grafias)} grafias)')
    return {'ok': True, 'snapshot': sid}


# ---------------- de-para ----------------

def _movimento_por_bruto(conn, coluna):
    mv = {}
    for x in conn.execute(f'SELECT {coluna} n, tipo, SUM(valor) s, COUNT(*) c FROM pi GROUP BY chave({coluna}), tipo'):
        k = chave(x['n'])
        d = mv.setdefault(k, {'FAT': 0, 'REAP': 0, 'pis': 0})
        d[x['tipo']] += x['s']
        d['pis'] += x['c']
    return mv


@rota('GET', '/api/depara/veiculos')
def depara_veiculos(r):
    exigir(r.pessoa, 'depara')
    mv = _movimento_por_bruto(r.conn, 'veiculo_bruto')
    dp = calc.Depara(r.conn)
    linhas, por_canon = [], {}
    for x in dp.veic_linhas:
        m = mv.get(x['chave'], {'FAT': 0, 'REAP': 0, 'pis': 0})
        canon = dp.veiculo(x['canonico'])
        linhas.append({**x, 'canonico': canon, **m})
        pc = por_canon.setdefault(canon, {'canonico': canon, 'FAT': 0, 'REAP': 0, 'pis': 0, 'grafias': 0})
        pc['grafias'] += 1
        for k in ('FAT', 'REAP', 'pis'):
            pc[k] += m[k]
    # nomes que aparecem nos PIs sem linha no de-para mas batem com um canônico (caixa diferente)
    for k, m in mv.items():
        if k not in dp.veic_por_chave and k in dp.veic_canon:
            pc = por_canon[dp.veic_canon[k]]
            for kk in ('FAT', 'REAP', 'pis'):
                pc[kk] += m[kk]
    nucleo = json.loads(db.get_config(r.conn, 'long_tail_nucleo', '[]'))
    return {'linhas': linhas, 'canonicos': sorted(por_canon.values(), key=lambda x: -x['FAT']),
            'long_tail_nucleo': nucleo, 'long_tail_cliente': db.get_config(r.conn, 'long_tail_cliente', '')}


def _validar_canonico(nome):
    nome = ' '.join(str(nome or '').split())
    if not nome:
        raise Erro('nome canônico vazio')
    if eh_livre(nome):
        raise Erro('LIVRE é reservado — nenhum veículo pode ter esse nome')
    return nome


@rota('POST', '/api/depara/veiculos')
def depara_veiculos_salvar(r):
    """Aponta grafias (ids) para um canônico. Se o canônico já existe com outra caixa, usa a grafia existente."""
    exigir(r.pessoa, 'depara')
    canon = _validar_canonico(r.body.get('canonico'))
    ids = [int(i) for i in r.body.get('ids') or []]
    dp = calc.Depara(r.conn)
    canon = dp.veic_canon.get(chave(canon), canon)
    with Transacao(r.conn):
        snapshot(r.conn, r.email, f'De-para de veículos → {canon}', {'veiculo': (None, ())})
        for i in ids:
            r.conn.execute("UPDATE veiculo SET canonico=?, status='ativo' WHERE id=?", (canon, i))
        log(r.conn, r.email, 'DEPARA_VEICULO', f'{len(ids)} grafias -> {canon}')
    return {'ok': True}


@rota('POST', '/api/depara/veiculos/novo')
def depara_veiculo_novo(r):
    exigir(r.pessoa, 'depara')
    bruto = ' '.join(str(r.body.get('nome_bruto') or '').split())
    if not bruto or eh_livre(bruto):
        raise Erro('nome bruto inválido')
    canon = _validar_canonico(r.body.get('canonico') or bruto)
    dp = calc.Depara(r.conn)
    canon = dp.veic_canon.get(chave(canon), canon)
    with Transacao(r.conn):
        if r.conn.execute('SELECT 1 FROM veiculo WHERE chave=?', (chave(bruto),)).fetchone():
            raise Erro('essa grafia já existe no de-para')
        r.conn.execute("INSERT INTO veiculo (nome_bruto, chave, canonico, status) VALUES (?,?,?,'ativo')", (bruto, chave(bruto), canon))
        log(r.conn, r.email, 'DEPARA_VEICULO', f'nova grafia {bruto} -> {canon}')
    return {'ok': True}


TABELAS_RENOMEAR = lambda tabela: {tabela: (None, ()), 'projecao': (None, ()), 'expectativa': (None, ()), 'meta': (None, ()),
                                   'pacote': (None, ()), 'config': ("chave LIKE 'long_tail%'", ())}


def _renomear(conn, quem, tipo, de, para, guardar=True):
    """Renomeia (ou funde) um canônico em todas as tabelas que guardam o nome.
    Projeção que colide na mesma célula é somada. guardar=False quando quem chama já fez o snapshot."""
    tabela = 'veiculo' if tipo == 'veiculo' else 'cliente'
    dp = calc.Depara(conn)
    existentes = dp.veic_canon if tipo == 'veiculo' else dp.cli_canon
    para = existentes.get(chave(para), para) if chave(para) != chave(de) else para
    col_proj = 'veiculo' if tipo == 'veiculo' else 'cliente'
    if guardar:
        snapshot(conn, quem, f'Renomear {tipo} {de} → {para}', TABELAS_RENOMEAR(tabela))
    # decidido por uma pessoa: deixa de ser pendente
    conn.execute(f"UPDATE {tabela} SET canonico=?, status='ativo' WHERE chave(canonico)=?", (para, chave(de)))
    # projeção: soma colisões
    linhas = [dict(x) for x in conn.execute(f'SELECT * FROM projecao WHERE chave({col_proj})=?', (chave(de),))]
    conn.execute(f'DELETE FROM projecao WHERE chave({col_proj})=?', (chave(de),))
    for l in linhas:
        l[col_proj] = para
        conn.execute('INSERT INTO projecao VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET valor=valor+excluded.valor',
                     (l['cliente'], l['veiculo'], l['tipo'], l['ano'], l['mes'], l['valor'], l['quem'], l['quando'], l['origem']))
    if tipo == 'veiculo':
        for l in [dict(x) for x in conn.execute('SELECT * FROM meta WHERE chave(veiculo)=?', (chave(de),))]:
            conn.execute('DELETE FROM meta WHERE chave(veiculo)=? AND ano=?', (chave(de), l['ano']))
            conn.execute('INSERT INTO meta VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET valor=valor+excluded.valor',
                         (para, l['ano'], l['valor'], quem, agora()))
        conn.execute('UPDATE pacote SET veiculo=? WHERE chave(veiculo)=?', (para, chave(de)))
        nucleo = json.loads(db.get_config(conn, 'long_tail_nucleo', '[]'))
        db.set_config(conn, 'long_tail_nucleo', json.dumps([para if chave(n) == chave(de) else n for n in nucleo]))
    else:
        for l in [dict(x) for x in conn.execute('SELECT * FROM expectativa WHERE chave(cliente)=?', (chave(de),))]:
            conn.execute('DELETE FROM expectativa WHERE chave(cliente)=? AND ano=?', (chave(de), l['ano']))
            conn.execute('INSERT INTO expectativa VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET valor=valor+excluded.valor',
                         (para, l['ano'], l['valor'], quem, agora()))
        conn.execute('UPDATE pacote SET cliente=? WHERE chave(cliente)=?', (para, chave(de)))
        if chave(db.get_config(conn, 'long_tail_cliente', '')) == chave(de):
            db.set_config(conn, 'long_tail_cliente', para)
    log(conn, quem, f'RENOMEAR_{tipo.upper()}', f'{de} -> {para}')
    return para


@rota('POST', '/api/depara/renomear')
def depara_renomear(r):
    exigir(r.pessoa, 'depara')
    tipo = r.body.get('tipo')
    if tipo not in ('veiculo', 'cliente'):
        raise Erro('tipo inválido')
    de = r.body.get('de')
    para = _validar_canonico(r.body.get('para')) if tipo == 'veiculo' else ' '.join(str(r.body.get('para') or '').split())
    if not para:
        raise Erro('nome vazio')
    with Transacao(r.conn):
        final = _renomear(r.conn, r.email, tipo, de, para)
    return {'ok': True, 'canonico': final}


def _par(a, b):
    return '|'.join(sorted([chave(a), chave(b)]))


@rota('GET', '/api/depara/sugestoes')
def depara_sugestoes(r):
    """Agrupa os pares parecidos em grupos (A~B e B~C viram um grupo A,B,C) e sugere
    qual nome fica: o já canonizado (não pendente) que mais movimenta."""
    exigir(r.pessoa, 'depara')
    dp = calc.Depara(r.conn)
    grafias = [{'nome_bruto': x['nome_bruto'], 'canonico': dp.veiculo(x['canonico'])} for x in dp.veic_linhas]
    ignoradas = set(json.loads(db.get_config(r.conn, 'fusoes_ignoradas', '[]')))
    pares = [p for p in fusoes.sugerir(grafias) if _par(p['a'], p['b']) not in ignoradas
             and f'{chave(p["a"])}|{chave(p["b"])}' not in ignoradas]
    # união dos pares em grupos
    pai = {}

    def raiz(x):
        pai.setdefault(x, x)
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x
    for p in pares:
        pai[raiz(p['a'])] = raiz(p['b'])
    grupos = {}
    for n in pai:
        grupos.setdefault(raiz(n), []).append(n)
    # movimento e situação de cada canônico
    mv = _movimento_por_bruto(r.conn, 'veiculo_bruto')
    info = {}
    for x in dp.veic_linhas:
        c = dp.veiculo(x['canonico'])
        d = info.setdefault(c, {'nome': c, 'FAT': 0, 'REAP': 0, 'pis': 0, 'pendente': True, 'grafias': []})
        m = mv.get(x['chave'], {'FAT': 0, 'REAP': 0, 'pis': 0})
        for k in ('FAT', 'REAP', 'pis'):
            d[k] += m[k]
        d['pendente'] = d['pendente'] and x['status'] == 'pendente'
        d['grafias'].append(x['nome_bruto'])
    saida = []
    for membros in grupos.values():
        ms = sorted((info.get(n, {'nome': n, 'FAT': 0, 'REAP': 0, 'pis': 0, 'pendente': False, 'grafias': [n]}) for n in membros),
                    key=lambda d: (d['pendente'], -d['FAT'], d['nome'].isupper(), chave(d['nome'])))
        saida.append({'membros': ms, 'sugerido': ms[0]['nome'],
                      'tem_pendente': any(m['pendente'] for m in ms),
                      'motivos': [p['motivo'] for p in pares if p['a'] in membros][:3]})
    saida.sort(key=lambda g: (not g['tem_pendente'], -sum(m['FAT'] for m in g['membros'])))
    return {'grupos': saida}


@rota('POST', '/api/depara/fundir')
def depara_fundir(r):
    """Junta vários grupos de uma vez: [{para, de: [...]}]. Um único snapshot para desfazer."""
    exigir(r.pessoa, 'depara')
    grupos = r.body.get('grupos') or []
    if not grupos:
        raise Erro('nada para juntar')
    n = 0
    with Transacao(r.conn):
        snapshot(r.conn, r.email, f'Juntar veículos ({len(grupos)} grupos)', TABELAS_RENOMEAR('veiculo'))
        for g in grupos:
            para = _validar_canonico(g.get('para'))
            for de in g.get('de') or []:
                if chave(de) != chave(para):
                    _renomear(r.conn, r.email, 'veiculo', de, para, guardar=False)
                    n += 1
            # o nome que fica também deixa de ser pendente
            r.conn.execute("UPDATE veiculo SET status='ativo' WHERE chave(canonico)=?", (chave(para),))
    return {'ok': True, 'juntados': n}


@rota('POST', '/api/depara/sugestoes/ignorar')
def depara_ignorar(r):
    """Marca nomes como 'não são o mesmo'. Aceita {a, b} ou {nomes: [...]} (todos os pares do grupo)."""
    exigir(r.pessoa, 'depara')
    ign = set(json.loads(db.get_config(r.conn, 'fusoes_ignoradas', '[]')))
    nomes = r.body.get('nomes') or [r.body.get('a'), r.body.get('b')]
    nomes = [n for n in nomes if n]
    for i, a in enumerate(nomes):
        for b in nomes[i + 1:]:
            ign.add(_par(a, b))
    with Transacao(r.conn):
        db.set_config(r.conn, 'fusoes_ignoradas', json.dumps(sorted(ign)))
        # quem foi revisado e não é igual a ninguém deixa de ser pendente
        for n in nomes:
            r.conn.execute("UPDATE veiculo SET status='ativo' WHERE chave(canonico)=?", (chave(n),))
    return {'ok': True}


@rota('GET', '/api/depara/clientes')
def depara_clientes(r):
    exigir(r.pessoa, 'depara')
    mv = _movimento_por_bruto(r.conn, 'cliente_bruto')
    dp = calc.Depara(r.conn)
    linhas = []
    for x in r.conn.execute('SELECT * FROM cliente ORDER BY canonico'):
        m = mv.get(x['chave'], {'FAT': 0, 'REAP': 0, 'pis': 0})
        linhas.append({**dict(x), 'canonico': dp.cliente(x['canonico']), **m})
    return {'linhas': linhas}


@rota('POST', '/api/depara/clientes')
def depara_clientes_salvar(r):
    exigir(r.pessoa, 'depara')
    canon = ' '.join(str(r.body.get('canonico') or '').split())
    if not canon:
        raise Erro('nome canônico vazio')
    dp = calc.Depara(r.conn)
    canon = dp.cli_canon.get(chave(canon), canon)
    ids = [int(i) for i in r.body.get('ids') or []]
    with Transacao(r.conn):
        snapshot(r.conn, r.email, f'De-para de clientes → {canon}', {'cliente': (None, ())})
        info = r.conn.execute('SELECT * FROM cliente WHERE chave(canonico)=? LIMIT 1', (chave(canon),)).fetchone()
        for i in ids:
            if info:
                r.conn.execute("UPDATE cliente SET canonico=?, status='ativo', diretor_email=?, grupo=?, ativo=? WHERE id=?",
                               (canon, info['diretor_email'], info['grupo'], info['ativo'], i))
            else:
                r.conn.execute("UPDATE cliente SET canonico=?, status='ativo' WHERE id=?", (canon, i))
        log(r.conn, r.email, 'DEPARA_CLIENTE', f'{len(ids)} grafias -> {canon}')
    return {'ok': True}


@rota('POST', '/api/config/long-tail')
def config_long_tail(r):
    exigir(r.pessoa, 'depara')
    nucleo = [n for n in (r.body.get('nucleo') or []) if str(n).strip()]
    with Transacao(r.conn):
        db.set_config(r.conn, 'long_tail_nucleo', json.dumps(nucleo, ensure_ascii=False))
        if 'cliente' in r.body:
            db.set_config(r.conn, 'long_tail_cliente', r.body['cliente'] or '')
        log(r.conn, r.email, 'LONG_TAIL', {'cliente': r.body.get('cliente'), 'nucleo': nucleo})
    return {'ok': True}


# ---------------- colar projeção em massa ----------------

@rota('POST', '/api/projecao/colar/previa')
def colar_previa(r):
    exigir(r.pessoa, 'colar_projecao')
    texto = r.body.get('texto') or ''
    linhas = xlsx.ler_csv(texto)
    if linhas and chave(linhas[0][0]) == 'cliente':
        linhas = linhas[1:]
    dp = calc.Depara(r.conn)
    ok, erros = [], []
    for i, l in enumerate(linhas, start=1):
        l = [x.strip() for x in l] + [''] * 6
        try:
            cli, vei, tipo, ano, mes, val = l[:6]
            if chave(cli) not in dp.cli_por_chave and chave(cli) not in dp.cli_canon:
                raise ValueError(f'cliente desconhecido: {cli}')
            if not dp.veiculo_conhecido(vei):
                raise ValueError(f'veículo desconhecido: {vei}')
            t = 'REAP' if chave(tipo) in ('reap', 'reaplicacao') else 'FAT' if chave(tipo) in ('fat', 'faturavel', 'veiculado', '') else None
            if not t:
                raise ValueError(f'tipo inválido: {tipo}')
            ano, mes = int(float(ano)), int(float(mes))
            if not 1 <= mes <= 12:
                raise ValueError('mês inválido')
            ok.append({'cliente': dp.cliente(cli), 'veiculo': dp.veiculo(vei), 'tipo': t, 'ano': ano, 'mes': mes,
                       'valor': centavos(val)})
        except (ValueError, IndexError) as e:
            erros.append(f'linha {i}: {e}')
    avisos = []
    for x in ok:
        if x['mes'] <= calc.mes_fechado(r.conn, x['ano']):
            avisos.append(f'{x["cliente"]} / {x["veiculo"]} {x["mes"]:02d}/{x["ano"]}: mês já fechado (fica gravado, não aparece)')
    por_cli = {}
    for x in ok:
        por_cli[x['cliente']] = por_cli.get(x['cliente'], 0) + x['valor']
    modo = r.body.get('modo', 'substituir')
    tok = _previa_guardar(r.email, {'linhas': ok, 'modo': modo})
    return {'token': tok, 'validas': len(ok), 'erros': erros, 'avisos': avisos[:30], 'total': sum(x['valor'] for x in ok),
            'por_cliente': sorted(por_cli.items(), key=lambda x: -x[1]), 'amostra': ok[:20], 'modo': modo}


@rota('POST', '/api/projecao/colar/confirmar')
def colar_confirmar(r):
    exigir(r.pessoa, 'colar_projecao')
    d = _previa_pegar(r.body.get('token'), r.email)
    anos = sorted({x['ano'] for x in d['linhas']})
    with Transacao(r.conn):
        sid = snapshot(r.conn, r.email, f'Colar projeção ({len(d["linhas"])} linhas, {d["modo"]})',
                       {'projecao': (f'ano IN ({",".join("?" * len(anos))})', anos)})
        for x in d['linhas']:
            if d['modo'] == 'somar':
                sql = 'ON CONFLICT DO UPDATE SET valor=valor+excluded.valor, quem=excluded.quem, quando=excluded.quando'
            else:
                sql = 'ON CONFLICT DO UPDATE SET valor=excluded.valor, quem=excluded.quem, quando=excluded.quando'
            r.conn.execute('INSERT INTO projecao (cliente, veiculo, tipo, ano, mes, valor, quem, quando, origem) VALUES (?,?,?,?,?,?,?,?,?) ' + sql,
                           (x['cliente'], x['veiculo'], x['tipo'], x['ano'], x['mes'], x['valor'], r.email, agora(), 'importação colada'))
        log(r.conn, r.email, 'PROJECAO_COLADA', {'linhas': len(d['linhas']), 'total': sum(x['valor'] for x in d['linhas']) / 100})
    return {'ok': True, 'snapshot': sid}


# ---------------- metas ----------------

@rota('GET', '/api/metas')
def metas(r):
    exigir(r.pessoa, 'visao_geral')
    a = calc.Ano(r.conn, r.ano())
    cons = a.consolidado()
    dentro = {c['cliente'] for c in cons['clientes'] if c['em_totais']}
    cm = a.comparacao_meta(a.grade(clientes=dentro))
    return {'metas': [{'veiculo': v, 'valor': m} for v, m in sorted(a.metas.items(), key=lambda x: -x[1])],
            'comparacao': cm, 'veiculos': sorted(set(a.dp.veic_canon.values()), key=chave),
            'pode_editar': pode(r.pessoa, 'meta')}


@rota('POST', '/api/metas')
def salvar_meta(r):
    exigir(r.pessoa, 'meta')
    ano = r.ano()
    dp = calc.Depara(r.conn)
    v = r.body.get('veiculo', '')
    if eh_livre(v) or not dp.veiculo_conhecido(v):
        raise Erro('veículo inválido (LIVRE não tem meta)')
    v = dp.veiculo(v)
    with Transacao(r.conn):
        r.conn.execute('DELETE FROM meta WHERE chave(veiculo)=? AND ano=?', (chave(v), ano))
        if r.body.get('valor') not in (None, ''):
            r.conn.execute('INSERT INTO meta VALUES (?,?,?,?,?)', (v, ano, centavos(r.body['valor']), r.email, agora()))
        log(r.conn, r.email, 'META', f'{v} {ano} = {r.body.get("valor")}')
    return {'ok': True}


# ---------------- uso, log, desfazer ----------------

@rota('GET', '/api/uso')
def uso(r):
    exigir(r.pessoa, 'uso')
    pessoas = [dict(x) for x in r.conn.execute(
        "SELECT a.email, MAX(a.nome) nome, MAX(a.papel) papel, COUNT(*) horas, COUNT(DISTINCT substr(a.quando,1,10)) dias, "
        "MAX(a.quando) ultimo FROM acesso a GROUP BY a.email ORDER BY ultimo DESC")]
    por_dia = [dict(x) for x in r.conn.execute(
        "SELECT substr(quando,1,10) dia, COUNT(DISTINCT email) pessoas FROM acesso GROUP BY 1 ORDER BY 1 DESC LIMIT 60")]
    return {'pessoas': pessoas, 'por_dia': por_dia}


@rota('GET', '/api/log')
def ver_log(r):
    exigir(r.pessoa, 'uso')
    busca = chave(r.q('q', ''))
    linhas = [dict(x) for x in r.conn.execute('SELECT * FROM log ORDER BY id DESC LIMIT 2000')]
    if busca:
        linhas = [l for l in linhas if busca in chave(' '.join(str(v) for v in l.values()))]
    return {'linhas': linhas[:500]}


@rota('GET', '/api/desfazer')
def listar_snapshots(r):
    exigir(r.pessoa, 'desfazer')
    return {'linhas': [dict(x) for x in r.conn.execute('SELECT id, quando, quem, descricao, desfeito FROM snapshot ORDER BY id DESC LIMIT 100')]}


@rota('POST', '/api/desfazer')
def desfazer(r):
    exigir(r.pessoa, 'desfazer')
    with Transacao(r.conn):
        try:
            db.restaurar(r.conn, int(r.body.get('id')), r.email)
        except ValueError as e:
            raise Erro(str(e))
    return {'ok': True}


# ---------------- To Do's ----------------

def _tarefas(conn, dp, cliente=None, arquivadas=False):
    sql = 'SELECT * FROM tarefa' + ('' if arquivadas else ' WHERE arquivada_em IS NULL')
    out = []
    for x in conn.execute(sql + " ORDER BY COALESCE(prazo, '9999-99-99'), id"):
        d = dict(x)
        d['cliente'] = dp.cliente(d['cliente'])
        if cliente is None or chave(d['cliente']) == chave(cliente):
            out.append(d)
    return out


@rota('GET', '/api/tarefas')
def listar_tarefas(r):
    exigir(r.pessoa, 'visao_geral')
    dp = calc.Depara(r.conn)
    cli = r.q('cliente')
    return {'tarefas': _tarefas(r.conn, dp, dp.cliente(cli) if cli else None, r.q('arquivadas') == '1'),
            'hoje': datetime.date.today().isoformat()}


@rota('POST', '/api/tarefas')
def criar_tarefa(r):
    exigir(r.pessoa, 'visao_geral')
    texto = ' '.join(str(r.body.get('texto') or '').split())
    if not texto:
        raise Erro('escreva o To Do')
    prazo = (r.body.get('prazo') or '').strip() or None
    if prazo:
        try:
            datetime.date.fromisoformat(prazo)
        except ValueError:
            raise Erro('prazo inválido')
    c = calc.Depara(r.conn).cliente(r.body.get('cliente') or '')
    with Transacao(r.conn):
        r.conn.execute('INSERT INTO tarefa (cliente, texto, prazo, criado_em, criado_por) VALUES (?,?,?,?,?)',
                       (c, texto, prazo, agora(), r.email))
        log(r.conn, r.email, 'TODO', f'{c}: {texto}')
    return {'ok': True}


@rota('POST', '/api/tarefas/arquivar')
def arquivar_tarefa(r):
    exigir(r.pessoa, 'visao_geral')
    tid = int(r.body.get('id'))
    voltar = bool(r.body.get('desarquivar'))
    with Transacao(r.conn):
        if voltar:
            n = r.conn.execute('UPDATE tarefa SET arquivada_em=NULL, arquivada_por=NULL WHERE id=?', (tid,)).rowcount
        else:
            n = r.conn.execute('UPDATE tarefa SET arquivada_em=?, arquivada_por=? WHERE id=?', (agora(), r.email, tid)).rowcount
        if not n:
            raise Erro('To Do não encontrado')
        log(r.conn, r.email, 'TODO_REABERTO' if voltar else 'TODO_ARQUIVADO', str(tid))
    return {'ok': True}


@rota('GET', '/api/backup')
def baixar_backup(r):
    """Superadmin baixa uma cópia completa do banco."""
    exigir(r.pessoa, 'backup')
    import os
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), f'midia-backup-{secrets.token_hex(4)}.db')
    try:
        db.backup(tmp)
        dados = open(tmp, 'rb').read()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    log(r.conn, r.email, 'BACKUP_BAIXADO', f'{len(dados)} bytes')
    return (dados, 'application/octet-stream', f'midia-sw-{datetime.date.today().isoformat()}.db')


@rota('POST', '/api/backup/restaurar')
def restaurar_backup(r):
    """Superadmin envia um .db e ele substitui o banco inteiro (o atual vai para backups/)."""
    exigir(r.pessoa, 'backup')
    if not r.raw:
        raise Erro('arquivo vazio')
    try:
        res = db.restaurar_arquivo(r.raw, r.email)
    except ValueError as e:
        raise Erro(str(e))
    with Transacao(r.conn):
        log(r.conn, r.email, 'BACKUP_RESTAURADO', res)
    return {'ok': True, **res}


# ---------------- chat e exportação ----------------

@rota('GET', '/api/chat')
def perguntar(r):
    a = calc.Ano(r.conn, r.ano())
    vis = visiveis(a, r.pessoa)
    if not vis:
        raise Proibido('sem acesso')
    filtro = filtro_carteira(r) if pode(r.pessoa, 'visao_geral') else 'todos'
    return chat.responder(a, vis, r.q('q', ''), reap=r.q('reap', '1') == '1', incluir_grupos=True,
                          casas=1 if r.q('casas') == '1' else 2,
                          carteira=filtrar_carteira(a, vis, filtro), rotulo_carteira=filtro)


@rota('GET', '/api/exportar')
def exportar(r):
    """Excel da tela atual. Respeita o botão de reap (reap=0 tira reaplicação de tudo).
    Diretor não exporta."""
    exigir(r.pessoa, 'exportar')
    a = calc.Ano(r.conn, r.ano())
    reap = r.q('reap', '1') == '1'
    escopo, nome = r.q('escopo', 'visao'), r.q('nome', '')
    cab_m = [m.capitalize() for m in MESES]

    def bloco(titulo_col, linhas):
        out = [[titulo_col, 'Tipo'] + cab_m + ['Realizado', 'Projetado', 'Ano']]
        for nome_l, l in sorted(linhas.items(), key=lambda x: (x[0] == LIVRE, -sum(x[1]['FAT']))):
            for t, rot in (('FAT', 'Faturável'), ('REAP', 'Reaplicação')):
                if t == 'REAP' and not reap:
                    continue
                p = a.partes(l[t])
                out.append([nome_l, rot] + [x / 100 for x in l[t]] + [p['realizado'] / 100, p['projetado'] / 100, p['ano'] / 100])
        return out

    abas = {}
    if escopo == 'cliente':
        c = _cliente_ou_404(a, nome, r.pessoa)
        res = a.resumo_cliente(c)
        abas['Resumo'] = [['Métrica', 'Valor'], ['Expectativa atual', res['expectativa_atual'] / 100],
                          ['Realizado faturável', res['realizado_fat'] / 100], ['Projetado faturável', res['projetado_fat'] / 100],
                          ['Livre', res['livre'] / 100], ['Expectativa inicial', (res['expectativa_inicial'] or 0) / 100]] + \
                         ([['Reaplicação', res['reap'] / 100]] if reap else []) + [['Mês fechado', MESES[a.fechado - 1] if a.fechado else '—']]
        abas['Grade'] = bloco('Veículo', a.grade(cliente=c))
        arq = f'{c}_{a.ano}.xlsx'
    elif escopo == 'veiculo':
        v = a.dp.veiculo(nome)
        vis = set(visiveis(a, r.pessoa))
        abas['Grade'] = bloco('Cliente', a.grade(por='cliente', veiculo=v, clientes=vis))
        arq = f'{v}_{a.ano}.xlsx'
    else:
        exigir(r.pessoa, 'visao_geral')
        lista = filtrar_carteira(a, a.clientes(), filtro_carteira(r))
        cons = a.consolidado(clientes=lista, incluir_grupos=True)
        cab = ['Cliente', 'Expectativa atual', 'Realizado fat.', 'Projetado fat.', 'Livre'] + (['Reaplicação'] if reap else []) + \
              ['Expectativa inicial', 'Diferença', 'Tipo']
        t = [cab]
        for c in cons['clientes']:
            t.append([c['cliente'], c['expectativa_atual'] / 100, c['realizado_fat'] / 100, c['projetado_fat'] / 100, c['livre'] / 100] +
                     ([c['reap'] / 100] if reap else []) + [(c['expectativa_inicial'] or 0) / 100, (c['diferenca'] or 0) / 100,
                                                            'whitelabel' if c['grupo'] else 'direto'])
        tt = cons['total']
        t.append(['TOTAL', tt['expectativa_atual'] / 100, tt['realizado_fat'] / 100, tt['projetado_fat'] / 100, tt['livre'] / 100] +
                 ([tt['reap'] / 100] if reap else []) + [tt['expectativa_inicial'] / 100, tt['diferenca'] / 100, ''])
        abas['Clientes'] = t
        abas['Veículos'] = bloco('Veículo', a.grade(clientes=set(lista)))
        sufixo = {'diretos': '_diretos', 'whitelabel': '_whitelabels', 'todos': ''}[filtro_carteira(r)]
        arq = f'Midia_SW_{a.ano}{sufixo}.xlsx'
    return (xlsx.escrever(abas), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', arq)
