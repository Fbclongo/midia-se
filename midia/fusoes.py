"""Sugestão de fusões de grafias de veículo.

Remove razão social e praça, ignora palavras genéricas (outdoor, shopping...)
e propõe pares de canônicos diferentes com núcleo de nome parecido.
Nunca funde sozinho: só sugere. Subsidiárias (Otima → Eletromidia) não se
descobrem por algoritmo.
"""
import re
from difflib import SequenceMatcher

from .util import chave

RAZAO_SOCIAL = {'ltda', 'sa', 's/a', 's.a', 'eireli', 'me', 'epp', 'mei', 'cia', 'comercio', 'servicos', 'servico',
                'participacoes', 'empreendimentos', 'holding', 'grupo', 'do', 'da', 'de', 'dos', 'das', 'e', 'em',
                'brasil', 'ind', 'industria', 'rpa', 'agencia'}
# o meio, não a marca: ES Outdoor, Cuiabá Outdoor e Outdoor Mídia são empresas diferentes
GENERICAS = {'outdoor', 'shopping', 'shoppings', 'midia', 'midias', 'publicidade', 'publicidades', 'propaganda',
             'propagandas', 'paineis', 'painel',
             'digital', 'digitais', 'dooh', 'ooh', 'led', 'comunicacao', 'marketing', 'exterior', 'mobiliario',
             'urbano', 'eventos', 'producoes', 'media', 'out', 'of', 'home', 'ads', 'tv', 'elevador', 'elevadores'}


def nucleo(nome):
    s = chave(nome)
    s = re.split(r'\s[-–/]\s', s)[0]          # "IMIDIA - SANTOS" → "imidia" (praça)
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    toks = [t for t in s.split() if t not in RAZAO_SOCIAL and t not in GENERICAS and len(t) > 1]
    return ' '.join(toks)


def compacto(nome):
    """Sem razão social e praça, mas mantendo as palavras genéricas, sem espaços:
    pega grafias coladas ("OOHBRASIL" = "OOH BRASIL", "EUROMIDIA" = "EURO MIDIA")."""
    s = chave(nome)
    s = re.split(r'\s[-–/]\s', s)[0]
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    toks = [t for t in s.split() if t not in RAZAO_SOCIAL - {'brasil'}]
    c = ''.join(toks)
    return c if len(c) >= 6 else ''


def sugerir(grafias, limiar=0.86):
    """grafias: lista de dicts {nome_bruto, canonico}. Retorna pares de canônicos a fundir."""
    por_canon = {}
    for g in grafias:
        por_canon.setdefault(g['canonico'], set()).add(g['nome_bruto'])
        por_canon[g['canonico']].add(g['canonico'])
    nucleos, compactos = {}, {}
    for c, nomes in por_canon.items():
        nucleos[c] = {nucleo(n) for n in nomes} - {''}
        compactos[c] = {compacto(n) for n in nomes} - {''}
    canons = sorted(por_canon, key=chave)
    pares = []
    for i, a in enumerate(canons):
        for b in canons[i + 1:]:
            melhor, motivo = 0, ''
            for x in nucleos[a]:
                for y in nucleos[b]:
                    if x == y:
                        melhor, motivo = 1.0, f'mesmo núcleo "{x}"'
                    else:
                        cx, cy = x.replace(' ', ''), y.replace(' ', '')
                        r = SequenceMatcher(None, cx, cy).ratio()
                        if min(len(cx), len(cy)) >= 5 and (cx.startswith(cy) or cy.startswith(cx)):
                            r = max(r, 0.9)
                        if r > melhor:
                            melhor, motivo = r, f'"{x}" ≈ "{y}"'
            if melhor < 1.0:
                for x in compactos[a]:
                    for y in compactos[b]:
                        if x == y or (min(len(x), len(y)) >= 8 and (x.startswith(y) or y.startswith(x))):
                            if 0.95 > melhor:
                                melhor, motivo = 0.95, f'grafia colada/estendida "{x}" ~ "{y}"'
            if melhor >= limiar:
                pares.append({'a': a, 'b': b, 'score': round(melhor, 2), 'motivo': motivo})
    pares.sort(key=lambda p: -p['score'])
    return pares
