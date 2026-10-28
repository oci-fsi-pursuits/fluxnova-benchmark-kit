#!/usr/bin/env python3
"""Render a one-page research brief from reviewed JSON and an original logo."""
import argparse
from html import escape
import hashlib
import json
from pathlib import Path


def build(data,logo,output):
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph,Table,TableStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.utils import ImageReader
    from pypdf import PdfReader
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF
    font_root=Path('C:/Windows/Fonts')
    normal,bold='Helvetica','Helvetica-Bold'
    if (font_root/'arial.ttf').exists():
        for name,file in [('BriefArial','arial.ttf'),('BriefArial-Bold','arialbd.ttf')]:pdfmetrics.registerFont(TTFont(name,str(font_root/file)))
        normal,bold='BriefArial','BriefArial-Bold'
        pdfmetrics.registerFontFamily(normal,normal=normal,bold=bold)
    assert not output.exists(),'Use a fresh output filename'
    output.parent.mkdir(parents=True,exist_ok=True)
    c=canvas.Canvas(str(output),pagesize=(612,792));c.setTitle(data['title']);c.setAuthor(data['author'])
    c.setSubject('Research findings, OCI value and benchmark adoption')
    ink=colors.HexColor('#173f39');blue=colors.HexColor('#0076e8');gray=colors.HexColor('#46556b')
    styles={
      'title':ParagraphStyle('title',fontName=bold,fontSize=20,leading=23,textColor=colors.black),
      'body':ParagraphStyle('body',fontName=normal,fontSize=9.2,leading=12.1,textColor=ink),
      'small':ParagraphStyle('small',fontName=normal,fontSize=8.0,leading=10.1,textColor=gray),
      'head':ParagraphStyle('head',fontName=bold,fontSize=11.3,leading=14,textColor=colors.black),
      'cell':ParagraphStyle('cell',fontName=normal,fontSize=8.8,leading=11.2,textColor=ink),
      'tablehead':ParagraphStyle('tablehead',fontName=bold,fontSize=8.8,leading=11,textColor=colors.white),
      'foot':ParagraphStyle('foot',fontName=normal,fontSize=7.1,leading=9.1,textColor=gray)}
    boxes=[]
    def para(text,y,kind='body',x=40,w=532):
        p=Paragraph(text,styles[kind]);_,h=p.wrap(w,1000);p.drawOn(c,x,y-h)
        boxes.append({'kind':kind,'left':x,'right':x+w,'top':y,'bottom':y-h})
        return y-h
    image=ImageReader(str(logo));iw,ih=image.getSize();c.drawImage(image,40,738,width=155,height=155*ih/iw,mask='auto')
    c.setFillColor(gray);c.setFont(normal,8.5);c.drawRightString(572,758,'Research brief | '+data['date_label'])
    c.drawRightString(572,744,'Evidence and adoption')
    y=para('Benchmarking Fluxnova for Financial<br/>and Agentic Payment Workflows',729,'title')
    y=para('<b>'+escape(data['author'])+'</b> | Research and OCI benchmark implementation',y-7)
    y=para('<b>Thesis.</b> '+escape(data['thesis']),y-10)
    y=para(data['intro'],y-7)
    y-=11
    labels=['Define the workload','Run a baseline','Change one factor','Verify the outcomes','Share the evidence']
    for i,label in enumerate(labels):
        x=40+i*108;c.setFillColor(colors.HexColor('#edf5f2'));c.setStrokeColor(colors.HexColor('#bed6cd'))
        c.roundRect(x,y-29,100,29,4,fill=1,stroke=1)
        p=Paragraph(escape(label),ParagraphStyle('flow',parent=styles['small'],fontName=bold,alignment=1,textColor=ink))
        _,h=p.wrap(92,40);p.drawOn(c,x+4,y-14.5-h/2)
        if i<4:
            c.setStrokeColor(blue);c.line(x+102,y-14.5,x+106,y-14.5)
    y-=40;y=para('What the completed studies show',y,'head')
    table_rows=[[Paragraph(v,styles['tablehead']) for v in ['Comparison','Observed result','Interpretation']]]
    for row in data['results']:table_rows.append([Paragraph(v,styles['cell']) for v in row])
    table=Table(table_rows,colWidths=[102,229,201]);table.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),ink),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#edf5f2'),colors.white]),
        ('LINEBELOW',(0,-1),(-1,-1),.5,colors.HexColor('#bed6cd'))]))
    _,h=table.wrap(532,700);table.drawOn(c,40,y-7-h);y-=h+15
    y=para(data['mixed_update'],y,'small');y=para(data['limits'],y-5,'small')
    y-=13
    left=para('Why OCI fits',y,'head',w=300)
    for text in data['oci']:left=para(text,left-6,w=300)
    right=para('Use the benchmark',y,'head',x=360,w=212)
    right=para(data['adoption'],right-6,x=360,w=212)
    qr=QrCodeWidget(data['repo_url']);bounds=qr.getBounds();size=73;qx=360;qtop=right-9
    drawing=Drawing(size,size,transform=[size/(bounds[2]-bounds[0]),0,0,size/(bounds[3]-bounds[1]),0,0]);drawing.add(qr)
    renderPDF.draw(drawing,c,qx,qtop-size)
    c.linkURL(data['repo_url'],(qx,qtop-size,qx+size,qtop),relative=0,thickness=0)
    para('<b>Source and guide</b><br/><link href="'+escape(data['repo_url'],quote=True)+'" color="#0065bc">oci-fsi-pursuits/<br/>fluxnova-benchmarking</link>',qtop-7,'small',x=442,w=130)
    y=min(left,qtop-size)-12
    y=para(data['next'],y,'small')
    y=para('<b>Research citation.</b> '+escape(data['citation']),y-8,'foot')
    links=' &nbsp; '.join(f'<link href="{escape(s["url"],quote=True)}" color="#0065bc">[{i}] {escape(s["label"])}</link>' for i,s in enumerate(data['sources'],1))
    y=para(links,y-5,'foot')
    y=para(data['credit'],y-4,'foot')
    if y<27:raise ValueError('One-page content exceeds bottom margin: '+str(y))
    c.showPage();c.save();reader=PdfReader(str(output));assert len(reader.pages)==1
    text=reader.pages[0].extract_text();assert data['author'] in text and '32.1%' in text and '1,200/1,200' in text
    uris=[a.get_object().get('/A',{}).get('/URI') for a in reader.pages[0].get('/Annots',[])]
    assert all(u.startswith('https://') for u in uris if u)
    return {'pages':1,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'bottom_margin_pt':y,
        'links':len([u for u in uris if u]),'logo_sha256':hashlib.sha256(logo.read_bytes()).hexdigest(),'text':text,'layout':boxes}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--content',type=Path,required=True)
    parser.add_argument('--logo',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--audit',type=Path,required=True);args=parser.parse_args()
    result=build(json.loads(args.content.read_text(encoding='utf-8')),args.logo,args.output)
    args.audit.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k not in ('layout','text')}))


if __name__=='__main__':main()
