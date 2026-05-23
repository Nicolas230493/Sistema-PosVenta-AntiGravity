import io
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors

def generate_sale_pdf(response, sale):
    """Reporte A4 estándar para una venta individual (Versión Pro)"""
    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    
    p.setFont("Helvetica-Bold", 20)
    p.setFillColor(colors.HexColor("#1e293b"))
    p.drawString(margin, height - margin, "IMPULSO SMART")
    
    p.setFont("Helvetica", 10)
    p.setFillColor(colors.grey)
    p.drawString(margin, height - margin - 0.8 * cm, "Comprobante de Operación")
    
    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(margin, height - margin - 2.5 * cm, f"VENTA #{sale.id}")
    p.setFont("Helvetica", 10)
    p.drawString(margin, height - margin - 3.2 * cm, f"Fecha: {sale.date.strftime('%d/%m/%Y %H:%M')}")
    
    y = height - margin - 5 * cm
    p.setFont("Helvetica-Bold", 11)
    p.drawString(margin, y, "Producto")
    p.drawRightString(width - margin, y, "Subtotal")
    p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
    
    y -= 0.8 * cm
    p.setFont("Helvetica", 10)
    for detail in sale.details.all():
        unit = getattr(detail.product, 'unit', '') if detail.product else ''
        unit_display = " un." if unit == 'unidad' else f" {unit}" if unit else ""
        p.drawString(margin, y, f"{detail.quantity}{unit_display} x {detail.product.name if detail.product else 'Prod. Eliminado'} (IVA {detail.tax_rate}%)")
        p.drawRightString(width - margin, y, f"${detail.subtotal}")
        y -= 0.5 * cm
        
    p.line(margin, y, width - margin, y)
    y -= 0.6 * cm
    p.setFont("Helvetica", 10)
    if sale.discount_amount > 0:
        p.drawRightString(width - margin, y, f"Descuento: -${sale.discount_amount}")
        y -= 0.5 * cm
    if sale.surcharge_amount > 0:
        p.drawRightString(width - margin, y, f"Recargo: +${sale.surcharge_amount}")
        y -= 0.5 * cm
        
    p.setFont("Helvetica-Bold", 12)
    p.drawRightString(width - margin, y - 0.5 * cm, f"TOTAL: ${sale.total_amount}")
    p.setFont("Helvetica", 9)
    p.drawRightString(width - margin, y - 1.2 * cm, f"Incluye IVA: ${sale.tax_amount}")
    
    p.setFont("Helvetica-Oblique", 8)
    p.drawCentredString(width/2, 1.5 * cm, "2026 Impulso Digital Sgo - Todos los derechos reservados")
    p.showPage()
    p.save()

def generate_thermal_ticket(sale):
    """Genera un ticket para impresora térmica de 80mm"""
    buffer = io.BytesIO()
    width = 80 * mm
    # Altura dinámica basada en la cantidad de productos
    height = 100 * mm + (sale.details.count() * 10 * mm)
    
    p = canvas.Canvas(buffer, pagesize=(width, height))
    
    p.setFont("Helvetica-Bold", 12)
    p.drawCentredString(width/2, height - 10*mm, "IMPULSO SMART")
    p.setFont("Helvetica", 8)
    p.drawCentredString(width/2, height - 15*mm, f"Ticket #{sale.id}")
    p.drawCentredString(width/2, height - 19*mm, sale.date.strftime('%d/%m/%Y %H:%M'))
    
    p.line(5*mm, height - 22*mm, 75*mm, height - 22*mm)
    
    y = height - 27*mm
    p.setFont("Helvetica-Bold", 7)
    p.drawString(5*mm, y, "CANT.  PRODUCTO")
    p.drawRightString(75*mm, y, "TOTAL")
    
    y -= 4*mm
    p.setFont("Helvetica", 7)
    for detail in sale.details.all():
        unit = getattr(detail.product, 'unit', '') if detail.product else ''
        unit_display = " un." if unit == 'unidad' else f" {unit}" if unit else ""
        product_name = (detail.product.name[:18] if detail.product else "Prod. Eliminado")
        
        p.drawString(5*mm, y, f"{detail.quantity}{unit_display}")
        p.drawString(20*mm, y, product_name)
        p.drawRightString(75*mm, y, f"${detail.subtotal}")
        y -= 4*mm
    
    p.line(5*mm, y, 75*mm, y)
    y -= 4*mm
    p.setFont("Helvetica", 7)
    if sale.discount_amount > 0:
        p.drawString(5*mm, y, "Descuento:")
        p.drawRightString(75*mm, y, f"-${sale.discount_amount}")
        y -= 4*mm
    if sale.surcharge_amount > 0:
        p.drawString(5*mm, y, "Recargo:")
        p.drawRightString(75*mm, y, f"+${sale.surcharge_amount}")
        y -= 4*mm

    p.setFont("Helvetica-Bold", 10)
    p.drawString(5*mm, y, "TOTAL:")
    p.drawRightString(75*mm, y, f"${sale.total_amount}")
    y -= 4*mm
    p.setFont("Helvetica", 6)
    p.drawString(5*mm, y, f"IVA Incluido: ${sale.tax_amount}")
    
    y -= 6*mm
    p.setFont("Helvetica", 8)
    p.drawString(5*mm, y, f"Método: {sale.payment_method.name if sale.payment_method else '---'}")
    
    y -= 10*mm
    p.setFont("Helvetica-Bold", 9)
    p.drawCentredString(width/2, y, "¡GRACIAS POR SU COMPRA!")
    
    y -= 6*mm
    p.setFont("Helvetica-Oblique", 6)
    p.drawCentredString(width/2, y, "2026 Impulso Digital Sgo")
    
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer

def generate_total_sales_report(sales):
    """Genera un reporte consolidado de ventas en A4"""
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    w, h = A4
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(2*cm, h - 2*cm, "IMPULSO SMART - REPORTE DE RECAUDACIÓN")
    
    y = h - 4*cm
    p.setFont("Helvetica-Bold", 10)
    p.drawString(2*cm, y, "ID")
    p.drawString(4*cm, y, "Fecha/Hora")
    p.drawString(10*cm, y, "Vendedor")
    p.drawRightString(19*cm, y, "Monto")
    p.line(2*cm, y-0.2*cm, 19*cm, y-0.2*cm)
    
    y -= 0.8*cm
    p.setFont("Helvetica", 9)
    total_general = 0
    for s in sales:
        p.drawString(2*cm, y, f"#{s.id}")
        p.drawString(4*cm, y, s.date.strftime("%d/%m/%Y %H:%M"))
        p.drawString(10*cm, y, s.user.username if s.user else "Admin")
        p.drawRightString(19*cm, y, f"${s.total_amount}")
        total_general += s.total_amount
        y -= 0.5*cm
        if y < 3*cm: p.showPage(); y = h - 2*cm
        
    p.line(2*cm, y+0.2*cm, 19*cm, y+0.2*cm)
    p.setFont("Helvetica-Bold", 12)
    p.drawRightString(19*cm, y-0.6*cm, f"RECAUDACIÓN TOTAL: ${total_general}")
    
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer

