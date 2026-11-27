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
    try:
        logo=ImageReader(BytesIO(base64.b64decode('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')))
        logo.getRGBData()
    except Exception:
        logo=None
    def header(c,d):
        w,h=landscape(A4)
        c.setFillColor(navy);c.rect(0,h-64,w,64,fill=1,stroke=0)
        if logo:c.drawImage(logo,32,h-54,width=180,height=44,preserveAspectRatio=True,anchor='c',mask='auto')
        else:
            c.setFillColor(colors.white);c.setFont('Helvetica-Bold',16);c.drawString(32,h-35,'ECOSUPPLY')
        c.setFillColor(colors.white);c.setFont('Helvetica-Bold',15);c.drawString(230,h-28,'Relatório de desempenho logístico')
        c.setFont('Helvetica',9);c.drawString(230,h-44,'EFI | Inteligência de faturamento')
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
    story.append(table(['Nº faturamento','Nº BM','Cliente','Cidade / região','Operação / faturamento','Placa','Produto / serviço','Volume coletado','Receita'],[[r['billing_id'],r['bm_id'],r['client'],str(r['city'])+' / '+str(r['region']),dt(r['operation_date'])+' / '+dt(r['billing_date']),r['plate'],str(r['product'] or '')+' / '+str(r['service'] or ''),vol(r),money(r['amount'] or 0)] for r in rows],[65,55,120,90,90,55,145,75,75]))
    story.extend([Spacer(1,10),p('Custo estimado pela maior distância de ida e volta por placa/base, multiplicada pelo custo por KM e viagens. Não representa lucro líquido. Volumes exibidos conforme registros importados; unidades não são somadas entre si.')])
    doc.build(story,onFirstPage=header,onLaterPages=header)
    return buf.getvalue()
