"""Chat sobre os dados — sem IA.

Código interpreta a pergunta; as contas vêm de midia.calc (as mesmas das telas).
Nunca inventa número. Quando não entende, diz.

Casamento por palavra inteira (erro antigo: "investimento" achava TIM).
Nomes que também são palavras comuns ("Nós") só casam com o acento exato
(erro antigo: "nos" achava o veículo Nós). Mês abreviado só com número ou
barra ao lado (erro antigo: "maiores" virava maio).
"""
import re
import unicodedata

from .util import chave, milhoes, reais, LIVRE, MESES

MESES_EXTENSO = ['janeiro', 'fevereiro', 'marco', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
                 'outubro', 'novembro', 'dezembro']
PALAVRAS_COMUNS = {'nos', 'sim', 'mais', 'mas', 'um', 'uma', 'ja', 'so', 'meta', 'livre', 'top', 'ate',
                   'de', 'da', 'do', 'em', 'no', 'na', 'o', 'a', 'e', 'que', 'por', 'para', 'com', 'b', 'mar',
                   'set', 'out', 'ola', 'via', 'boa', 'bem', 'mundo', 'total', 'grupo'}


def _toks(s):
    return re.findall(r'[a-z0-9]+', chave(s))


def _toks_acento(s):
    return re.findall(r'\w+', ' '.join(str(s).lower().split()))


def _contem(seq, sub):
    n = len(sub)
    return n > 0 and any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def achar_nomes(q, nomes):
    """nomes: {nome_exibição: [apelidos]}. Retorna nomes encontrados por palavra inteira."""
    tq, ta = _toks(q), _toks_acento(q)
    achados = []
    for nome, apelidos in nomes.items():
        for ap in [nome] + list(apelidos):
            t = _toks(ap)
            if not t:
                continue
            if len(t) == 1 and t[0] in PALAVRAS_COMUNS:
                # palavra comum: exige a grafia com acento e, se o nome tem maiúscula, maiúscula
                if _contem(ta, _toks_acento(ap)) and ap != chave(ap) and re.search(r'\b' + re.escape(ap) + r'\b', q):
                    achados.append(nome)
                    break
                continue
            if _contem(tq, t):
                achados.append(nome)
                break
    # se um nome achado está contido em outro achado maior, fica o maior
    return [n for n in achados if not any(n != m and _contem(_toks(m), _toks(n)) for m in achados)]


def achar_mes(q):
    tq = _toks(q)
    for i, m in enumerate(MESES_EXTENSO):
        if m in tq:
            return i + 1
    k = chave(q)
    for i, m in enumerate(MESES):
        if re.search(r'\b' + m + r'(\s*/\s*\d{2,4}|\s+(de\s+)?\d{2,4})\b', k):
            return i + 1
    m = re.search(r'\bmes\s+(\d{1,2})\b', k)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    return None


def achar_metrica(q):
    t = set(_toks(q))
    k = chave(q)
    if 'livre' in t or 'livres' in t:
        return 'livre'
    if t & {'reap', 'reaplicacao', 'reaplicacoes', 'bonificacao'}:
        return 'reap'
    if 'meta' in t or 'metas' in t:
        return 'meta'
    if 'inicial' in t or 'combinado' in t or 'combinada' in t:
        return 'inicial'
    if t & {'realizado', 'realizou', 'gastou', 'gasto', 'gastos', 'veiculado', 'pis', 'pi'}:
        return 'realizado'
    if t & {'projetado', 'projecao', 'projeta', 'projetada', 'previsto', 'vai'}:
        return 'projetado'
    if 'expectativa' in t or 'investimento' in t or 'total' in t or 'quanto' in t:
        return 'total'
    return None


def achar_quebra(q):
    t = _toks(q)
    k = ' '.join(t)
    rank = bool(set(t) & {'maiores', 'maior', 'ranking', 'top', 'principais', 'lideres'}) or 'quem mais' in k
    if re.search(r'\bpor (veiculo|veiculos)\b', k) or (rank and set(t) & {'veiculo', 'veiculos'}):
        return 'veiculo', rank
    if re.search(r'\bpor (cliente|clientes)\b', k) or rank:
        return 'cliente', rank
    return None, False


import contextvars

# casas decimais em milhões: 2 nas telas de diretor, 1 na visão geral
CASAS = contextvars.ContextVar('casas', default=2)


def M(c):
    if abs(c) >= 100_000_000 or c == 0:
        n = CASAS.get()
        return 'R$ ' + f'{c / 100_000_000:,.{n}f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + 'M'
    return 'R$ ' + f'{c / 100_000:,.1f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + ' mil'


def responder(a, clientes_visiveis, q, reap=True, incluir_grupos=False, casas=2, carteira=None, rotulo_carteira=None):
    """a: calc.Ano. clientes_visiveis: lista de clientes que a pessoa pode ver."""
    CASAS.set(1 if casas == 1 else 2)
    q = (q or '').strip()
    if not q:
        return {'texto': 'Faça uma pergunta. Ex.: "quanto a Keeta investe na Eletromidia?"'}
    nomes_cli = {c: [] for c in clientes_visiveis}
    nomes_vei = {}
    for l in a.dp.veic_linhas:
        nomes_vei.setdefault(a.dp.veiculo(l['canonico']), []).append(l['nome_bruto'])
    clientes = achar_nomes(q, nomes_cli)
    veiculos = [v for v in achar_nomes(q, nomes_vei) if v not in clientes]
    mes = achar_mes(q)
    metrica = achar_metrica(q)
    quebra, rank = achar_quebra(q)
    if not (clientes or veiculos or metrica or quebra or mes):
        return {'texto': 'Não entendi a pergunta. Eu entendo cliente, veículo, métrica (realizado, projetado, '
                         'expectativa, livre, reaplicação, meta), mês e rankings.\n'
                         'Ex.: "livre por cliente", "maiores veículos em reap", "Tim na JCDecaux em setembro".'}
    metrica = metrica or 'total'
    fora = []
    if carteira is not None:
        escopo_cli = clientes or list(carteira)
        if not clientes and rotulo_carteira in ('diretos', 'whitelabel'):
            fora.append('só clientes diretos' if rotulo_carteira == 'diretos' else 'só whitelabels')
    else:
        escopo_cli = clientes or [c for c in clientes_visiveis if a.em_totais(c, incluir_grupos)]
    if carteira is None and not clientes and not incluir_grupos:
        n_fora = len([c for c in clientes_visiveis if not a.em_totais(c)])
        if n_fora:
            fora.append(f'{n_fora} clientes de grupo (whitelabel) fora da conta')
    if metrica not in ('reap',) and reap:
        fora.append('reaplicação fora das contas de dinheiro (aparece à parte)')
    elif metrica != 'reap' and not reap:
        fora.append('reaplicação desligada')
    f = a.fechado
    rotulo_fechado = f'mês fechado: {MESES[f - 1]}' if f else 'nenhum mês fechado'

    def valores(clis, veis):
        """soma FAT e REAP (12 meses exibidos) para o recorte."""
        fat, rp, ab = [0] * 12, [0] * 12, [0] * 12
        for c in clis:
            g = a.grade(cliente=c)
            for v, l in g.items():
                if veis and v not in veis:
                    continue
                for m in range(12):
                    fat[m] += l['FAT'][m]
                    rp[m] += l['REAP'][m]
                    ab[m] += l['aberto_FAT'][m]
        return fat, rp, ab

    def decomp(meses):
        r, p = sum(meses[:f]), sum(meses[f:])
        return f'realizado {M(r)} + projetado {M(p)} = {M(r + p)}'

    # --- quebras / rankings ---
    if quebra:
        itens = []
        grupos = escopo_cli if quebra == 'cliente' else None
        if quebra == 'cliente':
            for c in escopo_cli:
                itens.append((c, valor_metrica(a, metrica, [c], veiculos or None, mes)))
        else:
            todos = {}
            for c in escopo_cli:
                for v in a.grade(cliente=c):
                    todos.setdefault(v, None)
            for v in todos:
                if v == LIVRE and metrica != 'livre':
                    continue
                itens.append((v, valor_metrica(a, metrica, escopo_cli, [v], mes)))
        itens = [(n, x) for n, x in itens if x is not None and x[0] != 0]
        if metrica == 'livre' and quebra == 'veiculo':
            return {'texto': 'Livre é verba sem veículo — não tem quebra por veículo. Tente "livre por cliente".'}
        itens.sort(key=lambda x: -x[1][0])
        limite = 10 if rank else len(itens)
        titulo = {'total': 'expectativa atual (faturável)', 'realizado': 'realizado faturável',
                  'projetado': 'projetado faturável', 'livre': 'verba livre', 'reap': 'reaplicação',
                  'inicial': 'expectativa inicial', 'meta': 'faturável × meta'}[metrica]
        linhas = [f'{i + 1}. {n}: {x[1]}' for i, (n, x) in enumerate(itens[:limite])]
        tot = sum(x[1][0] for x in itens)
        cab = f'{titulo.capitalize()} por {quebra}' + (f' em {MESES_EXTENSO[mes - 1]}' if mes else '') + \
              (' — ' + ', '.join(veiculos + clientes) if (veiculos or clientes) else '')
        txt = cab + ':\n' + '\n'.join(linhas)
        if len(itens) > limite:
            txt += f'\n… e mais {len(itens) - limite}.'
        txt += f'\nTotal ({len(itens)}): {M(tot)}.'
        return {'texto': txt + _rodape(fora, rotulo_fechado)}

    # --- valor único ---
    nome = ' na '.join(filter(None, [', '.join(clientes), ', '.join(veiculos)])) or 'Consolidado'
    if metrica == 'livre':
        if veiculos:
            return {'texto': 'Livre é verba sem veículo definido — não existe livre de um veículo.'}
        v = sum(a.resumo_cliente(c)['livre'] for c in escopo_cli)
        return {'texto': f'{nome} — verba livre (linha LIVRE, meses abertos): {M(v)} ({reais(v)}).'
                         + _rodape(fora, rotulo_fechado)}
    if metrica == 'inicial':
        v = sum(a.expectativa.get(c, 0) for c in escopo_cli)
        return {'texto': f'{nome} — expectativa inicial: {M(v)}.' + _rodape(fora, rotulo_fechado)}
    if metrica == 'meta':
        g = {}
        for c in escopo_cli:
            for vv, l in a.grade(cliente=c).items():
                x = g.setdefault(vv, {'FAT': [0] * 12, 'REAP': [0] * 12})
                for m in range(12):
                    x['FAT'][m] += l['FAT'][m]
        cm = a.comparacao_meta(g)
        alvo = veiculos or list(cm['por_veiculo'])
        linhas = []
        for vv in alvo:
            x = cm['por_veiculo'].get(vv)
            if x:
                linhas.append(f'{vv}: faturável {M(x["fat"])} de meta {M(x["meta"])} ({x["pct"]:.0%})')
            else:
                linhas.append(f'{vv}: sem meta cadastrada')
        return {'texto': 'Meta (só faturável, LIVRE fora):\n' + '\n'.join(linhas) + _rodape(fora, rotulo_fechado)}
    fat, rp, ab = valores(escopo_cli, veiculos or None)
    if mes:
        i = mes - 1
        if metrica == 'reap':
            v, base = rp[i], 'reaplicação'
        else:
            v, base = fat[i], 'faturável'
        status = 'realizado (PIs)' if mes <= f else 'projetado'
        txt = f'{nome} — {MESES_EXTENSO[mes - 1]}: {base} {M(v)}, {status}.'
        if mes > f and ab[i]:
            txt += f' Já emitido em PIs nesse mês aberto: {M(ab[i])} (ainda não conta como realizado).'
        return {'texto': txt + _rodape(fora, rotulo_fechado)}
    if metrica == 'reap':
        txt = f'{nome} — reaplicação: {decomp(rp)}.'
    elif metrica == 'realizado':
        txt = f'{nome} — realizado faturável: {M(sum(fat[:f]))} (até {MESES[f - 1] if f else "—"}).'
        if sum(ab):
            txt += f' Emitido em meses abertos: {M(sum(ab))}.'
    elif metrica == 'projetado':
        txt = f'{nome} — projetado faturável: {M(sum(fat[f:]))}.'
    else:
        txt = f'{nome} — faturável: {decomp(fat)}.'
        if reap and sum(rp):
            txt += f'\nReaplicação (à parte): {decomp(rp)}.'
    return {'texto': txt + _rodape(fora, rotulo_fechado)}


def valor_metrica(a, metrica, clis, veis, mes):
    """(valor_centavos, texto) para rankings."""
    f = a.fechado
    if metrica == 'livre':
        v = sum(a.resumo_cliente(c)['livre'] for c in clis)
        return v, M(v)
    if metrica == 'inicial':
        v = sum(a.expectativa.get(c, 0) for c in clis)
        return v, M(v)
    fat, rp = [0] * 12, [0] * 12
    for c in clis:
        for vv, l in a.grade(cliente=c).items():
            if veis and vv not in veis:
                continue
            for m in range(12):
                fat[m] += l['FAT'][m]
                rp[m] += l['REAP'][m]
    base = rp if metrica == 'reap' else fat
    if mes:
        v = base[mes - 1]
        return v, M(v)
    r, p = sum(base[:f]), sum(base[f:])
    if metrica == 'realizado':
        return r, M(r)
    if metrica == 'projetado':
        return p, M(p)
    return r + p, f'{M(r + p)} (real. {M(r)} + proj. {M(p)})'


def _rodape(fora, rotulo):
    partes = [rotulo] + fora
    return '\n(' + '; '.join(partes) + ')'
