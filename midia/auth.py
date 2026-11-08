"""Papéis e permissões. A barreira é aqui, no servidor.

Superadmin herda admin POR CONSTRUÇÃO: o conjunto dele é montado a partir do
conjunto do admin. Nenhuma checagem lista papéis — todas perguntam por
capacidade com `pode(pessoa, 'capacidade')`.
"""

PAPEIS = ('superadmin', 'admin', 'diretor', 'operacao')

_OPERACAO = {'pis'}
_DIRETOR = {'ficha_propria'}
_ADMIN = {'visao_geral', 'ficha_todas', 'editar_todas', 'pis', 'fechar_mes',
          'equipe', 'depara', 'grupos', 'colar_projecao', 'desfazer', 'exportar'}
_SUPERADMIN = _ADMIN | {'papeis_admin', 'meta', 'uso', 'backup'}

CAPACIDADES = {
    'superadmin': frozenset(_SUPERADMIN),
    'admin': frozenset(_ADMIN),
    'diretor': frozenset(_DIRETOR),
    'operacao': frozenset(_OPERACAO),
}

assert CAPACIDADES['admin'] <= CAPACIDADES['superadmin'], 'superadmin precisa herdar admin'


class Proibido(Exception):
    pass


def capacidades(pessoa):
    if not pessoa or not pessoa.get('ativo'):
        return frozenset()
    return CAPACIDADES.get(pessoa.get('papel'), frozenset())


def pode(pessoa, cap):
    return cap in capacidades(pessoa)


def exigir(pessoa, cap):
    if not pode(pessoa, cap):
        raise Proibido(f'sem permissão: {cap}')


def pode_ver_cliente(pessoa, info_cliente):
    if pode(pessoa, 'ficha_todas'):
        return True
    return (pode(pessoa, 'ficha_propria') and info_cliente.get('diretor_email')
            and info_cliente['diretor_email'].lower() == pessoa['email'].lower())


def pode_editar_cliente(pessoa, info_cliente):
    if pode(pessoa, 'editar_todas'):
        return True
    return pode_ver_cliente(pessoa, info_cliente) and pode(pessoa, 'ficha_propria')


def checar_mudanca_papel(conn, autor, email, novo_papel, novo_ativo):
    """Travas da equipe: só superadmin mexe em admin/superadmin; nunca fica sem superadmin ativo."""
    atual = conn.execute('SELECT * FROM pessoa WHERE lower(email)=lower(?)', (email,)).fetchone()
    envolvidos = {novo_papel} | ({atual['papel']} if atual else set())
    if envolvidos & {'admin', 'superadmin'}:
        exigir(autor, 'papeis_admin')
    else:
        exigir(autor, 'equipe')
    if novo_papel not in PAPEIS:
        raise ValueError('papel inválido')
    if atual and atual['papel'] == 'superadmin' and atual['ativo'] and (novo_papel != 'superadmin' or not novo_ativo):
        n = conn.execute("SELECT COUNT(*) FROM pessoa WHERE papel='superadmin' AND ativo=1").fetchone()[0]
        if n <= 1:
            raise ValueError('não dá para rebaixar ou inativar o último superadmin ativo')
