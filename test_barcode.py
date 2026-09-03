import os
import django
from io import BytesIO
from PIL import Image
import barcode
from barcode.writer import ImageWriter

# Configure Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from django.conf import settings

def test_barcode_generation():
    print("--- Probando generación de código de barras ---")
    barcode_data = "123456789012"
    filename = "test_barcode"
    filepath = os.path.join(settings.MEDIA_ROOT, f"{filename}.png")
    
    print(f"Directorio MEDIA: {settings.MEDIA_ROOT}")
    
    try:
        # Generar código de barras
        code_type = barcode.get_barcode_class('code128')
        bar = code_type(barcode_data, writer=ImageWriter())
        
        # Guardar en archivo
        bar.save(os.path.join(settings.MEDIA_ROOT, filename))
        
        # Verificar
        if os.path.exists(filepath):
            print(f"¡Éxito! Código de barras generado en: {filepath}")
        else:
            print("Error: El archivo no se encontró después de guardarlo.")
            
    except Exception as e:
        print(f"Error durante la generación: {e}")

if __name__ == "__main__":
    test_barcode_generation()
