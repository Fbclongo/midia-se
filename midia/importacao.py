"""Importação de PIs (upload) e seed a partir da planilha antiga."""
import datetime
import json
from collections import Counter, defaultdict

from . import xlsx
from .db import log, set_config, snapshot
from .util import chave, centavos, mes_ano, tipo_pi, numero_pi, agora, eh_livre, LIVRE

COLUNAS_PI = {
    'numero': ('numero do pi', 'numero', 'pi', 'n pi', 'no pi'),
    'cliente': ('cliente',),
    'veiculo': ('veiculo',),
    'tipo': ('tipo',),
    'valor': ('valor', 'valor liquido', 'valor bruto'),
    'mes': ('mes veiculacao', 'mes de veiculacao', 'mes', 'veiculacao'),
}


def ler_arquivo(conteudo, nome):
    """Arquivo enviado → lista de linhas (primeira aba com cabeçalho de PI)."""
    if nome.lower().endswith(('.xlsx', '.xlsm')):
        abas = xlsx.ler(conteudo)
        for linhas in abas.values():
            if linhas and _mapear_cabecalho(linhas[0]):
                return linhas
        raise ValueError('nenhuma aba com as colunas Cliente, Veiculo, Tipo, Valor e Mes veiculacao')
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return xlsx.ler_csv(conteudo.decode(enc))
        except UnicodeDecodeError:
            continue
    raise ValueError('não consegui ler o arquivo')


def _mapear_cabecalho(cab):
    idx = {}
    ks = [chave(c).replace('.', '').replace('º', '').replace('°', '') for c in cab]
    for campo, nomes in COLUNAS_PI.items():
        for i, k in enumerate(ks):
            if k in nomes:
                idx[campo] = i
                break
    obrig = {'cliente', 'veiculo', 'tipo', 'valor', 'mes'}
    return idx if obrig <= set(idx) else None


def interpretar_pis(linhas):
    """Linhas cruas → (pis_validos, descartados_por_tipo, erros)."""
    idx = _mapear_cabecalho(linhas[0])
    if not idx:
        raise ValueError('cabeçalho não reconhecido. Esperado: Numero do PI, Cliente, Veiculo, Tipo, Valor, Mes veiculacao')
    pis, descartados, erros = [], Counter(), []

    def cel(l, campo):
        i = idx.get(campo)
        return l[i] if i is not None and i < len(l) else ''

    for n, l in enumerate(linhas[1:], start=2):
        tipo = tipo_pi(cel(l, 'tipo'))
        if tipo is None:
            descartados[str(cel(l, 'tipo')).strip() or '(vazio)'] += 1
            continue
        try:
            ano, mes = mes_ano(cel(l, 'mes'))
            valor = centavos(cel(l, 'valor'))
        except ValueError as e:
            erros.append(f'linha {n}: {e}')
            continue
        cli, vei = ' '.join(str(cel(l, 'cliente')).split()), ' '.join(str(cel(l, 'veiculo')).split())
        if not cli or not vei:
            erros.append(f'linha {n}: cliente ou veículo vazio')
            continue
        pis.append({'numero': numero_pi(cel(l, 'numero')), 'cliente_bruto': cli, 'veiculo_bruto': vei,
                    'tipo': tipo, 'valor': valor, 'ano': ano, 'mes': mes})
    return pis, dict(descartados), erros


def registrar_nomes_novos(conn, pis):
    """Nome novo nunca some: entra como pendente, mapeado para si mesmo.
    Se já bate com um canônico ignorando caixa/acento, não é novo."""
    vk = {r[0] for r in conn.execute('SELECT chave FROM veiculo')} | \
         {chave(r[0]) for r in conn.execute('SELECT DISTINCT canonico FROM veiculo')}
    ck = {r[0] for r in conn.execute('SELECT chave FROM cliente')} | \
         {chave(r[0]) for r in conn.execute('SELECT DISTINCT canonico FROM cliente')}
    novos_v, novos_c = {}, {}
    for p in pis:
        kv, kc = chave(p['veiculo_bruto']), chave(p['cliente_bruto'])
        if kv not in vk and kv != 'livre':
            novos_v.setdefault(kv, p['veiculo_bruto'])
        if kc not in ck:
            novos_c.setdefault(kc, p['cliente_bruto'])
    for k, nome in novos_v.items():
        conn.execute("INSERT INTO veiculo (nome_bruto, chave, canonico, status) VALUES (?,?,?,'pendente')", (nome, k, nome))
    for k, nome in novos_c.items():
        conn.execute("INSERT INTO cliente (nome_bruto, chave, canonico, status) VALUES (?,?,?,'pendente')", (nome, k, nome))
    return list(novos_v.values()), list(novos_c.values())


def gravar_pis(conn, quem, pis, modo='substituir_ano', descricao=None):
    """modo:
       substituir_ano   — apaga e regrava os anos presentes no arquivo (comportamento antigo)
       substituir_meses — apaga e regrava só os meses presentes no arquivo
    Sempre guarda snapshot antes, para desfazer."""
    if modo not in ('substituir_ano', 'substituir_meses'):
        raise ValueError('modo inválido')
    if modo == 'substituir_ano':
        alvos = sorted({(p['ano'],) for p in pis})
        where = ' OR '.join('(ano=?)' for _ in alvos)
    else:
        alvos = sorted({(p['ano'], p['mes']) for p in pis})
        where = ' OR '.join('(ano=? AND mes=?)' for _ in alvos)
    params = [x for a in alvos for x in a]
    if not alvos:
        raise ValueError('nenhum PI válido no arquivo')
    sid = snapshot(conn, quem, descricao or f'Upload de PIs ({modo})',
                   {'pi': (where, params), 'veiculo': (None, ()), 'cliente': (None, ())})
    conn.execute(f'DELETE FROM pi WHERE {where}', params)
    conn.executemany('INSERT INTO pi (ano, mes, numero, cliente_bruto, veiculo_bruto, tipo, valor) VALUES (?,?,?,?,?,?,?)',
                     [(p['ano'], p['mes'], p['numero'], p['cliente_bruto'], p['veiculo_bruto'], p['tipo'], p['valor']) for p in pis])
    novos_v, novos_c = registrar_nomes_novos(conn, pis)
    return sid, novos_v, novos_c


# ---------------- seed ----------------

def _quando(v):
    if v in (None, ''):
        return None
    s = str(v).strip()
    try:
        f = float(s)
        d = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=f)
        return d.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        pass
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(s, fmt).strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    return s


def _bool(v):
    return chave(v) in ('true', '1', 'sim', 'verdadeiro', 'ativo', 'x')


def _int(v):
    return int(float(v))


def _tab(abas, nome):
    for k, v in abas.items():
        if chave(k) == chave(nome):
            if not v:
                return []
            cab = [chave(c) for c in v[0]]
            return [dict(zip(cab, l + [''] * (len(cab) - len(l)))) for l in v[1:]]
    return []


def _exibicao(nomes):
    """Entre grafias do mesmo canônico (OOH Brasil / OOH BRASIL), prefere a com caixa mista."""
    nomes = list(dict.fromkeys(nomes))
    mistas = [n for n in nomes if not n.isupper() and not n.islower()]
    return (mistas or nomes)[0]


def seed(conn, conteudo, quem='seed', mes_fechado_2026=7):
    abas = xlsx.ler(conteudo)
    rel = {}
    for t in ('pi', 'veiculo', 'cliente', 'pessoa', 'expectativa', 'projecao', 'meta', 'pacote', 'acesso', 'log'):
        conn.execute(f'DELETE FROM {t}')

    # veículos
    linhas = [l for l in _tab(abas, 'Veiculos') if l.get('nome bruto', '').strip()]
    disp = defaultdict(list)
    for l in linhas:
        disp[chave(l['nome canonico'])].append(l['nome canonico'].strip())
    vistos, conflitos = set(), []
    for l in linhas:
        k = chave(l['nome bruto'])
        canon = _exibicao(disp[chave(l['nome canonico'])])
        if eh_livre(canon) or k == 'livre':
            conflitos.append(f'LIVRE é reservado: {l["nome bruto"]}')
            continue
        if k in vistos:
            conflitos.append(f'grafia repetida (mesma chave): {l["nome bruto"]}')
            continue
        vistos.add(k)
        status = 'pendente' if chave(l.get('status')) == 'pendente' else 'ativo'
        conn.execute('INSERT INTO veiculo (nome_bruto, chave, canonico, status) VALUES (?,?,?,?)',
                     (l['nome bruto'].strip(), k, canon, status))
    unificados = {k: v for k, v in disp.items() if len(set(v)) > 1}
    rel['veiculos'] = {'grafias': len(vistos), 'canonicos': len(disp), 'conflitos': conflitos,
                       'canonicos_unificados': {k: sorted(set(v)) for k, v in unificados.items()}}

    # clientes
    n = 0
    for l in _tab(abas, 'Clientes'):
        if not l.get('nome bruto', '').strip():
            continue
        conn.execute('INSERT OR IGNORE INTO cliente (nome_bruto, chave, canonico, diretor_email, ativo, grupo) VALUES (?,?,?,?,?,?)',
                     (l['nome bruto'].strip(), chave(l['nome bruto']), l['nome canonico'].strip() or l['nome bruto'].strip(),
                      (l.get('diretor (email)') or '').strip().lower() or None, 1 if _bool(l.get('ativo')) else 0,
                      (l.get('grupo') or '').strip() or None))
        n += 1
    rel['clientes'] = n

    # equipe
    n = 0
    for l in _tab(abas, 'Equipe'):
        if not l.get('email', '').strip():
            continue
        conn.execute('INSERT OR REPLACE INTO pessoa (email, nome, papel, ativo) VALUES (?,?,?,?)',
                     (l['email'].strip().lower(), l.get('nome', '').strip(), chave(l['papel']), 1 if _bool(l.get('ativo')) else 0))
        n += 1
    rel['equipe'] = n

    # PIs
    bruto = next((v for k, v in abas.items() if chave(k) == 'pis'), [])
    pis, desc, erros = interpretar_pis(bruto)
    conn.executemany('INSERT INTO pi (ano, mes, numero, cliente_bruto, veiculo_bruto, tipo, valor) VALUES (?,?,?,?,?,?,?)',
                     [(p['ano'], p['mes'], p['numero'], p['cliente_bruto'], p['veiculo_bruto'], p['tipo'], p['valor']) for p in pis])
    novos_v, novos_c = registrar_nomes_novos(conn, pis)
    rel['pis'] = {'importados': len(pis), 'descartados': desc, 'erros': erros,
                  'fat': sum(p['valor'] for p in pis if p['tipo'] == 'FAT'),
                  'reap': sum(p['valor'] for p in pis if p['tipo'] == 'REAP'),
                  'veiculos_pendentes': novos_v, 'clientes_pendentes': novos_c}

    # expectativa
    n = 0
    for l in _tab(abas, 'Expectativa'):
        if not l.get('cliente', '').strip():
            continue
        conn.execute('INSERT OR REPLACE INTO expectativa VALUES (?,?,?,?,?)',
                     (l['cliente'].strip(), _int(l['ano']), centavos(l['valor']), l.get('quem') or quem, _quando(l.get('quando'))))
        n += 1
    rel['expectativa'] = n

    # projeção
    total, n, livre = 0, 0, 0
    for l in _tab(abas, 'Projecao'):
        if not l.get('cliente', '').strip():
            continue
        tipo = 'REAP' if chave(l['tipo']) in ('reap', 'reaplicacao') else 'FAT'
        v = centavos(l['valor'])
        veic = LIVRE if eh_livre(l['veiculo']) else l['veiculo'].strip()
        origem = 'OTHERS do forecast (migrado como LIVRE)' if veic == LIVRE else None
        conn.execute('INSERT INTO projecao VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO UPDATE SET valor=valor+excluded.valor',
                     (l['cliente'].strip(), veic, tipo, _int(l['ano']), _int(l['mes']), v,
                      l.get('quem') or quem, _quando(l.get('quando')) or agora(), origem))
        total += v
        n += 1
        if veic == LIVRE:
            livre += v
    rel['projecao'] = {'linhas': n, 'total': total, 'livre': livre}

    # metas
    n = 0
    for l in _tab(abas, 'Metas'):
        if l.get('veiculo', '').strip():
            conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?)',
                         (l['veiculo'].strip(), _int(l['ano']), centavos(l['valor']), l.get('quem') or quem, _quando(l.get('quando'))))
            n += 1
    rel['metas'] = n

    # pacotes
    n = 0
    for l in _tab(abas, 'Pacotes'):
        if l.get('id', '').strip():
            conn.execute('INSERT OR REPLACE INTO pacote VALUES (?,?,?,?,?,?,?,?,?,?)',
                         (l['id'].strip(), l['cliente'].strip(), l.get('veiculo', '').strip() or None,
                          centavos(l['valor']) if l.get('valor') else None, _quando(l.get('inicio')), _quando(l.get('fim')),
                          l.get('descricao'), l.get('status'), l.get('quem'), _quando(l.get('quando'))))
            n += 1
    rel['pacotes'] = n

    # histórico (dispensável, mas barato)
    for l in _tab(abas, 'Acessos'):
        if l.get('email'):
            conn.execute('INSERT INTO acesso VALUES (?,?,?,?)', (_quando(l.get('quando')), l['email'].lower(), l.get('nome'), l.get('papel')))
    for l in _tab(abas, 'Log'):
        if l.get('acao'):
            conn.execute('INSERT INTO log (quando, quem, acao, detalhe) VALUES (?,?,?,?)',
                         (_quando(l.get('quando')), l.get('quem'), l['acao'], l.get('detalhe')))

    set_config(conn, 'mes_fechado:2026', mes_fechado_2026)
    set_config(conn, 'long_tail_cliente', 'Mondelez')
    set_config(conn, 'long_tail_nucleo', json.dumps(['Eletromidia', 'JCDecaux', 'Kallas', 'NeOOH', 'BrMalls']))
    set_config(conn, 'seed', json.dumps({'quando': agora(), 'relatorio': rel}, ensure_ascii=False))
    log(conn, quem, 'SEED', {k: v for k, v in rel.items() if k != 'veiculos'})
    return rel
