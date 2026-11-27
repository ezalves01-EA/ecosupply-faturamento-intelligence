from io import BytesIO
from decimal import Decimal
from datetime import datetime
from xml.sax.saxutils import escape
import base64
import re
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, LongTable
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader

def generate(rows, summaries, trips, period, html, money):
    buf=BytesIO()
    navy=colors.HexColor('#102b40')
    blue=colors.HexColor('#2466ad')
    styles=getSampleStyleSheet()
    cell=ParagraphStyle('cell',fontName='Helvetica',fontSize=8,leading=10)
    white=ParagraphStyle('white',parent=cell,textColor=colors.white,fontName='Helvetica-Bold')
    def p(v,style=cell):return Paragraph(escape(str(v if v is not None else '—')),style)
    def table(headers, records, widths):
        t=LongTable([[p(x,white) for x in headers]]+[[p(x) for x in r] for r in records],colWidths=widths,repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),navy),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f0f5f8')]),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,0),(-1,0),1,blue)]))
        return t
    logo=None
    match=re.search(r'data:image/jpeg;base64,([A-Za-z0-9+/=]+)',html)
    if match:
        try:logo=ImageReader(BytesIO(base64.b64decode(match.group(1))))
        except Exception:pass
    def header(c,d):
        w,h=landscape(A4)
        c.setFillColor(navy);c.rect(0,h-64,w,64,fill=1,stroke=0)
        if logo:c.drawImage(logo,32,h-54,width=105,height=44,preserveAspectRatio=True,anchor='c',mask='auto')
        else:
            c.setFillColor(colors.white);c.setFont('Helvetica-Bold',16);c.drawString(32,h-35,'ECOSUPPLY')
        c.setFillColor(colors.white);c.setFont('Helvetica-Bold',15);c.drawString(155,h-28,'Relatório de desempenho logístico')
        c.setFont('Helvetica',9);c.drawString(155,h-44,'EFI | Inteligência de faturamento')
        c.setFillColor(navy);c.setFont('Helvetica',8);c.drawString(32,20,'Uso interno • Simulação de custo logístico');c.drawRightString(w-32,20,'Página '+str(d.page))
    doc=SimpleDocTemplate(buf,pagesize=landscape(A4),leftMargin=32,rightMargin=32,topMargin=82,bottomMargin=35)
    rev=sum((x['revenue'] for x in summaries),Decimal(0));cost=sum((x['cost'] for x in summaries),Decimal(0))
    margin=lambda r,c: f'{(r-c)/r*100:.2f}%'.replace('.',',') if r>0 else '—'
    story=[p(period,styles['Heading2']),p('Regiões: '+', '.join(sorted({str(r['region']) for r in rows}))),p('Viagens por placa/base: '+str(trips)),Spacer(1,12),table(['Receita','Custo estimado','Resultado logístico','Margem'],[[money(rev),money(cost),money(rev-cost),margin(rev,cost)]],[190]*4),Spacer(1,14),p('Resumo por veículo e base',styles['Heading2']),table(['Placa / tipo','Base','Destino de referência','KM total/viagem','R$/KM','Receita','Custo','Margem'],[[x['plate']+' / '+x['type'],x['base'],x['city'],x['km'],money(x['rate']),money(x['revenue']),money(x['cost']),margin(x['revenue'],x['cost'])] for x in summaries],[130,65,115,85,65,100,100,100]),Spacer(1,12),p('Detalhamento do faturamento',styles['Heading2'])]
    def dt(v):
        s=str(v or '')
        return s[8:10]+'/'+s[5:7]+'/'+s[:4] if len(s)==10 else s or '—'
    def vol(r):
        v=r['collected']
        return str(v).replace('.',',')+' '+str(r['unit'] or '') if v is not None and v!=0 else '—'
    story.append(table(['Cliente','Cidade / região','Operação / faturamento','Placa','Produto / serviço','Volume coletado','Receita'],[[r['client'],str(r['city'])+' / '+str(r['region']),dt(r['operation_date'])+' / '+dt(r['billing_date']),r['plate'],str(r['product'] or '')+' / '+str(r['service'] or ''),vol(r),money(r['amount'] or 0)] for r in rows],[145,110,105,65,165,85,85]))
    story.extend([Spacer(1,10),p('Custo estimado pela maior distância de ida e volta por placa/base, multiplicada pelo custo por KM e viagens. Não representa lucro líquido. Volumes exibidos conforme registros importados; unidades não são somadas entre si.')])
    doc.build(story,onFirstPage=header,onLaterPages=header)
    return buf.getvalue()
