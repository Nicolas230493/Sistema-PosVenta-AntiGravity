from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors

def generate_cash_report_pdf(response, session, sales_summary, payments_summary, expenses):
    """Genera un reporte de cierre de caja (Arqueo de Caja / X-Read)"""
    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    
    # Encabezado
    p.setFont("Helvetica-Bold", 18)
    p.drawString(margin, height - margin, "REPORTE DE CIERRE DE CAJA")
    
    p.setFont("Helvetica", 10)
    p.drawString(margin, height - margin - 0.8*cm, f"Cajero: {session.user.username}")
    p.drawString(margin, height - margin - 1.3*cm, f"Apertura: {session.start_date.strftime('%d/%m/%Y %H:%M')}")
    if not session.is_open:
        p.drawString(margin, height - margin - 1.8*cm, f"Cierre: {session.end_date.strftime('%d/%m/%Y %H:%M')}")
    else:
        p.drawString(margin, height - margin - 1.8*cm, "Estado: CAJA ABIERTA (Resumen Parcial)")
    
    y = height - margin - 3.5 * cm
    
    # 1. Resumen de Efectivo
    p.setFont("Helvetica-Bold", 12)
    p.drawString(margin, y, "RESUMEN DE EFECTIVO")
    p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
    y -= 0.8 * cm
    
    p.setFont("Helvetica", 11)
    p.drawString(margin, y, "Monto Inicial (Caja Chica):")
    p.drawRightString(width - margin, y, f"${session.initial_amount}")
    y -= 0.6 * cm
    
    p.drawString(margin, y, "Ventas y Pagos en Efectivo (+):")
    p.drawRightString(width - margin, y, f"${session.total_sales_cash}")
    y -= 0.6 * cm
    
    p.drawString(margin, y, "Egresos y Gastos Manuales (-):")
    p.drawRightString(width - margin, y, f"-${session.total_expenses}")
    y -= 0.6 * cm
    
    p.setFont("Helvetica-Bold", 11)
    p.drawString(margin, y, "TOTAL ESPERADO EN CAJA:")
    p.drawRightString(width - margin, y, f"${session.expected_final_amount}")
    y -= 0.8 * cm
    
    if not session.is_open:
        p.drawString(margin, y, "TOTAL REAL (ARQUEO):")
        p.drawRightString(width - margin, y, f"${session.real_final_amount}")
        y -= 0.6 * cm
        
        diff = session.real_final_amount - session.expected_final_amount
        p.setFont("Helvetica-Bold", 11)
        if diff == 0:
            p.setFillColor(colors.green)
            p.drawString(margin, y, "DIFERENCIA: SIN DIFERENCIAS")
        elif diff > 0:
            p.setFillColor(colors.blue)
            p.drawString(margin, y, f"DIFERENCIA (SOBRANTE): ${diff}")
        else:
            p.setFillColor(colors.red)
            p.drawString(margin, y, f"DIFERENCIA (FALTANTE): ${diff}")
        p.setFillColor(colors.black)
        y -= 1.2 * cm

    # 2. Otros Medios de Pago (No efectivo)
    p.setFont("Helvetica-Bold", 12)
    p.drawString(margin, y, "OTROS MEDIOS DE PAGO (VENTAS)")
    p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
    y -= 0.8 * cm
    
    p.setFont("Helvetica", 10)
    for method in sales_summary:
        if method['payment_method'] != 'CASH':
            p.drawString(margin, y, f"Ventas {method['payment_method']}:")
            p.drawRightString(width - margin, y, f"${method['total']}")
            y -= 0.5 * cm

    y -= 0.5 * cm
    
    # 3. Detalle de Egresos
    if expenses.exists():
        p.setFont("Helvetica-Bold", 12)
        p.drawString(margin, y, "DETALLE DE EGRESOS")
        p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
        y -= 0.8 * cm
        
        p.setFont("Helvetica-Bold", 9)
        p.drawString(margin, y, "Concepto")
        p.drawRightString(width - margin, y, "Monto")
        y -= 0.4 * cm
        
        p.setFont("Helvetica", 9)
        for ex in expenses:
            p.drawString(margin, y, f"{ex.description[:60]}")
            p.drawRightString(width - margin, y, f"${ex.amount}")
            y -= 0.4 * cm
            if y < 3*cm:
                p.showPage()
                y = height - margin
                
    p.showPage()
    p.save()
