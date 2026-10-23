from io import BytesIO
from decimal import Decimal
from PIL import Image
import barcode
from barcode.writer import ImageWriter

from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm as unit_mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from django.utils import timezone


def generate_product_labels(buffer, products):
    # Tamaño de etiqueta sugerido: 30x20mm
    label_width = 32 * unit_mm
    label_height = 22 * unit_mm
    
    c = canvas.Canvas(buffer, pagesize=(label_width, label_height))
    
    for product in products:
        if not product.barcode:
            continue
            
        # Nombre del producto
        c.setFont("Helvetica-Bold", 6)
        c.drawCentredString(label_width / 2, label_height - 3 * unit_mm, product.name[:30])
        
        # Generar código de barras
        code_type = barcode.get_barcode_class('code128')
        bar = code_type(product.barcode, writer=ImageWriter())
        
        # Guardar código de barras en memoria
        bar_buffer = BytesIO()
        bar.write(bar_buffer)
        bar_buffer.seek(0)
        
        # Dibujar código de barras
        img = Image.open(bar_buffer)
        c.drawInlineImage(img, 2 * unit_mm, 5 * unit_mm, width=28 * unit_mm, height=12 * unit_mm)
        
        # Precio
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(label_width / 2, 2 * unit_mm, f"${product.price}")
        
        c.showPage()
    
    c.save()


def generate_inventory_valuation_pdf(buffer, products, report_title="Reporte de Inventario Valorizado"):
    """
    SPEC-AUD-STOCK-06 §6.3.10: Generación de reporte PDF valorizado
    con totales al costo, precio de venta y margen teórico.
    """
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=15 * unit_mm,
        rightMargin=15 * unit_mm,
        topMargin=15 * unit_mm,
        bottomMargin=15 * unit_mm,
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'RepTitle',
        parent=styles['Heading1'],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1e293b"),
        fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        'RepMeta',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748b"),
        fontName="Helvetica",
    )
    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        fontName="Helvetica",
    )
    cell_bold_style = ParagraphStyle(
        'CellBold',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        fontName="Helvetica-Bold",
    )

    now_str = timezone.localtime(timezone.now()).strftime("%d/%m/%Y %H:%M")
    elements = [
        Paragraph("IMPULSO SMART — SISTEMA DE GESTIÓN", meta_style),
        Paragraph(report_title, title_style),
        Paragraph(f"Fecha de emisión: {now_str} | Productos listados: {len(products)}", meta_style),
        Spacer(1, 10),
    ]

    # Tabla de Inventario
    headers = [
        Paragraph("<b>Código / SKU</b>", cell_bold_style),
        Paragraph("<b>Producto</b>", cell_bold_style),
        Paragraph("<b>Categoría</b>", cell_bold_style),
        Paragraph("<b>Stock</b>", cell_bold_style),
        Paragraph("<b>P. Costo</b>", cell_bold_style),
        Paragraph("<b>Val. Costo</b>", cell_bold_style),
        Paragraph("<b>P. Venta</b>", cell_bold_style),
        Paragraph("<b>Val. Venta</b>", cell_bold_style),
    ]
    data = [headers]

    total_stock = Decimal('0.000')
    total_val_cost = Decimal('0.00')
    total_val_sale = Decimal('0.00')

    for p in products:
        val_cost = (p.stock * p.cost_price).quantize(Decimal('0.01'))
        val_sale = (p.stock * p.price).quantize(Decimal('0.01'))
        
        total_stock += p.stock
        total_val_cost += val_cost
        total_val_sale += val_sale

        sku_display = p.barcode or p.sku or '---'
        cat_display = p.category.name if p.category else '---'
        stock_display = f"{p.stock:g} {p.get_unit_display()}"

        data.append([
            Paragraph(sku_display, cell_style),
            Paragraph(p.name[:35], cell_style),
            Paragraph(cat_display[:20], cell_style),
            Paragraph(stock_display, cell_style),
            Paragraph(f"${p.cost_price:.2f}", cell_style),
            Paragraph(f"${val_cost:.2f}", cell_style),
            Paragraph(f"${p.price:.2f}", cell_style),
            Paragraph(f"${val_sale:.2f}", cell_style),
        ])

    # Fila de Totales
    data.append([
        Paragraph("<b>TOTALES</b>", cell_bold_style),
        Paragraph(f"<b>{len(products)} ítems</b>", cell_bold_style),
        Paragraph("", cell_bold_style),
        Paragraph(f"<b>{total_stock:g}</b>", cell_bold_style),
        Paragraph("", cell_bold_style),
        Paragraph(f"<b>${total_val_cost:.2f}</b>", cell_bold_style),
        Paragraph("", cell_bold_style),
        Paragraph(f"<b>${total_val_sale:.2f}</b>", cell_bold_style),
    ])

    col_widths = [90, 180, 95, 80, 70, 85, 70, 85]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor("#e2e8f0")),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    elements.append(table)

    # Resumen Financiero
    elements.append(Spacer(1, 15))
    margin_diff = total_val_sale - total_val_cost
    margin_pct = (margin_diff / total_val_sale * Decimal('100')).quantize(Decimal('0.1')) if total_val_sale > 0 else Decimal('0.0')

    summary_text = (
        f"<b>Resumen Valorizado:</b> &nbsp;&nbsp;"
        f"Capital Invertido al Costo: <b>${total_val_cost:.2f}</b> &nbsp;|&nbsp; "
        f"Valor Potencial a la Venta: <b>${total_val_sale:.2f}</b> &nbsp;|&nbsp; "
        f"Margen Bruto Proyectado: <b>${margin_diff:.2f} ({margin_pct}%)</b>"
    )
    elements.append(Paragraph(summary_text, meta_style))

    doc.build(elements)
