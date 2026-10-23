from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from django.utils import timezone

def generate_cash_report_pdf(response, session, sales_summary, payments_summary, expenses, stock_summary=None,
                             forced_adjustments=None, forced_adjustments_total=None):
    """Genera un reporte de cierre de caja (Arqueo de Caja / X-Read)"""
    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4
    margin = 2 * cm
    
    # Encabezado
    p.setFont("Helvetica-Bold", 18)
    p.drawString(margin, height - margin, "REPORTE DE CIERRE DE CAJA")
    
    p.setFont("Helvetica", 10)
    p.drawString(margin, height - margin - 0.8*cm, f"Cajero: {session.user.username if session.user else 'N/A'}")
    p.drawString(margin, height - margin - 1.3*cm, f"Apertura: {timezone.localtime(session.start_date).strftime('%d/%m/%Y %H:%M')}")
    if not session.is_open and session.end_date:
        p.drawString(margin, height - margin - 1.8*cm, f"Cierre: {timezone.localtime(session.end_date).strftime('%d/%m/%Y %H:%M')}")
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
    
    declared_cash = session.declared_cash if session.declared_cash is not None else session.real_final_amount
    if not session.is_open and declared_cash is not None:
        p.drawString(margin, y, "EFECTIVO DECLARADO (ARQUEO CIEGO):")
        p.drawRightString(width - margin, y, f"${declared_cash}")
        y -= 0.6 * cm
        if session.declared_digital is not None:
            p.setFont("Helvetica", 11)
            p.drawString(margin, y, "Digital declarado / esperado:")
            p.drawRightString(width - margin, y, f"${session.declared_digital} / ${session.expected_digital or 0}")
            y -= 0.6 * cm

        expected_cash = session.expected_cash if session.expected_cash is not None else session.expected_final_amount
        diff = session.difference if session.difference is not None else declared_cash - expected_cash
        p.setFont("Helvetica-Bold", 13)
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
        method_name = method.get('payment_method__name') or method.get('payment_method') or 'Sin especificar'
        if method_name != 'Efectivo':
            p.drawString(margin, y, f"Ventas {method_name}:")
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
                
    # 4. Mercadería vendida vs. auto-ajustes por desposte / stock no cargado
    if stock_summary:
        if y < 5 * cm:
            p.showPage()
            y = height - margin
        y -= 0.5 * cm
        p.setFont("Helvetica-Bold", 12)
        p.drawString(margin, y, "MERCADERÍA VENDIDA Y AUTO-AJUSTES")
        p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
        y -= 0.8 * cm

        p.setFont("Helvetica-Bold", 9)
        p.drawString(margin, y, "Producto")
        p.drawRightString(width - margin - 4*cm, y, "Vendido")
        p.drawRightString(width - margin, y, "Auto-ajuste")
        y -= 0.4 * cm

        p.setFont("Helvetica", 9)
        for row in stock_summary:
            unit = f" {row['unit']}" if row['unit'] else ""
            p.drawString(margin, y, row['product'][:50])
            p.drawRightString(width - margin - 4*cm, y, f"{row['sold']:.3f}{unit}")
            p.drawRightString(width - margin, y, f"{row['auto_adjusted']:.3f}{unit}")
            y -= 0.4 * cm
            if y < 3*cm:
                p.showPage()
                y = height - margin
                p.setFont("Helvetica", 9)

    # 5. Panel de Ajustes Forzosos (SPEC-FIN-06 §6.2): AUTO_ADJUST_IN del turno
    if y < 5 * cm:
        p.showPage()
        y = height - margin
    y -= 0.5 * cm
    p.setFont("Helvetica-Bold", 12)
    p.drawString(margin, y, "AJUSTES FORZOSOS (VENTAS EN DESCUBIERTO)")
    p.line(margin, y - 0.2*cm, width - margin, y - 0.2*cm)
    y -= 0.8 * cm
    if not forced_adjustments:
        p.setFont("Helvetica", 9)
        p.drawString(margin, y, "Sin ajustes automáticos en este turno.")
        y -= 0.5 * cm
    else:
        p.setFont("Helvetica-Bold", 9)
        p.drawString(margin, y, "Producto")
        p.drawString(margin + 8*cm, y, "Venta")
        p.drawRightString(width - margin - 4*cm, y, "Ajustado")
        p.drawRightString(width - margin, y, "Equivalente $")
        y -= 0.4 * cm
        p.setFont("Helvetica", 9)
        for row in forced_adjustments:
            p.drawString(margin, y, row['product'][:45])
            p.drawString(margin + 8*cm, y, f"#{row['sale_id']}")
            p.drawRightString(width - margin - 4*cm, y, f"{row['quantity']:.3f} {row['unit']}")
            p.drawRightString(width - margin, y, f"${row['amount']}")
            y -= 0.4 * cm
            if y < 3*cm:
                p.showPage()
                y = height - margin
                p.setFont("Helvetica", 9)
        p.setFont("Helvetica-Bold", 10)
        p.drawString(margin, y, "TOTAL AJUSTES FORZOSOS:")
        p.drawRightString(width - margin, y, f"${forced_adjustments_total}")

    p.showPage()
    p.save()
