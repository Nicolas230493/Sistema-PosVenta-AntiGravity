from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import mm
from reportlab.lib.units import mm as unit_mm
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
from PIL import Image

def generate_product_labels(buffer, products):
    # Tamaño de etiqueta sugerido: 30x20mm
    # Ajustamos a 32x22 para dar un pequeño margen
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
