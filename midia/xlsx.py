"""Leitura e escrita mínima de .xlsx usando só a biblioteca padrão."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
_T = '{%s}t' % NS['m']


def _col(ref):
    n = 0
    for ch in re.match(r'[A-Z]+', ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def ler(conteudo):
    """bytes de um .xlsx → {nome_aba: [[celula, ...], ...]} (linhas vazias removidas).
    Números vêm como string do jeito que estão gravados ('67704.0', '46023')."""
    z = zipfile.ZipFile(io.BytesIO(conteudo))
    ss = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS):
            ss.append(''.join(t.text or '' for t in si.iter(_T)))
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    relmap = {r.get('Id'): r.get('Target') for r in rels}
    abas = {}
    for s in wb.find('m:sheets', NS):
        alvo = relmap[s.get('{%s}id' % NS['r'])].lstrip('/')
        if not alvo.startswith('xl/'):
            alvo = 'xl/' + alvo
        raiz = ET.fromstring(z.read(alvo))
        linhas = []
        for row in raiz.iter('{%s}row' % NS['m']):
            vals = {}
            for c in row.findall('m:c', NS):
                t = c.get('t')
                v = c.find('m:v', NS)
                if t == 's':
                    val = ss[int(v.text)]
                elif t == 'inlineStr':
                    val = ''.join(x.text or '' for x in c.iter(_T))
                elif t == 'b':
                    val = 'TRUE' if v is not None and v.text == '1' else 'FALSE'
                else:
                    val = v.text if v is not None else ''
                vals[_col(c.get('r'))] = val
            linha = [vals.get(i, '') for i in range(max(vals) + 1)] if vals else []
            if any(str(x).strip() for x in linha):
                linhas.append(linha)
        abas[s.get('name')] = linhas
    return abas


def _nome_col(i):
    s = ''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def escrever(abas):
    """{nome_aba: [[valor, ...]]} → bytes .xlsx. Números ficam numéricos."""
    buf = io.BytesIO()
    z = zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED)
    nomes = list(abas)
    z.writestr('[Content_Types].xml',
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
               + ''.join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(nomes)))
               + '</Types>')
    z.writestr('_rels/.rels',
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
               '</Relationships>')
    z.writestr('xl/workbook.xml',
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
               + ''.join(f'<sheet name="{escape(n[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(nomes))
               + '</sheets></workbook>')
    z.writestr('xl/_rels/workbook.xml.rels',
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + ''.join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(nomes)))
               + f'<Relationship Id="rId{len(nomes) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
               '</Relationships>')
    # estilo 1 = número com 2 casas e separador de milhar
    z.writestr('xl/styles.xml',
               '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
               '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
               '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
               '<cellXfs count="3"><xf/><xf numFmtId="4" applyNumberFormat="1"/><xf fontId="1" applyFont="1"/></cellXfs>'
               '</styleSheet>')
    for i, n in enumerate(nomes):
        partes = []
        for r, linha in enumerate(abas[n]):
            cels = []
            for c, v in enumerate(linha):
                ref = f'{_nome_col(c)}{r + 1}'
                if v is None or v == '':
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cels.append(f'<c r="{ref}" s="1"><v>{v}</v></c>')
                else:
                    st = ' s="2"' if r == 0 else ''
                    cels.append(f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{escape(str(v))}</t></is></c>')
            partes.append(f'<row r="{r + 1}">{"".join(cels)}</row>')
        z.writestr(f'xl/worksheets/sheet{i + 1}.xml',
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                   + ''.join(partes) + '</sheetData></worksheet>')
    z.close()
    return buf.getvalue()


def ler_csv(texto):
    import csv
    amostra = texto[:2000]
    sep = ';' if amostra.count(';') > amostra.count(',') else ','
    if '\t' in amostra and amostra.count('\t') > amostra.count(sep):
        sep = '\t'
    return [l for l in csv.reader(io.StringIO(texto), delimiter=sep) if any(x.strip() for x in l)]
