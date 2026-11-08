"""Funções pequenas usadas em todo o sistema.

Regra: nomes nunca diferem por maiúscula/minúscula nem por acento.
Toda comparação de nome passa por `chave()`.
"""
import datetime
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

LIVRE = 'LIVRE'
MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']


def sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def chave(nome):
    """Chave de comparação: sem caixa, sem acento, espaços colapsados."""
    if nome is None:
        return ''
    return ' '.join(sem_acento(str(nome)).lower().split())


def eh_livre(nome):
    return chave(nome) == 'livre'


def tipo_pi(tipo):
    """Normaliza o tipo do PI. Retorna 'FAT', 'REAP' ou None (descartado)."""
    k = chave(tipo)
    if k in ('veiculado', 'fat', 'faturavel'):
        return 'FAT'
    if k in ('reaplicacao', 'reap', 'reaplicado'):
        return 'REAP'
    return None


def centavos(valor):
    """Converte valor (número, '1.482.322,52', '1482322.52', '1.5E7') em centavos inteiros.

    Nunca arredonda além do centavo. Levanta ValueError se não entender.
    """
    if valor is None or valor == '':
        raise ValueError('valor vazio')
    if isinstance(valor, int):
        return valor * 100
    if isinstance(valor, float):
        d = Decimal(repr(valor))
    else:
        s = str(valor).strip().replace('R$', '').replace(' ', '').replace(' ', '')
        neg = s.startswith('-')
        s = s.lstrip('-')
        if ',' in s:
            # formato brasileiro: ponto é milhar, vírgula é decimal
            s = s.replace('.', '').replace(',', '.')
        elif s.count('.') > 1:
            s = s.replace('.', '')
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ValueError(f'valor inválido: {valor!r}')
        if neg:
            d = -d
    return int((d * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def mes_ano(valor):
    """Lê o mês de veiculação. Aceita 'MM/AAAA', 'M/AAAA', 'AAAA-MM', data ISO
    e número de série de data do Excel/Sheets (46023 = 01/01/2026).
    Retorna (ano, mes)."""
    s = str(valor).strip()
    m = re.fullmatch(r'(\d{1,2})[/\-.](\d{4})', s)
    if m:
        mes, ano = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r'(\d{4})-(\d{1,2})(?:-\d{1,2}(?:[ T].*)?)?', s)
        if m:
            ano, mes = int(m.group(1)), int(m.group(2))
        else:
            m = re.fullmatch(r'\d{1,2}/(\d{1,2})/(\d{4})(?: .*)?', s)
            if m:
                mes, ano = int(m.group(1)), int(m.group(2))
            else:
                try:
                    serial = float(s)
                except ValueError:
                    raise ValueError(f'mês inválido: {valor!r}')
                d = datetime.date(1899, 12, 30) + datetime.timedelta(days=int(serial))
                ano, mes = d.year, d.month
    if not 1 <= mes <= 12 or not 2000 <= ano <= 2100:
        raise ValueError(f'mês inválido: {valor!r}')
    return ano, mes


def agora():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def reais(c):
    """Centavos → '1.482.322,52'."""
    neg = c < 0
    c = abs(c)
    s = f'{c // 100:,}'.replace(',', '.') + ',' + f'{c % 100:02d}'
    return ('-' if neg else '') + s


def milhoes(c):
    """Centavos → '1,48M'."""
    return f'{c / 100_000_000:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + 'M'


def numero_pi(v):
    s = str(v).strip()
    if re.fullmatch(r'\d+\.0', s):
        s = s[:-2]
    return s
