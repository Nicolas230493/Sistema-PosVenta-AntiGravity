from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors

def generate_customer_statement_pdf(response, customer, transactions):
    """Genera un resumen de cuenta (ventas y pagos) para un cliente"""
    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    
    p.setFont("Helvetica-Bold", 18)
    p.drawString(margin, height - margin, "RESUMEN DE CUENTA")
    
    p.setFont("Helvetica", 12)
    p.drawString(margin, height - margin - 1*cm, f"Cliente: {customer.full_name}")
    p.drawString(margin, height - margin - 1.6*cm, f"DNI/CUIT: {customer.dni_cuit or 'N/A'}")
    p.setFont("Helvetica-Bold", 14)
    p.setFillColor(colors.red if customer.balance > 0 else colors.black)
    p.drawRightString(width - margin, height - margin - 1*cm, f"SALDO ACTUAL: ${customer.balance}")
    p.setFillColor(colors.black)
    
    y = height - margin - 3 * cm
    p.setFont("Helvetica-Bold", 10)
    p.drawString(margin, y, "Fecha")
    p.drawString(margin + 3*cm, y, "Concepto")
    p.drawRightString(width - margin - 3*cm, y, "Debe (Venta)")
    p.drawRightString(width - margin, y, "Haber (Pago)")
    p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
    
    y -= 0.8 * cm
    p.setFont("Helvetica", 9)
    for t in transactions:
        p.drawString(margin, y, t['date'].strftime('%d/%m/%Y'))
        p.drawString(margin + 3*cm, y, t['concept'])
        if t['type'] == 'SALE':
            p.drawRightString(width - margin - 3*cm, y, f"${t['amount']}")
        else:
            p.drawRightString(width - margin, y, f"${t['amount']}")
        
        y -= 0.5 * cm
        if y < 3*cm:
            p.showPage()
            y = height - margin
            p.setFont("Helvetica", 9)

    p.showPage()
    p.save()
