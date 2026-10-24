"""SPEC-IMP-04: importador tolerante a fallos para archivos CSV / Excel."""
import csv
import io
import re
import unicodedata
import uuid
from decimal import Decimal, InvalidOperation

from django.db import transaction

from suppliers.models import Supplier

from .models import Category, Product

ENCODINGS = ('utf-8-sig', 'utf-8', 'latin-1')

# Cabecera normalizada -> campo del modelo Product
PRODUCT_COLUMN_ALIASES = {
    'name': ('nombre', 'name', 'producto', 'descripcion_producto', 'articulo'),
    'sku': ('sku', 'codigo', 'codigo_interno', 'cod'),
    'barcode': ('barcode', 'codigo_de_barras', 'codigo_barras', 'ean'),
    'price': ('precio_venta', 'precio', 'p_venta', 'price', 'sale_price', 'pventa'),
    'cost_price': ('precio_costo', 'costo', 'p_costo', 'cost_price', 'cost', 'pcosto'),
    'stock': ('stock', 'existencia', 'cantidad', 'stock_actual'),
    'min_stock': ('stock_minimo', 'stock_min', 'min_stock', 'minimo'),
    'supplier': ('proveedor', 'supplier', 'supplier_name', 'supplier__name'),
    'category': ('categoria', 'category', 'rubro', 'category__name'),
    'description': ('descripcion', 'description', 'detalle'),
    'unit': ('unidad', 'unit', 'unidad_de_medida'),
}
_ALIAS_LOOKUP = {alias: field for field, aliases in PRODUCT_COLUMN_ALIASES.items() for alias in aliases}

UNIT_ALIASES = {
    'unidad': 'unidad', 'un': 'unidad', 'u': 'unidad', 'unid': 'unidad',
    'kg': 'kg', 'kilo': 'kg', 'kilos': 'kg', 'kilogramo': 'kg',
    'lt': 'litro', 'l': 'litro', 'litro': 'litro', 'litros': 'litro',
    'm': 'm', 'metro': 'm', 'caja': 'caja', 'paquete': 'paquete',
}


def decode_bytes(raw):
    """Decodifica probando utf-8-sig -> utf-8 -> latin-1 (latin-1 nunca falla)."""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')


def detect_delimiter(text):
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=',;').delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample else ''
        return ';' if first_line.count(';') > first_line.count(',') else ','


def normalize_header(header):
    """'Precio Venta ' / 'precio_venta' / 'PRECIO' -> 'precio_venta' / 'precio_venta' / 'precio'."""
    text = unicodedata.normalize('NFKD', str(header or '')).encode('ascii', 'ignore').decode('ascii')
    text = text.strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return text.strip('_')


def map_header(header):
    return _ALIAS_LOOKUP.get(normalize_header(header))


def parse_decimal(value, default=Decimal('0.00')):
    """
    Sanitiza importes: quita '$', espacios y separadores de miles; acepta coma decimal.
    '$ 1.234,50' -> 1234.50 | '1,234.50' -> 1234.50 | '0,350' -> 0.350 | '' -> default
    """
    if value is None:
        return default
    if isinstance(value, (int, float, Decimal)):
        try:
            result = Decimal(str(value))
        except InvalidOperation:
            return default
        return default if not result.is_finite() else result

    text = str(value).replace('$', '').replace('\xa0', '').replace(' ', '').strip()
    if not text or text.lower() in ('nan', 'none', '-'):
        return default

    if ',' in text and '.' in text:
        # El separador que aparece último es el decimal
        if text.rfind(',') > text.rfind('.'):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
    elif ',' in text:
        text = text.replace(',', '.') if text.count(',') == 1 else text.replace(',', '')
    elif text.count('.') > 1:
        text = text.replace('.', '')

    try:
        result = Decimal(text)
    except InvalidOperation:
        return default
    return result if result.is_finite() else default


def _clean_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    return '' if text.lower() in ('nan', 'none') else text


def read_csv_rows(uploaded_file):
    """Devuelve (headers, filas) donde cada fila es (numero_de_linea, dict campo->valor)."""
    text = decode_bytes(uploaded_file.read())
    reader = csv.reader(io.StringIO(text), delimiter=detect_delimiter(text))
    rows = list(reader)
    if not rows:
        return [], []
    headers = [map_header(h) for h in rows[0]]
    data = []
    for line_number, values in enumerate(rows[1:], start=2):
        if not any(str(v).strip() for v in values):
            continue
        data.append((line_number, {h: v for h, v in zip(headers, values) if h}))
    return headers, data


def read_excel_rows(uploaded_file):
    import pandas as pd
    df = pd.read_excel(uploaded_file, dtype=object)
    headers = [map_header(h) for h in df.columns]
    data = []
    for index, values in enumerate(df.itertuples(index=False), start=2):
        row = {h: (None if pd.isna(v) else v) for h, v in zip(headers, values) if h}
        if any(_clean_text(v) for v in row.values()):
            data.append((index, row))
    return headers, data


def _unique_sku():
    while True:
        sku = f"IMP{uuid.uuid4().hex[:8].upper()}"
        if not Product.objects.filter(sku=sku).exists():
            return sku


def import_product_row(row):
    """Crea o actualiza un producto a partir de una fila ya mapeada. Lanza ValueError si es inválida."""
    name = _clean_text(row.get('name'))
    sku = re.sub(r'[^A-Za-z0-9]', '', _clean_text(row.get('sku')))
    barcode = _clean_text(row.get('barcode'))
    if barcode.endswith('.0') and barcode[:-2].isdigit():  # Excel convierte códigos a float
        barcode = barcode[:-2]
    if not name and not sku:
        raise ValueError("la fila no tiene nombre ni SKU")

    defaults = {}
    for field in ('price', 'cost_price', 'stock', 'min_stock'):
        if field in row:
            amount = parse_decimal(row.get(field), default=None)
            if amount is None:
                # Celda vacía -> 0; texto no numérico ('PRECIO_ROTO') invalida la fila (SPEC-QA-13 §13.2)
                raw = _clean_text(row.get(field))
                if raw and raw != '-':
                    raise ValueError(f"valor no numérico en '{field}': '{raw}'")
                amount = Decimal('0')
            if amount < 0:
                raise ValueError(f"valor negativo en '{field}'")
            defaults[field] = amount.quantize(Decimal('0.001') if field in ('stock', 'min_stock') else Decimal('0.01'))
    if 'description' in row:
        defaults['description'] = _clean_text(row.get('description'))
    if 'unit' in row and _clean_text(row.get('unit')):
        defaults['unit'] = UNIT_ALIASES.get(normalize_header(row.get('unit')), 'unidad')
    if barcode:
        defaults['barcode'] = barcode

    supplier_name = _clean_text(row.get('supplier'))
    if supplier_name:
        defaults['supplier'] = Supplier.objects.filter(name__iexact=supplier_name).first() \
            or Supplier.objects.create(name=supplier_name)
    category_name = _clean_text(row.get('category'))
    if category_name:
        defaults['category'] = Category.objects.filter(name__iexact=category_name).first() \
            or Category.objects.create(name=category_name)

    product = Product.objects.filter(sku=sku).first() if sku else None
    if product is None and name:
        product = Product.objects.filter(name__iexact=name).first()

    if product is None:
        if not name:
            raise ValueError(f"SKU '{sku}' no existe y la fila no tiene nombre para crearlo")
        defaults.setdefault('price', Decimal('0.00'))
        return Product.objects.create(name=name, sku=sku or _unique_sku(), **defaults), True

    if name:
        product.name = name
    if sku and product.sku != sku:
        product.sku = sku
    for field, value in defaults.items():
        setattr(product, field, value)
    product.save()
    return product, False


def import_products(uploaded_file):
    """
    Procesa el archivo fila por fila, cada una en su propio savepoint.
    Devuelve (creados, actualizados, advertencias).
    """
    filename = (uploaded_file.name or '').lower()
    if filename.endswith(('.xlsx', '.xls')):
        headers, rows = read_excel_rows(uploaded_file)
    else:
        headers, rows = read_csv_rows(uploaded_file)

    if 'name' not in headers and 'sku' not in headers:
        return 0, 0, ["No se encontró una columna de nombre o SKU en la cabecera."]

    created = updated = 0
    warnings = []
    for line_number, row in rows:
        try:
            with transaction.atomic():
                _, was_created = import_product_row(row)
            if was_created:
                created += 1
            else:
                updated += 1
        except Exception as e:
            warnings.append(f"Fila {line_number} omitida: {e}")
    return created, updated, warnings
